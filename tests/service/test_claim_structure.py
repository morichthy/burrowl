"""R23：只改结论结构不让复核失效（AC-01）；先写概念链接、后建概念页，图自动接上（AC-03）；矛盾不是谓词（AC-04）。"""
import pytest

from burrowl.core.config import IndexConfig
from burrowl.core.errors import BurrowlError
from burrowl.services.indexing import IndexService
from burrowl.services.memory.context import Storage
from burrowl.services.memory.service import MemoryService
from burrowl.services.review import ReviewService, claim_lookup_factory
from tests.service.helpers import HUMAN, head_of, narrative_draft, register_source, request


def _relation(cause: str) -> dict:
    return {"predicate": "causes", "participants": [{"role": "cause", "value": cause},
                                                   {"role": "effect", "value": "[[求和误差]]"}]}


@pytest.fixture
def lab(tmp_path):
    storage = Storage.open(tmp_path)
    library = storage.owners.create("knowledge", "concepts", "概念库").owner_id
    owner = storage.owners.create("research", "order", "顺序").owner_id
    index = IndexService.open(storage, IndexConfig(), fake_vectors=True, concept_owner=lambda: library)
    memory = MemoryService(storage, index=index, claim_lookup_factory=claim_lookup_factory(storage),
                           concept_owner=lambda: library)
    evidence = register_source(storage, "SRC-ORDER-1", "research/order/data/result.txt", "升序误差 0.0\n")
    draft = narrative_draft(owner, evidence)
    draft["payload"]["claims"][0]["relation"] = _relation("[[求和顺序]]")
    receipt = memory.commit(request(owner, None, {"op": "put_record", "client_key": "n", "draft": draft}))
    return storage, memory, index, library, owner, receipt["record_results"][0]["record_id"], evidence


def _revise(memory, owner, rid, draft):
    record = memory.inspect(owner, record_id=rid)["record"]
    return memory.commit(request(owner, head_of(memory, owner), {"op": "put_record", "record_id": rid,
                                                                  "expected_revision": record["revision"],
                                                                  "draft": draft}))


def test_relation_only_revision_keeps_review(lab):
    storage, memory, _index, _library, owner, rid, evidence = lab
    review = ReviewService(storage, memory)
    review.submit({"target_claim_id": "CLM-ORDER-1", "state": "accepted", "reason": "实验可复算", "actor": HUMAN,
                   "scope": "整数输入", "evidence_refs": [evidence]})
    before = memory.inspect(owner, record_id=rid)["record"]

    draft = narrative_draft(owner, evidence)
    draft["payload"]["claims"][0]["relation"] = _relation("[[加法顺序|求和顺序]]")  # 只改结构
    _revise(memory, owner, rid, draft)
    after = memory.inspect(owner, record_id=rid)["record"]
    assert after["revision"] == before["revision"] + 1 and after["content_hash"] == before["content_hash"]
    assert review.queue(states=["accepted"])["items"][0]["claim_id"] == "CLM-ORDER-1"

    draft["payload"]["claims"][0]["scope"] = "整数与小数输入"  # 改权威内容：复核按原规则失效
    _revise(memory, owner, rid, draft)
    assert review.queue()["items"][0]["status"] == "stale"


def test_concept_page_created_later_connects(lab):
    storage, memory, index, library, owner, rid, evidence = lab
    index.ensure_graph()
    parts = lambda: {(e["type"], e["dst"]) for e in index.db.graph_all_edges() if e["category"] == "claim_part"}  # noqa: E731
    assert parts() == set()
    assert any(g["gap"] == "missing_concept" and "求和顺序" in g["names"] for g in index.gaps())

    page = {"owner_id": library, "kind": "wiki_page", "title": "求和顺序", "body_markdown": "加数的先后次序。",
            "record_reason": "建立概念", "sensitivity": "internal", "sources": [evidence],
            "payload": {"page_type": "concept", "aliases": [], "source_refs": [evidence], "domains": []}}
    made = memory.commit(request(library, None, {"op": "put_record", "client_key": "c", "draft": page}))
    concept = made["record_results"][0]["record_id"]
    index.ensure_graph()
    assert parts() == {("cause", concept)}
    assert memory.inspect(owner, record_id=rid)["record"]["revision"] == 1  # 记录本身没有被改写
    assert not any(g["gap"] == "missing_concept" and "求和顺序" in g["names"] for g in index.gaps())
    assert index.status(owner)["details"]["graph.edges"] == "indexed"


def test_contradicts_is_not_a_predicate(lab):
    _storage, memory, _index, _library, owner, _rid, evidence = lab
    draft = narrative_draft(owner, evidence, claim_id="CLM-ORDER-2")
    draft["payload"]["claims"][0]["relation"] = {"predicate": "contradicts", "participants": [
        {"role": "x-a", "value": "[[!ORDER-1]]"}, {"role": "x-b", "value": "升序更好"}]}
    with pytest.raises(BurrowlError) as caught:
        memory.commit(request(owner, head_of(memory, owner), {"op": "put_record", "client_key": "m", "draft": draft}))
    assert any(e["path"].endswith("/relation/predicate") for e in caught.value.errors)
