"""影响、溯源、导出、关联、巩固、续接与总结（样例 + 新建对象）。"""
import pytest

from burrowl.core.errors import BurrowlError
from burrowl.services import workflow
from burrowl.services.memory import associations, consolidation, export, provenance
from burrowl.services.memory.context import Storage
from burrowl.services.memory.service import MemoryService
from tests.service.helpers import AI, narrative_draft, register_source, request, services

OWNER = "RES-FLOATING-POINT-SUMMATION"


def test_sample_readonly_views(sample_workspace):
    storage = Storage.open(sample_workspace)
    memory = MemoryService(storage)
    records = memory.inspect(OWNER)["records"]
    detail = next(r for r in records.values() if r["kind"] == "detail")
    impacted = provenance.impact(storage, [detail["record_id"]])
    assert impacted["affected"] and impacted["writes"] == 0
    lineage = provenance.lineage(storage, [detail["record_id"]])
    assert lineage["source_count"] >= 1
    out = export.export(memory, OWNER, fmt="markdown", include_raw_materials=True)
    assert out["content"].startswith("# 记忆导出") and out["manifest"]["raw_materials"]
    html = export.export(memory, OWNER, fmt="html", record_ids=[detail["record_id"]])
    assert "<script" not in html["content"]
    resumed = workflow.resume(memory, OWNER)
    assert resumed["parts"] and resumed["execution_started"] is False
    materials = workflow.summary_materials(memory, [OWNER])
    assert materials["coverage"][0]["status"] == "covered" and not materials["summary_written"]


def test_semantic_maintenance(tmp_path):
    from burrowl.services.maintenance import MaintenanceService

    storage, memory, review = services(tmp_path)
    owner_id = storage.owners.create("research", "order", "顺序").owner_id
    evidence = register_source(storage, "SRC-1", "research/order/r.txt", "结果\n")
    first = memory.commit(request(owner_id, None, {"op": "put_record", "client_key": "a",
                                                   "draft": narrative_draft(owner_id, evidence)}))
    rid = first["record_results"][0]["record_id"]
    maintenance = MaintenanceService(memory, review)
    plan = maintenance.plan(["SRC-1"], actor=AI, note="来源更新")
    assert [i["target"]["target_id"] for i in plan["items"]] == [rid] and plan["items"][0]["content"]
    with pytest.raises(BurrowlError):
        maintenance.apply(plan["plan_id"], actor=AI, expected_revision=1)  # 还没处置
    revised = narrative_draft(owner_id, evidence, statement="升序误差更小（复核后改写）")
    reviewed = maintenance.review(plan["plan_id"], [{"target_id": rid, "action": "revise", "reason": "措辞更精确",
                                                    "draft": revised}])
    assert reviewed["preview"][0]["ok"]
    applied = maintenance.apply(plan["plan_id"], actor=AI, expected_revision=reviewed["revision"])
    assert applied["status"] == "applied"
    again = maintenance.apply(plan["plan_id"], actor=AI, expected_revision=applied["revision"])
    assert again["status"] == "applied"  # 重试幂等
    assert memory.inspect(owner_id, record_id=rid)["record"]["revision"] == 2


def test_associations_and_consolidation(tmp_path):
    storage, memory, _review = services(tmp_path)
    owner_id = storage.owners.create("research", "order", "顺序").owner_id
    evidence = register_source(storage, "SRC-1", "research/order/r.txt", "结果\n")
    first = memory.commit(request(owner_id, None, {"op": "put_record", "client_key": "a",
                                                   "draft": narrative_draft(owner_id, evidence) | {"keywords": ["求和", "顺序"]}},
                                  {"op": "put_record", "client_key": "b",
                                   "draft": narrative_draft(owner_id, evidence, claim_id="CLM-2") | {"keywords": ["求和", "顺序"]}}))
    ids = [r["record_id"] for r in first["record_results"]]
    proposed = associations.propose(storage, [ids[0]])
    assert proposed["candidates"][0]["to"]["target_id"] == ids[1]
    candidate = proposed["candidates"][0]
    with pytest.raises(BurrowlError):
        associations.decide(memory, owner_id, {"from": candidate["from"], "to": candidate["to"], "relation": "supports",
                                               "explanation": "x", "shared_structure": "x", "transfer_limits": [],
                                               "basis_refs": [], "status": "accepted"}, actor=AI, reason="x")
    payload = {"from": candidate["from"], "to": candidate["to"], "relation": "same_problem", "explanation": "同一问题",
               "shared_structure": "大数吃小数", "transfer_limits": ["只适用整数输入"], "basis_refs": [], "status": "accepted"}
    associations.decide(memory, owner_id, payload, actor=AI, reason="同一问题的两次实验")
    assert associations.list_associations(storage, status="accepted")
    plan = consolidation.prepare(memory, owner_id)
    assert plan["changed"] and plan["basis_hash"]
    decisions = [{"target": plan["changed"][0], "action": "retain", "reason": "仍成立"}]
    saved = consolidation.apply(memory, owner_id, plan, decisions, actor=AI, reason="阶段巩固")
    assert saved["save_status"] == "committed"
    assert consolidation.prepare(memory, owner_id)["changed"] == []  # 巩固之后没有新变化
