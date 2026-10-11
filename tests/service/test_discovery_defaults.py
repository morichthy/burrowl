"""R19 D-01、D-02：默认可见范围按记录类型；修订沿用上一修订；对象策略显式设置优先。

复现（R18 走查，2026-10-11）：AI 保存 L3 时没写 discovery，取了工作区默认 owner_only，全库召回里看不到它。
"""
import pytest

from burrowl.services.memory import associations
from tests.service.helpers import AI, goal_draft, head_of, narrative_draft, register_source, request, services

OWNER = "RES-VIS"


@pytest.fixture
def env(tmp_path):
    storage, memory, _review = services(tmp_path)
    storage.owners.create("research", "vis", "可见范围", {})
    evidence = register_source(storage, "SRC-VIS", "work/vis.csv", "a\n1\n")
    return storage, memory, evidence


def put(memory, draft, key="k", record_id=None, **extra):
    operation = {"op": "put_record", "draft": draft} | (
        {"record_id": record_id, "expected_revision": memory.inspect(OWNER, record_id=record_id)["record"]["revision"]}
        if record_id else {"client_key": key})
    receipt = memory.commit(request(OWNER, head_of(memory, OWNER), operation) | extra)
    return receipt["record_results"][0]["record_id"]


def discovery(memory, record_id):
    return memory.inspect(OWNER, record_id=record_id)["record"]["discovery"]


def test_knowledge_records_default_to_workspace(env):
    _storage, memory, evidence = env
    goal = put(memory, goal_draft(OWNER), "g")
    narrative = put(memory, narrative_draft(OWNER, evidence), "n")
    assert discovery(memory, narrative) == "workspace_summary"
    assert discovery(memory, goal) == "owner_only"  # 工作状态按位置读取，不进召回


def test_explicit_draft_value_wins(env):
    _storage, memory, evidence = env
    narrative = put(memory, narrative_draft(OWNER, evidence) | {"discovery": "owner_only"}, "n")
    assert discovery(memory, narrative) == "owner_only"


def test_revision_keeps_previous_value(env):
    _storage, memory, evidence = env
    narrative = put(memory, narrative_draft(OWNER, evidence) | {"discovery": "owner_only"}, "n")
    revised = narrative_draft(OWNER, evidence) | {"title": "顺序实验经过（修订）", "change_reason": "补标题"}
    put(memory, revised, record_id=narrative)
    assert discovery(memory, narrative) == "owner_only"  # D-02：默认值不能静默改变已有记录


def test_owner_policy_overrides_kind_default(env):
    _storage, memory, evidence = env
    policy = {"owner_id": OWNER, "kind": "policy", "title": "保密对象", "body_markdown": "", "record_reason": "保密",
              "sensitivity": "internal", "sources": [], "provenance_gap": "用户口头要求",
              "payload": {"mode": "explore", "overrides": {"discovery": "owner_only"}}}
    memory.commit(request(OWNER, head_of(memory, OWNER), {"op": "set_policy", "client_key": "p", "draft": policy}))
    narrative = put(memory, narrative_draft(OWNER, evidence), "n")
    assert discovery(memory, narrative) == "owner_only"


def test_association_defaults_to_workspace(env):
    _storage, memory, evidence = env
    ids = [put(memory, narrative_draft(OWNER, evidence, claim_id=f"CLM-ORDER-{i}"), f"n{i}") for i in range(3)]
    refs = [memory.inspect(OWNER, record_id=r)["record"] for r in ids]
    ends = [{"target_kind": "record", "target_id": r["record_id"], "revision": r["revision"], "sha256": r["record_hash"],
             "locator": "record", "relation": "references"} for r in refs]
    receipt = associations.decide(memory, OWNER, {"from": ends[0], "to": ends[1], "explanation": "同一组实验的两次记录"},
                                  actor=AI, reason="一起看")
    assert discovery(memory, receipt["record_results"][0]["record_id"]) == "workspace_summary"
    secret = associations.decide(memory, OWNER, {"from": ends[0], "to": ends[2], "explanation": "保密对照"},
                                 actor=AI, reason="保密", discovery="owner_only")
    assert discovery(memory, secret["record_results"][0]["record_id"]) == "owner_only"
