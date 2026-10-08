"""语义维护：来源变化或新反证之后，对受影响内容做"保留 / 修订 / 重新综合 / 撤回 / 暂缓"。

plan    列出受影响的可编辑记录，交付其完整正文（不猜测怎么改）；计划存为不可覆盖的版本（.burrowl/plans/）
review  记录 AI/人逐项给出的判断与修订草案，逐对象预检（不写记忆）
apply   按对象逐个提交（请求 ID 由计划确定，失败后重试幂等）；跨对象可能部分完成，状态如实记录
撤回（retract）= 把该记录中的结论复核为 retracted，不删除任何内容。
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
EDITABLE = {"detail", "narrative", "experience", "overview", "document", "document_section"}


class MaintenanceService:
    def __init__(self, memory: MemoryService, review: ReviewService):
        self.memory = memory
        self.reviews = review

    def _doc(self, plan_id: str) -> RevisionedDoc:
        if not plan_id.startswith("MP-") or len(plan_id) != 39:
            raise BurrowlError("INVALID_ARGUMENT", "计划 ID 格式无效")
        return RevisionedDoc(self.memory.storage.ws.state(f"plans/{plan_id}"))

    def plan(self, changed_ids: list[str], *, actor: dict, note: str = "") -> dict:
        impacted = provenance.impact(self.memory.storage, changed_ids)
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
                "created_at": utc_now(), "basis_heads": heads, "items": items, "applied": {}, "status": "planned"}
        self._doc(plan["plan_id"]).create(plan)
        return {**plan, "revision": 1}

    def review(self, plan_id: str, decisions: list[dict], *, expected_revision: int | None = None) -> dict:
        """decisions: [{target_id, action, reason, draft?}]。revise/resynthesize 必须给出完整修订草案。"""
        def change(plan: dict) -> dict:
            if plan["status"] == "applied":
                raise BurrowlError("INVALID_TRANSITION", "计划已应用；如需新的处置请重新生成计划")
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
            return plan
        plan, revision = self._doc(plan_id).update(expected_revision, change)
        return {"plan_id": plan_id, "revision": revision, "preview": self._preview(plan)}

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
            draft = dict(decision["draft"]) | {"change_reason": decision["reason"]}
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
        if any(item["decision"] is None for item in plan["items"]):
            raise BurrowlError("INVALID_TRANSITION", "还有未处置的条目（可用 defer 暂缓）")
        applied: dict[str, Any] = dict(plan["applied"])
        for owner_id, request in self._requests(plan, actor).items():
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
        plan, revision = doc.update(None, lambda p: p | {"applied": applied, "status": status})
        return {"plan_id": plan_id, "status": status, "applied": applied, "revision": revision}

    def status(self, plan_id: str) -> dict:
        plan, revision = self._doc(plan_id).read()
        return {**plan, "revision": revision}
