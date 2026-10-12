"""R22 删除记录与对象：墓碑提交、引用护栏、恢复、对象回收站、关键操作记录、检索投影。"""
import pytest

from burrowl.core.config import IndexConfig
from burrowl.core.errors import BurrowlError
from burrowl.interfaces.ops.registry import load
from burrowl.services.activity import ActivityLog
from burrowl.services.health import validate
from burrowl.services.indexing import IndexService
from burrowl.services.memory.context import Storage
from burrowl.services.memory.service import MemoryService
from burrowl.services.review import claim_lookup_factory
from tests.service.helpers import AI, HUMAN, goal_draft, head_of, narrative_draft, register_source, request


def build(root, *, concept_owner=None):
    storage = Storage.open(root)
    index = IndexService.open(storage, IndexConfig(), fake_vectors=True)
    memory = MemoryService(storage, index=index, claim_lookup_factory=claim_lookup_factory(storage),
                           concept_owner=(lambda: concept_owner) if concept_owner else None)
    return storage, memory, index


def found(index, query, record_id):
    return any(h.get("record_id") == record_id for h in index.search(query)["items"])


def put(memory, owner_id, *drafts):
    receipt = memory.commit(request(owner_id, head_of(memory, owner_id),
                                    *({"op": "put_record", "client_key": f"k{i}", "draft": d}
                                      for i, d in enumerate(drafts))))
    return [r["record_id"] for r in receipt["record_results"]]


def ref_to(memory, owner_id, record_id):
    record = memory.storage.store.read_record(memory.owners().get(owner_id), record_id)
    return {"target_kind": "record", "target_id": record_id, "revision": record["revision"],
            "sha256": record["record_hash"], "locator": "", "relation": "background"}


def citing(memory, owner_id, evidence, target_owner, target_id, claim_id):
    draft = narrative_draft(owner_id, evidence, claim_id=claim_id, statement="另一条结论")
    draft["title"] = "引用前一条的经过"
    draft["sources"] = [evidence, ref_to(memory, target_owner, target_id)]
    return draft


@pytest.fixture
def setup(tmp_path):
    storage, memory, index = build(tmp_path)
    storage.owners.create("research", "a", "甲")
    evidence = register_source(storage, "SRC-A", "data/a.txt", "1,2,3")
    goal, first = put(memory, "RES-A", goal_draft("RES-A"), narrative_draft("RES-A", evidence))
    (second,) = put(memory, "RES-A", citing(memory, "RES-A", evidence, "RES-A", first, "CLM-ORDER-2"))
    return storage, memory, index, evidence, {"goal": goal, "first": first, "second": second}


def test_discard_record_is_a_tombstone_and_restore_is_identical(setup):
    storage, memory, index, _, ids = setup
    owner = memory.owners().get("RES-A")
    before = storage.store.read_record(owner, ids["second"])
    assert found(index, "另一条结论", ids["second"])
    result = memory.removal.discard_records("RES-A", [ids["second"]], reason="测试数据", actor=AI)
    assert result["record_results"] == [{"record_id": ids["second"], "status": "removed"}]
    assert ids["second"] not in memory.snapshot(owner).records                      # D-02：当前快照里没有
    assert not found(index, "另一条结论", ids["second"])  # 检索也没有
    with pytest.raises(BurrowlError) as caught:
        storage.store.read_record(owner, ids["second"])
    assert caught.value.code == "NOT_FOUND" and caught.value.details["removed"] is True
    assert storage.store.read_record(owner, ids["second"], before["revision"]) == before  # 固定引用照常可读
    listed = memory.removal.discarded_records("RES-A")
    assert [(r["record_id"], r["reason"]) for r in listed] == [(ids["second"], "测试数据")]

    memory.removal.restore_records("RES-A", [ids["second"]], actor=HUMAN)
    assert storage.store.read_record(owner, ids["second"]) == before                  # D-05：逐字节相同
    assert memory.removal.discarded_records("RES-A") == []
    assert found(index, "另一条结论", ids["second"])
    summaries = [e["summary"] for e in ActivityLog(storage).list(owner_id="RES-A", kinds=["remove"])["items"]]
    assert summaries == ["RES-A 恢复 1 条记录", "RES-A 删除 1 条记录"]
    memory.removal.discard_records("RES-A", [ids["second"]], reason="再删一次", actor=AI)
    assert validate(storage, index)["problems"] == []                                 # 带墓碑的提交链通过完整性检查


