"""检索投影的维护与基础检索（记忆服务的 IndexSink 实现）。

维护：提交后把该对象的投影标为待更新并同步；reconcile 按 HEAD 补偿；rebuild 强制重建。
检索：在"已就绪且与当前 HEAD 一致"的对象范围内做关键词/向量/身份三路召回并 RRF 融合；
未就绪的对象如实列入 gaps，不悄悄退回其他投影。命中仍需调用方回到固定版本核对。
"""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from ..core.config import IndexConfig
from ..core.errors import BurrowlError
from ..core.hashing import sha256_bytes
from ..index import extract, projections, query_plan
from ..index.db import IndexDB
from ..index.embed import Embedder, FakeEmbedder, FastEmbedEmbedder
from ..index.fusion import fuse_routes, owner_key, record_key, rrf
from ..storage.owners import Owner, OwnerIndex
from .memory.claims import ClaimGraph
from .memory.context import Storage

Corpus = Literal["fulltext", "discovery", "source"]
Channel = Literal["identity", "lexical", "dense"]
REVIEW_FILTERS = {"all": None, "reviewed_only": ["accepted"], "unreviewed_only": ["none", "unreviewed"],
                  "exclude_unsafe": ["none", "accepted", "unreviewed", "mixed"]}
VERSIONS = {"fulltext": projections.FULLTEXT_VERSION, "discovery": projections.DISCOVERY_VERSION}
BUILDERS = {"fulltext": projections.fulltext, "discovery": projections.discovery}


def build_embedder(config: IndexConfig, data_root: Path, *, default_model_dir: Path | None = None) -> Embedder | None:
    if config.vector.kind == "none" or config.embedding.provider == "none":
        return None
    directory = Path(config.embedding.path) if config.embedding.path else default_model_dir
    if directory is None:
        return None
    if not directory.is_absolute():
        directory = data_root / directory
    try:
        return FastEmbedEmbedder(directory, config.embedding.model, config.embedding.dim)
    except BurrowlError:
        return None  # 模型缺失：向量通道报告 disabled，关键词检索照常


