"""来源登记不可改写，停用来源后派生记录在读取路径上被隐去（inspect / resume / 总结材料）。"""
import shutil

import pytest

from burrowl.core.errors import BurrowlError
from burrowl.services import workflow
from tests.service.helpers import narrative_draft, register_source, request, services


@pytest.fixture
def setup(tmp_path):
    storage, memory, _review = services(tmp_path)
    owner_id = storage.owners.create("research", "order", "顺序").owner_id
    evidence = register_source(storage, "SRC-ORDER-1", "research/order/data/result.txt", "升序误差 0.0\n")
    rid = memory.commit(request(owner_id, None, {"op": "put_record", "client_key": "n",
                                                 "draft": narrative_draft(owner_id, evidence)}))["record_results"][0]
    return storage, memory, owner_id, rid["record_id"]


def test_registration_cannot_be_rewritten(setup, tmp_path):
    storage, *_ = setup
    again = storage.sources.register("SRC-ORDER-1", "research/order/data/result.txt", sensitivity="confidential")
    assert again["sensitivity"] == "confidential"                       # 幂等，只能提高敏感级别
    assert storage.sources.register("SRC-ORDER-1", "research/order/data/result.txt")["sensitivity"] == "confidential"
    (tmp_path / "research/order/data/result.txt").write_text("被改过\n", encoding="utf-8")
    with pytest.raises(BurrowlError) as caught:
        storage.sources.register("SRC-ORDER-1", "research/order/data/result.txt")
    assert caught.value.code == "CONFLICT"


def test_relocate_requires_identical_bytes(setup, tmp_path):
    storage, *_ = setup
    moved = tmp_path / "archive/result.txt"
    moved.parent.mkdir()
    shutil.copy2(tmp_path / "research/order/data/result.txt", moved)
    entry = storage.sources.relocate("SRC-ORDER-1", "archive/result.txt")
    assert entry["path"] == "archive/result.txt" and entry["relocated_from"] == ["research/order/data/result.txt"]
    moved.write_text("不同内容\n", encoding="utf-8")
    with pytest.raises(BurrowlError) as caught:
        storage.sources.relocate("SRC-ORDER-1", "archive/result.txt")
    assert caught.value.code == "STALE_BASIS"


def test_disabled_source_hides_derived_records(setup):
    storage, memory, owner_id, record_id = setup
    assert record_id in memory.inspect(owner_id)["records"]
    storage.sources.update("SRC-ORDER-1", enabled=False)                 # 用户在工作台停用
    with pytest.raises(BurrowlError) as caught:
        storage.sources.register("SRC-ORDER-1", "research/order/data/result.txt")
    assert caught.value.code == "ACCESS_DENIED"                          # agent 不能借重复登记重新启用
    view = memory.inspect(owner_id)
    assert record_id not in view["records"] and view["omitted"][0]["record_id"] == record_id
    with pytest.raises(BurrowlError) as caught:
        memory.inspect(owner_id, record_id=record_id)
    assert caught.value.code == "ACCESS_DENIED"
    resumed = workflow.resume(memory, owner_id)
    assert all(p["ref"]["target_id"] != record_id for p in resumed["parts"])
    assert any(g["code"] == "ACCESS_DENIED" for g in resumed["gaps"])
    summary = workflow.summary_materials(memory, [owner_id])
    assert all(p["ref"]["target_id"] != record_id for p in summary["parts"])