def test_cited_and_pointer_records_cannot_be_discarded(setup):
    _, memory, _, _, ids = setup
    preview = memory.removal.discard_records("RES-A", [ids["first"]], reason="重复", actor=AI, dry_run=True)
    assert not preview["allowed"]
    cited = {b["target_id"]: [c["record_id"] for c in b.get("cited_by", [])] for b in preview["blockers"]}
    assert cited[ids["first"]] == [ids["second"]]
    with pytest.raises(BurrowlError) as caught:
        memory.removal.discard_records("RES-A", [ids["first"]], reason="重复", actor=AI)
    assert caught.value.code == "INVALID_TRANSITION"
    with pytest.raises(BurrowlError) as caught:                                       # D-04：指针记录
        memory.removal.discard_records("RES-A", [ids["goal"]], reason="重复", actor=AI)
    assert "指针" in caught.value.details["blockers"][0]["reason"]
    # 引用者与被引用者一起删除不算阻碍
    memory.removal.discard_records("RES-A", [ids["first"], ids["second"]], reason="整组测试数据", actor=AI)
    with pytest.raises(BurrowlError) as caught:
        memory.removal.discard_records("RES-A", [ids["first"]], reason="再删一次", actor=AI)
    assert caught.value.details["already_removed"] == [ids["first"]]


def test_restore_requires_cited_targets_and_deleted_ids_cannot_be_rewritten(setup):
    storage, memory, _, evidence, ids = setup
    memory.removal.discard_records("RES-A", [ids["first"], ids["second"]], reason="整组测试数据", actor=AI)
    preview = memory.removal.restore_records("RES-A", [ids["second"]], actor=AI, dry_run=True)
    assert preview["blockers"][0]["missing"] == sorted([ids["first"]])
    draft = narrative_draft("RES-A", evidence)
    with pytest.raises(BurrowlError) as caught:      # 已删除的记录不能被普通提交改写
        memory.commit(request("RES-A", head_of(memory, "RES-A"),
                              {"op": "put_record", "record_id": ids["first"], "expected_revision": 1, "draft": draft}))
    assert caught.value.code == "NOT_FOUND" and caught.value.details["removed"] is True
    memory.removal.restore_records("RES-A", [ids["first"], ids["second"]], actor=AI)  # 一起恢复视为存在
    assert {ids["first"], ids["second"]} <= set(memory.snapshot(memory.owners().get("RES-A")).records)


def test_new_commits_keep_tombstones(setup):
    storage, memory, _, evidence, ids = setup
    memory.removal.discard_records("RES-A", [ids["second"]], reason="测试数据", actor=AI)
    put(memory, "RES-A", narrative_draft("RES-A", evidence, claim_id="CLM-ORDER-3", statement="第三条"))
    assert [r["record_id"] for r in memory.removal.discarded_records("RES-A")] == [ids["second"]]
    with pytest.raises(BurrowlError) as caught:      # 不能新引用已删除的记录
        put(memory, "RES-A", {**narrative_draft("RES-A", evidence, claim_id="CLM-ORDER-4"),
                              "sources": [evidence, {**ref_to(memory, "RES-A", ids["first"]), "target_id": ids["second"]}]})
    assert caught.value.code == "UNRESOLVED_REFERENCE" and caught.value.details["removed"] is True


