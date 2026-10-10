"""语义维护：来源变化或新反证之后，对受影响内容做"保留 / 修订 / 重新综合 / 撤回 / 暂缓"。

plan    列出受影响的可编辑记录，交付其完整正文（不猜测怎么改）；计划存为不可覆盖的版本（.burrowl/plans/）
review  记录 AI 逐项给出的判断与修订草案，逐对象预检（不写记忆）
apply   （仅用户，R16 D-03）按对象逐个提交（请求 ID 由计划确定，失败后重试幂等）；跨对象可能部分完成，状态如实记录
撤回（retract）= 把该记录中的结论复核为 retracted，不删除任何内容。

状态（R16 D-02）：planned 已生成 → queued 交给 AI → reviewed 待你确认 → applied / partial。
"""
import uuid
from typing import Any

from ..core.clock import utc_now
from ..core.errors import BurrowlError
from ..core.revdoc import RevisionedDoc
from .memory import provenance
from .memory.claims import ClaimGraph
from .memory.reader import Reader, fixed_ref
from .memory.service import MemoryService
from .query.assemble import record_text
from .review import ReviewService

ACTIONS = ("retain", "revise", "resynthesize", "retract", "defer")
REVIEWABLE = {"planned", "queued", "reviewed"}        # AI 可以写处置的状态
HANDOFF_FROM = {"planned", "reviewed", "partial"}     # 用户可以交给或退回 AI 的状态
APPLICABLE = {"reviewed", "partial", "applied"}        # applied 时重试幂等
EDITABLE = {"detail", "narrative", "experience", "overview", "document", "document_section"}


