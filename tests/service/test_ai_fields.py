"""R20：AI 填写的草案字段——服务能推导的可以省略，契约标注与服务行为一致，错了一次说全。"""
import json
from copy import deepcopy
from importlib.resources import files

import pytest

from burrowl.core import contract
from burrowl.core.errors import BurrowlError
from burrowl.core.records import SERVER_FIELDS
from burrowl.services import workflow
from burrowl.services.memory import associations
from tests.service.helpers import AI, head_of, request, services

OWNER = "RES-FLOATING-POINT-SUMMATION"
EXAMPLES = files("burrowl.core.schema") / "examples"
KINDS = sorted(p.name.removesuffix(".json") for p in EXAMPLES.iterdir() if p.name.endswith(".json"))


def _example(kind: str) -> dict:
    return json.loads((EXAMPLES / f"{kind}.json").read_text(encoding="utf-8"))["operation"]


def _dry_run(memory, *operations) -> dict:
    return memory.commit(request(OWNER, head_of(memory, OWNER), *operations, dry_run=True))


def _paths(exc: pytest.ExceptionInfo) -> set[str]:
    return {e["path"] for e in exc.value.errors}


@pytest.mark.parametrize("kind", KINDS)
def test_contract_fill_matches_service(sample_workspace, kind):
    """D-07：契约标 required 的字段省略后提交失败且指向该字段；标 optional 的省略后照常通过；服务端字段写了被拒。"""
    _storage, memory, _review = services(sample_workspace)
    operation = _example(kind)
    fields = contract.draft_fields(kind)
    for name, entry in fields.items():
        if name not in operation["draft"]:
            continue
        trimmed = deepcopy(operation)
        del trimmed["draft"][name]
        if entry["fill"] == "required":
            with pytest.raises(BurrowlError) as caught:
                _dry_run(memory, trimmed)
            assert f"/operations/0/draft/{name}" in _paths(caught), (kind, name, caught.value.errors)
        elif entry["fill"] == "optional":
            assert _dry_run(memory, trimmed)["valid"], (kind, name)
    for name in sorted(SERVER_FIELDS & set(contract.contract(kind)["do_not_write"]) - {"links"}):
        written = deepcopy(operation)
        written["draft"][name] = "MEM-x" if name == "record_id" else 1
        with pytest.raises(BurrowlError) as caught:
            _dry_run(memory, written)
        assert f"/operations/0/draft/{name}" in _paths(caught), (kind, name)


def test_contract_marks_change_reason_conditional_and_payload_defaults():
    detail = contract.contract("detail")
    assert detail["draft_fields"]["change_reason"]["fill"] == "conditional"
    assert detail["draft_fields"]["owner_id"]["fill"] == "optional"
    assert "change_reason" not in detail["do_not_write"]
    assert "missing_refs（可选）" in detail["payload_fields"]
    assert "memory_commit" in detail["write_with"] and "expected_revision" in detail["write_with"]


def test_owner_id_mismatch_names_the_draft(sample_workspace):
    """D-01：草案写了别的对象时，报错指出是第几条、写的是谁、提交的是谁。"""
    _storage, memory, _review = services(sample_workspace)
    good, bad = _example("goal"), deepcopy(_example("question"))
    bad["draft"]["owner_id"] = "RES-OTHER"
    with pytest.raises(BurrowlError) as caught:
        _dry_run(memory, good, bad)
    (error,) = caught.value.errors
    assert error["path"] == "/operations/1/draft/owner_id" and error["expected"] == [OWNER]
    assert "RES-OTHER" in error["message"] and OWNER in error["message"]