def test_owner_discard_restore_and_trash(setup):
    storage, memory, index, evidence, _ = setup
    storage.owners.create("research", "b", "乙")
    put(memory, "RES-B", goal_draft("RES-B"))
    folder = storage.ws.root / "research/b"
    preview = memory.removal.discard_owner("RES-B", reason="误建", actor=AI, dry_run=True)
    assert preview["allowed"] and preview["mode"] == "folder" and preview["record_count"] == 1
    with pytest.raises(BurrowlError):
        memory.removal.discard_owner("RES-B", reason="  ", actor=AI)
    with pytest.raises(BurrowlError) as caught:
        memory.removal.discard_owner("RES-B", reason="误建", actor=AI, expected_head="COM-other")
    assert caught.value.code == "VERSION_CONFLICT"
    result = memory.removal.discard_owner("RES-B", reason="误建", actor=AI, expected_head=preview["head"])
    assert result["status"] == "discarded" and result["files_deleted"] == 0 and not folder.exists()
    assert storage.owners.scan().find("RES-B") is None
    assert "RES-B" not in {s["owner_id"] for s in index.db.states()}               # D-08：投影已删除
    assert [t["owner_id"] for t in memory.removal.trash()] == ["RES-B"]
    memory.removal.restore_owner("RES-B", actor=HUMAN)
    assert folder.is_dir() and storage.owners.scan().get("RES-B").title == "乙"
    assert memory.removal.trash() == [] and "RES-B" in {s["owner_id"] for s in index.db.states()}
    memory.removal.discard_owner("RES-B", reason="还是不要", actor=AI)
    assert memory.removal.empty_trash(actor=HUMAN) == {"removed": ["RES-B"]}
    assert not (storage.ws.state("trash/owners") / "res-b").exists()
    summaries = [e["summary"] for e in ActivityLog(storage).list(kinds=["remove"])["items"]]
    assert summaries[:4] == ["永久删除对象 RES-B「乙」", "删除对象（移入回收站） RES-B「乙」：还是不要",
                             "恢复对象 RES-B「乙」", "删除对象（移入回收站） RES-B「乙」：误建"]


def test_owner_discard_guards(setup):
    storage, memory, _, evidence, ids = setup
    storage.owners.create("research", "c", "丙")
    (cited,) = put(memory, "RES-C", narrative_draft("RES-C", evidence, claim_id="CLM-C"))
    put(memory, "RES-A", citing(memory, "RES-A", evidence, "RES-C", cited, "CLM-A-C"))
    preview = memory.removal.discard_owner("RES-C", reason="误建", actor=AI, dry_run=True)
    assert [b["target_id"] for b in preview["blockers"]] == [cited]                  # D-07 ①
    with pytest.raises(BurrowlError) as caught:
        memory.removal.discard_owner("RES-C", reason="误建", actor=AI)
    assert caught.value.code == "INVALID_TRANSITION"
    # 同一对象内部的引用（RES-A 自己的记录互相引用）和它对外的引用都不阻碍删除它
    assert memory.removal.discard_owner("RES-A", reason="误建", actor=AI, dry_run=True)["blockers"] == []
    # ② 嵌套对象：在对象目录里采用一份文档
    nested = storage.ws.root / "research/c/notes.md"
    nested.write_text("笔记", encoding="utf-8")
    from burrowl.core.hashing import sha256_file
    storage.owners.adopt("research/c/notes.md", sha256_file(nested), AI)
    blockers = memory.removal.discard_owner("RES-C", reason="误建", actor=AI, dry_run=True)["blockers"]
    assert any(b.get("nested") for b in blockers)
    # ③ 概念库
    _, guarded, _ = build(storage.ws.root, concept_owner="RES-A")
    blockers = guarded.removal.discard_owner("RES-A", reason="误建", actor=AI, dry_run=True)["blockers"]
    assert any("概念库" in b["reason"] for b in blockers)


def test_adopted_document_keeps_original(tmp_path):
    storage, memory, _ = build(tmp_path)
    doc = tmp_path / "knowledge/x.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("原件", encoding="utf-8")
    from burrowl.core.hashing import sha256_file
    owner = storage.owners.adopt("knowledge/x.md", sha256_file(doc), AI)
    preview = memory.removal.discard_owner(owner.owner_id, reason="采用错了", actor=AI, dry_run=True)
    assert preview["mode"] == "memory_home" and preview["kept_original"] == "knowledge/x.md"
    memory.removal.discard_owner(owner.owner_id, reason="采用错了", actor=AI)
    assert doc.read_text(encoding="utf-8") == "原件" and not (tmp_path / "knowledge/x.md.memory").exists()
    assert storage.owners.scan().find(owner.owner_id) is None
    memory.removal.restore_owner(owner.owner_id, actor=AI)
    assert storage.owners.scan().get(owner.owner_id).native_path == "knowledge/x.md"


def test_effects():
    specs = load()
    assert specs["memory.owner_empty_trash"].effect == "user"
    for name in ("memory.records_discard", "memory.records_restore", "memory.owner_discard", "memory.owner_restore"):
        assert specs[name].effect == "write"
