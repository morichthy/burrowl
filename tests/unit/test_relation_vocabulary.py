"""R19 D-09：两层关系词表。超出推荐范围只给 warning，不拒绝（R12 D-27）。"""
from burrowl.services.memory.vocabulary import relation_warnings


def ref(relation: str) -> dict:
    return {"target_kind": "record", "target_id": "MEM-X", "revision": 1, "sha256": "s" * 64, "locator": "record",
            "relation": relation}


def draft(kind="narrative", sources=(), payload=None) -> dict:
    return {"draft": {"kind": kind, "sources": list(sources), "payload": payload or {}}}


def relations(warnings) -> list[tuple[str, str]]:
    return [(w["path"].split("/draft/", 1)[1], w["relation"]) for w in warnings]


def test_recommended_relations_produce_no_warning():
    payload = {"claims": [{"evidence_refs": [ref("supports"), {**ref("contradicts"), "note": "表 2 的反例"}, ref("input")]}],
               "technical_refs": [ref("references")]}
    assert relation_warnings([draft(sources=[ref("references"), ref("input")], payload=payload)]) == []


def test_fine_grained_names_warn_by_field():
    payload = {"claims": [{"evidence_refs": [ref("references")]}], "stages": [{"evidence_refs": [ref("background")]}],
               "technical_refs": [ref("derived_from")]}
    found = relations(relation_warnings([draft(sources=[ref("background")], payload=payload)]))
    assert found == [("sources/0", "background"), ("payload/claims/0/evidence_refs/0", "references"),
                     ("payload/stages/0/evidence_refs/0", "background"), ("payload/technical_refs/0", "derived_from")]


def test_association_relation_and_skipped_kinds():
    association = draft("association", payload={"from": ref("references"), "to": ref("references"),
                                                 "relation": "contradicts", "basis_refs": [ref("supports")]})
    found = relation_warnings([association])
    assert [(w["relation"], w["recommended"][0]) for w in found] == [("contradicts", "related_to")]
    assert relation_warnings([draft("document_section", payload={"blocks": [{"refs": [ref("contains")]}]})]) == []
    assert relation_warnings([draft("association", payload={"relation": "same_problem"})]) == []


def test_contradicts_without_note_warns():
    """R23 D-13：反例要说明反驳了什么；缺 note 只返回 warning。"""
    payload = {"claims": [{"evidence_refs": [ref("contradicts"), {**ref("supports")}]}]}
    found = relation_warnings([draft(payload=payload)])
    assert [(w["code"], w["path"].split("/draft/", 1)[1]) for w in found] == [
        ("EVIDENCE_NOTE_MISSING", "payload/claims/0/evidence_refs/0")]
