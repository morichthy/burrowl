"""R15 工作台笔记：状态转换、AI 方案的预演与预览、用户执行时的依据核对与部分失败、编号不复用。"""
import json

import pytest

from burrowl.core.config import IndexConfig
from burrowl.core.errors import BurrowlError
from burrowl.services.indexing import IndexService
from burrowl.services.memory.context import Storage
from burrowl.services.memory.service import MemoryService
from burrowl.services.notes import NOTES_FILE, WorkbenchNotes
from burrowl.services.review import claim_lookup_factory
from tests.service.helpers import AI, goal_draft, head_of, narrative_draft, register_source, request


@pytest.fixture
def lab(tmp_path):
    storage = Storage.open(tmp_path)
    owner = storage.owners.create("research", "lab", "补偿求和研究").owner_id
    other = storage.owners.create("research", "other", "排序研究").owner_id
    index = IndexService.open(storage, IndexConfig(), fake_vectors=True)
    memory = MemoryService(storage, index=index, claim_lookup_factory=claim_lookup_factory(storage))
    evidence = register_source(storage, "SRC-LAB-1", "data/lab.txt", "1e16 + 1 = 1e16\n")
    made = memory.commit(request(owner, None, {"op": "put_record", "client_key": "n", "draft": narrative_draft(owner, evidence)}))
    far = memory.commit(request(other, None, {"op": "put_record", "client_key": "g", "draft": goal_draft(other)}))
    refs = {"narrative": made["record_results"][0]["fixed_ref"], "goal": far["record_results"][0]["fixed_ref"]}
    return storage, memory, WorkbenchNotes(storage, memory, index), owner, other, refs


def _queued(notes, owner, title="顺序与补偿能否统一"):
    note = notes.add("bridge-question", title, "", [owner])
    return notes.resolve(note["id"], "queued")


def _association_step(owner, refs):
    return {"op": "relations.decide", "owner_id": owner,
            "payload": {"from": refs["narrative"], "to": refs["goal"], "explanation": "都在讨论误差来源"}}


def _goal_step(memory, owner, objective="比较补偿与排序"):
    return {"op": "memory.commit", "owner_id": owner, "expected_head": head_of(memory, owner),
            "operations": [{"op": "put_record", "client_key": "x", "draft": goal_draft(owner, objective)}]}


def test_user_transitions_and_ai_only_on_queued(lab):
    """R15 §3：用户转换表；AI 只能回写 queued；问答往返；完成后可重新打开。"""
    _, _, notes, owner, _, _ = lab
    note = notes.add("bridge-question", "想法", "", [owner])
    assert note["status"] == "open"
    with pytest.raises(BurrowlError) as exc:
        notes.reply(note["id"], "question", "要看哪个对象？", None, actor=AI)
    assert exc.value.code == "INVALID_STATE"                     # 草稿还没交给 AI
    with pytest.raises(BurrowlError):
        notes.resolve(note["id"], "handled")                      # handled 只能由 AI 回写或执行产生
    notes.resolve(note["id"], "queued")
    asked = notes.reply(note["id"], "question", "要看哪个对象？", None, actor=AI)
    assert asked["status"] == "needs_user"
    answered = notes.answer(note["id"], "看求和研究")
    assert answered["status"] == "queued" and [e["kind"] for e in answered["thread"]] == ["queued", "question", "answer"]
    declined = notes.reply(note["id"], "declined", "已有记录覆盖这一点", None, actor=AI)
    assert declined["status"] == "handled" and declined["result"]["declined"]
    assert notes.resolve(note["id"], "open")["status"] == "open"
    with pytest.raises(BurrowlError):
        notes.reply(note["id"], "question", "附带步骤", [{"op": "memory.commit"}], actor=AI)


def test_proposal_preview_and_apply_writes_exactly_once(lab):
    """R15 D-05、D-06、D-08：预演不写入；预览来自操作；执行写入并带笔记编号；提交排在关联前。"""
    _, memory, notes, owner, other, refs = lab
    note = _queued(notes, owner)
    before = head_of(memory, owner)
    proposed = notes.reply(note["id"], "proposal", "建一条关联并补一个目标",
                           [_association_step(owner, refs), _goal_step(memory, owner)], actor=AI)
    assert head_of(memory, owner) == before                     # 预演没有写入
    proposal = proposed["proposal"]
    assert [s["op"] for s in proposal["steps"]] == ["memory.commit", "relations.decide"]
    assert proposal["preview"][0]["records"][0]["title"] == "研究目标"
    assert proposal["preview"][1]["action"] == "association" and proposal["preview"][1]["to"]["id"] == refs["goal"]["target_id"]

    done = notes.apply(note["id"], proposal["id"])
    assert done["status"] == "handled" and len(done["result"]["writes"]) == 2
    goal_id = done["result"]["writes"][0]["record_ids"][0]
    written = memory.inspect(owner, record_id=goal_id)["record"]
    assert f"工作台笔记 {note['id']}" in written["record_reason"]
    assert written["created_by"] == AI                          # 写入者是起草方案的 AI（D-06）
    with pytest.raises(BurrowlError):
        notes.apply(note["id"], proposal["id"])                  # 不会重复执行


