"""迁移核对补回的边界：受限材料、run.json 自引用、回滚串改、核心算法准入、反馈幂等、导出来源。"""
import json
import uuid

import pytest

from burrowl.core.errors import BurrowlError
from burrowl.services.memory import export
from burrowl.services.runs import RunService
from tests.service.helpers import AI, narrative_draft, register_source, request, services


@pytest.fixture
def setup(tmp_path):
    storage, memory, _review = services(tmp_path)
    storage.owners.create("research", "order", "顺序")
    return storage, memory, RunService(storage)


def test_restricted_owner_material_cannot_enter_run(setup):
    storage, _memory, runs = setup
    storage.owners.create("research", "secret", "保密", {"sensitivity": "restricted"})
    secret = storage.ws.root / "research/secret/data.txt"
    secret.write_text("x", encoding="utf-8")
    run_id = runs.create("实验", owner_id="RES-ORDER")["run_id"]
    with pytest.raises(BurrowlError) as caught:
        runs.register(run_id, inputs=["research/secret/data.txt"])
    assert caught.value.code == "ACCESS_DENIED"


def test_run_json_is_never_material_and_rollback_rechecks(setup):
    storage, _memory, runs = setup
    created = runs.create("实验", owner_id="RES-ORDER")
    with pytest.raises(BurrowlError):
        runs.execute(created["run_id"], script=created["path"])
    data = storage.ws.root / "research/order/data.txt"
    data.write_text("1", encoding="utf-8")
    done = runs.register(created["run_id"], inputs=["research/order/data.txt"])
    runs.update(created["run_id"], {"conclusion": "后来又改了"})
    with pytest.raises(BurrowlError) as caught:                     # 登记之后又有修改：拒绝用旧回执覆盖
        runs.rollback(done["receipt"])
    assert caught.value.code == "VERSION_CONFLICT"
    stale = runs.update(created["run_id"], {"conclusion": "A"})
    with pytest.raises(BurrowlError) as caught:
        runs.update(created["run_id"], {"conclusion": "B"}, expected_sha256="0" * 64)
    assert caught.value.code == "VERSION_CONFLICT" and stale["status"] == "updated"


def test_core_algorithm_requires_company_document(setup):
    storage, *_ = setup
    with pytest.raises(BurrowlError) as caught:
        storage.owners.create("core-algorithm", "my-script", "普通脚本")
    assert "source_document" in caught.value.message
    owner = storage.owners.create("core-algorithm", "kalman", "卡尔曼滤波", {"source_document": "ALG-DOC-007"})
    assert owner.owner_id.endswith("KALMAN")


def test_feedback_is_idempotent_per_request_id(setup):
    storage, memory, _runs = setup
    evidence = register_source(storage, "SRC-1", "research/order/data/r.txt", "x\n")
    record = memory.commit(request("RES-ORDER", None, {"op": "put_record", "client_key": "n",
                                                       "draft": narrative_draft("RES-ORDER", evidence)}))
    rid = record["record_results"][0]
    target = {"target_kind": "record", "target_id": rid["record_id"], "revision": rid["revision"],
              "sha256": rid["record_hash"], "locator": "record", "relation": "references"}
    request_id = str(uuid.uuid4())
    first = export.feedback(memory, "RES-ORDER", query_id="Q-1", target=target, label="adopted", actor=AI,
                            request_id=request_id)
    again = export.feedback(memory, "RES-ORDER", query_id="Q-1", target=target, label="adopted", actor=AI,
                            request_id=request_id)
    assert again["commit_id"] == first["commit_id"]
    with pytest.raises(BurrowlError):
        export.feedback(memory, "RES-ORDER", query_id="Q-1", target=target, label="outcome", actor=AI,
                        adopted_in={"target_kind": "owner", "target_id": "RES-ORDER", "revision": None,
                                    "sha256": None, "locator": "", "relation": "used_in"})


def test_export_includes_fixed_sources(setup):
    storage, memory, _runs = setup
    evidence = register_source(storage, "SRC-1", "research/order/data/r.txt", "x\n")
    memory.commit(request("RES-ORDER", None, {"op": "put_record", "client_key": "n",
                                              "draft": narrative_draft("RES-ORDER", evidence)}))
    content = export.export(memory, "RES-ORDER")["content"]
    assert "**固定来源**" in content and "file:SRC-1" in content
    assert json.loads(export.export(memory, "RES-ORDER", fmt="json")["content"])["records"]
