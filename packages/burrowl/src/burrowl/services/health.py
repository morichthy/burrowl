"""全工作区校验（burrowl validate / system.validate）：只读，逐项报告问题，不修复任何东西。

检查：每个对象的提交链与记录指纹（读取即校验）、写锁与孤立提交、登记来源是否仍在原位且指纹一致、
检索投影是否落后于 HEAD。业务结论是否正确不在检查范围内。
"""
from typing import Any

from ..core.errors import BurrowlError
from ..core.hashing import sha256_file
from ..storage import recovery
from .indexing import IndexService
from .memory.context import Storage


def validate(storage: Storage, index: IndexService) -> dict[str, Any]:
    problems: list[dict] = []
    warnings: list[dict] = []  # 不影响当前数据正确性的提示
    try:
        owners = storage.owners.scan()
    except BurrowlError as exc:
        return {"ok": False, "problems": [{"area": "owners", "code": exc.code, "message": exc.message}]}
    checked = 0
    for owner in owners.owners:
        if not owner.persisted:
            continue
        checked += 1
        try:
            report = recovery.inspect(storage.store, owner)  # 读取 HEAD 与整条提交链，指纹不符会报错
        except BurrowlError as exc:
            problems.append({"area": "memory", "owner_id": owner.owner_id, "code": exc.code, "message": exc.message})
            continue
        if report["lock_state"] == "active_or_unknown":
            problems.append({"area": "memory", "owner_id": owner.owner_id, "code": "LOCKED",
                             "message": "存在写锁（可能有写入正在进行）"})
        if report["unreachable_commits"] or report["uncommitted_staging"]:
            warnings.append({"area": "memory", "owner_id": owner.owner_id, "code": "RESIDUE",
                             "message": "有孤立提交或暂存残留（不影响当前数据；用原 request_id 重试可得到幂等结果）",
                             "details": {"commits": report["unreachable_commits"][:5],
                                         "staging": report["uncommitted_staging"][:5]}})
        state = index.status(owner.owner_id)
        if state["index_status"] != "indexed":
            problems.append({"area": "index", "owner_id": owner.owner_id, "code": "INDEX_PENDING",
                             "message": "检索投影落后于 HEAD（运行 index.reconcile）", "details": state["details"]})
    for entry in storage.sources.entries():
        if not entry.get("enabled", True):
            continue
        try:
            source = storage.sources.resolve(entry["source_id"])
            if sha256_file(source.path) != entry.get("sha256"):
                problems.append({"area": "sources", "source_id": entry["source_id"], "code": "STALE_BASIS",
                                 "message": "来源文件内容与登记时不同（引用它的结论会显示依据失效）"})
        except (BurrowlError, OSError) as exc:
            problems.append({"area": "sources", "source_id": entry["source_id"],
                             "code": getattr(exc, "code", "STORAGE_ERROR"), "message": str(exc)})
    return {"ok": not problems, "owners_checked": checked, "sources_checked": len(storage.sources.entries()),
            "problems": problems, "warnings": warnings}
