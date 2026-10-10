"""记忆服务：对象发现、提交、幂等、冲突、故障恢复，以及在样例数据上的读取。"""
import pytest

from burrowl.core.errors import BurrowlError
from burrowl.storage import recovery
from tests.conftest import load
from tests.service.helpers import AI, goal_draft, head_of, request, services


@pytest.fixture
def research(tmp_path):
    storage, memory, review = services(tmp_path)
    owner = storage.owners.create("research", "order-error", "求和顺序误差")
    return storage, memory, review, owner.owner_id


def test_sample_owners_and_fingerprints(sample_workspace):
    storage, memory, _ = services(sample_workspace)
    index = memory.owners()
    research = index.get("RES-FLOATING-POINT-SUMMATION")
    assert research.persisted and research.memory_home == "research/floating-point-summation/memory"
    # R14 D-02 / D-04：Run 不在对象列表中，按位置找到；样例中 3 个 Run 的业务卡 owner_id 为空，仍归研究对象
    assert index.readable("run") == []
    runs = index.all_runs()
    assert len(runs) == 5 and all(index.run_parent(r).owner_id == research.owner_id for r in runs)
    # 样例记录中 owner 引用的指纹必须等于新实现算出的业务卡指纹。
    records = memory.snapshot(research).records.values()
    owner_refs = [ref for r in records for ref in r["sources"] if ref["target_kind"] == "owner"]
    by_id = {o.owner_id: o.fingerprint for o in runs}
    assert owner_refs and all(by_id[ref["target_id"]] == ref["sha256"] for ref in owner_refs
                              if ref["target_id"] in by_id)
    view = memory.inspect("RES-FLOATING-POINT-SUMMATION")
    assert view["head"]["generation"] == 19 and len(view["records"]) > 10
    assert view["policy"]["values"]["mode"] in {"explore", "basic"}


def test_commit_revise_idempotent_and_conflict(research):
    storage, memory, _, owner_id = research
    req = request(owner_id, None, {"op": "put_record", "client_key": "goal", "draft": goal_draft(owner_id)})
    preview = memory.commit({**req, "dry_run": True})
    assert preview["dry_run"] and preview["record_results"][0]["status"] == "would_create"
    assert not (storage.ws.root / "research/order-error/memory").exists()  # 预检不创建目录
    receipt = memory.commit(req)
    assert receipt["save_status"] == "committed" and receipt["generation"] == 1
    assert receipt["index"]["index_status"] == "not_configured"
    assert memory.commit(req)["commit_id"] == receipt["commit_id"]  # 重试幂等
    with pytest.raises(BurrowlError) as caught:
        memory.commit({**req, "operations": [{"op": "put_record", "client_key": "goal",
                                              "draft": goal_draft(owner_id, "别的目标")}]})
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    rid = receipt["record_results"][0]["record_id"]
    record = memory.inspect(owner_id, record_id=rid)["record"]
    # 更改目标必须固定关联上一修订并说明影响（领域规则）。
    draft = goal_draft(owner_id, "新的目标")
    with pytest.raises(BurrowlError) as caught:
        memory.commit(request(owner_id, receipt["commit_id"],
                              {"op": "put_record", "record_id": rid, "expected_revision": 1, "draft": draft}))
    assert caught.value.code == "INVALID_TRANSITION"
    draft["payload"].update(change_impact="旧尝试仍有效", previous_goal_ref={
        "target_kind": "record", "target_id": rid, "revision": 1, "sha256": record["record_hash"],
        "locator": "", "relation": "derived_from"})
    second = memory.commit(request(owner_id, receipt["commit_id"],
                                   {"op": "put_record", "record_id": rid, "expected_revision": 1, "draft": draft}))
    assert second["generation"] == 2
    with pytest.raises(BurrowlError) as caught:  # 过期的 expected_head
        memory.commit(request(owner_id, receipt["commit_id"], {"op": "put_record", "client_key": "g2",
                                                               "draft": goal_draft(owner_id)}))
    assert caught.value.code == "VERSION_CONFLICT"
    history = memory.storage.store.read_record(memory.owners().get(owner_id), rid, 1)
    assert history["payload"]["objective"] == "比较求和顺序误差"  # 旧修订不可变


def test_no_change_commit_writes_receipt_only(research):
    _storage, memory, _, owner_id = research
    first = memory.commit(request(owner_id, None, {"op": "put_record", "client_key": "goal",
                                                   "draft": goal_draft(owner_id)}))
    rid = first["record_results"][0]["record_id"]
    again = memory.commit(request(owner_id, first["commit_id"], {"op": "put_record", "record_id": rid,
                                                                 "expected_revision": 1,
                                                                 "draft": goal_draft(owner_id)}))
    assert again["save_status"] == "no_change" and again["generation"] == 1


@pytest.mark.parametrize("point", ["write", "after_rename", "before_head"])
def test_fault_injection_never_publishes_partial_commit(tmp_path, point):
    storage, memory, _ = services(tmp_path)
    owner_id = storage.owners.create("research", "faulty", "故障注入").owner_id

    def fault(name):
        if name == point:
            raise OSError("injected")

    storage.store.fault = fault
    req = request(owner_id, None, {"op": "put_record", "client_key": "goal", "draft": goal_draft(owner_id)})
    with pytest.raises(OSError):
        memory.commit(req)
    assert head_of(memory, owner_id) is None
    report = recovery.inspect(storage.store, memory.owners().get(owner_id))
    assert report["lock_state"] == "absent"
    storage.store.fault = lambda _name: None
    assert memory.commit(req)["save_status"] == "committed"  # 同一请求可重试


def test_restricted_owner_is_unreadable(research):
    storage, memory, _, owner_id = research
    card = storage.ws.root / "research/order-error/research.json"
    data = load(card)
    card.write_text(__import__("json").dumps({**data, "sensitivity": "restricted"}), encoding="utf-8")
    with pytest.raises(BurrowlError) as caught:
        memory.inspect(owner_id)
    assert caught.value.code == "ACCESS_DENIED"


def test_unknown_actor_kind_rejected(research):
    _storage, memory, _, owner_id = research
    req = request(owner_id, None, {"op": "put_record", "client_key": "g", "draft": goal_draft(owner_id)})
    req["actor"] = {"kind": "robot", "id": "x"}
    with pytest.raises(BurrowlError):
        memory.commit(req)
    assert AI["kind"] == "ai"
