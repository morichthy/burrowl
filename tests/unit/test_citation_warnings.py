"""引用提醒的边界：结构化依据、代码伪引用、共同出处依赖和自足记录。"""
from burrowl.services.memory.citations import citation_warnings

REF = {"target_kind": "file", "target_id": "SRC-TEST"}


def warnings(kind, body="结果", *, blocks=None, sources=None):
    return citation_warnings([{"op": "put_record", "draft": {
        "kind": kind, "body_markdown": body, "sources": [REF] if sources is None else sources,
        "payload": {"blocks": blocks or []}}}])


def test_only_readable_evidence_links_count():
    assert warnings("overview", "误差为 1。[[补偿求和]] `[[ @原文 ]]` ![图](figure:0)")
    assert warnings("overview", "```\n[[@原文]]\n```")
    assert not warnings("overview", "误差为 1。[[@实验#results|结果表]]")
    assert not warnings("experience", "原文结论：[[!失效边界]]")
    assert not warnings("goal")
    assert not warnings("overview", sources=[])


def test_each_l1_block_checked_and_shared_citation_follows_dependencies():
    blocks = [{"block_id": "context", "markdown": "共同出处：[[@原件]]", "requires_block_ids": []},
              {"block_id": "definitions", "markdown": "原文定义", "requires_block_ids": ["context"]},
              {"block_id": "results", "markdown": "原文结果", "requires_block_ids": ["definitions"]},
              {"block_id": "other", "markdown": "另一出处", "requires_block_ids": []}]
    result = warnings("detail", "", blocks=blocks)
    assert [w["path"] for w in result] == ["/operations/0/draft/payload/blocks/3/markdown"]
    assert result[0]["code"] == "INLINE_CITATION_MISSING"


def test_client_key_basis_and_prose_scanned_but_unit_not_scanned():
    operation = {"draft": {"kind": "document_section", "sources": [], "payload": {"blocks": [
        {"type": "prose", "markdown": "本节结论", "evidence_refs": [{"client_key": "detail"}]},
        {"type": "unit", "ref": {"client_key": "detail"}}]}}}
    assert [w["path"] for w in citation_warnings([operation])] == ["/operations/0/draft/payload/blocks/0/markdown"]


def test_bad_draft_shapes_left_to_schema_validator():
    for payload in ("bad", {"blocks": "bad"}, {"blocks": [None]}, {"blocks": [{"markdown": "原文"}]},
                    {"blocks": [{"block_id": "x", "markdown": 4, "requires_block_ids": [["x"]]}]}):
        assert citation_warnings([{"draft": {"kind": "detail", "sources": [REF], "payload": payload}}]) == []
