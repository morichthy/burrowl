"""R19 D-04、D-05：结论、已采纳的关联、概念页进发现投影；关联条目有可检索的文字；统一边标准的读法与扩展权重。"""
from burrowl.index import graph, projections

OWNER = projections.OwnerInfo("RES-A", "research", "对象 A", "internal", "f" * 64)


def record(rid: str, kind: str, payload: dict, *, level=None, title="标题", body="", discovery="workspace_summary",
           sensitivity="internal") -> dict:
    return {"record_id": rid, "kind": kind, "level": level, "revision": 1, "content_hash": "c" * 64,
            "record_hash": "r" * 64, "title": title, "body_markdown": body, "payload": payload,
            "discovery": discovery, "sensitivity": sensitivity, "keywords": [], "sources": []}


def ref(target: str, locator="record") -> dict:
    return {"target_kind": "record", "target_id": target, "revision": 1, "sha256": "s" * 64, "locator": locator,
            "relation": "references"}


NARRATIVE = record("MEM-N", "narrative", {
    "question": "顺序是否影响误差", "stages": [], "limitations": [],
    "claims": [{"claim_id": "CLM-N-1", "statement": "升序求和误差更小", "kind": "fact", "scope": "整数输入",
                "evidence_refs": [], "relation": {"subject": "[[升序]]", "predicate": "improves", "object": "[[误差]]"}}]},
    level="L2", title="顺序实验经过", discovery="owner_only")


def association(status="accepted", relation="same_problem") -> dict:
    return record("MEM-ASSOC", "association", {
        "from": ref("MEM-N"), "to": ref("MEM-X", "block:b1"), "relation": relation, "status": status,
        "explanation": "两者都在比较求和顺序", "shared_structure": "大数抵消", "transfer_limits": ["只适用整数"],
        "differences": [], "certainty": "plausible"}, title="关联判断")


CONCEPT = record("MEM-C", "wiki_page", {"page_type": "concept", "aliases": ["Kahan summation"], "domains": ["数值"],
                                         "status": "active"}, title="补偿求和", body="减小舍入误差的求和方法")
TITLES = {"MEM-N": "顺序实验经过", "MEM-X#b1": "对象 X 的实验 › b1"}


def by_key(entries):
    return {e["entry_id"].rsplit(":", 1)[1]: e for e in entries}


def test_discovery_includes_claims_associations_and_concepts():
    entries = by_key(projections.discovery(OWNER, [NARRATIVE, association(), CONCEPT], {"CLM-N-1": "accepted"},
                                           set(), TITLES))
    claim = entries["CLM-N-1"]
    assert claim["kind"] == "claim" and claim["record_id"] == "MEM-N" and claim["level"] == "L2"
    assert "整数输入" in claim["text"] and "improves" in claim["text"]
    assert claim["discovery"] == "owner_only" and claim["review"] == "accepted"  # 可见范围跟随所在记录
    assoc = entries["MEM-ASSOC"]
    assert assoc["title"] == "关联：顺序实验经过 ↔ 对象 X 的实验 › b1"
    assert "是同一问题（plausible）" in assoc["text"] and "两者都在比较求和顺序" in assoc["text"]
    assert "共同结构：大数抵消" in assoc["text"] and "迁移边界：只适用整数" in assoc["text"]
    assert "Kahan summation" in entries["MEM-C"]["text"] and "减小舍入误差" in entries["MEM-C"]["text"]


def test_only_accepted_associations_are_indexed():
    for status in ("candidate", "rejected", "withdrawn"):
        for build in (projections.discovery, projections.fulltext):
            assert "MEM-ASSOC" not in by_key(build(OWNER, [association(status)], {}, set(), TITLES))
    full = by_key(projections.fulltext(OWNER, [association()], {}, set(), TITLES))["MEM-ASSOC"]
    assert "两者都在比较求和顺序" in full["text"]  # 此前全文条目实际为空


def test_unknown_endpoint_falls_back_to_id_and_restricted_is_skipped():
    entries = by_key(projections.discovery(OWNER, [association()], {}, set(), {}))
    assert entries["MEM-ASSOC"]["title"] == "关联：MEM-N ↔ MEM-X"
    hidden = record("MEM-H", "narrative", NARRATIVE["payload"], level="L2", sensitivity="restricted")
    assert "CLM-N-1" not in by_key(projections.discovery(OWNER, [hidden], {}, set(), {}))


def test_phrases_and_weights():
    assert graph.phrase("contradicts", "A", "B") == "A 与 B 矛盾"
    assert graph.phrase("supports", "A", "B") == "A 以 B 为依据"
    assert graph.phrase("contradicts", "A", "B", edge_category="navigation") == "A 与 B 相关"  # 旧关联里的证据类关系名
    assert graph.phrase("fails_on", "补偿求和", "大数抵消", edge_category="claim_relation") == "补偿求和 fails_on 大数抵消"
    assert graph.phrase("x-custom", "A", "B") == "A 参考了 B"  # 未列出的关系名按类别通用读法
    assert graph.expand_weight({"category": "structure"}) == 0
    assert graph.expand_weight({"category": "evidence"}) == 1.0
    assert graph.expand_weight({"category": "navigation", "certainty": "speculative"}) == 0.2
    assert graph.expand_weight({"category": "claim_relation"}, maturity_factor=1.0) == 0.6
