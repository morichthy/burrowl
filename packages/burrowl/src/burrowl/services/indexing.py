"""检索投影的维护与基础检索（记忆服务的 IndexSink 实现）。

维护：提交后把该对象的投影标为待更新并同步；reconcile 按 HEAD 补偿；rebuild 强制重建。
检索：在"已就绪且与当前 HEAD 一致"的对象范围内做关键词/向量/身份三路召回并 RRF 融合；
未就绪的对象如实列入 gaps，不悄悄退回其他投影。命中仍需调用方回到固定版本核对。
"""
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from ..core import links as link_syntax
from ..core.config import GraphRoute, IndexConfig
from ..core.errors import BurrowlError
from ..core.hashing import sha256_bytes
from ..index import extract, projections, query_plan
from ..index import graph as graph_projection
from ..index.db import IndexDB
from ..index.embed import Embedder, FakeEmbedder, FastEmbedEmbedder, query_truncated
from ..index.fusion import fuse_routes, owner_key, record_key, rrf
from ..storage.owners import Owner, OwnerIndex
from .memory import concepts
from .memory.claims import ClaimGraph
from .memory.context import Storage

Corpus = Literal["fulltext", "discovery", "source"]
Channel = Literal["identity", "lexical", "dense"]
REVIEW_FILTERS = {"all": None, "reviewed_only": ["accepted"], "unreviewed_only": ["none", "unreviewed"],
                  "exclude_unsafe": ["none", "accepted", "unreviewed", "mixed"]}
VERSIONS = {"fulltext": projections.FULLTEXT_VERSION, "discovery": projections.DISCOVERY_VERSION,
            "graph": graph_projection.GRAPH_VERSION}
BUILDERS = {"fulltext": projections.fulltext, "discovery": projections.discovery}
# 总体就绪必须覆盖检索和图的全部水位。关系页只补图，不能据此宣布材料已能被检索。
REQUIRED_STATES = {(corpus, channel) for corpus in BUILDERS for channel in ("lexical", "vector")} | {
    ("graph", "edges")}


def build_embedder(config: IndexConfig, data_root: Path, *, default_model_dir: Path | None = None) -> Embedder | None:
    if config.vector.kind == "none" or config.embedding.provider == "none":
        return None
    directory = Path(config.embedding.path) if config.embedding.path else default_model_dir
    if directory is None:
        return None
    if not directory.is_absolute():
        directory = data_root / directory
    try:
        return FastEmbedEmbedder(directory, config.embedding.model, config.embedding.dim,
                                 max_tokens=config.embedding.max_tokens, query_prefix=config.embedding.query_prefix,
                                 passage_prefix=config.embedding.passage_prefix)
    except BurrowlError:
        return None  # 模型缺失：向量通道报告 disabled，关键词检索照常


