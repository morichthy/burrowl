"""全工作区校验（burrowl validate / system.validate）：只读，逐项报告问题，不修复任何东西。

检查：每个对象的提交链与记录指纹（读取即校验）、写锁与孤立提交、登记来源是否仍在原位且指纹一致、
检索投影是否落后于 HEAD、Run 的编号与位置（R14）。业务结论是否正确不在检查范围内。
"""
from pathlib import PurePosixPath
from typing import Any

from ..core import links
from ..core.errors import BurrowlError
from ..core.hashing import sha256_file
from ..storage import recovery
from .indexing import IndexService
from .memory.claims import FIXING_LEVELS, ClaimGraph, citations
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
        if owner.readable:  # R21 D-10：正文里的长编号只提示，由用户在维护页"引用改写"生成计划
            unreadable = [rid for rid, record in storage.store.read_snapshot(owner).records.items()
                          if record["sensitivity"] != "restricted" and links.raw_identifiers(record)]
            if unreadable:
                warnings.append({"area": "memory", "owner_id": owner.owner_id, "code": "UNREADABLE_REF",
                                 "message": f"{len(unreadable)} 条记录的正文写了长编号或指纹；在维护页“引用改写”处理",
                                 "details": {"records": unreadable[:5]}})
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
    runs_checked = _check_runs(storage, owners, problems, warnings)
    return {"ok": not problems, "owners_checked": checked, "sources_checked": len(storage.sources.entries()),
            "runs_checked": runs_checked, "problems": problems, "warnings": warnings}


def _check_runs(storage: Storage, owners, problems: list[dict], warnings: list[dict]) -> int:
    """R14：Run 按目录位置与编号定位。编号重复是问题；目录名不规范、业务卡 owner_id 与位置不一致、
    没有被任何 L1–L4 记录引用（执行知识尚未写成记录，D-09）是提示。"""
    try:
        runs = owners.all_runs()
    except BurrowlError as exc:
        problems.append({"area": "runs", "code": exc.code, "message": exc.message, "details": exc.details})
        return 0
    for run in runs:
        folder = PurePosixPath(run.native_path).parent
        if folder.name != run.owner_id.casefold():
            warnings.append({"area": "runs", "owner_id": run.owner_id, "code": "RUN_DIR_NAME",
                             "message": "Run 目录名不是小写的 Run 编号，按编号查找会走慢路径", "details": {"path": run.native_path}})
        parent, declared = owners.run_parent(run), run.native.get("owner_id")
        if declared and (parent is None or parent.owner_id != declared):
            warnings.append({"area": "runs", "owner_id": run.owner_id, "code": "RUN_LOCATION",
                             "message": "业务卡的 owner_id 与 Run 所在位置不一致；上级以位置为准",
                             "details": {"declared": declared, "by_location": parent.owner_id if parent else None}})
    if runs:
        cited = citations(ClaimGraph(storage, owners), {r.owner_id for r in runs}, FIXING_LEVELS)
        unfixed = [r.owner_id for r in runs if r.owner_id not in cited]
        if unfixed:
            warnings.append({"area": "runs", "code": "RUN_UNFIXED",
                             "message": f"{len(unfixed)} 个 Run 没有被任何 L1–L4 记录引用：把执行知识写成记录，"
                                        "没有知识的用 runs.discard 丢弃（runs.search unfixed_only=true 查看）",
                             "details": {"run_ids": unfixed[:20]}})
    return len(runs)
