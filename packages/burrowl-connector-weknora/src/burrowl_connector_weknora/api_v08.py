"""WeKnora v0.8.x 的 REST 契约（路径、字段、状态值）。只在这里出现版本相关的细节。

响应信封：{"success": true, "data": ...}；认证头 X-API-Key；分页 page / page_size（≤100）。
"""
TESTED = "0.8"
PREFIX = "/api/v1"
PAGE_SIZE = 100
METADATA_MAX_KEYS = 20

KBS = "/knowledge-bases"
KB = "/knowledge-bases/{kb}"
KB_KNOWLEDGE = "/knowledge-bases/{kb}/knowledge"
MANUAL_CREATE = "/knowledge-bases/{kb}/knowledge/manual"
MANUAL_UPDATE = "/knowledge/manual/{id}"
KNOWLEDGE = "/knowledge/{id}"
BATCH = "/knowledge/batch"
SEARCH = "/knowledge-search"
WIKI_PAGES = "/knowledgebase/{kb}/wiki/pages"
WIKI_PAGE = "/knowledgebase/{kb}/wiki/pages/{slug}"

# parse_status → 连接器 SPI 的状态
PARSE_STATES = {"pending": "pending", "processing": "processing", "finalizing": "processing",
                "completed": "completed", "failed": "failed"}


def manual_body(title: str, markdown: str) -> dict:
    return {"title": title, "content": markdown, "status": "publish"}


def metadata_body(title: str, metadata: dict[str, str]) -> dict:
    return {"title": title, "custom_metadata": dict(list(metadata.items())[:METADATA_MAX_KEYS])}


def kb_body(name: str) -> dict:
    return {"name": name, "description": "由 burrowl 发布的已挑选知识", "type": "document"}


def wiki_settings(auto: bool, synthesis_model_id: str | None) -> dict:
    body: dict = {"indexing_strategy": {"wiki_enabled": auto}}
    if synthesis_model_id:
        body["wiki_config"] = {"synthesis_model_id": synthesis_model_id}
    return body


def wiki_body(slug: str, title: str, markdown: str, page_type: str, summary: str) -> dict:
    return {"slug": slug, "title": title, "content": markdown, "page_type": page_type, "summary": summary,
            "status": "published"}


def search_body(query: str, kb_ids: list[str], limit: int) -> dict:
    return {"query": query, "knowledge_base_ids": kb_ids, "match_count": min(limit, 200)}
