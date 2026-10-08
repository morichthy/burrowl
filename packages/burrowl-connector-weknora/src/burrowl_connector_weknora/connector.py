"""WeKnora 连接器实现（HubConnector + SupportsWiki）。

- 知识条目以"手动文本知识"（Markdown）导入；local_key / 内容哈希 / 固定引用写入 custom_metadata，
  以便映射丢失后按 local_key 认领。
- 导入是异步的：upsert 返回 pending，之后用 /knowledge/batch 轮询 parse_status。
- Wiki 写入只能走 REST（WeKnora 内置 MCP 的 Wiki 工具是只读的）。外部被人工修改的页面
  last_edit_source=user，同步引擎据此判断漂移。
"""
from typing import Any

import httpx

from burrowl.connectors.spi import (
    Capabilities,
    Collection,
    ConnectorError,
    ExternalItem,
    ExternalRef,
    Hit,
    PublishItem,
    WikiPage,
)

from . import api_v08 as api

RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


class WeKnoraConnector:
    type = "weknora"

    def __init__(self, connector_id: str, *, base_url: str | None = None, api_key: str | None = None,
                 tenant_id: str | None = None, options: dict | None = None,
                 transport: httpx.BaseTransport | None = None):
        if not base_url:
            raise ConnectorError("WeKnora 需要 base_url，例如 http://localhost:8080")
        self.id = connector_id
        options = options or {}
        headers = {"X-API-Key": api_key} if api_key else {}
        if tenant_id:
            headers["X-Tenant-ID"] = str(tenant_id)
        self.http = httpx.Client(base_url=base_url.rstrip("/") + api.PREFIX, headers=headers,
                                 timeout=float(options.get("timeout", 30)), transport=transport)

    # ---------- HTTP ----------
    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"无法连接 WeKnora：{exc}", retryable=True) from exc
        if response.status_code >= 400:
            try:
                message = response.json().get("error", {}).get("message") or response.text
            except ValueError:
                message = response.text
            raise ConnectorError(f"WeKnora {method} {path} → {response.status_code}: {str(message)[:300]}",
                                 status=response.status_code, retryable=response.status_code in RETRYABLE)
        if not response.content:
            return None
        body = response.json()
        if isinstance(body, dict) and body.get("success") is False:
            raise ConnectorError(f"WeKnora 返回失败：{str(body.get('message') or body)[:300]}")
        return body.get("data") if isinstance(body, dict) and "data" in body else body

    @staticmethod
    def _items(data: Any) -> list[dict]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "list", "data", "pages", "knowledge"):
                if isinstance(data.get(key), list):
                    return data[key]
        return []

    def _paged(self, path: str, **params: Any) -> list[dict]:
        out, page = [], 1
        while True:
            batch = self._items(self._call("GET", path, params={**params, "page": page, "page_size": api.PAGE_SIZE}))
            out.extend(batch)
            if len(batch) < api.PAGE_SIZE:
                return out
            page += 1

    # ---------- 基本 ----------
    def capabilities(self) -> Capabilities:
        return Capabilities(async_ingest=True, metadata_max_keys=api.METADATA_MAX_KEYS,
                            wiki_read=True, wiki_write=True, wiki_auto=True)

    def health(self) -> dict[str, Any]:
        self._call("GET", api.KBS, params={"page": 1, "page_size": 1})
        return {"ok": True, "api": api.TESTED, "compat": "ok"}

    def ensure_collection(self, name: str) -> Collection:
        for kb in self._items(self._call("GET", api.KBS)):
            if name in (kb.get("id"), kb.get("name")):
                return Collection(str(kb["id"]), kb.get("name", name))
        created = self._call("POST", api.KBS, json=api.kb_body(name))
        return Collection(str(created["id"]), created.get("name", name))

    def upsert(self, collection: Collection, item: PublishItem, existing: ExternalRef | None) -> ExternalRef:
        body = api.manual_body(item.title, item.markdown)
        if existing:
            self._call("PUT", api.MANUAL_UPDATE.format(id=existing.external_id), json=body)
            knowledge_id = existing.external_id
        else:
            created = self._call("POST", api.MANUAL_CREATE.format(kb=collection.id), json=body)
            knowledge_id = str(created["id"])
        metadata = {"burrowl_local_key": item.local_key, "burrowl_hash": item.content_hash, "burrowl_ref": item.ref_uri,
                    **item.metadata}
        self._call("PUT", api.KNOWLEDGE.format(id=knowledge_id), json=api.metadata_body(item.title, metadata))
        return ExternalRef(knowledge_id, "pending")

    def delete(self, collection: Collection, ref: ExternalRef) -> None:
        try:
            self._call("DELETE", api.KNOWLEDGE.format(id=ref.external_id))
        except ConnectorError as exc:
            if exc.status != 404:  # 已不存在即视为删除成功
                raise

    def status(self, refs: list[ExternalRef]) -> list[ExternalRef]:
        if not refs:
            return []
        rows = self._items(self._call("GET", api.BATCH, params={"ids": ",".join(r.external_id for r in refs)}))
        found = {str(r.get("id")): r for r in rows}
        out = []
        for ref in refs:
            row = found.get(ref.external_id)
            if row is None:
                out.append(ExternalRef(ref.external_id, "missing"))
            else:
                detail = row.get("error_message") or row.get("stall_state") or ""
                out.append(ExternalRef(ref.external_id, api.PARSE_STATES.get(row.get("parse_status"), "processing"),
                                       str(detail)))
        return out

    def list_items(self, collection: Collection) -> list[ExternalItem]:
        out = []
        for row in self._paged(api.KB_KNOWLEDGE.format(kb=collection.id)):
            meta = row.get("custom_metadata") or {}
            out.append(ExternalItem(str(row["id"]), meta.get("burrowl_local_key"), meta.get("burrowl_hash"),
                                    row.get("title", "")))
        return out

    def search(self, collections: list[Collection], query: str, limit: int) -> list[Hit]:
        rows = self._items(self._call("POST", api.SEARCH, json=api.search_body(query, [c.id for c in collections],
                                                                                 limit)))
        best: dict[str, Hit] = {}
        for row in rows:  # 结果是分块级的：同一知识条目只保留最高分的块
            kid = str(row.get("knowledge_id") or row.get("id"))
            hit = Hit(kid, row.get("knowledge_title", ""), row.get("content", ""), float(row.get("score") or 0),
                      locator=str(row.get("id", "")))
            if kid not in best or hit.score > best[kid].score:
                best[kid] = hit
        return sorted(best.values(), key=lambda h: -h.score)[:limit]

    def read(self, ref: ExternalRef) -> dict[str, Any]:
        row = self._call("GET", api.KNOWLEDGE.format(id=ref.external_id))
        return {"title": row.get("title", ""), "content": row.get("content") or row.get("description", ""),
                "metadata": row.get("custom_metadata") or {}, "parse_status": row.get("parse_status")}

    # ---------- Wiki ----------
    @staticmethod
    def _page(row: dict) -> WikiPage:
        return WikiPage(slug=row["slug"], title=row.get("title", ""), markdown=row.get("content", ""),
                        page_type=row.get("page_type", "synthesis"), summary=row.get("summary", ""),
                        version=row.get("version"), edited_by=row.get("last_edit_source", ""))

    def wiki_list(self, collection: Collection, query: str = "", limit: int = 100) -> list[WikiPage]:
        params = {"page": 1, "page_size": min(limit, api.PAGE_SIZE)} | ({"keyword": query} if query else {})
        return [self._page(r) for r in self._items(self._call("GET", api.WIKI_PAGES.format(kb=collection.id),
                                                              params=params))][:limit]

    def wiki_read(self, collection: Collection, slug: str) -> WikiPage | None:
        try:
            return self._page(self._call("GET", api.WIKI_PAGE.format(kb=collection.id, slug=slug)))
        except ConnectorError as exc:
            if exc.status == 404:
                return None
            raise

    def wiki_write(self, collection: Collection, page: WikiPage) -> WikiPage:
        body = api.wiki_body(page.slug, page.title, page.markdown, page.page_type, page.summary)
        if self.wiki_read(collection, page.slug) is None:
            row = self._call("POST", api.WIKI_PAGES.format(kb=collection.id), json=body)
        else:
            row = self._call("PUT", api.WIKI_PAGE.format(kb=collection.id, slug=page.slug), json=body)
        return self._page(row) if isinstance(row, dict) and "slug" in row else self.wiki_read(collection, page.slug)

    def wiki_delete(self, collection: Collection, slug: str) -> None:
        try:
            self._call("DELETE", api.WIKI_PAGE.format(kb=collection.id, slug=slug))
        except ConnectorError as exc:
            if exc.status != 404:
                raise

    def wiki_configure(self, collection: Collection, *, auto: bool, synthesis_model_id: str | None) -> dict:
        if auto and not synthesis_model_id:
            return {"auto": False, "note": "开启自动 Wiki 需要在连接器配置 wiki.synthesis_model_id（WeKnora 中的模型 ID）"}
        self._call("PUT", api.KB.format(kb=collection.id), json=api.wiki_settings(auto, synthesis_model_id))
        return {"auto": auto, "synthesis_model_id": synthesis_model_id}