class MaintenanceService:
    def __init__(self, memory: MemoryService, review: ReviewService):
        self.memory = memory
        self.reviews = review

    def _doc(self, plan_id: str) -> RevisionedDoc:
        if not plan_id.startswith("MP-") or len(plan_id) != 39:
            raise BurrowlError("INVALID_ARGUMENT", "计划 ID 格式无效")
        return RevisionedDoc(self.memory.storage.ws.state(f"plans/{plan_id}"))

    def plan(self, changed_ids: list[str], *, actor: dict, note: str = "", targets: list[str] | None = None) -> dict:
        """targets：只把这些受影响的记录放进计划（R16 D-01，从所选候选生成计划时用）。"""
        impacted = provenance.impact(self.memory.storage, changed_ids)
        if targets is not None:
            wanted = set(targets)
            impacted["affected"] = [i for i in impacted["affected"] if i["canonical_id"] in wanted]
        graph = ClaimGraph(self.memory.storage)
        reader = Reader(self.memory.storage)
        items, heads = [], {}
        for item in impacted["affected"]:
            record = graph.records.get(item["canonical_id"])
            if record is None or record["kind"] not in EDITABLE or record["sensitivity"] == "restricted":
                continue
            current = reader.record(fixed_ref(record))
            head = graph.heads.get(record["owner_id"])
            heads[record["owner_id"]] = head["commit_id"] if head else None
            items.append({"target": fixed_ref(current), "owner_id": record["owner_id"], "kind": record["kind"],
                          "title": record["title"], "content": record_text(current),
                          "formal_dependency": item["formal_dependency"], "paths": item["paths"][:3],
                          "decision": None})
        reader.recheck()
        plan = {"plan_id": f"MP-{uuid.uuid4()}", "changed_ids": changed_ids, "note": note, "created_by": actor,
                "created_at": utc_now(), "basis_heads": heads, "items": items, "applied": {}, "status": "planned",
                "thread": []}
        self._doc(plan["plan_id"]).create(plan)
        return {**plan, "revision": 1}

    def review(self, plan_id: str, decisions: list[dict], *, expected_revision: int | None = None,
               actor: dict | None = None, message: str = "") -> dict:
        """decisions: [{target_id, action, reason, draft?}]。revise/resynthesize 必须给出完整修订草案。
        写完处置后计划进入 reviewed，由用户在工作台预览后执行（R16 D-02、D-03）。"""
        def change(plan: dict) -> dict:
            if plan["status"] not in REVIEWABLE:
                raise BurrowlError("INVALID_TRANSITION", f"计划当前为 {plan['status']}，不能再写处置；如需新的处置请重新生成计划",
                                   {"status": plan["status"]})
            by_id = {i["target"]["target_id"]: i for i in plan["items"]}
            for decision in decisions:
                item = by_id.get(decision.get("target_id"))
                if item is None:
                    raise BurrowlError("INVALID_ARGUMENT", "处置目标不在计划中", {"target_id": decision.get("target_id")})
                if decision.get("action") not in ACTIONS or not str(decision.get("reason", "")).strip():
                    raise BurrowlError("INVALID_ARGUMENT", "action 必须是 retain/revise/resynthesize/retract/defer，且要说明理由")
                if decision["action"] in {"revise", "resynthesize"} and not isinstance(decision.get("draft"), dict):
                    raise BurrowlError("INVALID_ARGUMENT", "修订必须附完整草案（draft）")
                item["decision"] = {k: decision.get(k) for k in ("action", "reason", "draft")}
            plan["status"] = "reviewed"
            if actor is not None:
                plan["reviewed_by"] = actor
            _event(plan, actor, "reviewed", message or "已写好处置，等待用户确认")
            return plan
        plan, revision = self._doc(plan_id).update(expected_revision, change)
        return {"plan_id": plan_id, "revision": revision, "preview": self._preview(plan)}

    def describe(self, target_ids: list[str]) -> dict[str, dict]:
        """说明受影响的身份是什么、能否放进计划修订（R16 D-01）。

        影响分析会同时列出记录和结论；结论落到包含它的记录上修订。L0 原件记录、复核记录等不可修订，
        只能确认无需处理。读不到的身份如实标为不可修订，不猜测。
        """
        graph = ClaimGraph(self.memory.storage)
        out = {}
        for target in target_ids:
            claim, record = graph.claims.get(target, (None, None))
            record = record or graph.records.get(target)
            if record is None:
                out[target] = {"kind": None, "title": target, "record_id": None, "owner_id": None, "editable": False}
                continue
            editable = record["kind"] in EDITABLE and record["sensitivity"] != "restricted"
            out[target] = {"kind": "claim" if claim else record["kind"], "title": claim["statement"] if claim else record["title"],
                           "record_id": record["record_id"], "owner_id": record["owner_id"], "editable": editable}
        return out

    # ---------- 用户在工作台的操作（R16 D-02、D-06） ----------
    def handoff(self, plan_id: str, note: str = "", *, actor: dict) -> dict:
        """交给 AI 起草处置，或把待确认 / 部分完成的计划退回 AI；处置保留，AI 在其基础上修改。"""
        def change(plan: dict) -> dict:
            if plan["status"] not in HANDOFF_FROM:
                raise BurrowlError("INVALID_TRANSITION", f"计划当前为 {plan['status']}，不能交给 AI", {"status": plan["status"]})
            kind = "handoff" if plan["status"] == "planned" else "sent_back"
            plan["status"] = "queued"
            _event(plan, actor, kind, note or ("交给 AI 起草处置" if kind == "handoff" else "退回 AI 修改"))
            return plan
        plan, revision = self._doc(plan_id).update(None, change)
        return {**plan, "revision": revision}

    def plans(self, status: list[str] | None = None) -> list[dict]:
        """计划概况，最近有变化的在前。"""
        root = self.memory.storage.ws.state("plans")
        out = []
        for directory in (root.iterdir() if root.is_dir() else []):
            doc = RevisionedDoc(directory)
            if not directory.name.startswith("MP-") or not doc.exists:
                continue
            plan, revision = doc.read()
            if status and plan["status"] not in status:
                continue
            thread = plan.get("thread") or []
            out.append({"plan_id": plan["plan_id"], "status": plan["status"], "revision": revision,
                        "created_at": plan["created_at"], "changed_ids": plan["changed_ids"], "note": plan.get("note", ""),
                        "items": len(plan["items"]), "updated_at": thread[-1]["at"] if thread else plan["created_at"]})
        return sorted(out, key=lambda p: p["updated_at"], reverse=True)

    def _requests(self, plan: dict, actor: dict) -> dict[str, dict]:
        """按对象组织修订请求；请求 ID 由计划与对象确定，重试不会重复写入。"""
        out: dict[str, dict] = {}
        for item in plan["items"]:
            decision = item["decision"] or {}
            if decision.get("action") not in {"revise", "resynthesize"}:
                continue
            owner_id = item["owner_id"]
            request = out.setdefault(owner_id, {
                "schema_version": 3, "actor": actor, "owner_id": owner_id,
                "request_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{plan['plan_id']}:{owner_id}")),
                "expected_head": plan["basis_heads"].get(owner_id), "operations": []})
            # R16 D-04：修订原因带计划编号，说明它经用户在工作台批准后写入
            draft = dict(decision["draft"]) | {
                "change_reason": f"{decision['reason']}（维护计划 {plan['plan_id']}，经用户在工作台批准后写入）"}
            request["operations"].append({"op": "put_record", "record_id": item["target"]["target_id"],
                                          "expected_revision": item["target"]["revision"], "draft": draft})
        return out

    def _preview(self, plan: dict) -> list[dict]:
        results = []
        for owner_id, request in self._requests(plan, plan["created_by"]).items():
            try:
                checked = self.memory.validate_draft(request)
                results.append({"owner_id": owner_id, "ok": True, "record_results": checked["record_results"]})
            except BurrowlError as exc:
                results.append({"owner_id": owner_id, "ok": False, "error": exc.problem()})
        return results

    def apply(self, plan_id: str, *, actor: dict, expected_revision: int) -> dict:
        doc = self._doc(plan_id)
        plan, revision = doc.read()
        if revision != expected_revision:
            raise BurrowlError("VERSION_CONFLICT", "计划已被修改，请基于最新版本应用", {"current_revision": revision})
        if plan["status"] not in APPLICABLE:
            raise BurrowlError("INVALID_TRANSITION", f"计划当前为 {plan['status']}，要等 AI 写好处置后才能执行",
                               {"status": plan["status"]})
        if any(item["decision"] is None for item in plan["items"]):
            raise BurrowlError("INVALID_TRANSITION", "还有未处置的条目（可用 defer 暂缓）")
        applied: dict[str, Any] = dict(plan["applied"])
        # R16 D-04：修订署名为写处置的 AI；撤回结论的复核署名为执行的用户
        author = plan.get("reviewed_by") or actor
        for owner_id, request in self._requests(plan, author).items():
            if applied.get(owner_id, {}).get("ok"):
                continue
            try:
                receipt = self.memory.commit(request)
                applied[owner_id] = {"ok": True, "commit_id": receipt["commit_id"], "at": utc_now()}
            except BurrowlError as exc:
                applied[owner_id] = {"ok": False, "error": exc.problem(), "at": utc_now()}
        for item in plan["items"]:
            if (item["decision"] or {}).get("action") != "retract":
                continue
            claims = self.memory.inspect(item["owner_id"], record_id=item["target"]["target_id"])["record"]["payload"]
            for claim in claims.get("claims", []):
                key = f"retract:{claim['claim_id']}"
                if applied.get(key, {}).get("ok"):
                    continue
                try:
                    self.reviews.submit({"target_claim_id": claim["claim_id"], "state": "retracted",
                                        "reason": item["decision"]["reason"], "actor": actor})
                    applied[key] = {"ok": True, "at": utc_now()}
                except BurrowlError as exc:
                    applied[key] = {"ok": False, "error": exc.problem(), "at": utc_now()}
        status = "applied" if all(v["ok"] for v in applied.values()) else "partial"
        previous = plan["status"]

        def finish(current: dict) -> dict:
            current |= {"applied": applied, "status": status}
            if previous != "applied":  # applied 后的重试是幂等的，不再记事件
                _event(current, actor, status, "已按处置写入" if status == "applied" else "部分对象写入失败，可重试或退回 AI")
            return current
        plan, revision = doc.update(None, finish)
        return {"plan_id": plan_id, "status": status, "applied": applied, "revision": revision}

    def status(self, plan_id: str) -> dict:
        plan, revision = self._doc(plan_id).read()
        out = {**plan, "revision": revision}
        if plan["status"] in {"reviewed", "partial"}:  # R16 D-05：用户确认前看到逐对象预检
            out["preview"] = self._preview(plan)
        return out


def _event(plan: dict, actor: dict | None, kind: str, text: str) -> None:
    plan.setdefault("thread", []).append({"at": utc_now(), "by": (actor or {}).get("kind", "system"), "kind": kind,
                                          "text": text})
