"""工作流记录（问题、目标、路线、检查点）的状态规则与操作构造器。

所有提交都经过 validate_change，换一个入口也不能绕过状态机，
也不能用"执行成功"代替"问题已解决"。本模块是纯函数，不写文件。
"""
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from ...core.errors import BurrowlError
from ...core.records import CONTENT_FIELDS, content_hash

QUESTION_TRANSITIONS = {
    "open": {"investigating", "blocked", "resolved", "superseded"},
    "investigating": {"open", "blocked", "resolved", "superseded"},
    "blocked": {"open", "investigating", "resolved", "superseded"},
    "resolved": {"open", "investigating"},
    "superseded": {"open", "investigating"},
}
ROUTE_TRANSITIONS = {
    "planned": {"active", "blocked", "closed", "superseded"},
    "active": {"blocked", "closed", "superseded"},
    "blocked": {"active", "closed", "superseded"},
    "closed": {"active"},
    "superseded": {"active"},
}
Resolve = Callable[[dict], Any] | None


def _fail(message: str, path: str):
    raise BurrowlError("INVALID_TRANSITION", message,
                   errors=[{"code": "INVALID_TRANSITION", "path": path, "message": message}])


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _resolved(ref: Any, resolve: Resolve, path: str) -> dict | None:
    if not isinstance(ref, dict):
        _fail("需要固定引用", path)
    if ref.get("target_kind") == "record":
        if type(ref.get("revision")) is not int or ref["revision"] < 1:
            _fail("记录依据必须固定正整数修订", path + "/revision")
    elif not ref.get("sha256"):
        _fail("对象、结论或文件依据必须绑定内容指纹", path + "/sha256")
    if resolve is None:
        return None  # 构造草案时允许不解析；实际提交由服务传入解析器
    value = resolve(ref)
    if not isinstance(value, dict):
        _fail("固定依据无法解析", path)
    return value


def _kind(value: dict) -> str | None:
    if value.get("claim") is not None:
        return "claim"
    return value.get("kind") or value.get("owner_type")


def _require_kind(ref: Any, kinds: set[str], resolve: Resolve, path: str) -> None:
    value = _resolved(ref, resolve, path)
    if value is not None and _kind(value) not in kinds:
        _fail("引用目标类别不符合用途：" + ", ".join(sorted(kinds)), path)


def _answer(ref: Any, record_id: str, resolve: Resolve, path: str) -> None:
    """解决依据必须是真实的答案或决策，不能只是导航、步骤或另一个未决问题。"""
    if isinstance(ref, dict) and ref.get("target_id") == record_id:
        _fail("问题不能用自身作为解决依据", path)
    value = _resolved(ref, resolve, path)
    if value is None or _kind(value) == "claim" or ref.get("target_kind") == "claim":
        return
    kind, payload = _kind(value), value.get("payload", {})
    native = value.get("native", {})
    if kind == "narrative" and (payload.get("claims") or any(
            _text(s.get("outcome")) and s.get("evidence_refs") for s in payload.get("stages", []))):
        return
    if kind == "experience" and (_text(payload.get("recommendation")) or payload.get("claims")
                                 or payload.get("claim_refs")):
        return
    if kind == "overview" and payload.get("claims"):
        return
    if kind == "run" and (_text(native.get("conclusion")) or _text(native.get("decision"))):
        return
    _fail("解决依据必须是可取得的答案或决策，不能只引用导航、步骤或未决问题", path)


