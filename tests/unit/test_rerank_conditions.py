"""显式条件核对，以及（本机有模型时）真实 Cross-encoder。"""
from pathlib import Path

import pytest

from burrowl.index import conditions, rerank

REF = {"target_kind": "record", "target_id": "MEM-1", "revision": 1, "sha256": "0" * 64}
OLD_RERANKER = Path(__file__).resolve().parents[3] / "A_work_auto_flow" / "services" / "reranker" / "model"


def test_conditions():
    rules = [{"id": "t", "kind": "numeric", "field": "温度", "expected": 25, "operator": "le", "unit": "°C"},
             {"id": "m", "kind": "literal", "field": "型号", "expected": "X200", "operator": "eq"}]
    parts = [{"text": "温度：290 K\n型号：X200", "ref": REF}]  # 290 K = 16.85°C ≤ 25°C
    result = {r["id"]: r["status"] for r in conditions.evaluate(rules, parts)}
    assert result == {"t": "satisfied", "m": "satisfied"}
    hot = conditions.evaluate(rules[:1], [{"text": "温度：40 °C", "ref": REF}])[0]
    assert hot["status"] == "conflict"
    vague = conditions.evaluate(rules[1:], [{"text": "型号：X200 或 X300", "ref": REF}])[0]
    assert vague["status"] == "unknown"


@pytest.mark.real_model
@pytest.mark.skipif(not OLD_RERANKER.is_dir(), reason="本机没有重排模型")
def test_real_cross_encoder_orders_relevant_first():
    model = rerank.load(OLD_RERANKER)
    scores = model.score_pairs([("浮点求和误差", "Kahan 补偿求和可以降低浮点累加误差"),
                                ("浮点求和误差", "今天天气晴朗适合散步")])
    assert scores[0] > scores[1]
