"""一次操作内复用对象扫描（OwnerRegistry.request_scope）：保存中的普通读取只扫描一次；写业务卡后复用结果作废；
发布前的依据复核仍重新扫描，请求开头之后业务卡被改照样拦下。"""
import json

import pytest

from burrowl.app import App
from burrowl.core.errors import BurrowlError
from burrowl.interfaces.ops.registry import Ctx, call
from burrowl.services.memory.reader import Reader
from burrowl.storage.owners import OwnerRegistry
from tests.service.helpers import AI, goal_draft, narrative_draft, register_source, request, services


@pytest.fixture
def scans(monkeypatch):
    """统计真实扫描（_scan）的次数，复用结果不计。"""
    count = {"n": 0}
    original = OwnerRegistry._scan

    def counting(self):
        count["n"] += 1
        return original(self)
    monkeypatch.setattr(OwnerRegistry, "_scan", counting)
    return count


def _op(app: App, name: str, **arguments):
    return call(Ctx(app=app, actor=AI, channel="mcp"), name, arguments)


def _owner_ref(owner) -> dict:
    return {"target_kind": "owner", "target_id": owner.owner_id, "revision": None, "sha256": owner.fingerprint,
            "locator": "", "relation": "references"}


def test_commit_scans_owner_tree_once(tmp_path, scans):
    app = App.open(tmp_path, create=True, fake_vectors=True)
    for i in range(3):
        _op(app, "memory.owner_create", owner_type="research", slug=f"o{i}", title=f"对象{i}")
    head = _op(app, "memory.commit", owner_id="RES-O0", expected_head=None,
               operations=[{"op": "put_record", "client_key": "g", "draft": goal_draft("RES-O0")}])["commit_id"]
    evidence = register_source(app.storage, "SRC-E", "research/o0/e.txt", "x\n")
    scans["n"] = 0
    receipt = _op(app, "memory.commit", owner_id="RES-O0", expected_head=head,
                  operations=[{"op": "put_record", "client_key": "n", "draft": narrative_draft("RES-O0", evidence)}])
    # 预检、锁内准备、索引同步、操作摘要共用 1 次扫描；每次提交都复核目标对象的访问权限（owner_access），
    # 预检、锁内、发布前两次共 4 次复核各重新扫描（改前共 12 次）
    assert receipt["save_status"] == "committed" and receipt["activity"]
    assert scans["n"] == 5


def test_recheck_rescans_when_owner_basis_is_checked(tmp_path, scans):
    app = App.open(tmp_path, create=True, fake_vectors=True)
    _op(app, "memory.owner_create", owner_type="research", slug="a", title="甲")
    _op(app, "memory.owner_create", owner_type="research", slug="b", title="乙")
    draft = goal_draft("RES-A")
    draft["sources"] = [_owner_ref(app.storage.owners.scan().get("RES-B"))]
    draft.pop("provenance_gap")
    scans["n"] = 0
    _op(app, "memory.commit", owner_id="RES-A", expected_head=None,
        operations=[{"op": "put_record", "client_key": "g", "draft": draft}])
    # 1 次复用扫描 + 首次保存写 owner.json 后 1 次 + 预检/锁内/发布前两次共 4 次复核各重新扫描
    assert scans["n"] == 6


def test_card_change_after_request_start_is_caught_before_publish(tmp_path):
    """业务卡在本次操作已扫描之后被改：发布前复核用新扫描比较指纹，拒绝发布。"""
    state = {"armed": False}
    storage, memory, _ = services(tmp_path, fault=lambda point: state["armed"] and point == "before_head"
                                  and _edit_card(storage, state))
    storage.owners.create("research", "a", "甲")
    target = storage.owners.create("research", "b", "乙")
    draft = goal_draft("RES-A")
    draft["sources"] = [_owner_ref(target)]
    draft.pop("provenance_gap")
    with OwnerRegistry.request_scope():
        storage.owners.scan()          # 本次操作的复用结果里是修改前的业务卡
        state["armed"] = True
        with pytest.raises(BurrowlError) as caught:
            memory.commit(request("RES-A", None, {"op": "put_record", "client_key": "g", "draft": draft}))
    assert caught.value.code == "STALE_BASIS"
    owner = storage.owners.scan().get("RES-A")
    assert storage.store.read_snapshot(owner).head is None   # 没有发布


def test_reader_recheck_rescans(tmp_path):
    """读取路径的返回前回源：本次操作已扫描后业务卡被改，同样报 STALE_BASIS。"""
    storage, _, _ = services(tmp_path)
    storage.owners.create("research", "b", "乙")
    with OwnerRegistry.request_scope():
        reader = Reader(storage)
        reader.owner("RES-B")
        _edit_card(storage, {"armed": True})
        with pytest.raises(BurrowlError) as caught:
            reader.recheck()
    assert caught.value.code == "STALE_BASIS"


def _edit_card(storage, state) -> None:
    state["armed"] = False
    path = storage.ws.path("research/b/research.json")
    card = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**card, "title": "乙（被改）"}, ensure_ascii=False), encoding="utf-8")


def test_writes_invalidate_reused_scan(tmp_path):
    app = App.open(tmp_path, create=True, fake_vectors=True)
    owners = app.storage.owners
    owners.create("research", "a", "甲")
    with OwnerRegistry.request_scope():
        first = owners.scan()
        assert owners.scan() is first                       # 同一操作内复用
        owners.create("research", "b", "乙")               # 写业务卡 → 作废
        assert owners.scan().find("RES-B") is not None
        run = app.runs.create(title="一次实验", owner_id="RES-A")
        assert owners.scan().find(run["run_id"]) is not None
        before = owners.scan().get(run["run_id"]).fingerprint
        app.runs.update(run["run_id"], {"title": "改名"})
        assert owners.scan().get(run["run_id"]).fingerprint != before
        assert owners.scan(fresh=True) is not first
    assert owners.scan() is not owners.scan()               # 作用域外不复用
