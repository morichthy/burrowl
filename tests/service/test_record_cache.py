"""已校验记录缓存（MemoryStore.verified）：命中与重读等价、跨进程新提交可见、完整性检查绕过缓存、
容量有界；提交与查看不再为未变化的对象重读记录正文。"""
import pytest

from burrowl.app import App
from burrowl.core.errors import BurrowlError
from burrowl.interfaces.ops.registry import Ctx, call
from burrowl.services.memory import claims
from burrowl.storage import memory_store, recovery
from burrowl.storage.memory_store import MemoryStore
from tests.service.helpers import AI, goal_draft, narrative_draft, register_source, request, services

SAMPLE = "RES-FLOATING-POINT-SUMMATION"


@pytest.fixture
def record_reads(monkeypatch):
    """统计读盘的记录正文文件（commits/*/records/*.json），按记忆目录归类。"""
    seen: list[str] = []
    original = memory_store.read_json

    def counting(path):
        if path.parent.name == "records":
            seen.append(path.as_posix())
        return original(path)
    monkeypatch.setattr(memory_store, "read_json", counting)
    return seen


def test_hit_equals_disk_read_and_returns_independent_copies(sample_workspace, record_reads):
    storage, _, _ = services(sample_workspace)
    owner = storage.owners.scan().get(SAMPLE)
    first = storage.store.read_snapshot(owner)
    assert record_reads and len(record_reads) == len(first.records)
    record_reads.clear()
    second = storage.store.read_snapshot(owner)
    assert record_reads == [] and second.records == first.records and second.head == first.head
    # 调用方修改返回值不能污染缓存
    rid = next(iter(second.records))
    second.records[rid]["title"] = "被调用方改过"
    assert storage.store.read_snapshot(owner).records[rid]["title"] == first.records[rid]["title"]


def test_commit_from_another_process_is_visible(sample_workspace):
    storage_a, memory_a, _ = services(sample_workspace)
    storage_b, memory_b, _ = services(sample_workspace)   # 第二个进程：独立的缓存
    owner = storage_a.owners.scan().get(SAMPLE)
    before = storage_a.store.read_snapshot(owner)
    receipt = memory_b.commit(request(SAMPLE, before.commit_id,
                                      {"op": "put_record", "client_key": "g", "draft": goal_draft(SAMPLE, "新目标")}))
    after = storage_a.store.read_snapshot(owner)
    assert after.commit_id == receipt["commit_id"] != before.commit_id
    assert after.pointer("goal")["payload"]["objective"] == "新目标"


def test_integrity_checks_bypass_cache(sample_workspace):
    storage, _, _ = services(sample_workspace)
    owner = storage.owners.scan().get(SAMPLE)
    snapshot = storage.store.read_snapshot(owner)
    rid, entry = next(iter(snapshot.manifest["record_heads"].items()))
    path = storage.store.path(owner, entry["path"])
    path.write_text(path.read_text(encoding="utf-8").replace('"title":', '"title_":', 1), encoding="utf-8")
    # 已知取舍：首次读取后的篡改，普通读取在进程重启前不会发现（见 VerifiedRecords 说明）
    assert storage.store.read_snapshot(owner).records[rid] == snapshot.records[rid]
    with pytest.raises(BurrowlError) as caught:
        storage.store.read_snapshot(owner, fresh=True)
    assert caught.value.code == "INTEGRITY_ERROR"
    with pytest.raises(BurrowlError):
        recovery.inspect(storage.store, owner)   # system.validate 经由这里逐条校验
    restarted, _, _ = services(sample_workspace)
    with pytest.raises(BurrowlError):
        restarted.store.read_snapshot(restarted.owners.scan().get(SAMPLE))


def test_cache_is_bounded_and_can_be_disabled(sample_workspace, record_reads):
    storage, _, _ = services(sample_workspace, record_cache_size=3)
    owner = storage.owners.scan().get(SAMPLE)
    total = len(storage.store.read_snapshot(owner).records)
    assert total > 3 and len(storage.store.verified) == 3
    disabled, _, _ = services(sample_workspace, record_cache_size=0)
    owner = disabled.owners.scan().get(SAMPLE)
    disabled.store.read_snapshot(owner)
    record_reads.clear()
    disabled.store.read_snapshot(owner)
    assert len(record_reads) == total and len(disabled.store.verified) == 0


def _op(app: App, name: str, **arguments):
    return call(Ctx(app=app, actor=AI, channel="mcp"), name, arguments)


def test_commit_and_inspect_do_not_reread_unchanged_owners(tmp_path, record_reads, monkeypatch):
    app = App.open(tmp_path, create=True, fake_vectors=True)
    heads = {}
    for i in range(3):
        oid = f"RES-O{i}"
        _op(app, "memory.owner_create", owner_type="research", slug=f"o{i}", title=f"对象{i}")
        evidence = register_source(app.storage, f"SRC-O{i}", f"research/o{i}/e.txt", "x\n")
        head = _op(app, "memory.commit", owner_id=oid, expected_head=None,
                   operations=[{"op": "put_record", "client_key": "g", "draft": goal_draft(oid)}])["commit_id"]
        heads[oid] = (head, evidence)
    head, evidence = heads["RES-O0"]
    record_reads.clear()
    receipt = _op(app, "memory.commit", owner_id="RES-O0", expected_head=head,
                  operations=[{"op": "put_record", "client_key": "n",
                               "draft": narrative_draft("RES-O0", evidence, claim_id="CLM-O0")}])
    # 发布前的暂存回读照常读盘；之后只读本次新写入的那条记录，其他对象一条都不重读
    assert receipt["save_status"] == "committed"
    staged = [p for p in record_reads if "/memory/staging/" in p]
    published = [p for p in record_reads if "/memory/staging/" not in p]
    assert len(staged) == 1
    assert len(published) == 1 and "/research/o0/memory/commits/" in published[0], record_reads

    built = []
    original = claims.ClaimGraph.__init__

    def counting(self, *args, **kwargs):
        built.append(1)
        original(self, *args, **kwargs)
    monkeypatch.setattr(claims.ClaimGraph, "__init__", counting)
    record_reads.clear()
    view = _op(app, "memory.inspect", owner_id="RES-O0")
    assert "CLM-O0" in view["claims"]
    assert len(built) == 1          # 依据链过滤与结论徽标共用一张复核图
    assert record_reads == []       # 全部命中已校验缓存


def test_store_default_cache_size():
    assert MemoryStore.__init__.__kwdefaults__["record_cache_size"] == memory_store.RECORD_CACHE_SIZE > 0
