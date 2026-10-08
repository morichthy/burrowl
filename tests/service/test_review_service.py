"""复核：待复核队列、预检、接受、失效、批量、历史。"""
import pytest

from burrowl.core.errors import BurrowlError
from tests.service.helpers import HUMAN, narrative_draft, register_source, request, services


@pytest.fixture
def claim_setup(tmp_path):
    storage, memory, review = services(tmp_path)
    owner_id = storage.owners.create("research", "order", "顺序").owner_id
    evidence = register_source(storage, "SRC-ORDER-1", "research/order/data/result.txt", "升序误差 0.0\n")
    receipt = memory.commit(request(owner_id, None, {"op": "put_record", "client_key": "n",
                                                     "draft": narrative_draft(owner_id, evidence)}))
    return storage, memory, review, owner_id, receipt["record_results"][0]["record_id"], evidence


def test_queue_accept_and_stale(claim_setup):
    storage, memory, review, owner_id, rid, evidence = claim_setup
    queue = review.queue()
    assert [i["claim_id"] for i in queue["items"]] == ["CLM-ORDER-1"]
    assert queue["items"][0]["status"] == "not-reviewed" and queue["counts"]["not-reviewed"] == 1
    accept = {"target_claim_id": "CLM-ORDER-1", "state": "accepted", "reason": "实验可复算", "actor": HUMAN,
              "scope": "整数输入", "evidence_refs": [evidence]}
    preview = review.submit({**accept, "dry_run": True})
    assert preview["dry_run"] and preview["writes"] == 0
    assert review.queue()["counts"]["not-reviewed"] == 1
    done = review.submit(accept)
    assert done["save_status"] == "committed"
    assert review.queue()["items"] == []
    accepted = review.queue(states=["accepted"])["items"]
    assert accepted[0]["review"]["state"] == "accepted"
    # 修改结论所在记录 → 复核失效，重新出现在队列中。
    record = memory.inspect(owner_id, record_id=rid)["record"]
    draft = narrative_draft(owner_id, evidence, statement="排序后误差显著更小")
    head = memory.inspect(owner_id)["head"]["commit_id"]
    memory.commit(request(owner_id, head, {"op": "put_record", "record_id": rid,
                                           "expected_revision": record["revision"], "draft": draft}))
    stale = review.queue()["items"]
    assert stale[0]["status"] == "stale" and stale[0]["problems"]
    assert review.history("CLM-ORDER-1")["revisions"][0]["state"] == "accepted"


def test_accept_requires_scope_and_support(claim_setup):
    _storage, _memory, review, *_rest, evidence = claim_setup
    with pytest.raises(BurrowlError) as caught:
        review.submit({"target_claim_id": "CLM-ORDER-1", "state": "accepted", "reason": "x", "actor": HUMAN,
                       "scope": None, "evidence_refs": [evidence]})
    assert caught.value.code == "EVIDENCE_INELIGIBLE"
    with pytest.raises(BurrowlError) as caught:
        review.submit({"target_claim_id": "CLM-ORDER-1", "state": "accepted", "reason": "x", "actor": HUMAN,
                       "scope": "其他范围", "evidence_refs": [evidence]})
    assert caught.value.code == "EVIDENCE_INELIGIBLE"


def test_source_change_blocks_accepted_claim(claim_setup):
    storage, _memory, review, *_rest, evidence = claim_setup
    review.submit({"target_claim_id": "CLM-ORDER-1", "state": "accepted", "reason": "ok", "actor": HUMAN,
                   "scope": "整数输入", "evidence_refs": [evidence]})
    (storage.ws.root / "research/order/data/result.txt").write_text("被修改\n", encoding="utf-8")
    item = review.queue()["items"][0]
    assert item["status"] == "upstream-blocked"
    assert any(p["code"] == "STALE_BASIS" for p in item["problems"])


def test_batch_and_dispute(claim_setup):
    _storage, _memory, review, *_ = claim_setup
    out = review.batch([{"target_claim_id": "CLM-ORDER-1", "state": "disputed", "reason": "样本太少"},
                        {"target_claim_id": "CLM-MISSING", "state": "disputed", "reason": "x"}], actor=HUMAN)
    assert out["succeeded"] == 1 and out["failed"] == 1
    assert review.queue()["items"][0]["status"] == "disputed"
    formal = review.graph().formal_projection(["CLM-ORDER-1"], "整数输入")
    assert formal["claims"] == [] and formal["rejected"]


def test_maturity_levels_and_ai_cannot_establish(claim_setup):
    """R12 T-P2.5（D-15、D-16）：成熟度存在复核里；AI 不能登记 established，只能提出升级，由人确认。"""
    from tests.service.helpers import AI  # noqa: PLC0415

    storage, _memory, review, *_rest, evidence = claim_setup
    base = {"target_claim_id": "CLM-ORDER-1", "reason": "一次固定输入实验支持", "evidence_refs": [evidence]}
    with pytest.raises(BurrowlError) as caught:
        review.submit({**base, "state": "accepted", "scope": "整数输入", "actor": AI, "maturity": "established",
                       "evidence_basis": "replication"})
    assert caught.value.code == "PERMISSION_DENIED"
    with pytest.raises(BurrowlError) as caught:   # 只有一个原始来源，不够"重复验证"
        review.submit({**base, "state": "not-reviewed", "actor": AI, "maturity": "replicated"})
    assert caught.value.code == "EVIDENCE_INELIGIBLE"

    review.submit({**base, "state": "not-reviewed", "actor": AI, "maturity": "preliminary",
                   "requested_maturity": "established"})
    item = review.queue()["items"][0]
    assert item["status"] == "maturity-requested" and item["maturity"] == "preliminary"
    assert item["requested_maturity"] == "established" and item["stats"]["supports"] == 1

    second = register_source(storage, "SRC-ORDER-2", "research/order/data/rerun.txt", "复算误差 0.0\n")
    review.submit({**base, "evidence_refs": [evidence, second], "state": "accepted", "scope": "整数输入",
                   "actor": HUMAN, "maturity": "established", "evidence_basis": "replication",
                   "reason": "用户在工作台确认：两次独立复算一致"})
    done = review.queue(states=["accepted"])["items"][0]
    assert done["maturity"] == "established" and done["requested_maturity"] is None
    assert done["stats"]["supports"] == 2
    with pytest.raises(BurrowlError):   # established 必须带证据类型
        review.submit({**base, "state": "accepted", "scope": "整数输入", "actor": HUMAN, "maturity": "established"})
