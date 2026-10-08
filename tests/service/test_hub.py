"""知识中枢同步：发布策略、预览/执行、异步状态、认领、撤回传播、联邦检索、Wiki 双向漂移。"""
import pytest

from burrowl.connectors.fake import FakeHub
from burrowl.connectors.mdvault import MarkdownVault
from burrowl.core.config import BurrowlConfig
from burrowl.core.errors import BurrowlError
from burrowl.services.hub import HubService
from tests.service.helpers import HUMAN, narrative_draft, register_source, request, services

POLICY = {"levels": ["L2", "L3", "L4"], "include_documents": False}


def _hub(storage, connector: dict, hubs: dict) -> HubService:
    cfg = BurrowlConfig.model_validate({"connectors": [connector]})

    def factory(kind, cid, **settings):
        hubs.setdefault(cid, {"fake": FakeHub, "mdvault": MarkdownVault}[kind](cid, **settings))
        return hubs[cid]
    return HubService(storage, lambda: cfg, factory=factory)


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "data").mkdir()
    storage, memory, review = services(tmp_path / "data")
    owner_id = storage.owners.create("research", "order", "顺序").owner_id
    evidence = register_source(storage, "SRC-ORDER-1", "research/order/data/result.txt", "升序误差 0.0\n")
    receipt = memory.commit(request(owner_id, None, {"op": "put_record", "client_key": "n",
                                                     "draft": narrative_draft(owner_id, evidence)}))
    return storage, memory, review, owner_id, receipt["record_results"][0], evidence


def _accept(review, evidence):
    review.submit({"target_claim_id": "CLM-ORDER-1", "state": "accepted", "reason": "可复算", "actor": HUMAN,
                   "scope": "整数输入", "evidence_refs": [evidence]})


def test_reviewed_only_publish_cycle(setup):
    storage, _memory, review, owner_id, result, evidence = setup
    hubs: dict = {}
    hub = _hub(storage, {"id": "hub", "type": "fake", "publish": POLICY, "options": {"async_ingest": True}}, hubs)
    assert hub.plan("hub")["counts"]["create"] == 0          # 未复核：不发布
    _accept(review, evidence)
    plan = hub.plan("hub")
    assert [a["op"] for a in plan["actions"]] == ["create"]
    with pytest.raises(BurrowlError) as caught:
        hub.apply("hub", "0" * 64)
    assert caught.value.code == "CONFLICT"
    out = hub.apply("hub", plan["plan_hash"])
    assert out["summary"] == {"created": 1}
    assert hub.status("hub", refresh=False)["counts"] == {"processing": 1}
    hubs["hub"].finish_processing()
    assert hub.status("hub")["counts"] == {"completed": 1}
    again = hub.plan("hub")                                   # 重跑无变化
    assert again["actions"] == [] and again["counts"]["unchanged"] == 1
    # 联邦检索命中能回到本地固定引用，并按本地当前状态给出复核类别。
    found = hub.search("顺序实验经过", review_filter="reviewed_only")
    assert found["hits"][0]["fixed_ref"]["target_id"] == result["record_id"]
    assert found["hits"][0]["review"] == "accepted"
    # 撤回 → 下次同步删除外部条目。
    review.submit({"target_claim_id": "CLM-ORDER-1", "state": "retracted", "reason": "数据有误", "actor": HUMAN})
    assert hub.search("顺序实验经过", review_filter="reviewed_only")["hits"] == []
    plan = hub.plan("hub")
    assert [a["op"] for a in plan["actions"]] == ["delete"]
    assert hub.apply("hub", plan["plan_hash"])["summary"] == {"deleted": 1}
    assert hubs["hub"].state["docs"] == {}


def test_adopt_existing_and_failures(setup):
    storage, _memory, review, *_rest, evidence = setup
    _accept(review, evidence)
    hubs: dict = {}
    hub = _hub(storage, {"id": "hub", "type": "fake", "publish": POLICY}, hubs)
    hub.apply("hub", hub.plan("hub")["plan_hash"])
    # 映射表丢失（派生状态）：重新发布时按 local_key 认领外部已有条目，不重复创建。
    for key in list(hub.state.mappings("hub")):
        hub.state.remove("hub", key)
    out = hub.apply("hub", hub.plan("hub")["plan_hash"])
    assert out["summary"] == {"created": 1} and len(hubs["hub"].state["docs"]) == 1
    # 外部故障逐条记录，不抛出。
    hubs["hub"].fail_on = {"delete"}
    review.submit({"target_claim_id": "CLM-ORDER-1", "state": "disputed", "reason": "样本少", "actor": HUMAN})
    failed = hub.apply("hub", hub.plan("hub")["plan_hash"])
    assert failed["summary"] == {"failed": 1} and failed["results"][0]["retryable"]


