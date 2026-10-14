"""dry_run、实际保存和幂等重试给出相同的引用提示；提醒不写入规范记录。"""
from tests.service.helpers import head_of, narrative_draft, register_source, request, services


def test_missing_citation_warns_without_blocking_and_revision_fixes_it(sample_workspace):
    storage, memory, _review = services(sample_workspace)
    oid = "RES-FLOATING-POINT-SUMMATION"
    ref = register_source(storage, "SRC-CITATION-TEST", "citation-test.txt", "误差为 1")
    draft = narrative_draft(oid, ref, claim_id="CLM-CITATION-TEST")
    req = request(oid, head_of(memory, oid), {"op": "put_record", "client_key": "test", "draft": draft})
    preview = memory.commit(req | {"dry_run": True})
    assert preview["writes"] == 0
    assert preview["warnings"][0]["code"] == "INLINE_CITATION_MISSING"
    result = memory.commit(req)
    assert result["save_status"] == "committed"
    assert result["warnings"] == preview["warnings"] == memory.commit(req)["warnings"]
    rid = result["record_results"][0]["record_id"]
    record = memory.inspect(oid, record_id=rid)["record"]
    assert "warnings" not in record
    # 固定支持记录仍由解析器校验；加一个真实可读引用后通过修订消除提醒。
    draft["body_markdown"] = "实验经过与方法依据：[[@浮点求和方法:精确基准、递推与适用条件#reference]]"
    draft["change_reason"] = "补写可读出处"
    repaired = memory.commit(request(oid, head_of(memory, oid), {
        "op": "put_record", "record_id": rid, "expected_revision": 1, "draft": draft}))
    assert not any(w["code"] == "INLINE_CITATION_MISSING" for w in repaired.get("warnings", []))
    assert repaired["record_results"][0]["revision"] == 2
