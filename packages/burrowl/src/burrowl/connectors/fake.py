"""测试用的内存知识中枢：模拟异步解析、元数据、重复导入冲突、Wiki，以及可注入的故障。

状态保存在 options["store"] 指定的 JSON 文件中（缺省只在内存），便于跨进程测试。
"""
import json
import uuid
from pathlib import Path
from typing import Any

from .spi import (
    Capabilities,
    Collection,
    ConnectorError,
    ExternalItem,
    ExternalRef,
    Hit,
    PublishItem,
    WikiPage,
)


class FakeHub:
    type = "fake"

    def __init__(self, connector_id: str, *, base_url: str | None = None, api_key: str | None = None,
                 tenant_id: str | None = None, options: dict | None = None):
        self.id = connector_id
        options = options or {}
        self.path = Path(options["store"]) if options.get("store") else None
        self.async_ingest = bool(options.get("async_ingest", False))
        self.fail_on = set(str(options.get("fail_on", "")).split(",")) - {""}  # 例如 "upsert,delete"
        self.state = (json.loads(self.path.read_text(encoding="utf-8")) if self.path and self.path.exists()
                      else {"collections": {}, "docs": {}, "wiki": {}})

    def _save(self) -> None:
        if self.path:
            self.path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")

    def _maybe_fail(self, operation: str) -> None:
        if operation in self.fail_on:
            raise ConnectorError(f"injected failure: {operation}", status=500, retryable=True)

    def capabilities(self) -> Capabilities:
        return Capabilities(async_ingest=self.async_ingest, wiki_read=True, wiki_write=True, wiki_auto=False)

    def health(self) -> dict[str, Any]:
        return {"ok": True, "server_version": "fake-1", "compat": "ok"}

    def ensure_collection(self, name: str) -> Collection:
        for cid, existing in self.state["collections"].items():
            if existing == name or cid == name:
                return Collection(cid, existing)
        cid = f"kb-{uuid.uuid4().hex[:8]}"
        self.state["collections"][cid] = name
        self._save()
        return Collection(cid, name)

    def upsert(self, collection: Collection, item: PublishItem, existing: ExternalRef | None) -> ExternalRef:
        self._maybe_fail("upsert")
        duplicate = next((k for k, d in self.state["docs"].items()
                          if d["collection"] == collection.id and d["local_key"] == item.local_key), None)
        if existing is None and duplicate:
            raise ConnectorError("duplicate", status=409)
        doc_id = existing.external_id if existing else f"doc-{uuid.uuid4().hex[:10]}"
        if existing and doc_id not in self.state["docs"]:
            raise ConnectorError("not found", status=404)
        self.state["docs"][doc_id] = {"collection": collection.id, "local_key": item.local_key, "title": item.title,
                                      "content": item.markdown, "hash": item.content_hash, "meta": item.metadata,
                                      "state": "processing" if self.async_ingest else "completed"}
        self._save()
        return ExternalRef(doc_id, "processing" if self.async_ingest else "completed")

    def finish_processing(self) -> None:
        """测试辅助：把所有处理中的条目标为完成。"""
        for doc in self.state["docs"].values():
            doc["state"] = "completed"
        self._save()

    def delete(self, collection: Collection, ref: ExternalRef) -> None:
        self._maybe_fail("delete")
        self.state["docs"].pop(ref.external_id, None)
        self._save()

    def status(self, refs: list[ExternalRef]) -> list[ExternalRef]:
        return [ExternalRef(r.external_id, self.state["docs"][r.external_id]["state"])
                if r.external_id in self.state["docs"] else ExternalRef(r.external_id, "missing") for r in refs]

    def list_items(self, collection: Collection) -> list[ExternalItem]:
        return [ExternalItem(k, d["local_key"], d["hash"], d["title"]) for k, d in self.state["docs"].items()
                if d["collection"] == collection.id]

    def search(self, collections: list[Collection], query: str, limit: int) -> list[Hit]:
        ids = {c.id for c in collections}
        terms = [t for t in query.casefold().split() if t]
        hits = []
        for doc_id, doc in self.state["docs"].items():
            if doc["collection"] in ids and doc["state"] == "completed":
                score = sum(doc["content"].casefold().count(t) + doc["title"].casefold().count(t) for t in terms)
                if score:
                    hits.append(Hit(doc_id, doc["title"], doc["content"][:300], float(score), doc["local_key"]))
        return sorted(hits, key=lambda h: -h.score)[:limit]

    def read(self, ref: ExternalRef) -> dict[str, Any]:
        doc = self.state["docs"].get(ref.external_id)
        if doc is None:
            raise ConnectorError("not found", status=404)
        return {"title": doc["title"], "content": doc["content"], "metadata": doc["meta"]}

    # ---------- Wiki ----------
    def _pages(self, collection: Collection) -> dict:
        return self.state["wiki"].setdefault(collection.id, {})

    def wiki_list(self, collection: Collection, query: str = "", limit: int = 100) -> list[WikiPage]:
        pages = [WikiPage(**p) for p in self._pages(collection).values()]
        return [p for p in pages if query.casefold() in (p.title + p.markdown).casefold()][:limit]

    def wiki_read(self, collection: Collection, slug: str) -> WikiPage | None:
        page = self._pages(collection).get(slug)
        return WikiPage(**page) if page else None

    def wiki_write(self, collection: Collection, page: WikiPage) -> WikiPage:
        pages = self._pages(collection)
        version = (pages.get(page.slug, {}).get("version") or 0) + 1
        stored = {**page.__dict__, "version": version, "edited_by": "agent"}
        pages[page.slug] = stored
        self._save()
        return WikiPage(**stored)

    def user_edit(self, collection: Collection, slug: str, markdown: str) -> None:
        """测试辅助：模拟有人在外部系统里直接修改了页面。"""
        page = self._pages(collection)[slug]
        page.update(markdown=markdown, version=page["version"] + 1, edited_by="user", content_hash="")
        self._save()

    def wiki_delete(self, collection: Collection, slug: str) -> None:
        self._pages(collection).pop(slug, None)
        self._save()

    def wiki_configure(self, collection: Collection, *, auto: bool, synthesis_model_id: str | None) -> dict:
        return {"auto": False, "note": "fake 连接器不支持自动 Wiki"}