class IndexService:
    def __init__(self, storage: Storage, db: IndexDB):
        self.storage = storage
        self.db = db

    @classmethod
    def open(cls, storage: Storage, config: IndexConfig, *, embedder: Embedder | None = None,
             fake_vectors: bool = False) -> "IndexService":
        path = Path(config.path)
        path = path if path.is_absolute() else storage.ws.root / path
        if embedder is None and fake_vectors:
            embedder = FakeEmbedder()
        if embedder is None:
            embedder = build_embedder(config, storage.ws.root)
        return cls(storage, IndexDB(path, embedder=embedder))

    # ---------- IndexSink ----------
    def on_commit(self, owner_id: str, head: dict | None) -> None:
        self.db.mark_pending(owner_id, head)
        self.sync_owner(owner_id)

    def status(self, owner_id: str) -> dict[str, Any]:
        owner = self.storage.owners.scan().find(owner_id)
        head = self.storage.store.read_snapshot(owner).head if owner and owner.persisted else None
        details = {f"{s['corpus']}.{s['channel']}": s["status"] if self._fresh(s, owner, head) else
                   ("stale" if s["status"] == "indexed" else s["status"]) for s in self.db.states([owner_id])}
        ready = bool(details) and all(v in {"indexed", "disabled"} for v in details.values())
        return {"index_status": "indexed" if ready else "pending", "details": details}

    # ---------- 维护 ----------
    @staticmethod
    def _fresh(state: dict, owner: Owner | None, head: dict | None) -> bool:
        return (owner is not None and state["status"] == "indexed" and json.loads(state["head"] or "null") == head
                and state["owner_fingerprint"] in (None, owner.fingerprint)
                and state["projection_version"] == VERSIONS.get(state["corpus"]))

    def _project(self, owner: Owner, graph: ClaimGraph) -> tuple[dict | None, dict[str, list[dict]]]:
        snapshot = self.storage.store.read_snapshot(owner) if owner.persisted else None
        records = list(snapshot.records.values()) if snapshot else []
        badges = {cid: graph.badge(cid) for cid, (_c, r) in graph.claims.items() if r["owner_id"] == owner.owner_id}
        hidden = {r["record_id"] for r in records
                  if any(e["code"] in {"ACCESS_DENIED", "UNSAFE_PATH"} for e in graph.access_errors(r["record_id"]))}
        info = projections.OwnerInfo(owner.owner_id, owner.owner_type, owner.title, owner.sensitivity,
                                     owner.fingerprint, tuple(owner.native.get("keywords", [])),
                                     str(owner.native.get("description") or owner.native.get("question") or ""))
        return (snapshot.head if snapshot else None,
                {corpus: build(info, records, badges, hidden) for corpus, build in BUILDERS.items()})

    def sync_owner(self, owner_id: str, *, graph: ClaimGraph | None = None, index: OwnerIndex | None = None) -> dict:
        index = index or self.storage.owners.scan()
        owner = index.get(owner_id)
        if not owner.readable:
            self.db.drop_owner(owner_id)
            return {"owner_id": owner_id, "status": "removed", "reason": "access_denied"}
        graph = graph or ClaimGraph(self.storage, index)
        head, projected = self._project(owner, graph)
        result: dict[str, Any] = {"owner_id": owner_id, "generation": head["generation"] if head else 0}
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
        return result

    def reconcile(self, owner_id: str | None = None, *, force: bool = False) -> dict:
        """按当前 HEAD 补齐所有不新鲜的投影；删除已消失对象的派生数据。从不写记忆记录。"""
        index = self.storage.owners.scan()
        graph = ClaimGraph(self.storage, index)
        targets = [index.get(owner_id)] if owner_id else index.owners
        results = []
        for owner in targets:
            head = self.storage.store.read_snapshot(owner).head if owner.persisted else None
            states = self.db.states([owner.owner_id])
            fresh = states and all(self._fresh(s, owner, head) or s["status"] == "disabled" for s in states)
            if force or not fresh or len(states) < len(BUILDERS) * 2:
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
        return {"synced": results, "removed_owners": removed, "writes_to_memory": 0}

    def gaps(self) -> list[dict]:
        """确定性的发现缺口：长期对象缺 L4、技术单元缺检索说明（内容是否充分仍需 AI 阅读判断）。"""
        out = []
        for owner in self.storage.owners.scan().readable():
            if not owner.persisted or owner.owner_type == "run":
                continue
            records = self.storage.store.read_snapshot(owner).records.values()
            if not any(r["kind"] == "overview" for r in records):
                out.append({"owner_id": owner.owner_id, "gap": "missing_overview"})
            for record in records:
                if record["kind"] == "detail" and not record["payload"]["retrieval_description"]["question"].strip():
                    out.append({"owner_id": owner.owner_id, "record_id": record["record_id"],
                                "gap": "empty_retrieval_description"})
        return out

    # ---------- 来源文件投影 ----------
    @staticmethod
    def _file_head(path: Path) -> dict:
        stat = path.stat()
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    def sync_sources(self, *, ocr_enabled: bool = True, max_bytes: int = 50 * 1024 * 1024) -> dict:
        """抽取已登记且可读的来源文件；未变化（大小与修改时间相同）的跳过，撤销登记的删除。"""
        states = {s["owner_id"]: s for s in self.db.states() if s["corpus"] == "source" and s["channel"] == "lexical"}
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
            if state and state["status"] == "indexed" and json.loads(state["head"]) == head \
                    and state["projection_version"] == projections.SOURCE_VERSION:
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
            if state and state["status"] == "indexed" and json.loads(state["head"]) == self._file_head(source.path):
                ready.append(source_id)
            elif not (state and state["status"] == "disabled"):
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
            elif not (state and state["status"] == "disabled"):
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
                results["dense"] = self.db.search_vector(corpus, query, vector_owners, limit=per_channel, **common)
        fused = rrf(results, owner_key if group == "owner" else record_key)
        return {"query": query, "corpus": corpus, "review_filter": review_filter,
                "items": fused[:limit], "total": len(fused), "gaps": gaps,
                "channels": {k: len(v) for k, v in results.items()}}

    def search_plan(self, plan: dict, *, corpus: Corpus = "fulltext", owner_ids: list[str] | None = None,
                    levels: list[str] | None = None, kinds: list[str] | None = None, review_filter: str = "all",
                    group: str = "record", per_route: int = 100, dense: bool = True,
                    explicit_owners: bool | None = None,
                    channels: tuple[Channel, ...] = ("identity", "lexical", "dense")) -> dict:
        """按冻结的查询计划并行执行各路线并融合。按 Owner 汇总时每个信号族只投一票。

        explicit_owners：调用方是否"显式指定了对象"（决定 owner_only 记录是否可见）；
        默认按 owner_ids 是否给出判断。服务层先按类型等条件过滤出对象列表时应明确传入。
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

        def run(route: dict) -> tuple[dict, list[dict]]:
            if route["channel"] == "identity":
                ids = [t for t in route["text"].split() if "-" in t and t.split("-", 1)[0].isupper()]
                return route, self.db.search_identity(corpus, ids, lexical_owners)
            if route["channel"] == "lexical":
                return route, self.db.search_lexical(corpus, route["text"], lexical_owners, limit=per_route, **common)
            return route, self.db.search_vector(corpus, route["text"], vector_owners, limit=per_route, **common)

        with ThreadPoolExecutor(max_workers=4) as pool:
            batches = list(pool.map(run, selected))
        fused = fuse_routes(batches, owner_key if group == "owner" else record_key, by_family=group == "owner")
        return {"plan_id": plan["plan_id"], "corpus": corpus, "items": fused, "gaps": gaps,
                "routes": [{"id": r["id"], "hits": len(rows)} for r, rows in batches]}
