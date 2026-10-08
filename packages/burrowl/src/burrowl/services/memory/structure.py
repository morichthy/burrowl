"""结构导航：对象清单（按类型）与单个对象内的记录目录（按层级，分页）。只读元数据，不读正文。"""
from ...core.errors import BurrowlError
from .context import Storage

LEVEL_ORDER = {"L4": 0, "L3": 1, "L2": 2, "L1": 3, "L0": 4, None: 5}
MAIN_KINDS = {"source", "detail", "narrative", "experience", "overview", "document", "document_section"}


def owners(storage: Storage, *, owner_types: list[str] | None = None, query: str = "") -> dict:
    needle = query.casefold()
    groups: dict[str, list[dict]] = {}
    for owner in storage.owners.scan().readable():
        if owner_types and owner.owner_type not in owner_types:
            continue
        if needle and needle not in owner.title.casefold() and needle not in owner.owner_id.casefold():
            continue
        records = storage.store.read_snapshot(owner).records if owner.persisted else {}
        counts: dict[str, int] = {}
        for record in records.values():
            if record["sensitivity"] != "restricted":
                key = record["level"] or record["kind"]
                counts[key] = counts.get(key, 0) + 1
        groups.setdefault(owner.owner_type, []).append(owner.summary() | {"counts": counts})
    return {"groups": [{"owner_type": t, "owners": sorted(items, key=lambda o: o["title"])}
                       for t, items in sorted(groups.items())]}


def records(storage: Storage, owner_id: str, *, levels: list[str] | None = None, kinds: list[str] | None = None,
            query: str = "", main_only: bool = True, offset: int = 0, limit: int = 100) -> dict:
    """对象内记录目录。main_only=True 时只列 L0–L4 与文稿；目标、检查点、复核等工作流记录按需展开。

    kinds 按记录类型筛选，query 匹配标题或记录 ID。kind_counts 是 kinds 筛选之前
    （已应用 main_only、levels、query）的各类型数量，供界面在筛选项上显示计数。
    """
    if offset < 0 or not 1 <= limit <= 500:
        raise BurrowlError("INVALID_ARGUMENT", "offset 必须非负，limit 范围 1..500")
    owner = storage.owners.scan().get(owner_id).require_readable()
    snapshot = storage.store.read_snapshot(owner) if owner.persisted else None
    needle = query.strip().casefold()
    rows: list[dict] = []
    kind_counts: dict[str, int] = {}
    for record in (snapshot.records.values() if snapshot else []):
        if record["sensitivity"] == "restricted" or (main_only and record["kind"] not in MAIN_KINDS):
            continue
        if levels and record["level"] not in levels:
            continue
        if needle and needle not in record["title"].casefold() and needle not in record["record_id"].casefold():
            continue
        kind_counts[record["kind"]] = kind_counts.get(record["kind"], 0) + 1
        if kinds and record["kind"] not in kinds:
            continue
        rows.append({"record_id": record["record_id"], "kind": record["kind"], "level": record["level"],
                     "title": record["title"], "revision": record["revision"], "updated_at": record["updated_at"],
                     "excerpt": _excerpt(record),
                     "claims": [c["claim_id"] for c in record["payload"].get("claims", [])]})
    rows.sort(key=lambda r: (LEVEL_ORDER.get(r["level"], 5), r["kind"], r["title"]))
    end = offset + limit
    return {"owner": owner.summary(), "head": snapshot.head if snapshot else None, "items": rows[offset:end],
            "total": len(rows), "next_offset": end if end < len(rows) else None, "kind_counts": kind_counts}


EXCERPT_CHARS = 120


def _excerpt(record: dict) -> str:
    """目录行的一句摘要：正文第一行非空文字（去掉标题、引用、列表符号），没有正文时用保存原因。只截断不改写。"""
    for line in (record.get("body_markdown") or "").splitlines():
        text = line.strip().lstrip("#>-* ").strip()
        if text:
            return text[:EXCERPT_CHARS]
    return (record.get("record_reason") or "")[:EXCERPT_CHARS]