def test_sensitivity_defaults_and_carries_over(sample_workspace):
    """D-01：新记录不写 sensitivity 为 internal；修订不写时沿用原值，不会悄悄降级。"""
    _storage, memory, _review = services(sample_workspace)
    draft = _example("question")["draft"]
    assert "sensitivity" not in draft and "owner_id" not in draft
    created = memory.commit(request(OWNER, head_of(memory, OWNER), {"op": "put_record", "client_key": "q",
                                                                     "draft": dict(draft, sensitivity="confidential")}))
    rid = created["record_results"][0]["record_id"]
    memory.commit(request(OWNER, head_of(memory, OWNER), {"op": "put_record", "record_id": rid, "expected_revision": 1,
                                                          "draft": dict(draft, title="成对求和是否更稳健（修订）",
                                                                        change_reason="改标题")}))
    assert memory.inspect(OWNER, record_id=rid)["record"]["sensitivity"] == "confidential"
    fresh = memory.commit(request(OWNER, head_of(memory, OWNER), {"op": "put_record", "client_key": "n", "draft": draft}))
    rid2 = fresh["record_results"][0]["record_id"]
    assert memory.inspect(OWNER, record_id=rid2)["record"]["sensitivity"] == "internal"


def test_rule_errors_point_into_the_draft(sample_workspace):
    """规则层（如修订规则）的错误同样指向提交参数里的草案位置。"""
    _storage, memory, _review = services(sample_workspace)
    created = memory.commit(request(OWNER, head_of(memory, OWNER), _example("goal")))
    rid = created["record_results"][0]["record_id"]
    revised = {"op": "put_record", "record_id": rid, "expected_revision": 1,
               "draft": dict(_example("goal")["draft"], title="新目标", change_reason="改目标")}
    with pytest.raises(BurrowlError) as caught:
        _dry_run(memory, revised)
    assert all(p.startswith("/operations/0/draft/") for p in _paths(caught)) and _paths(caught)


def test_draft_errors_listed_at_once_with_notes(sample_workspace):
    """D-05：缺多个字段时一次列全，路径从提交参数根算起，带字段说明与 memory_contract 提示。"""
    _storage, memory, _review = services(sample_workspace)
    operation = _example("goal")
    del operation["draft"]["title"], operation["draft"]["record_reason"]
    with pytest.raises(BurrowlError) as caught:
        _dry_run(memory, operation)
    errors = {e["path"]: e["message"] for e in caught.value.errors}
    assert "能单独看懂的标题" in errors["/operations/0/draft/title"]
    assert "为什么保存这一条" in errors["/operations/0/draft/record_reason"]
    assert 'memory_contract("goal")' in caught.value.details["hint"]


def test_enum_error_lists_allowed_values(sample_workspace):
    _storage, memory, _review = services(sample_workspace)
    operation = _example("question")
    operation["draft"]["payload"]["status"] = "pending"
    with pytest.raises(BurrowlError) as caught:
        _dry_run(memory, operation)
    error = next(e for e in caught.value.errors if e["path"] == "/operations/0/draft/payload/status")
    assert "open" in error["expected"] and "pending" in error["message"]


def test_summary_save_owner_optional_and_paths_rebased(sample_workspace):
    """memory_summary_save：owner_id 可省略；草案错误的路径指向本工具的 /draft。"""
    _storage, memory, _review = services(sample_workspace)
    draft = _example("experience")["draft"]
    assert workflow.save_summary(memory, OWNER, draft, actor=AI, basis_heads={}, dry_run=True)["valid"]
    broken = {k: v for k, v in draft.items() if k != "title"}
    with pytest.raises(BurrowlError) as caught:
        workflow.save_summary(memory, OWNER, broken, actor=AI, basis_heads={}, dry_run=True)
    assert "/draft/title" in _paths(caught)
    with pytest.raises(BurrowlError) as caught:
        workflow.save_summary(memory, OWNER, dict(draft, kind="detail", owner_id="RES-OTHER"), actor=AI,
                              basis_heads={}, record_id="MEM-x", dry_run=True)
    assert {"/draft/kind", "/draft/owner_id", "/draft/change_reason", "/expected_revision"} <= _paths(caught)


def test_association_errors_point_at_payload(sample_workspace):
    _storage, memory, _review = services(sample_workspace)
    with pytest.raises(BurrowlError) as caught:
        associations.decide(memory, OWNER, {"from": "MEM-x", "to": "MEM-y", "explanation": "e"}, actor=AI,
                            reason="r", dry_run=True)
    assert "/payload/from" in _paths(caught)