def test_proposal_validation_rejects_bad_steps(lab):
    _, memory, notes, owner, other, refs = lab
    note = _queued(notes, owner)
    cases = [
        [],                                                                    # 没有步骤
        [{"op": "memory.delete", "owner_id": owner}],                          # 不允许的操作
        [_goal_step(memory, owner), _goal_step(memory, owner, "另一个目标")],   # 同一对象提交两次
        [{**_goal_step(memory, owner), "expected_head": "C-not-current"}],     # HEAD 不是当前值
        [_association_step(owner, refs)] * 11,                                 # 超过 10 步
    ]
    for steps in cases:
        with pytest.raises(BurrowlError):
            notes.reply(note["id"], "proposal", "方案", steps, actor=AI)
    assert notes.items(status=["queued"])[0]["id"] == note["id"]   # 校验失败不改变状态


def test_apply_refuses_when_basis_changed(lab):
    """R15 D-06：方案生成后对象有新提交，不写入，退回 AI。"""
    _, memory, notes, owner, other, refs = lab
    note = _queued(notes, owner)
    proposal = notes.reply(note["id"], "proposal", "补目标", [_goal_step(memory, owner)], actor=AI)["proposal"]
    memory.commit(request(owner, head_of(memory, owner), {"op": "put_record", "client_key": "y", "draft": goal_draft(owner, "插队")}))
    moved = head_of(memory, owner)
    back = notes.apply(note["id"], proposal["id"])
    assert back["status"] == "queued" and "proposal" not in back
    assert back["thread"][-1]["kind"] == "stale" and back["thread"][-1]["changed_owners"] == [owner]
    assert head_of(memory, owner) == moved


def test_apply_reports_partial_failure(lab, monkeypatch):
    """R15 D-07：中途失败时停止，报告已写入的步骤，笔记退回 AI。"""
    _, memory, notes, owner, other, refs = lab
    note = _queued(notes, owner)
    steps = [_goal_step(memory, owner), _goal_step(memory, other, "另一边的目标")]
    proposal = notes.reply(note["id"], "proposal", "两个对象各补目标", steps, actor=AI)["proposal"]
    real = notes._run

    def fail_second(step, actor, *, dry_run):
        if not dry_run and step["owner_id"] == other:
            raise BurrowlError("STORE_FAILED", "模拟写入失败")
        return real(step, actor, dry_run=dry_run)
    monkeypatch.setattr(notes, "_run", fail_second)
    back = notes.apply(note["id"], proposal["id"])
    event = back["thread"][-1]
    assert back["status"] == "queued" and event["kind"] == "failed"
    assert [w["owner_id"] for w in event["writes"]] == [owner] and event["error"] == "STORE_FAILED"


def test_reject_and_clear_keep_ids_unique(lab):
    """R15 D-10、D-11：退回方案带意见；清除已处理后编号不复用；旧列表格式可读。"""
    storage, memory, notes, owner, _, _ = lab
    note = _queued(notes, owner)
    proposal = notes.reply(note["id"], "proposal", "补目标", [_goal_step(memory, owner)], actor=AI)["proposal"]
    back = notes.reject(note["id"], proposal["id"], "目标写得太宽")
    assert back["status"] == "queued" and back["thread"][-1]["text"] == "目标写得太宽"
    notes.resolve(note["id"], "done")
    assert notes.clear()["removed"] == [note["id"]]
    assert notes.add("cluster-name", "新的", "", [])["id"] == "WN-2"

    path = storage.ws.state(NOTES_FILE)
    path.write_text(json.dumps([{"id": "WN-7", "kind": "cluster-name", "title": "旧", "text": "", "refs": [],
                                 "status": "done", "created_at": "2026-10-01T00:00:00Z"}]), encoding="utf-8")
    assert notes.items()[0]["thread"] == []
    assert notes.add("cluster-name", "续号", "", [])["id"] == "WN-8"


def test_list_by_owner_matches_owner_or_its_records(lab):
    _, _, notes, owner, other, refs = lab
    by_record = notes.add("relation-idea", "记录上的想法", "", [refs["narrative"]["target_id"]])
    notes.add("relation-idea", "别的对象", "", [other])
    assert [n["id"] for n in notes.items(owner_id=owner)] == [by_record["id"]]


def test_edit_rules(lab):
    """编辑：草稿和进行中的笔记可改；待你确认时原方案作废并退回 AI；已处理的先重新打开。"""
    _, memory, notes, owner, _, _ = lab
    note = notes.add("bridge-question", "初稿", "", [owner])
    assert notes.edit(note["id"], title="改过的标题", text="补充说明")["title"] == "改过的标题"
    notes.resolve(note["id"], "queued")
    notes.reply(note["id"], "proposal", "补目标", [_goal_step(memory, owner)], actor=AI)
    edited = notes.edit(note["id"], text="换个方向")
    assert edited["status"] == "queued" and "proposal" not in edited
    assert "原方案作废" in edited["thread"][-1]["text"]
    notes.resolve(note["id"], "done")
    with pytest.raises(BurrowlError):
        notes.edit(note["id"], title="已完成后再改")
    with pytest.raises(BurrowlError):
        notes.edit(notes.resolve(note["id"], "open")["id"], title="  ")