def validate_change(old: dict | None, record: dict, *, record_id: str, resolve: Resolve) -> None:
    """schema 校验之后、计算指纹之前调用。只加研究语义，不替代来源与复核检查。"""
    kind, payload = record["kind"], record["payload"]
    before = old["payload"] if old else None
    if kind == "question":
        state = payload["status"]
        if old and state != before["status"]:
            if state not in QUESTION_TRANSITIONS[before["status"]]:
                _fail("问题状态转换不在允许范围", "/payload/status")
            if before["status"] in {"resolved", "superseded"} and not _text(payload["reopen_reason"]):
                _fail("已结束问题重开必须说明原因", "/payload/reopen_reason")
        if state == "resolved":
            if not payload["resolution_refs"]:
                _fail("resolved 必须有固定答案或决策引用", "/payload/resolution_refs")
            for index, ref in enumerate(payload["resolution_refs"]):
                _answer(ref, record_id, resolve, f"/payload/resolution_refs/{index}")
        if state == "superseded":
            ref = payload["replacement_ref"]
            if not ref or ref.get("target_id") == record_id:
                _fail("替代必须指向另一个问题", "/payload/replacement_ref")
            _require_kind(ref, {"question"}, resolve, "/payload/replacement_ref")
    elif kind == "goal":
        ref = payload["previous_goal_ref"]
        if ref:
            _require_kind(ref, {"goal"}, resolve, "/payload/previous_goal_ref")
        if old and content_hash(record) != old["content_hash"]:
            if not ref or ref.get("target_id") != record_id or ref.get("revision") != old["revision"]:
                _fail("更改目标必须固定关联本目标上一修订", "/payload/previous_goal_ref")
            if not _text(payload["change_impact"]):
                _fail("更改目标必须说明对既有尝试的影响", "/payload/change_impact")
        elif not old and ref and ref.get("target_id") == record_id:
            _fail("首版目标不能引用自身不存在的旧版本", "/payload/previous_goal_ref")
    elif kind == "route":
        _validate_route(old, record, record_id, resolve)
    elif kind == "checkpoint":
        _require_kind(payload["goal_ref"], {"goal"}, resolve, "/payload/goal_ref")
        for name, expected in (("route_refs", "route"), ("question_refs", "question")):
            for index, ref in enumerate(payload[name]):
                _require_kind(ref, {expected}, resolve, f"/payload/{name}/{index}")
        budget = payload["budget_remaining"]
        # 未知用 UnknownValue 原样保存；明确余额不得为负。
        if "value" not in budget and any(v < 0 for v in budget.values()):
            _fail("预算余额不得为负数；未知使用 UnknownValue", "/payload/budget_remaining")


def _validate_route(old: dict | None, record: dict, record_id: str, resolve: Resolve) -> None:
    payload, state = record["payload"], record["payload"]["status"]
    _require_kind(payload["goal_ref"], {"goal"}, resolve, "/payload/goal_ref")
    if state == "blocked" and not _text(payload["blocker"]):
        _fail("路线阻塞必须说明具体条件", "/payload/blocker")
    if state in {"active", "closed"} and payload["blocker"] is not None:
        _fail("active/closed 路线不能继续携带未解决 blocker", "/payload/blocker")
    if not _text(payload["next_step"]) or not _text(payload["reopen_condition"]):
        _fail("路线必须说明下一步或关闭后动作，以及重新开启条件", "/payload/next_step")
    if state == "superseded":
        ref = payload["replacement_ref"]
        if not ref or ref.get("target_id") == record_id:
            _fail("替代必须指向另一条路线", "/payload/replacement_ref")
        _require_kind(ref, {"route"}, resolve, "/payload/replacement_ref")
    if not old:
        return
    before = old["payload"]
    if payload["goal_ref"] != before["goal_ref"]:
        _fail("更换目标应建新路线；旧尝试继续绑定旧目标", "/payload/goal_ref")
    if any(ref not in payload["attempt_refs"] for ref in before["attempt_refs"]):
        _fail("路线修订不能移除已登记尝试", "/payload/attempt_refs")
    if state != before["status"] and state not in ROUTE_TRANSITIONS[before["status"]]:
        _fail("路线状态转换不在允许范围", "/payload/status")
    if before["status"] in {"blocked", "closed", "superseded"} and state == "active":
        if not _text(record.get("change_reason")):
            _fail("重新 active 必须记录重新开启理由", "/change_reason")
        if not any(ref not in old["sources"] for ref in record["sources"]):
            _fail("重新 active 必须绑定变化后的固定依据", "/sources")


# ---------- 操作构造器（便于 AI 只传语义字段） ----------
def draft_from(record: dict) -> dict:
    """剥离服务字段，得到可再次提交的草案。"""
    return {k: deepcopy(record[k]) for k in ("schema_version", *CONTENT_FIELDS, "record_reason", "change_reason")
            if k in record}


def transition_question(record: dict, state: str, basis: dict) -> dict:
    if record.get("kind") != "question" or state not in QUESTION_TRANSITIONS:
        _fail("目标不是问题或状态未知", "/payload/status")
    if not _text(basis.get("change_reason")):
        _fail("转换必须说明变更理由", "/change_reason")
    draft = draft_from(record)
    draft["payload"]["status"] = state
    for key, value in basis.items():
        (draft if key in {"change_reason", "sources"} else draft["payload"])[key] = deepcopy(value)
    if state != "superseded":
        draft["payload"]["replacement_ref"] = None
    draft["payload"]["reopen_reason"] = basis.get("reopen_reason")
    validate_change(record, draft, record_id=record["record_id"], resolve=None)
    return {"op": "transition_question", "record_id": record["record_id"],
            "expected_revision": record["revision"], "draft": draft}
