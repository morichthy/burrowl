"""知识中枢同步引擎：把本地挑选出的知识发布到外部系统，并提供联邦检索。

期望状态对账：
1. 按连接器的发布策略，从各 Owner 当前 HEAD 算出"应发布集合"（默认只含已复核的 L4/L3 与成稿文档）。
2. 与映射表比较，得出 create / update / delete / mark / unchanged。
3. plan 只预览并给出 plan_hash；apply(plan_hash) 重新计算，哈希一致才执行（防止预览后数据变了）。
4. 外部异步解析：apply 后条目为 processing，status(refresh) 轮询。
5. 外部已存在同一 local_key（409 或映射丢失）时认领已有条目，而不是重复创建。
本地记忆是唯一权威；外部只是派生副本，所以任何失败都只影响中枢，不回写本地。
"""
import dataclasses
from collections.abc import Callable
from typing import Any

from ..connectors import registry
from ..connectors.mdvault import MarkdownVault
from ..connectors.spi import (
    Collection,
    ConnectorError,
    ExternalRef,
    PublishItem,
    SupportsCandidates,
    SupportsFinalize,
    SupportsWiki,
    WikiPage,
)
from ..core.config import BurrowlConfig, ConnectorConfig
from ..core.errors import BurrowlError
from ..core.hashing import canonical_hash
from ..index.projections import review_class
from ..storage import secrets
from ..storage.hub_state import HubState
from .memory.claims import ClaimGraph
from .memory.context import Storage
from .memory.documents import build_document
from .query.assemble import record_text
from .render import Page

RECORD_KINDS = {"narrative", "experience", "overview", "detail", "checkpoint", "route", "goal", "question"}
CLASS_FILTERS = {"all": None, "reviewed_only": {"accepted"}, "unreviewed_only": {"none", "unreviewed"},
                 "exclude_unsafe": {"none", "accepted", "unreviewed", "mixed"}}
BADGE_LABELS = {"accepted": "已确认", "not-reviewed": "未复核", "disputed": "有争议", "retracted": "已撤回",
                "superseded": "已替代", "stale": "已失效"}
BLOCKING = {"ACCESS_DENIED", "UNSAFE_PATH"}  # 依据链上有受限来源时，派生文本也不能外发
SENSITIVITY_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
DEFAULT_COLLECTION = "burrowl"
WIKI_PREFIX = "burrowl-"
# 交给外部图的边类别（R12 T-P4.1）：结构边和证据边留在本地，外部只拿导航与概念关系
GRAPH_EXPORT_CATEGORIES = {"reference", "navigation", "concept", "claim_relation"}
GRAPH_EXCLUDED_STATES = {"rejected", "withdrawn", "invalid"}  # 宿主 AI 编写的页面统一放在 <type>/burrowl-* 命名空间，与外部自动生成的页面分开


def ref_uri(record: dict) -> str:
    return f"burrowl://{record['owner_id']}/{record['record_id']}@{record['revision']}#{record['record_hash'][:12]}"


def wiki_slug(record: dict) -> str:
    """slug 可省略（R12 §4.6）：没有时按 <页面类型>/c-<记录 ID 前 8 位十六进制> 生成，只用于外部中枢。"""
    payload = record["payload"]
    if payload.get("slug"):
        return payload["slug"]
    digits = "".join(ch for ch in record["record_id"][4:] if ch in "0123456789abcdef")[:8]
    return f"{payload['page_type']}/c-{digits}"


def external_slug(slug: str) -> str:
    page_type, _, name = slug.partition("/")
    return f"{page_type}/{name if name.startswith(WIKI_PREFIX) else WIKI_PREFIX + name}"


def _fixed(record: dict) -> dict:
    return {"target_kind": "record", "target_id": record["record_id"], "revision": record["revision"],
            "sha256": record["record_hash"], "locator": "record", "relation": "references"}


def _document_markdown(doc: dict) -> str:
    parts = []
    for section in doc["report"]["sections"]:
        parts.append(f"## {section['title']}")
        for block in section["blocks"]:
            if block["type"] == "prose" and block.get("markdown"):
                parts.append(block["markdown"])
            elif block.get("resolved_blocks"):
                parts.extend(b["markdown"] for b in block["resolved_blocks"])
    return "\n\n".join(parts)