class IndexService:
    def __init__(self, storage: Storage, db: IndexDB, *, concept_owner=None):
        self.storage = storage
        self.db = db
        self.concept_owner = concept_owner or (lambda: None)  # 概念库对象 ID（配置 graph.concept_owner_id）

    @classmethod
    def open(cls, storage: Storage, config: IndexConfig, *, embedder: Embedder | None = None,
             fake_vectors: bool = False, concept_owner=None) -> "IndexService":
        path = Path(config.path)
        path = path if path.is_absolute() else storage.ws.root / path
        if embedder is None and fake_vectors:
            embedder = FakeEmbedder()
        if embedder is None:
            embedder = build_embedder(config, storage.ws.root)
        return cls(storage, IndexDB(path, embedder=embedder), concept_owner=concept_owner)

    # ---------- IndexSink ----------
    def on_commit(self, owner_id: str, head: dict | None) -> None:
        self.db.mark_pending(owner_id, head)
        self.sync_owner(owner_id)

    def status(self, owner_id: str) -> dict[str, Any]:
        owner = self.storage.owners.scan().find(owner_id)
        head = self.storage.store.read_snapshot(owner).head if owner and owner.persisted else None
        states = {(s["corpus"], s["channel"]): s for s in self.db.states([owner_id])}
        details = {}
        ready = True
        for corpus, channel in sorted(REQUIRED_STATES):
            state = states.get((corpus, channel))
            fresh = state is not None and self._fresh(state, owner, head)
            # 只有确实未配置模型的向量通道允许 disabled；缺失或过时的关键词/图水位必须补偿。
            disabled = state is not None and channel == "vector" and self._disabled_ok(state)
            details[f"{corpus}.{channel}"] = (
                "not_indexed" if state is None else "indexed" if fresh else "disabled" if disabled
                else "stale" if state["status"] in {"indexed", "disabled"} else state["status"])
            ready = ready and (fresh or disabled)
        return {"index_status": "indexed" if ready else "pending", "details": details}

    # ---------- 维护 ----------
    def _model_ok(self, state: dict) -> bool:
        """向量水位要对上当前模型指纹：换模型或换切窗规则（R12 T-P0.1）后，旧水位不能冒充就绪。"""
        return state["channel"] != "vector" or self.db.embedder is None \
            or state["model_fp"] == self.db.embedder.fingerprint

    def _disabled_ok(self, state: dict) -> bool:
        """disabled 只在当前确实没有模型时才算正常；装上模型后要补建，并在补建前报告缺口。"""
        return state["channel"] == "vector" and state["status"] == "disabled" and self.db.embedder is None

    def _fresh(self, state: dict, owner: Owner | None, head: dict | None) -> bool:
        return (owner is not None and state["status"] == "indexed" and json.loads(state["head"] or "null") == head
                and state["owner_fingerprint"] in (None, owner.fingerprint)
                and state["projection_version"] == VERSIONS.get(state["corpus"]) and self._model_ok(state))

    def _project(self, owner: Owner, graph: ClaimGraph, *,
                 graph_only: bool = False) -> tuple[dict | None, dict[str, list[dict]], tuple]:
        snapshot = self.storage.store.read_snapshot(owner) if owner.persisted else None
        records = list(snapshot.records.values()) if snapshot else []
        badges = {cid: graph.badge(cid) for cid, (_c, r) in graph.claims.items() if r["owner_id"] == owner.owner_id}
        hidden = {r["record_id"] for r in records
                  if any(e["code"] in {"ACCESS_DENIED", "UNSAFE_PATH"} for e in graph.access_errors(r["record_id"]))}
        info = projections.OwnerInfo(owner.owner_id, owner.owner_type, owner.title, owner.sensitivity,
                                     owner.fingerprint, tuple(owner.native.get("keywords", [])),
                                     str(owner.native.get("description") or owner.native.get("question") or ""))
        maturity = {cid: graph.maturity(cid)["maturity"] for cid in badges}
        parent = owner.native.get("owner_id") if owner.owner_type == "run" else None
        return (snapshot.head if snapshot else None,
                {} if graph_only else {corpus: build(info, records, badges, hidden) for corpus, build in BUILDERS.items()},
                graph_projection.graph(info, records, badges, hidden, maturity, produced_by=parent))

    def ensure_graph(self) -> dict:
        """只补齐不新鲜的图投影（关系页和图路线用）；不重算检索条目、不调用嵌入模型。"""
        index = self.storage.owners.scan()
        states = {s["owner_id"]: s for s in self.db.states() if s["corpus"] == "graph"}
        graph: ClaimGraph | None = None
        synced = []
        for owner in index.owners:
            if not owner.readable:
                if owner.owner_id in states:
                    self.db.drop_owner(owner.owner_id)
                continue
            head = self.storage.store.read_snapshot(owner).head if owner.persisted else None
            state = states.get(owner.owner_id)
            if state and self._fresh(state, owner, head):
                continue
            graph = graph or ClaimGraph(self.storage, index)
            head, _projected, (nodes, edges) = self._project(owner, graph, graph_only=True)
            self.db.sync_graph(owner.owner_id, nodes, edges, head=head, version=VERSIONS["graph"],
                               owner_fingerprint=owner.fingerprint,
                               signature=graph_projection.signature(nodes, edges))
            synced.append(owner.owner_id)
        known = {o.owner_id for o in index.owners}
        for owner_id in set(states) - known:
            self.db.drop_owner(owner_id)
        return {"synced": synced, "dangling_edges": self.db.graph_dangling()}

    def sync_owner(self, owner_id: str, *, graph: ClaimGraph | None = None, index: OwnerIndex | None = None) -> dict:
        index = index or self.storage.owners.scan()
        owner = index.get(owner_id)
        if not owner.readable:
            self.db.drop_owner(owner_id)
            return {"owner_id": owner_id, "status": "removed", "reason": "access_denied"}
        graph = graph or ClaimGraph(self.storage, index)
        head, projected, (nodes, edges) = self._project(owner, graph)
        result: dict[str, Any] = {"owner_id": owner_id, "generation": head["generation"] if head else 0}
        # R12 T-P2.7：图投影与检索条目同批更新；图只是导航，失败不影响其他通道
        result["graph"] = self.db.sync_graph(owner_id, nodes, edges, head=head, version=VERSIONS["graph"],
                                             owner_fingerprint=owner.fingerprint,
                                             signature=graph_projection.signature(nodes, edges))
        for corpus, entries in projected.items():
            signature = projections.signature(entries, VERSIONS[corpus])
            lexical = self.db.sync_lexical(owner_id, corpus, entries, head=head, version=VERSIONS[corpus],
                                           owner_fingerprint=owner.fingerprint, signature=signature)
            try:
                vector = self.db.sync_vectors(owner_id, corpus, entries, head=head, version=VERSIONS[corpus],
                                              signature=signature)
            except Exception as exc:  # noqa: BLE001 - 向量失败只影响该通道，已写入 failed 水位
                vector = {"status": "failed", "error": type(exc).__name__}
            result[corpus] = {"entries": len(entries), "lexical": lexical, "vector": vector}
        try:
            result["candidates"] = self._sync_candidates(owner)
        except Exception as exc:  # noqa: BLE001 - 候选只是线索，失败不影响检索通道
            result["candidates"] = {"status": "failed", "error": type(exc).__name__}
        return result

    # ---------- 关联候选（R12 T-P2.10）----------
    SIMILAR_LIMIT, SIMILAR_MIN = 5, 0.75
    CANDIDATE_SKIP = {"review", "policy", "feedback", "association", "representation", "consolidation"}

    def _concept_names(self) -> list[tuple[str, str]]:
        library = self.storage.owners.scan().find(self.concept_owner() or "")
        if library is None or not library.readable or not library.persisted:
            return []
        return [(name, record["record_id"]) for record in self.storage.store.read_snapshot(library).records.values()
                if concepts.is_concept(record) and concepts.status(record) == "active"
                and record["sensitivity"] != "restricted" for name in concepts.names(record)]

    def _sync_candidates(self, owner: Owner) -> dict:
        """只为本次变化的记录计算候选：①正文提到了概念名或别名却没有链接；②向量近邻（前 5、相似度 ≥ 0.75）。"""
        snapshot = self.storage.store.read_snapshot(owner) if owner.persisted else None
        records = {rid: r for rid, r in (snapshot.records.items() if snapshot else [])
                   if r["kind"] not in self.CANDIDATE_SKIP and r["sensitivity"] != "restricted"}
        state = self.db.candidate_state(owner.owner_id)
        signature = self.db.embedder.fingerprint if self.db.embedder else "none"
        changed = {rid: r for rid, r in records.items() if state.get(rid) != f"{r['record_hash']}:{signature}"}
        names = self._concept_names() if changed else []
        computed: dict[str, tuple[str, list[dict]]] = {}
        for rid, record in changed.items():
            rows = []
            linked = {x["ref"]["target_id"] for x in record.get("links", []) if x.get("ref")}
            text = "\n".join(link_syntax.link_texts(record))
            for name, concept_id in names:
                if concept_id != rid and concept_id not in linked and query_plan.contains(text, name):
                    rows.append({"src": rid, "dst": concept_id, "kind": "concept_mention", "score": 1.0})
            for other, similarity in self.db.nearest_records(f"fulltext:{owner.owner_id}:{rid}",
                                                              limit=self.SIMILAR_LIMIT, min_similarity=self.SIMILAR_MIN):
                if other not in linked:
                    rows.append({"src": rid, "dst": other, "kind": "similar", "score": similarity})
            computed[rid] = (f"{record['record_hash']}:{signature}", list({(r["dst"], r["kind"]): r for r in rows}.values()))
        removed = set(state) - set(records)
        if computed or removed:
            self.db.replace_candidates(owner.owner_id, computed, removed)
        return {"computed": len(computed), "removed": len(removed)}

    def reconcile(self, owner_id: str | None = None, *, force: bool = False) -> dict:
        """按当前 HEAD 补齐所有不新鲜的投影；删除已消失对象的派生数据。从不写记忆记录。"""
        index = self.storage.owners.scan()
        graph = ClaimGraph(self.storage, index)
        targets = [index.get(owner_id)] if owner_id else index.owners
        results = []
        for owner in targets:
            head = self.storage.store.read_snapshot(owner).head if owner.persisted else None
            states = self.db.states([owner.owner_id])
            fresh = states and all(self._fresh(s, owner, head) or self._disabled_ok(s) for s in states)
            if force or not fresh or not REQUIRED_STATES <= {(s["corpus"], s["channel"]) for s in states}:
                results.append(self.sync_owner(owner.owner_id, graph=graph, index=index))
        removed = []
        if owner_id is None:
            known = {o.owner_id for o in index.owners}
            for state in self.db.states():
                if state["corpus"] == "source":  # 来源文件由 sync_sources 单独维护
                    continue
                if state["owner_id"] not in known and state["owner_id"] not in removed:
                    self.db.drop_owner(state["owner_id"])
                    removed.append(state["owner_id"])
        out = {"synced": results, "removed_owners": removed, "writes_to_memory": 0}
        if self.db.rebuilt_from is not None:  # 索引结构升级后整库重建过（R12 T-P1.5）
            out["index_rebuilt_from"] = self.db.rebuilt_from
        return out

    LONG_BLOCK_TOKENS = 1500  # 超过这个长度的 L1 块不便整块阅读，也超出多数嵌入模型的单窗口

    def gaps(self) -> list[dict]:
        """确定性的发现缺口（内容是否充分仍需 AI 阅读判断）：
        长期对象缺 L4、技术单元缺检索说明；R12 T-P2.11 起还有：L1 块过长、结果块没有声明依赖、
        有未解析的概念链接、概念页没有任何入边、结论有未处理的反例、有待确认的成熟度升级提议。"""
        out = []
        index = self.storage.owners.scan()
        count = self.db.embedder.count_tokens if self.db.embedder is not None else len
        library = self.concept_owner()
        for owner in index.readable():
            if not owner.persisted or owner.owner_type == "run":
                continue
            records = self.storage.store.read_snapshot(owner).records.values()
            if owner.owner_type != "knowledge" and not any(r["kind"] == "overview" for r in records):
                out.append({"owner_id": owner.owner_id, "gap": "missing_overview"})
            for record in records:
                where = {"owner_id": owner.owner_id, "record_id": record["record_id"]}
                if record["kind"] == "detail":
                    if not record["payload"]["retrieval_description"]["question"].strip():
                        out.append(where | {"gap": "empty_retrieval_description"})
                    for block in record["payload"]["blocks"]:
                        if count(block["markdown"]) > self.LONG_BLOCK_TOKENS:
                            out.append(where | {"gap": "long_block", "block_id": block["block_id"]})
                        if block["role"] == "results" and not block["requires_block_ids"]:
                            out.append(where | {"gap": "results_without_dependency", "block_id": block["block_id"]})
                missing = [x["text"] for x in record.get("links", []) if x["status"] == "unresolved_concept"]
                if missing:
                    out.append(where | {"gap": "missing_concept", "names": missing})
        self.ensure_graph()
        if library:
            incoming = {e["dst"] for e in self.db.graph_all_edges() if e["type"] != "belongs_to"}
            for node in self.db.graph_nodes():
                if node["node_type"] == "concept" and node["owner_id"] == library and node["node_id"] not in incoming:
                    out.append({"owner_id": library, "record_id": node["node_id"], "gap": "orphan_concept"})
        graph = ClaimGraph(self.storage, index)
        for cid, (_claim, record) in sorted(graph.claims.items()):
            if record["sensitivity"] == "restricted":
                continue
            where = {"owner_id": record["owner_id"], "record_id": record["record_id"], "claim_id": cid}
            if graph.stats(cid)["open_counterexamples"]:
                out.append(where | {"gap": "open_counterexample"})
            if graph.maturity(cid)["requested_maturity"]:
                out.append(where | {"gap": "maturity_requested"})
        return out

    # ---------- 来源文件投影 ----------
    @staticmethod
    def _file_head(path: Path) -> dict:
        stat = path.stat()
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    def sync_sources(self, *, ocr_enabled: bool = True, max_bytes: int = 50 * 1024 * 1024) -> dict:
        """抽取已登记且可读的来源文件；未变化（大小与修改时间相同）的跳过，撤销登记的删除。"""
        states = {s["owner_id"]: s for s in self.db.states() if s["corpus"] == "source" and s["channel"] == "lexical"}
        vector_states = {s["owner_id"]: s for s in self.db.states()
                         if s["corpus"] == "source" and s["channel"] == "vector"}
        seen, synced, skipped = set(), [], []
        for entry in self.storage.sources.entries():
            source_id = entry.get("source_id", "")
            try:
                source = self.storage.sources.resolve(source_id)
            except BurrowlError as exc:
                skipped.append({"source_id": source_id, "reason": exc.code})
                continue
            seen.add(source_id)
            head = self._file_head(source.path)
            state = states.get(source_id)
            vector_state = vector_states.get(source_id)
            vector_ok = self.db.embedder is None or (vector_state is not None and vector_state["status"] == "indexed"
                                                    and self._model_ok(vector_state))
            if state and state["status"] == "indexed" and json.loads(state["head"]) == head \
                    and state["projection_version"] == projections.SOURCE_VERSION and vector_ok:
                continue
            if head["size"] > max_bytes:
                skipped.append({"source_id": source_id, "reason": "too_large"})
                continue
            raw = source.path.read_bytes()
            try:
                extracted = extract.extract(source.path.name, raw, cache_dir=self.db.path.parent / "assets",
                                            ocr_enabled=ocr_enabled)
            except BurrowlError as exc:
                skipped.append({"source_id": source_id, "reason": exc.code})
                continue
            entries = projections.source(source_id, entry.get("title") or source.path.name, sha256_bytes(raw),
                                         source.sensitivity, extracted["units"])
            signature = projections.signature(entries, projections.SOURCE_VERSION)
            self.db.sync_lexical(source_id, "source", entries, head=head, version=projections.SOURCE_VERSION,
                                 owner_fingerprint=sha256_bytes(raw), signature=signature)
            try:
                self.db.sync_vectors(source_id, "source", entries, head=head, version=projections.SOURCE_VERSION,
                                     signature=signature)
            except Exception:  # noqa: BLE001 - 向量失败只影响该通道
                pass
            synced.append({"source_id": source_id, "units": len(entries), "warnings": extracted["warnings"]})
        for source_id in set(states) - seen:
            self.db.drop_owner(source_id)
        return {"synced": synced, "skipped": skipped, "removed": sorted(set(states) - seen)}

    def _ready_sources(self, channel: str, source_ids: list[str] | None) -> tuple[list[str], list[dict]]:
        states = {s["owner_id"]: s for s in self.db.states() if s["corpus"] == "source" and s["channel"] == channel}
        ready, gaps = [], []
        wanted = source_ids or [e.get("source_id", "") for e in self.storage.sources.entries()]
        for source_id in wanted:
            try:
                source = self.storage.sources.resolve(source_id)
            except BurrowlError as exc:
                gaps.append({"owner_id": source_id, "channel": channel, "reason": exc.code})
                continue
            state = states.get(source_id)
            if state and state["status"] == "indexed" and json.loads(state["head"]) == self._file_head(source.path) \
                    and self._model_ok(state):
                ready.append(source_id)
            elif not (state and self._disabled_ok(state)):
                gaps.append({"owner_id": source_id, "channel": channel,
                             "reason": state["status"] if state else "not_indexed"})
        return ready, gaps

    # ---------- 检索 ----------
    def ready_owners(self, corpus: Corpus, channel: Literal["lexical", "vector"],
                     owner_ids: list[str] | None = None) -> tuple[list[str], list[dict]]:
        """返回 (可检索的对象, 缺口)。只有水位与当前 HEAD 一致的对象才参与该通道。"""
        if corpus == "source":
            return self._ready_sources(channel, owner_ids)
        index = self.storage.owners.scan()
        candidates = [index.get(o) for o in owner_ids] if owner_ids else index.readable()
        states = {(s["owner_id"]): s for s in self.db.states() if s["corpus"] == corpus and s["channel"] == channel}
        ready, gaps = [], []
        for owner in candidates:
            if not owner.readable:
                gaps.append({"owner_id": owner.owner_id, "channel": channel, "reason": "access_denied"})
                continue
            head = self.storage.store.read_snapshot(owner).head if owner.persisted else None
            state = states.get(owner.owner_id)
            if state and self._fresh(state, owner, head):
                ready.append(owner.owner_id)
            elif not (state and self._disabled_ok(state)):
                gaps.append({"owner_id": owner.owner_id, "channel": channel,
                             "reason": state["status"] if state else "not_indexed"})
        return ready, gaps

    def search(self, query: str, *, corpus: Corpus = "fulltext", channels: tuple[Channel, ...] = ("identity", "lexical", "dense"),
               owner_ids: list[str] | None = None, levels: list[str] | None = None, kinds: list[str] | None = None,
               review_filter: str = "all", limit: int = 20, per_channel: int = 100, group: str = "record") -> dict:
        if review_filter not in REVIEW_FILTERS:
            raise BurrowlError("INVALID_ARGUMENT", "未知的复核过滤", {"review_filter": review_filter})
        common = {"levels": levels, "kinds": kinds, "review": REVIEW_FILTERS[review_filter],
                  "explicit_owners": owner_ids is not None}
        results: dict[str, list[dict]] = {}
        gaps: list[dict] = []
        lexical_owners, lexical_gaps = self.ready_owners(corpus, "lexical", owner_ids)
        gaps.extend(lexical_gaps)
        if "identity" in channels:
            identifiers = [t for t in query.split() if "-" in t and t.split("-", 1)[0].isupper()]
            results["identity"] = self.db.search_identity(corpus, identifiers, lexical_owners)
        if "lexical" in channels:
            results["lexical"] = self.db.search_lexical(corpus, query, lexical_owners, limit=per_channel, **common)
        if "dense" in channels:
            if self.db.embedder is None:
                gaps.append({"channel": "dense", "reason": "embedder_unavailable"})
            else:
                vector_owners, vector_gaps = self.ready_owners(corpus, "vector", owner_ids)
                gaps.extend(vector_gaps)
                if query_truncated(self.db.embedder, query):
                    gaps.append({"channel": "dense", "reason": "query_truncated"})
                results["dense"] =self.db.search_vector(corpus, query, vector_owners, limit=per_channel, **common)
        fused = rrf(results, owner_key if group == "owner" else record_key)
        return {"query": query, "corpus": corpus, "review_filter": review_filter,
                "items": fused[:limit], "total": len(fused), "gaps": gaps,
                "channels": {k: len(v) for k, v in results.items()}}

    def search_plan(self, plan: dict, *, corpus: Corpus = "fulltext", owner_ids: list[str] | None = None,
                    levels: list[str] | None = None, kinds: list[str] | None = None, review_filter: str = "all",
                    group: str = "record", per_route: int = 100, dense: bool = True,
                    explicit_owners: bool | None = None,
                    channels: tuple[Channel, ...] = ("identity", "lexical", "dense"),
                    graph_route: GraphRoute | None = None, graph_limits: tuple[int, int] | None = None,
                    hub_hits: list[dict] | None = None, hub_weight: float = 0.5) -> dict:
        """按冻结的查询计划并行执行各路线并融合。按 Owner 汇总时每个信号族只投一票。

        explicit_owners：调用方是否"显式指定了对象"（决定 owner_only 记录是否可见）；
        默认按 owner_ids 是否给出判断。服务层先按类型等条件过滤出对象列表时应明确传入。
        graph_route：图路线设置（R12 T-P2.9）；为 None 或 enabled=false 时结果与没有图路线时完全一致。
        graph_limits：(最多节点数, 最多边数)，来自调用方的预算；达到上限即停止扩展并在 gaps 中报告。
        hub_hits：中枢路线（R12 T-P4.2）的外部命中（按外部名次排列，含 record_id）；映射回当前投影中通过同样过滤的
        记录级条目后作为一路参与融合。对不上本地的命中不进入融合（调用方作为 external_only 提示）。
        """
        if review_filter not in REVIEW_FILTERS:
            raise BurrowlError("INVALID_ARGUMENT", "未知的复核过滤", {"review_filter": review_filter})
        explicit = owner_ids is not None if explicit_owners is None else explicit_owners
        common = {"levels": levels, "kinds": kinds, "review": REVIEW_FILTERS[review_filter],
                  "explicit_owners": explicit}
        dense = dense and "dense" in channels
        lexical_owners, gaps = self.ready_owners(corpus, "lexical", owner_ids)
        vector_owners: list[str] = []
        if dense and self.db.embedder is not None:
            vector_owners, vector_gaps = self.ready_owners(corpus, "vector", owner_ids)
            gaps = gaps + vector_gaps
        elif dense:
            gaps = gaps + [{"channel": "dense", "reason": "embedder_unavailable"}]
        selected = [r for r in query_plan.routes(plan)
                    if r["channel"] in channels and (r["channel"] != "dense" or vector_owners)]
        embedder = self.db.embedder
        if embedder is not None and any(r["channel"] == "dense" and query_truncated(embedder, r["text"])
                                        for r in selected):
            gaps = gaps + [{"channel": "dense", "reason": "query_truncated"}]  # R12 T-P0.1：截断如实报告，不再报错

        def run(route: dict) -> tuple[dict, list[dict]]:
            if route["channel"] == "identity":
                ids = [t for t in route["text"].split() if "-" in t and t.split("-", 1)[0].isupper()]
                return route, self.db.search_identity(corpus, ids, lexical_owners)
            if route["channel"] == "lexical":
                return route, self.db.search_lexical(corpus, route["text"], lexical_owners, limit=per_route, **common)
            return route, self.db.search_vector(corpus, route["text"], vector_owners, limit=per_route, **common)

        with ThreadPoolExecutor(max_workers=4) as pool:
            batches = list(pool.map(run, selected))
        key = owner_key if group == "owner" else record_key
        fused = fuse_routes(batches, key, by_family=group == "owner")
        graph_stats = None
        if graph_route is not None and graph_route.enabled and fused and corpus != "source":
            rows, graph_stats = self._graph_route(fused, graph_route, corpus=corpus, owner_ids=lexical_owners,
                                                  key=key, limits=graph_limits, **common)
            if graph_stats["truncated"]:
                gaps = gaps + [{"channel": "graph", "reason": "graph_budget_truncated"}]
            if rows:
                batches.append(({"id": "graph", "kind": "graph", "channel": "graph", "weight": graph_route.weight},
                                rows))
                fused = fuse_routes(batches, key, by_family=group == "owner")
        if hub_hits:
            order = {h["record_id"]: rank for rank, h in enumerate(hub_hits)}
            entries = self.db.entries_for_nodes(corpus, list(order), lexical_owners, **common)
            rows = sorted((e | {"channel": "hub", "raw_score": 1 / (1 + order[e["record_id"]])} for e in entries
                           if e.get("block_id") is None and e["kind"] != "claim"), key=lambda r: order[r["record_id"]])
            if rows:
                batches.append(({"id": "hub", "kind": "hub", "channel": "hub", "weight": hub_weight}, rows))
                fused = fuse_routes(batches, key, by_family=group == "owner")
        result = {"plan_id": plan["plan_id"], "corpus": corpus, "items": fused, "gaps": gaps,
                  "routes": [{"id": r["id"], "hits": len(rows)} for r, rows in batches]}
        if graph_stats is not None:
            result["graph"] = graph_stats
        return result

    # ---------- 图路线（R12 §4.11）----------
    MATURITY_FACTOR = {"established": 1.0, "replicated": 0.8, "preliminary": 0.6, "conjecture": 0.4, None: 0.5}
    EXCLUDED_STATES = {"rejected", "withdrawn", "invalid"}

    def _edge_weight(self, edge: dict, weights, maturity: dict[str, str | None]) -> float:
        category = edge["category"]
        if category == "navigation":
            return getattr(weights, f"navigation_{edge['certainty'] or 'plausible'}", weights.navigation_plausible)
        if category == "claim_relation":
            return weights.claim_relation * self.MATURITY_FACTOR.get(maturity.get(edge["declared_by"]), 0.5)
        return getattr(weights, category, 0.0)

    def _graph_route(self, fused: list[dict], settings: GraphRoute, *, corpus: str, owner_ids: list[str], key,
                     levels, kinds, review, explicit_owners,
                     limits: tuple[int, int] | None = None) -> tuple[list[dict], dict]:
        r"""从融合结果的前 seeds 名出发沿图走 hops 跳：
        得分 $s(n)=\sum_{种子} \mathrm{RRF}(种子)\cdot\prod_{路径} w_e\cdot\frac{1}{\log_2(2+\deg n)}$，
        $w_e$ 为边类别权重 × 确定度（或结论成熟度）系数，$\deg n$ 为节点度数（热门概念降权）。
        排除 rejected/withdrawn 关联与失效结论的快捷边；节点映射回当前投影中通过同样过滤的条目，映射不到的丢弃。"""

        self.ensure_graph()
        seeds = {}
        for item in fused[:settings.seeds]:
            node = item["canonical_id"] if item.get("kind") == "claim" else item["record_id"]
            seeds[node] = max(seeds.get(node, 0.0), item["score"])
        seen_seed_keys = {key(item) for item in fused[:settings.seeds]}
        weights = settings.category_weights
        max_nodes, max_edges = limits or (10**9, 10**9)
        frontier, scores, via, visited_edges, truncated = dict(seeds), {}, {}, 0, False
        for _hop in range(settings.hops):
            edges = [e for e in self.db.graph_edges(list(frontier)) if e["state"] not in self.EXCLUDED_STATES]
            if visited_edges + len(edges) > max_edges:
                edges, truncated = edges[:max(0, max_edges - visited_edges)], True
            visited_edges += len(edges)
            claim_ids = sorted({e["declared_by"] for e in edges if e["category"] == "claim_relation"})
            maturity = {n["node_id"]: n["maturity"] for n in self.db.graph_nodes(claim_ids)}
            neighbors: dict[str, list[tuple[float, str, dict]]] = {}
            for edge in edges:
                weight = self._edge_weight(edge, weights, maturity)
                if weight <= 0:
                    continue
                for here, there in ((edge["src"], edge["dst"]), (edge["dst"], edge["src"])):
                    if here in frontier and there not in seeds:
                        neighbors.setdefault(here, []).append((weight, there, edge))
            degree = self.db.graph_degrees(sorted({t for items in neighbors.values() for _w, t, _e in items}))
            following: dict[str, float] = {}
            for here, items in neighbors.items():
                for weight, there, edge in sorted(items, key=lambda x: -x[0])[:settings.fanout]:
                    if there not in scores and len(scores) >= max_nodes:
                        truncated = True
                        continue
                    gain = frontier[here] * weight / math.log2(2 + degree.get(there, 0))
                    scores[there] = scores.get(there, 0.0) + gain
                    following[there] = max(following.get(there, 0.0), gain)
                    via.setdefault(there, []).append({"from": here, "edge_type": edge["type"],
                                                      "category": edge["category"], "declared_by": edge["declared_by"]})
            frontier = following
            if not frontier:
                break
        entries = self.db.entries_for_nodes(corpus, sorted(scores), owner_ids, levels=levels, kinds=kinds,
                                            review=review, explicit_owners=explicit_owners)
        rows = []
        for entry in entries:
            node = f"{entry['record_id']}#{entry['block_id']}" if entry.get("block_id") else (
                entry["canonical_id"] if entry["kind"] == "claim" else entry["record_id"])
            if node not in scores or key(entry) in seen_seed_keys:
                continue  # 种子本身已在前面的路线里，不重复加分
            rows.append(entry | {"channel": "graph", "raw_score": scores[node], "via": via[node][:5]})
        rows.sort(key=lambda r: (-r["raw_score"], r["entry_id"]))
        return rows, {"seeds": len(seeds), "nodes": len(scores), "edges": visited_edges, "hits": len(rows),
                      "truncated": truncated}
