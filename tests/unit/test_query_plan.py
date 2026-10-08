"""查询计划：保护项、数值一致性、词库扩展与路线权重。"""
import pytest

from burrowl.core.errors import BurrowlError
from burrowl.index import query_plan

TERMS = [{"id": "kahan", "domain": "numerics", "zh": ["补偿求和"], "en": ["compensated summation"],
          "aliases": ["Kahan summation"], "related": ["pairwise summation"], "sources": ["doi:10.1145/363707.363723"],
          "status": "active"}]


def test_plan_expansion_and_routes():
    plan = query_plan.build("X200 的补偿求和误差", domains=["numerics"], terms=TERMS,
                            variants=[{"language": "en", "question": "X200 compensated summation error",
                                       "lexical_terms": ["X200", "compensated", "summation"]}])
    assert "X200" in plan["protected_terms"] and plan["matched_terms"] == ["kahan"]
    english = plan["variants"][1]
    assert "compensated summation" in english["lexical_terms"] and "Kahan summation" in english["lexical_terms"]
    routes = query_plan.routes(plan)
    assert [r["channel"] for r in routes].count("dense") == 2
    assert routes[-1]["kind"] == "related" and routes[-1]["weight"] == 0.25


def test_variants_must_keep_protected_numbers():
    with pytest.raises(BurrowlError):
        query_plan.build("X200 误差", variants=[{"language": "en", "question": "X2000 error"}])
    with pytest.raises(BurrowlError):
        query_plan.build("误差", variants=[{"language": "en", "question": "a"}, {"language": "en", "question": "b"},
                                         {"language": "en", "question": "c"}])