class HubService:
    def __init__(self, storage: Storage, config: Callable[[], BurrowlConfig], *, state: HubState | None = None,
                 factory: Callable[..., Any] = registry.create, graph_source: Callable[[], tuple] | None = None):
        self.storage = storage
        self.config = config
        self.graph_source = graph_source  # () -> (节点, 边)：本地图投影，给接收整理图的连接器用
        self.state = state or HubState(storage.ws)
        self.factory = factory
        self._cache: dict[str, tuple[str, Any]] = {}

    # ---------- 连接器 ----------
    def _settings(self, connector_id: str) -> ConnectorConfig:
        found = next((c for c in self.config().connectors if c.id == connector_id), None)
        if found is None:
            raise BurrowlError("NOT_FOUND", "未配置该连接器", {"connector_id": connector_id})
        if not found.enabled:
            raise BurrowlError("CAPABILITY_UNAVAILABLE", "该连接器已停用", {"connector_id": connector_id})
        return found

    def connector(self, connector_id: str):
        cfg = self._settings(connector_id)
        key = canonical_hash(cfg.model_dump(mode="json"))
        cached = self._cache.get(connector_id)
        if cached is None or cached[0] != key:
            conn = self.factory(cfg.type, cfg.id, base_url=cfg.base_url, api_key=secrets.resolve(cfg.secret_ref),
                                tenant_id=cfg.tenant_id, options=dict(cfg.options))
            self._cache[connector_id] = cached = (key, conn)
        return cfg, cached[1]

    def _collection(self, cfg: ConnectorConfig, conn) -> Collection:
        return conn.ensure_collection(cfg.collection or DEFAULT_COLLECTION)

    def health(self, connector_id: str) -> dict:
        cfg = self._settings(connector_id)
        try:
            _, conn = self.connector(connector_id)
            return {"connector_id": connector_id, "type": cfg.type, **conn.health(),
                    "capabilities": conn.capabilities().as_dict(), "secret": secrets.status(cfg.secret_ref)}
        except (ConnectorError, BurrowlError) as exc:
            return {"connector_id": connector_id, "type": cfg.type, "ok": False, "error": str(exc),
                    "secret": secrets.status(cfg.secret_ref)}

    def list_connectors(self) -> list[dict]:
        return [{"id": c.id, "type": c.type, "enabled": c.enabled, "collection": c.collection or DEFAULT_COLLECTION,
                 "wiki_mode": c.wiki.mode, "publish": c.publish.model_dump(mode="json"),
                 "published": len(self.state.mappings(c.id)) if c.enabled else 0,
                 "available": c.type in registry.factories()} for c in self.config().connectors]

    # ---------- 应发布集合 ----------
    def desired(self, cfg: ConnectorConfig, conn=None) -> dict[str, tuple[str, PublishItem]]:
        """local_key → (channel, 条目)。channel：item（知识条目）/ wiki（宿主编写的 Wiki 页）。
        conn 支持 graph_import 时，记录条目附带它声明的整理图（只含同样满足发布策略的端点）。"""
        policy = cfg.publish
        index = self.storage.owners.scan()
        graph = ClaimGraph(self.storage, index)
        allowed = CLASS_FILTERS[policy.review_filter]
        excluded = set(policy.exclude_sensitivity) | {"restricted"}
        wiki = cfg.wiki.mode in {"authored", "both"}
        out: dict[str, tuple[str, PublishItem]] = {}
        chosen: dict[str, dict] = {}  # local_key → 记录（交换格式渲染用）
        for owner in index.readable():
            if not owner.persisted or (policy.owner_types and owner.owner_type not in policy.owner_types) \
                    or (policy.owner_ids and owner.owner_id not in policy.owner_ids):
                continue
            records = self.storage.store.read_snapshot(owner).records.values()
            for record in records:
                sensitivity = max(owner.sensitivity, record["sensitivity"], key=lambda v: SENSITIVITY_RANK.get(v, 3))
                if sensitivity in excluded or any(e["code"] in BLOCKING for e in graph.access_errors(record["record_id"])):
                    continue
                if record["kind"] == "wiki_page" and wiki:
                    out[f"wiki:{record['record_id']}"] = ("wiki", self._wiki_item(record))
                    chosen[f"wiki:{record['record_id']}"] = record
                elif record["kind"] == "document" and policy.include_documents:
                    item = self._document_item(owner.owner_id, record)
                    if item:
                        out[item.local_key] = ("item", item)
                        chosen[item.local_key] = record
                elif record["kind"] in RECORD_KINDS and record["level"] in policy.levels:
                    badges = [graph.badge(c["claim_id"]) for c in record["payload"].get("claims", [])]
                    klass = review_class(badges)
                    if allowed is None or klass in allowed:
                        item = self._record_item(record, badges, klass)
                        out[item.local_key] = ("item", item)
                        chosen[item.local_key] = record
        if cfg.type == MarkdownVault.type:
            out = self._as_exchange(out, chosen)
        if conn is not None and conn.capabilities().graph_import and self.graph_source is not None:
            out = self._with_graph(out, chosen, graph, excluded)
        return out

    def _with_graph(self, items: dict[str, tuple[str, PublishItem]], chosen: dict[str, dict], graph: ClaimGraph,
                    excluded: set[str]) -> dict[str, tuple[str, PublishItem]]:
        """给每个记录条目附上它声明的实体与关系（R12 T-P4.1）。端点只能是本次发布的记录及其结论，或敏感级别
        允许的概念页；其他端点（未发布的记录、来源文件）不外发，相关的边一并省略。"""
        nodes, edges = self.graph_source()
        node_by_id = {n["node_id"]: n for n in nodes}
        published = {r["record_id"]: r for key, r in chosen.items() if not key.startswith("wiki:")}
        claims = {c["claim_id"]: (c, r) for r in published.values() for c in r["payload"].get("claims", []) or []}

        def entity(node_id: str) -> dict | None:
            node_id = node_id.split("#", 1)[0]                     # 块 → 所在记录
            if node_id in published:
                record = published[node_id]
                return {"id": node_id, "name": f"{record['title']} [{node_id[4:12]}]", "type": "record",
                        "description": f"{record.get('level') or record['kind']}：{record['title']}"}
            if node_id in claims:
                claim, _record = claims[node_id]
                return {"id": node_id, "name": node_id, "type": "claim",
                        "description": f"{claim['statement']}（适用范围：{claim['scope']}）"}
            node = node_by_id.get(node_id)
            record = graph.records.get(node_id)
            if node and node["node_type"] == "concept" and record and record["sensitivity"] not in excluded:
                return {"id": node_id, "name": record["title"], "type": "concept",
                        "description": (record["body_markdown"] or record["title"])[:500]}
            return None

        out = {}
        for key, (channel, item) in items.items():
            record = chosen[key]
            if channel != "item" or record["record_id"] not in published:
                out[key] = (channel, item)
                continue
            declared = {record["record_id"], *(c["claim_id"] for c in record["payload"].get("claims", []) or [])}
            entities = {e["id"]: e for e in map(entity, sorted(declared)) if e}
            relationships = []
            for edge in edges:
                if edge["declared_by"] not in declared or edge["category"] not in GRAPH_EXPORT_CATEGORIES \
                        or edge["state"] in GRAPH_EXCLUDED_STATES:
                    continue
                src, dst = entity(edge["src"]), entity(edge["dst"])
                if src is None or dst is None or src["id"] == dst["id"]:
                    continue
                entities.setdefault(src["id"], src)
                entities.setdefault(dst["id"], dst)
                relationships.append({"src": src["id"], "dst": dst["id"], "type": edge["type"],
                                      "category": edge["category"], "certainty": edge["certainty"],
                                      "state": edge["state"]})
            payload = {"entities": sorted(entities.values(), key=lambda e: e["id"]),
                       "relationships": sorted(relationships, key=lambda r: (r["src"], r["dst"], r["type"]))}
            out[key] = (channel, dataclasses.replace(
                item, graph=payload, content_hash=canonical_hash({"base": item.content_hash, "graph": payload})))
        return out

    def _as_exchange(self, items: dict[str, tuple[str, PublishItem]],
                     chosen: dict[str, dict]) -> dict[str, tuple[str, PublishItem]]:
        """Markdown 库按交换格式发布（R12 T-P3.3）：同一个渲染器，链接只指向本次一起发布的页面。"""
        wiki_paths = {r["record_id"]: MarkdownVault.wiki_stem(external_slug(wiki_slug(r)))
                      for key, r in chosen.items() if key.startswith("wiki:")}
        concept_owner = self.config().graph.concept_owner_id
        page = Page.build(self.storage, list(chosen.values()), concept_owner=concept_owner,
                          path_for=lambda record: wiki_paths.get(record["record_id"]))
        out = {}
        for key, (channel, item) in items.items():
            record = chosen[key]
            markdown = page.content(record)
            if channel == "wiki":
                out[key] = (channel, dataclasses.replace(
                    item, markdown=markdown, content_hash=canonical_hash({"md": markdown, "title": item.title,
                                                                          "slug": item.metadata["slug"]})))
                continue
            path = page.paths[record["record_id"]] + ".md"
            front = {k: v for k, v in page.front(record).items() if k not in {"tags"}}
            out[key] = (channel, dataclasses.replace(
                item, markdown=markdown, path=path, front_matter=front,
                content_hash=canonical_hash({"md": markdown, "title": item.title, "path": path, "front": front})))
        return out

    @staticmethod
    def _record_item(record: dict, badges: list[str], klass: str) -> PublishItem:
        claims = record["payload"].get("claims", [])
        lines = [record_text(record)]
        if claims:
            lines.append("## 结论")
            lines.extend(f"- {c['statement']}（适用范围：{c['scope']}；{BADGE_LABELS.get(b, b)}）"
                         for c, b in zip(claims, badges, strict=True))
        lines.append(f"\n---\n来源：{ref_uri(record)}")
        markdown = "\n\n".join(lines)
        return PublishItem(local_key=f"rec:{record['owner_id']}/{record['record_id']}", title=record["title"],
                           markdown=markdown, content_hash=canonical_hash({"md": markdown, "title": record["title"]}),
                           fixed_ref=_fixed(record), ref_uri=ref_uri(record), tags=tuple(record.get("keywords", [])),
                           metadata={"burrowl_owner": record["owner_id"], "burrowl_kind": record["kind"],
                                     "burrowl_level": record["level"] or "", "burrowl_review": klass})

    def _document_item(self, owner_id: str, record: dict) -> PublishItem | None:
        doc = build_document(self.storage, owner_id, document_id=record["record_id"])
        if not doc.get("report") or not doc["report"]["complete"]:
            return None  # 依据不完整的文稿不发布
        markdown = _document_markdown(doc) + f"\n\n---\n来源：{ref_uri(record)}"
        return PublishItem(local_key=f"doc:{owner_id}/{record['record_id']}", title=record["title"],
                           markdown=markdown, content_hash=canonical_hash({"md": markdown, "title": record["title"]}),
                           fixed_ref=_fixed(record), ref_uri=ref_uri(record),
                           metadata={"burrowl_owner": owner_id, "burrowl_kind": "document",
                                     "burrowl_document_type": record["payload"]["document_type"]})

    @staticmethod
    def _wiki_item(record: dict) -> PublishItem:
        payload = record["payload"]
        sources = "\n".join(f"- burrowl://{r['target_id']}@{r.get('revision')}" for r in payload["source_refs"])
        markdown = f"{record['body_markdown']}\n\n## 来源\n\n{sources}"
        return PublishItem(local_key=f"wiki:{record['record_id']}", title=record["title"], markdown=markdown,
                           content_hash=canonical_hash({"md": markdown, "title": record["title"],
                                                        "slug": wiki_slug(record)}),
                           fixed_ref=_fixed(record), ref_uri=ref_uri(record),
                           metadata={"slug": external_slug(wiki_slug(record)), "page_type": payload["page_type"]})

    # ---------- 预览 / 执行 ----------
    def plan(self, connector_id: str) -> dict:
        cfg, conn = self.connector(connector_id)
        desired = self.desired(cfg, conn)
        mapped = self.state.mappings(connector_id)
        caps = conn.capabilities()
        actions = []
        for key, (channel, item) in sorted(desired.items()):
            if channel == "wiki" and not caps.wiki_write:
                continue
            current = mapped.get(key)
            if current is None:
                op = "create"
            elif current["content_hash"] != item.content_hash or current["state"] in {"failed", "missing", "marked"}:
                op = "update"
            else:
                op = "unchanged"
            actions.append({"op": op, "local_key": key, "channel": channel, "title": item.title,
                            "content_hash": item.content_hash})
        for key, current in sorted(mapped.items()):
            if key not in desired:
                op = "delete" if cfg.publish.on_retract == "delete" or current["channel"] == "wiki" else "mark"
                if op == "mark" and current["state"] == "marked":
                    op = "unchanged"
                actions.append({"op": op, "local_key": key, "channel": current["channel"], "title": current["title"],
                                "content_hash": current["content_hash"], "reason": "不再满足发布策略（撤回、复核失效或策略变化）"})
        counts = {op: sum(a["op"] == op for a in actions) for op in ("create", "update", "delete", "mark", "unchanged")}
        changes = [a for a in actions if a["op"] != "unchanged"]
        return {"connector_id": connector_id, "plan_hash": canonical_hash(changes), "counts": counts,
                "actions": changes, "policy": cfg.publish.model_dump(mode="json")}

    def apply(self, connector_id: str, plan_hash: str, *, force_wiki: bool = False) -> dict:
        preview = self.plan(connector_id)
        if preview["plan_hash"] != plan_hash:
            raise BurrowlError("CONFLICT", "预览之后本地数据或策略已变化，请重新预览", {"plan_hash": preview["plan_hash"]})
        cfg, conn = self.connector(connector_id)
        desired = {key: item for key, (_channel, item) in self.desired(cfg, conn).items()}
        collection = self._collection(cfg, conn)
        mapped = self.state.mappings(connector_id)
        if any(a["op"] == "create" and a["channel"] == "item" for a in preview["actions"]) and                 conn.capabilities().listing:
            # 映射表是派生状态，可能丢失；外部若已有同一 local_key 的条目就认领，而不是再建一份。
            for remote in conn.list_items(collection):
                if remote.local_key in desired and remote.local_key not in mapped:
                    mapped[remote.local_key] = {"external_id": remote.external_id, "title": remote.title,
                                                "adopted": True}
        run_id = self.state.start_run(connector_id, plan_hash)
        results = []
        for action in preview["actions"]:
            key = action["local_key"]
            try:
                if action["channel"] == "wiki":
                    outcome = self._apply_wiki(connector_id, conn, collection, action, desired.get(key), mapped.get(key),
                                               force=force_wiki)
                else:
                    outcome = self._apply_item(connector_id, conn, collection, action, desired.get(key), mapped.get(key))
            except ConnectorError as exc:
                if key in mapped:
                    self.state.set_state(connector_id, key, "failed", str(exc))
                outcome = {"result": "failed", "error": str(exc), "status": exc.status, "retryable": exc.retryable}
            results.append({"local_key": key, "op": action["op"], **outcome})
        summary = {r: sum(x["result"] == r for x in results) for r in {x["result"] for x in results}}
        self.state.finish_run(run_id, summary)
        out = {"connector_id": connector_id, "run_id": run_id, "summary": summary, "results": results}
        if isinstance(conn, SupportsFinalize):  # 例如 LightRAG：按全部条目重建或追加外部索引
            try:
                out["finalize"] = conn.finalize(collection)
            except ConnectorError as exc:
                out["finalize"] = {"ok": False, "error": str(exc)}
        return out

    def _apply_item(self, cid: str, conn, collection: Collection, action: dict, item: PublishItem | None,
                    current: dict | None) -> dict:
        if action["op"] == "delete":
            conn.delete(collection, ExternalRef(current["external_id"]))
            self.state.remove(cid, action["local_key"])
            return {"result": "deleted"}
        if action["op"] == "mark":
            item = PublishItem(local_key=action["local_key"], title=f"[已撤回] {current['title']}",
                               markdown="此条目已在本地撤回、复核失效或不再满足发布策略，不应作为有效知识引用。",
                               content_hash=current["content_hash"], fixed_ref=current["fixed_ref"] or {},
                               ref_uri="", metadata={"burrowl_status": "withdrawn"})
        existing = ExternalRef(current["external_id"]) if current else None
        try:
            ref = conn.upsert(collection, item, existing)
        except ConnectorError as exc:
            if exc.status == 404 and existing:          # 外部被删：重新创建
                ref = conn.upsert(collection, item, None)
            elif exc.status == 409 and not existing:    # 外部已有同一 local_key：认领后更新
                adopted = next((x for x in conn.list_items(collection) if x.local_key == item.local_key), None)
                if adopted is None:
                    raise
                ref = conn.upsert(collection, item, ExternalRef(adopted.external_id))
            else:
                raise
        state = "marked" if action["op"] == "mark" else ref.state
        self.state.put(cid, action["local_key"], channel="item", collection_id=collection.id,
                       external_id=ref.external_id, content_hash=item.content_hash, fixed_ref=item.fixed_ref,
                       title=current["title"] if action["op"] == "mark" else item.title, state=state, detail=ref.detail)
        return {"result": "marked" if action["op"] == "mark" else action["op"] + "d", "external_id": ref.external_id,
                "state": state}

    def _apply_wiki(self, cid: str, conn: SupportsWiki, collection: Collection, action: dict,
                    item: PublishItem | None, current: dict | None, *, force: bool) -> dict:
        slug = item.metadata["slug"] if item else current["external_id"]
        remote = conn.wiki_read(collection, slug)
        if remote and not force and current and (remote.version != current["external_version"]
                                                 or remote.edited_by == "user"):
            self.state.set_state(cid, action["local_key"], "drift", f"外部页面已被修改（版本 {remote.version}）")
            return {"result": "drift", "slug": slug,
                    "hint": "外部 Wiki 页面在上次发布后被修改；确认覆盖请使用 force_wiki=true，或先把外部修改合并进本地 wiki_page"}
        if remote and not force and current is None and remote.edited_by == "user":
            return {"result": "drift", "slug": slug, "hint": "外部已有同名且由人工维护的页面，未覆盖"}
        if action["op"] == "delete":
            conn.wiki_delete(collection, slug)
            self.state.remove(cid, action["local_key"])
            return {"result": "deleted", "slug": slug}
        page = conn.wiki_write(collection, WikiPage(slug=slug, title=item.title, markdown=item.markdown,
                                                    page_type=item.metadata["page_type"],
                                                    content_hash=item.content_hash))
        self.state.put(cid, action["local_key"], channel="wiki", collection_id=collection.id, external_id=slug,
                       content_hash=item.content_hash, fixed_ref=item.fixed_ref, title=item.title, state="completed",
                       external_version=page.version)
        return {"result": action["op"] + "d", "slug": slug, "version": page.version}

    def status(self, connector_id: str, *, refresh: bool = True) -> dict:
        _, conn = self.connector(connector_id)
        mapped = self.state.mappings(connector_id, "item")
        pending = [m for m in mapped.values() if m["state"] in {"pending", "processing"}]
        if refresh and pending:
            for ref in conn.status([ExternalRef(m["external_id"]) for m in pending]):
                local = next(m for m in pending if m["external_id"] == ref.external_id)
                self.state.set_state(connector_id, local["local_key"], ref.state, ref.detail)
            mapped = self.state.mappings(connector_id, "item")
        everything = {**mapped, **self.state.mappings(connector_id, "wiki")}
        counts: dict[str, int] = {}
        for m in everything.values():
            counts[m["state"]] = counts.get(m["state"], 0) + 1
        problems = [{"local_key": k, "state": m["state"], "detail": m["detail"]} for k, m in everything.items()
                    if m["state"] in {"failed", "missing", "drift"}]
        return {"connector_id": connector_id, "counts": counts, "problems": problems,
                "runs": self.state.runs(connector_id, 5)}

    # ---------- 联邦检索 ----------
    def search(self, query: str, *, connector_ids: list[str] | None = None, limit: int = 10,
               review_filter: str = "all") -> dict:
        targets = connector_ids or [c.id for c in self.config().connectors if c.enabled]
        allowed = CLASS_FILTERS[review_filter]
        graph = ClaimGraph(self.storage)
        hits, errors = [], []
        for cid in targets:
            try:
                cfg, conn = self.connector(cid)
                if not conn.capabilities().search:
                    continue
                found = conn.search([self._collection(cfg, conn)], query, limit)
            except (ConnectorError, BurrowlError) as exc:
                errors.append({"connector_id": cid, "error": str(exc)})
                continue
            mapped = self.state.mappings(cid)
            by_external = self.state.by_external(cid, [h.external_id for h in found if not h.local_key])
            for hit in found:
                local = mapped.get(hit.local_key) if hit.local_key else by_external.get(hit.external_id)
                review = self._current_review(graph, local)
                if allowed is not None and review not in allowed:
                    continue
                hits.append({"connector_id": cid, "external_id": hit.external_id, "title": hit.title,
                             "snippet": hit.content, "score": hit.score,
                             "local_key": local["local_key"] if local else hit.local_key,
                             "fixed_ref": local["fixed_ref"] if local else None, "review": review})
        hits.sort(key=lambda h: -h["score"])
        return {"query": query, "hits": hits[:limit], "errors": errors}

    def external_candidates(self, connector_id: str, limit: int = 200) -> dict:
        """外部工具回流的关系线索（R12 T-P4.3）：local_key 对上本地已发布条目的才保留，映射成记录 ID。
        结果只供写入关联候选（origin=tool:<id>），由 AI 在收口时判断，从不直接写成记录。"""
        cfg, conn = self.connector(connector_id)
        if not isinstance(conn, SupportsCandidates):
            return {"connector_id": connector_id, "rows": [], "unmatched": 0, "supported": False}
        mapped = self.state.mappings(connector_id)
        rows, unmatched = [], 0
        for link in conn.candidates(self._collection(cfg, conn), limit):
            ends = [mapped.get(k, {}).get("fixed_ref") for k in (link.src_local_key, link.dst_local_key)]
            if not all(e and e.get("target_kind") == "record" for e in ends) or ends[0]["target_id"] == ends[1]["target_id"]:
                unmatched += 1
                continue
            owner = next((o.owner_id for o in self.storage.owners.scan().readable() if o.persisted
                          and ends[0]["target_id"] in self.storage.store.read_snapshot(o).records), None)
            if owner:
                rows.append({"owner_id": owner, "src": ends[0]["target_id"], "dst": ends[1]["target_id"],
                             "kind": f"external:{link.kind}", "score": link.score})
        return {"connector_id": connector_id, "rows": rows, "unmatched": unmatched, "supported": True}

    @staticmethod
    def _current_review(graph: ClaimGraph, local: dict | None) -> str:
        """按本地当前状态重新给出复核类别（外部副本可能落后于本地）。"""
        if not local or not local.get("fixed_ref"):
            return "none"
        record = graph.records.get(local["fixed_ref"]["target_id"])
        if record is None:
            return "unsafe"
        return review_class([graph.badge(c["claim_id"]) for c in record["payload"].get("claims", [])])

    # ---------- Wiki ----------
    def _wiki(self, connector_id: str) -> tuple[ConnectorConfig, Any, Collection]:
        cfg, conn = self.connector(connector_id)
        if not isinstance(conn, SupportsWiki) or not conn.capabilities().wiki_read:
            raise BurrowlError("CAPABILITY_UNAVAILABLE", "该连接器不支持 Wiki", {"connector_id": connector_id})
        return cfg, conn, self._collection(cfg, conn)

    def wiki_list(self, connector_id: str, query: str = "", limit: int = 50) -> dict:
        _, conn, collection = self._wiki(connector_id)
        pages = conn.wiki_list(collection, query, limit)
        return {"pages": [{"slug": p.slug, "title": p.title, "page_type": p.page_type, "summary": p.summary,
                           "version": p.version, "edited_by": p.edited_by,
                           "authored_here": p.slug.partition("/")[2].startswith(WIKI_PREFIX)} for p in pages]}

    def wiki_read(self, connector_id: str, slug: str) -> dict:
        _, conn, collection = self._wiki(connector_id)
        page = conn.wiki_read(collection, slug)
        if page is None:
            raise BurrowlError("NOT_FOUND", "Wiki 页面不存在", {"slug": slug})
        return {"slug": page.slug, "title": page.title, "page_type": page.page_type, "markdown": page.markdown,
                "version": page.version, "edited_by": page.edited_by}

    def wiki_configure(self, connector_id: str) -> dict:
        """按配置开启或关闭外部系统的自动 Wiki（WeKnora：wiki_enabled + synthesis_model_id）。"""
        cfg, conn, collection = self._wiki(connector_id)
        auto = cfg.wiki.mode in {"auto", "both"}
        if auto and not conn.capabilities().wiki_auto:
            return {"auto": False, "note": "该连接器不支持自动 Wiki，仅可发布宿主编写的页面"}
        return conn.wiki_configure(collection, auto=auto, synthesis_model_id=cfg.wiki.synthesis_model_id)