def test_mark_instead_of_delete(setup):
    storage, _memory, review, *_rest, evidence = setup
    _accept(review, evidence)
    hubs: dict = {}
    hub = _hub(storage, {"id": "hub", "type": "fake", "publish": {**POLICY, "on_retract": "mark"}}, hubs)
    hub.apply("hub", hub.plan("hub")["plan_hash"])
    review.submit({"target_claim_id": "CLM-ORDER-1", "state": "retracted", "reason": "错", "actor": HUMAN})
    hub.apply("hub", hub.plan("hub")["plan_hash"])
    doc = next(iter(hubs["hub"].state["docs"].values()))
    assert doc["title"].startswith("[已撤回]") and doc["meta"]["burrowl_status"] == "withdrawn"
    assert hub.plan("hub")["actions"] == []


def _wiki_draft(owner_id: str, record: dict) -> dict:
    ref = {"target_kind": "record", "target_id": record["record_id"], "revision": record["revision"],
           "sha256": None, "locator": "record", "relation": "references"}
    return {"owner_id": owner_id, "kind": "wiki_page", "title": "求和顺序", "body_markdown": "升序求和误差更小。",
            "record_reason": "整理 Wiki", "sensitivity": "internal", "sources": [ref],
            "payload": {"slug": "concept/summation-order", "page_type": "concept", "aliases": ["求和次序"],
                        "source_refs": [ref], "missing_refs": []}}


@pytest.mark.parametrize("kind", ["fake", "mdvault"])
def test_authored_wiki_and_drift(setup, tmp_path, kind):
    storage, memory, _review, owner_id, result, _evidence = setup
    head = memory.inspect(owner_id)["head"]["commit_id"]
    wiki = memory.commit(request(owner_id, head, {"op": "put_record", "client_key": "w",
                                                  "draft": _wiki_draft(owner_id, result)}))["record_results"][0]
    hubs: dict = {}
    options = {"path": str(tmp_path / "vault")} if kind == "mdvault" else {}
    hub = _hub(storage, {"id": "hub", "type": kind, "publish": POLICY, "options": options}, hubs)
    plan = hub.plan("hub")
    assert [(a["op"], a["channel"]) for a in plan["actions"]] == [("create", "wiki")]
    hub.apply("hub", plan["plan_hash"])
    page = hub.wiki_read("hub", "concept/burrowl-summation-order")
    assert "升序求和误差更小" in page["markdown"] and page["edited_by"] == "agent"
    assert hub.wiki_list("hub")["pages"][0]["authored_here"]
    # 外部有人直接改了页面 → 本地更新后发布时检测到漂移，不覆盖。
    conn = hubs["hub"]
    collection = conn.ensure_collection("burrowl")
    if kind == "fake":
        conn.user_edit(collection, "concept/burrowl-summation-order", "人工修改")
    else:
        path = conn._page_path(collection, "concept/burrowl-summation-order")
        path.write_text(path.read_text(encoding="utf-8") + "\n人工补充", encoding="utf-8")
    record = memory.inspect(owner_id, record_id=wiki["record_id"])["record"]
    draft = _wiki_draft(owner_id, result) | {"body_markdown": "升序求和误差更小，且与 Kahan 补偿可叠加。"}
    head = memory.inspect(owner_id)["head"]["commit_id"]
    memory.commit(request(owner_id, head, {"op": "put_record", "record_id": record["record_id"],
                                           "expected_revision": record["revision"], "draft": draft}))
    plan = hub.plan("hub")
    out = hub.apply("hub", plan["plan_hash"])
    assert out["results"][0]["result"] == "drift"
    assert hub.status("hub", refresh=False)["problems"][0]["state"] == "drift"
    forced = hub.apply("hub", hub.plan("hub")["plan_hash"], force_wiki=True)
    assert forced["results"][0]["result"] == "updated"
    assert "Kahan" in hub.wiki_read("hub", "concept/burrowl-summation-order")["markdown"]


def test_unknown_or_disabled_connector(setup):
    storage, *_ = setup
    hub = _hub(storage, {"id": "hub", "type": "fake", "enabled": False}, {})
    with pytest.raises(BurrowlError) as caught:
        hub.plan("hub")
    assert caught.value.code == "CAPABILITY_UNAVAILABLE"
    with pytest.raises(BurrowlError):
        hub.plan("other")
