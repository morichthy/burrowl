"""跨模块的工作流读取：续接上下文（resume）与跨对象总结的材料准备/保存。

这里只组织固定材料并核对依据，不生成任何总结——总结由 AI/人写好后再保存。
"""
import uuid

from ..core.config import Budget
from ..core.errors import BurrowlError, rebase, schema_error
from .memory.claims import blocked_records
from .memory.reader import Reader, fixed_ref
from .memory.service import MemoryService
from .query.assemble import Packet
from .query.budget import Ledger

RESUME_ORDER = ("overview", "narrative", "detail", "route", "question", "experience")
SUMMARY_KINDS = {"detail", "narrative", "experience", "overview", "question", "goal", "route"}


def resume(memory: MemoryService, owner_id: str, *, budget: Budget | None = None) -> dict:
    """续接某对象：目标 → 检查点 → 最新概览/经过/技术单元 → 路线/问题/经验。只读，不执行任何下一步。"""
    reader = Reader(memory.storage)
    snapshot = reader.state(owner_id)
    ledger = Ledger(budget or Budget(output_chars=40_000))
    packet = Packet(reader, ledger, allowed_owner_ids=None, include_figures=False, defer_evidence=True)
    goal, checkpoint = snapshot.pointer("goal"), snapshot.pointer("checkpoint")
    notice = None
    if goal and checkpoint:
        bound = checkpoint["payload"]["goal_ref"]
        if bound["target_id"] != goal["record_id"] or bound.get("revision") != goal["revision"]:
            notice = "检查点绑定的是旧版本目标；当前目标的约束不能回填给旧的尝试"
    ordered = [r for r in (goal, checkpoint) if r]
    for kind in RESUME_ORDER:
        rows = [r for r in snapshot.records.values() if r["kind"] == kind and r["sensitivity"] != "restricted"]
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        if kind == "question":  # 未决问题优先，已解决的也保留（答案可能已失效）
            rows.sort(key=lambda r: r["payload"]["status"] not in {"open", "investigating", "blocked"})
        ordered += rows
    blocked = blocked_records(memory.storage, [r["record_id"] for r in ordered])
    for record in ordered:
        if record["record_id"] in blocked:
            packet.gap("ACCESS_DENIED", "依据链包含受限或不可用的来源，未加入", fixed_ref(record))
            continue
        try:
            packet.add(fixed_ref(record))
        except BurrowlError as exc:
            if exc.code != "BUDGET_EXCEEDED":
                raise
            packet.gap("BUDGET_EXCEEDED", "续接预算用完，其余记录未加入", fixed_ref(record))
            break
    return {"owner_id": owner_id, "notice": notice, "execution_started": False, **packet.result()}


def summary_materials(memory: MemoryService, owner_ids: list[str], *, budget: Budget | None = None,
                      exclude_ids: list[str] | None = None) -> dict:
    """为跨对象总结准备固定材料包（不调用模型、不声称总结已完成）。"""
    if not owner_ids:
        raise BurrowlError("INVALID_ARGUMENT", "需要明确选择要总结的对象")
    reader = Reader(memory.storage)
    ledger = Ledger(budget or Budget(output_chars=80_000))
    packet = Packet(reader, ledger, allowed_owner_ids=set(owner_ids), include_figures=False, defer_evidence=True)
    excluded, coverage = set(exclude_ids or []), []
    for owner_id in dict.fromkeys(owner_ids):
        if owner_id in excluded:
            coverage.append({"owner_id": owner_id, "status": "excluded"})
            continue
        records = [r for r in reader.state(owner_id).records.values()
                   if r["kind"] in SUMMARY_KINDS and r["record_id"] not in excluded and r["sensitivity"] != "restricted"]
        blocked = blocked_records(memory.storage, [r["record_id"] for r in records], graph=reader.claims())
        records = [r for r in records if r["record_id"] not in blocked]
        for rid in blocked:
            packet.gap("ACCESS_DENIED", "依据链包含受限或不可用的来源，未加入", {"target_kind": "record", "target_id": rid})
        for record in records:
            try:
                packet.add(fixed_ref(record))
            except BurrowlError as exc:
                if exc.code != "BUDGET_EXCEEDED":
                    raise
                packet.gap("BUDGET_EXCEEDED", "材料预算用完", fixed_ref(record))
                break
        coverage.append({"owner_id": owner_id, "record_ids": [r["record_id"] for r in records],
                         "status": "covered" if records else "no_material"})
    result = packet.result()
    return {**result, "coverage": coverage, "prepared_only": True, "summary_written": False}


def save_summary(memory: MemoryService, owner_id: str, draft: dict, *, actor: dict, basis_heads: dict,
                 record_id: str | None = None, expected_revision: int | None = None, request_id: str | None = None,
                 dry_run: bool = False) -> dict:
    """把 AI/人写好的总结保存为单一对象的 L3/L4；依据对象有新提交时拒绝（不用旧材料覆盖新结果）。"""
    draft = dict(draft)
    written = draft.setdefault("owner_id", owner_id)  # R20 D-01：可以省略，写了必须是本对象
    errors = []
    if draft.get("kind") not in {"experience", "overview"}:
        errors.append(schema_error("/draft/kind", "总结只能保存为 experience（L3）或 overview（L4）",
                                   expected=["experience", "overview"]))
    if written != owner_id:
        errors.append(schema_error("/draft/owner_id", f"草案写的是 {written}，总结只能保存到本对象 {owner_id}；"
                                                      "owner_id 可以省略", expected=[owner_id]))
    if record_id and not str(draft.get("change_reason", "")).strip():
        errors.append(schema_error("/draft/change_reason", "修订已有总结必须写 change_reason（为什么改）"))
    if (record_id is None) != (expected_revision is None):
        errors.append(schema_error("/expected_revision", "修订时 record_id 与 expected_revision（当前修订号）要一起给；新建时都不给"))
    if errors:
        raise BurrowlError("INVALID_ARGUMENT", "总结草案不符合要求", {"hint": f"字段与示例用 memory_contract(\"{draft.get('kind') or 'overview'}\") 查询"},
                           errors=errors)
    operation: dict = {"op": "put_record", "draft": draft}
    operation |= {"record_id": record_id, "expected_revision": expected_revision} if record_id else {"client_key": "summary"}
    owner = memory.owners().get(owner_id)
    head = memory.snapshot(owner).commit_id if owner.persisted else None
    try:
        return memory.commit({"schema_version": 3, "request_id": request_id or str(uuid.uuid4()), "actor": actor,
                              "owner_id": owner_id, "expected_head": head, "operations": [operation],
                              "dry_run": dry_run},
                             basis_heads={k: v for k, v in basis_heads.items() if k != owner_id} or None)
    except BurrowlError as exc:  # 内部请求的路径换成本工具参数的路径
        exc.errors = rebase(exc.errors, "/operations/0/draft", "/draft")
        raise
