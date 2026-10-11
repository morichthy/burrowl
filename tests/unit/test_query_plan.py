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


def test_errors_name_the_missing_terms():
    # R18 走查：英文改写漏了保护项 L1，报错却没说漏了哪个，只能逐个对照
    with pytest.raises(BurrowlError) as lost:
        query_plan.build("中文长 L1 记录的 late 切分", protected_terms=["late", "L1"],
                         variants=[{"language": "en", "question": "late chunking of long records"}])
    assert "L1" in lost.value.message and lost.value.details["missing"] == ["L1"]
    assert lost.value.details["variant"] == "late chunking of long records"
    with pytest.raises(BurrowlError) as foreign:
        query_plan.build("切分方式对比", protected_terms=["late"])
    assert foreign.value.details["missing"] == ["late"] and "late" in foreign.value.message
    with pytest.raises(BurrowlError) as changed:
        query_plan.build("X200 误差", variants=[{"language": "en", "question": "X200 X300 error"}])
    assert changed.value.details["added"] == ["X300"] and "X300" in changed.value.message


def test_numeric_tokens_keep_scientific_notation_whole():
    """1e16 曾被切成 "1e"，导致 build 报"保护项必须来自原问题"。"""
    assert query_plan.numeric_tokens("为什么 1e16 加 1 还是 1e16，X200 与 3.12.10") == ["1e16", "1", "1e16", "X200", "3.12.10"]
    plan = query_plan.build("三项 [1e16, 1, -1e16] 下 Kahan 会失败吗")
    assert "1e16" in plan["protected_terms"]
