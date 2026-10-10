"""研究时间线：按发生时间列出某对象全部记录的全部修订，以及显式关联的 Run。

当前被限制的记录，其旧修订也一并隐藏（不可变历史不是绕过授权的通道）。
"""
from copy import deepcopy

from ...core.errors import BurrowlError
from .claims import iter_refs
from .context import Storage
from .reader import Reader, fixed_ref

ROLE = {"source": "raw_material", "detail": "technical_unit", "narrative": "process", "experience": "experience",
        "overview": "overview", "document": "document", "document_section": "document_section",
        "goal": "goal", "checkpoint": "checkpoint", "route": "route", "question": "question"}


def _run_ids(reader: Reader, owner_id: str, records: list[dict]) -> set[str]:
    """记录里的 RUN 引用，加上位于该对象目录下的 Run（R14 D-04：上级按目录位置判断）。标题不算。"""
    runs = {ref["target_id"] for r in records for ref in iter_refs(r["payload"])
            if ref.get("target_kind") == "owner" and ref["target_id"].startswith("RUN-")}
    owner = reader.index.get(owner_id)
    if owner.owner_type == "run":
        runs.add(owner.owner_id)
    else:
        runs.update(r.owner_id for r in reader.index.runs_under(owner))
    return runs


def timeline(storage: Storage, owner_id: str, *, offset: int = 0, limit: int = 50) -> dict:
    if offset < 0 or not 1 <= limit <= 500:
        raise BurrowlError("INVALID_ARGUMENT", "offset 必须非负，limit 范围为 1..500")
    reader = Reader(storage)
    owner = reader.owner(owner_id)
    current = reader.state(owner_id)
    rows, missing = [], []
    records = storage.store.read_history(owner, current)
    for record in records:
        if record["sensitivity"] == "restricted" or current.records[record["record_id"]]["sensitivity"] == "restricted":
            continue
        issues = reader.try_check(list(iter_refs({"sources": record["sources"], "payload": record["payload"]})))
        if any(i["code"] in {"ACCESS_DENIED", "UNSAFE_PATH"} for i in issues):
            missing.append({"target_id": record["record_id"], "code": "ACCESS_DENIED"})
            continue
        occurred = record["payload"].get("occurred_at")
        rows.append({"id": f"{record['record_id']}@{record['revision']}", "record_id": record["record_id"],
                     "revision": record["revision"], "current": current.records[record["record_id"]]["revision"]
                     == record["revision"], "kind": record["kind"], "level": record["level"],
                     "role": ROLE.get(record["kind"], record["kind"]), "title": record["title"],
                     "ref": fixed_ref(record), "occurred_at": occurred, "created_at": record["created_at"],
                     "updated_at": record["updated_at"], "source_issues": issues})
    for run_id in sorted(_run_ids(reader, owner_id, records)):
        try:
            run = reader.owner(run_id)
        except BurrowlError as exc:
            missing.append({"target_id": run_id, "code": exc.code})
            continue
        rows.append({"id": run_id, "record_id": run_id, "revision": None, "current": True, "kind": "run", "level": "L0",
                     "role": "run_attempt", "title": run.title, "occurred_at": run.native.get("started_at"),
                     "created_at": run.native.get("created_at"), "updated_at": run.native.get("ended_at"),
                     "status": run.native.get("status"), "source_issues": [],
                     "ref": {"target_kind": "owner", "target_id": run_id, "revision": None, "sha256": run.fingerprint,
                             "locator": "", "relation": "references"}})
    rows.sort(key=lambda r: (r["occurred_at"] or r["created_at"] or "", r["record_id"], r["revision"] or 0))
    reader.recheck()
    end = offset + limit
    return {"owner_id": owner_id, "basis_heads": reader.heads(), "items": rows[offset:end], "total": len(rows),
            "offset": offset, "next_offset": end if end < len(rows) else None, "missing": missing, "writes": 0}


def record_payload(storage: Storage, owner_id: str, record_id: str, revision: int) -> dict:
    """时间线条目的完整内容（按需读取，列表本身不带正文）。"""
    reader = Reader(storage)
    reader.state(owner_id)
    record = reader.record({"target_id": record_id, "revision": revision})
    if record["owner_id"] != owner_id:
        raise BurrowlError("ACCESS_DENIED", "记录不属于所选对象")
    reader.recheck()
    return deepcopy(record)
