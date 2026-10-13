"""R23：结论的结构（谓词框架、结构不计入内容指纹、结论要素边、概念链接在投影时解析）。"""
from burrowl.core import predicates
from burrowl.core.records import claim_hash, content_hash
from burrowl.index import graph, projections

OWNER = projections.OwnerInfo("RES-A", "research", "对象 A", "internal", "f" * 64)


def rel(predicate: str, *parts: tuple[str, str], **extra) -> dict:
    return {"predicate": predicate, "participants": [{"role": r, "value": v} for r, v in parts], **extra}


def test_frame_validation():
    """D-03 至 D-07：必填角色、框架外角色、条件与矛盾的写法。"""
    assert predicates.validate(rel("causes", ("cause", "A"), ("cause", "B"), ("effect", "C"))) == []
    assert predicates.validate(rel("improves", ("agent", "A"), ("target", "B"), ("x-catalyst", "D"))) == []
    assert predicates.validate(rel("x-mixes", ("x-input", "A"), ("x-output", "B"))) == []
    assert [p for p, _ in predicates.validate(rel("causes", ("cause", "A")))] == ["/participants"]
    assert [p for p, _ in predicates.validate(rel("causes", ("cause", "A"), ("effect", "B"), ("who", "C")))] == [
        "/participants/2/role"]
    assert [p for p, _ in predicates.validate(rel("causes", ("cause", "A"), ("effect", "B"),
                                                  ("condition", "T")))] == ["/participants/2/role"]
    assert [p for p, _ in predicates.validate(rel("equivalent_to", ("item", "A")))] == ["/participants"]
    assert "evidence_refs" in predicates.validate(rel("contradicts", ("x-a", "A"), ("x-b", "B")))[0][1]
    assert [p for p, _ in predicates.validate(rel("x-only", ("x-a", "A")))] == ["/participants"]
    assert [p for p, _ in predicates.validate(rel("unknown", ("x-a", "A"), ("x-b", "B")))] == ["/predicate"]


def test_reading_and_display():
    relation = rel("causes", ("effect", "瞬态误差"), ("cause", "[[温漂|温度漂移]]"), ("cause", "[[基准噪声]]"),
                   negated=True)
    assert predicates.reading(relation) == "不导致｜原因：温度漂移、基准噪声；结果：瞬态误差"
    assert predicates.link_inner("[[温漂|温度漂移]]") == "温漂"
    assert predicates.link_inner("瞬态误差") is None


def _record(claims, links=()):
    return {"record_id": "MEM-N", "owner_id": "RES-A", "kind": "narrative", "level": "L2", "revision": 1,
            "record_hash": "r" * 64, "title": "经过", "keywords": [], "body_markdown": "正文",
            "payload": {"question": "q", "stages": [], "limitations": [], "claims": claims}, "sources": [],
            "provenance_gap": None, "discovery": "workspace_summary", "sensitivity": "internal",
            **({"links": list(links)} if links else {})}


CLAIM = {"claim_id": "CLM-N-1", "statement": "温漂导致误差", "kind": "fact", "scope": "在 [[满载]] 下",
         "evidence_refs": []}


def _link(text, target=None, status="resolved"):
    ref = target and {"target_kind": "record", "target_id": target, "revision": 1, "sha256": "s" * 64,
                      "locator": "record", "relation": "mentions"}
    return {"text": text, "ref": ref, "status": status if target else "unresolved_concept"}


def test_relation_not_in_fingerprints():
    """D-02、D-16：只改 relation（及只出现在 relation 里的链接）不改变内容指纹与结论指纹；没有 relation 时指纹不变。"""
    plain = _record([CLAIM], [_link("满载", "MEM-LOAD")])
    with_relation = _record([{**CLAIM, "relation": rel("causes", ("cause", "[[温漂]]"), ("effect", "误差"))}],
                            [_link("满载", "MEM-LOAD"), _link("温漂", "MEM-DRIFT")])
    assert content_hash(plain) == content_hash(with_relation)
    assert claim_hash(CLAIM) == claim_hash(with_relation["payload"]["claims"][0])
    changed_scope = _record([{**CLAIM, "scope": "在 [[空载]] 下"}], [_link("空载", "MEM-IDLE")])
    assert content_hash(changed_scope) != content_hash(plain)


def test_claim_part_edges_and_projection_time_concepts():
    """D-09、D-11：参与者按角色连出，适用范围的链接连成条件边；没有快捷边；未解析的概念名按当前目录接上，
    被合并的页面跟随重定向；纯文字不画边；只出现在结论要素里的链接不再从记录连提及边。"""
    claim = {**CLAIM, "relation": rel("causes", ("cause", "[[温漂]]"), ("cause", "[[噪声]]"), ("effect", "误差"))}
    record = _record([claim], [_link("满载", "MEM-LOAD-OLD"), _link("温漂"), _link("噪声", "MEM-NOISE")])
    concepts = graph.Concepts({"温漂": "MEM-DRIFT"}, {"MEM-LOAD-OLD": "MEM-LOAD"})
    _nodes, edges = graph.graph(OWNER, [record], {"CLM-N-1": "accepted"}, set(), concepts=concepts)
    parts = sorted((e["src"], e["type"], e["dst"]) for e in edges if e["category"] == "claim_part")
    assert parts == [("CLM-N-1", "cause", "MEM-DRIFT"), ("CLM-N-1", "cause", "MEM-NOISE"),
                     ("CLM-N-1", "condition", "MEM-LOAD")]
    assert all(e["declared_by"] == "CLM-N-1" and e["state"] == "active" for e in edges if e["category"] == "claim_part")
    assert not any(e["src"] == "MEM-N" and e["category"] == "mention" for e in edges)
    assert set(graph.CATEGORIES) >= {e["category"] for e in edges}

    unresolved = graph.graph(OWNER, [record], {"CLM-N-1": "retracted"}, set())[1]
    assert {e["dst"] for e in unresolved if e["category"] == "claim_part"} == {"MEM-LOAD-OLD", "MEM-NOISE"}
    assert {e["state"] for e in unresolved if e["category"] == "claim_part"} == {"invalid"}


def test_statement_links_still_mention():
    record = _record([{**CLAIM, "statement": "[[温漂]] 导致误差"}], [_link("温漂", "MEM-DRIFT"), _link("满载", "MEM-LOAD")])
    edges = graph.graph(OWNER, [record], {}, set())[1]
    assert ("MEM-N", "MEM-DRIFT", "mention") in {(e["src"], e["dst"], e["category"]) for e in edges}
