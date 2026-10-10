"""R16 语义维护交给 AI：候选一键成计划 → 交给 AI → AI 写处置 → 用户预览后执行或退回。"""
import pytest

from burrowl.app import App
from burrowl.core.errors import BurrowlError
from burrowl.interfaces.ops.registry import Ctx, call
from tests.service.helpers import narrative_draft

HUMAN = {"kind": "human", "id": "workbench-user"}
AI = {"kind": "ai", "id": "test-assistant"}
SOURCE = "SRC-LAB-RESULT"


@pytest.fixture
def lab(tmp_path):
    app = App.open(tmp_path / "data", create=True, fake_vectors=True)
    user, ai = Ctx(app=app, actor=HUMAN, channel="test"), Ctx(app=app, actor=AI, channel="test")
    owner = call(ai, "memory.owner_create", {"owner_type": "research", "slug": "lab", "title": "顺序研究"})["owner_id"]
    (app.root / "notes").mkdir()
    path = app.root / "notes" / "result.txt"
    path.write_text("升序误差更小\n", encoding="utf-8")
    registered = call(ai, "sources.register", {"source_id": SOURCE, "path": "notes/result.txt"})
    evidence = {"target_kind": "file", "target_id": SOURCE, "revision": None, "sha256": registered["sha256"],
                "locator": "", "relation": "input"}
    ops = []
    for key, title in (("a", "第一轮经过"), ("b", "第二轮经过")):
        draft = narrative_draft(owner, evidence, f"CLM-LAB-{key}", f"{title}的结论")
        draft["title"] = title
        ops.append({"op": "put_record", "client_key": key, "draft": draft})
    receipt = call(ai, "memory.commit", {"owner_id": owner, "expected_head": None, "operations": ops})
    ids = [r["record_id"] for r in receipt["record_results"]]
    call(user, "monitor.run", {})                                   # 首次扫描只建立基线
    path.write_text("升序误差更小（数据已更正）\n", encoding="utf-8")
    call(user, "monitor.run", {})
    return user, ai, owner, ids, evidence


def test_candidates_to_plan_then_ai_drafts_and_user_applies(lab):
    user, ai, owner, (first, second), evidence = lab
    candidates = {c["target_id"] for c in call(user, "monitor.candidates", {})}
    assert {first, second} <= candidates

    # D-01：只为所选候选生成计划；候选标为 planned 并记下计划编号
    plan = call(user, "maintenance.plan_from_candidates", {"target_ids": [first]})
    assert [i["target"]["target_id"] for i in plan["items"]] == [first] and plan["changed_ids"] == [SOURCE]
    marked = {c["target_id"]: c for c in call(user, "monitor.candidates", {"status": None})}
    assert marked[first]["status"] == "planned" and marked[first]["note"] == plan["plan_id"]
    assert marked[second]["status"] == "open"

    # D-03：还没有处置时不能执行；交给 AI 后 AI 能按状态找到
    with pytest.raises(BurrowlError):
        call(user, "maintenance.apply", {"plan_id": plan["plan_id"], "expected_revision": 1})
    call(user, "maintenance.handoff", {"plan_id": plan["plan_id"], "note": "数据更正了，请改写结论"})
    assert [p["plan_id"] for p in call(ai, "maintenance.plans", {"status": ["queued"]})] == [plan["plan_id"]]

    # 原件内容变了：按规则用新来源 ID 登记新版本，修订稿引用新版本
    corrected = call(ai, "sources.register", {"source_id": SOURCE + "-V2", "path": "notes/result.txt"})
    revised = narrative_draft(owner, {**evidence, "target_id": SOURCE + "-V2", "sha256": corrected["sha256"]},
                              "CLM-LAB-a", "更正后的结论")
    revised["title"] = "第一轮经过"
    reviewed = call(ai, "maintenance.review", {"plan_id": plan["plan_id"], "message": "按更正后的数据改写结论",
                                               "decisions": [{"target_id": first, "action": "revise",
                                                              "reason": "来源数据更正", "draft": revised}]})
    status = call(user, "maintenance.status", {"plan_id": plan["plan_id"]})
    assert status["status"] == "reviewed" and status["preview"][0]["ok"], status["preview"]  # D-05：确认前附预检

    # 退回：处置保留，意见进入往来；AI 重写后再执行
    call(user, "maintenance.handoff", {"plan_id": plan["plan_id"], "note": "理由写得再具体些"})
    back = call(user, "maintenance.status", {"plan_id": plan["plan_id"]})
    assert back["status"] == "queued" and back["items"][0]["decision"]["action"] == "revise"
    assert back["thread"][-1]["kind"] == "sent_back" and back["thread"][-1]["text"] == "理由写得再具体些"
    reviewed = call(ai, "maintenance.review", {"plan_id": plan["plan_id"],
                                               "decisions": [{"target_id": first, "action": "revise",
                                                              "reason": "来源第 1 行数据更正", "draft": revised}]})

    applied = call(user, "maintenance.apply", {"plan_id": plan["plan_id"], "expected_revision": reviewed["revision"]})
    assert applied["status"] == "applied"
    record = call(user, "memory.inspect", {"owner_id": owner, "record_id": first})["record"]
    assert record["revision"] == 2 and plan["plan_id"] in record["change_reason"]
    assert record["updated_by"] == AI                                           # D-04：修订署名为写处置的 AI，而非执行的用户
    assert call(user, "maintenance.plans", {"status": ["applied"]})[0]["plan_id"] == plan["plan_id"]


def test_apply_is_user_only_over_mcp_tools():
    from burrowl.interfaces.ops.registry import REGISTRY, load

    load()
    assert REGISTRY["maintenance.apply"].effect == "user"
    assert REGISTRY["maintenance.handoff"].effect == "user"
    assert REGISTRY["maintenance.plan_from_candidates"].effect == "user"
    assert REGISTRY["maintenance.plans"].effect == "read"


def test_claim_candidates_map_to_their_record_and_empty_plans_are_refused(lab):
    """候选里的结论落到包含它的记录上；只选了不可修订的候选时拒绝，而不是生成空计划。"""
    user, _ai, _owner, (first, _second), _evidence = lab
    candidates = {c["target_id"]: c for c in call(user, "monitor.candidates", {})}
    claim = candidates["CLM-LAB-a"]
    assert claim["kind"] == "claim" and claim["record_id"] == first and claim["editable"]
    plan = call(user, "maintenance.plan_from_candidates", {"target_ids": ["CLM-LAB-a"]})
    assert [i["target"]["target_id"] for i in plan["items"]] == [first]

    with pytest.raises(BurrowlError) as refused:
        call(user, "maintenance.plan_from_candidates", {"target_ids": [SOURCE]})   # 不是候选
    assert refused.value.code == "INVALID_ARGUMENT"
