"""R19 §8 可见范围补齐：按旧默认存成 owner_only 的知识记录 → 用户勾选 → 生成已写好处置的计划 → 预览后执行。"""
import pytest

from burrowl.app import App
from burrowl.core.errors import BurrowlError
from burrowl.interfaces.ops.registry import REGISTRY, Ctx, call
from tests.service.helpers import goal_draft, narrative_draft

HUMAN = {"kind": "human", "id": "workbench-user"}
AI = {"kind": "ai", "id": "test-assistant"}


@pytest.fixture
def lab(tmp_path):
    app = App.open(tmp_path / "data", create=True, fake_vectors=True)
    user, ai = Ctx(app=app, actor=HUMAN, channel="test"), Ctx(app=app, actor=AI, channel="test")
    (app.root / "notes").mkdir()
    (app.root / "notes" / "r.txt").write_text("升序误差更小\n", encoding="utf-8")
    registered = call(ai, "sources.register", {"source_id": "SRC-R", "path": "notes/r.txt"})
    evidence = {"target_kind": "file", "target_id": "SRC-R", "revision": None, "sha256": registered["sha256"],
                "locator": "", "relation": "input"}
    ids = {}
    for slug, secret in (("open", False), ("secret", True)):
        owner = call(ai, "memory.owner_create", {"owner_type": "research", "slug": slug, "title": f"对象 {slug}"})
        ops = []
        if secret:  # 对象策略显式要求保密：它的记录不列为候选
            ops.append({"op": "set_policy", "client_key": "p", "draft": {
                "owner_id": owner["owner_id"], "kind": "policy", "title": "保密", "body_markdown": "",
                "record_reason": "用户要求保密", "sensitivity": "internal", "sources": [], "provenance_gap": "口头要求",
                "payload": {"mode": "explore", "overrides": {"discovery": "owner_only"}}}})
        old = narrative_draft(owner["owner_id"], evidence, f"CLM-{slug.upper()}-1", "升序求和误差更小") | {
            "title": f"{slug} 的旧经过", "discovery": "owner_only"}  # 模拟 R19 之前取了旧默认
        ops += [{"op": "put_record", "client_key": "n", "draft": old},
                {"op": "put_record", "client_key": "g", "draft": goal_draft(owner["owner_id"])}]
        receipt = call(ai, "memory.commit", {"owner_id": owner["owner_id"], "expected_head": None, "operations": ops})
        ids[slug] = {r["client_key"]: r["record_id"] for r in receipt["record_results"]}
    return user, ai, ids


def search_ids(ctx, question):
    found = call(ctx, "query.search", {"question": question, "content_source": "process"})  # 记录是 L2 经过
    return {i["record_id"] for i in found["items"]}


def test_backfill_lists_old_knowledge_records_and_user_applies(lab):
    user, ai, ids = lab
    narrative, goal = ids["open"]["n"], ids["open"]["g"]
    candidates = {c["record_id"] for c in call(ai, "maintenance.visibility_candidates", {})}
    assert narrative in candidates
    assert goal not in candidates                     # 工作状态本来就只在本对象可见
    assert ids["secret"]["n"] not in candidates       # 对象策略显式保密
    assert narrative not in search_ids(user, "open 的旧经过")

    plan = call(user, "maintenance.plan_visibility", {"target_ids": [narrative]})
    assert plan["status"] == "reviewed" and plan["preview"][0]["ok"]
    assert plan["items"][0]["decision"]["draft"]["discovery"] == "workspace_summary"
    applied = call(user, "maintenance.apply", {"plan_id": plan["plan_id"], "expected_revision": plan["revision"]})
    assert applied["status"] == "applied"
    record = call(user, "memory.inspect", {"owner_id": "RES-OPEN", "record_id": narrative})["record"]
    assert record["discovery"] == "workspace_summary" and record["revision"] == 2
    assert "维护计划" in record["change_reason"]
    assert narrative in search_ids(user, "open 的旧经过")
    assert narrative not in {c["record_id"] for c in call(ai, "maintenance.visibility_candidates", {})}


def test_only_user_generates_backfill_plan_and_only_from_candidates(lab):
    user, _ai, ids = lab
    assert REGISTRY["maintenance.plan_visibility"].effect == "user"
    with pytest.raises(BurrowlError):
        call(user, "maintenance.plan_visibility", {"target_ids": [ids["secret"]["n"]]})
