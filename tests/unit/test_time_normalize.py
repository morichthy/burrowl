"""时间字段：带时区的写法在保存前换算为 UTC（Z）；已是 UTC 的不动（已有指纹不变）；没有时区的不猜。"""
import pytest

from burrowl.core.clock import to_utc
from burrowl.core.records import normalize_draft


@pytest.mark.parametrize(("raw", "saved"), [
    ("2026-10-13T16:00:00+08:00", "2026-10-13T08:00:00Z"),
    ("2026-10-13T16:00:00.5-02:30", "2026-10-13T18:30:00.500000Z"),
    ("2026-10-13T08:00:00+00:00", "2026-10-13T08:00:00Z"),
    ("2026-10-13T08:00:00.123Z", "2026-10-13T08:00:00.123Z"),   # 已是 UTC：原样保留，不改精度
    ("2026-10-13", "2026-10-13"),                               # 只有日期：不猜，交给校验拒绝
    ("2026-10-13T16:00:00", "2026-10-13T16:00:00"),             # 没有时区：同上
    (None, None),
])
def test_to_utc(raw, saved):
    assert to_utc(raw) == saved


def test_normalize_draft_converts_nested_time_fields_only():
    draft = {"kind": "experience", "title": "t", "payload": {
        "applicability": {"valid_from": "2026-01-01T08:00:00+08:00", "valid_until": None},
        "missing_refs": [{"observed_at": "2026-10-13T16:00:00+08:00", "reason": "2026-10-13T16:00:00+08:00"}]}}
    record = normalize_draft(draft)
    assert record["payload"]["applicability"]["valid_from"] == "2026-01-01T00:00:00Z"
    assert record["payload"]["missing_refs"][0]["observed_at"] == "2026-10-13T08:00:00Z"
    assert record["payload"]["missing_refs"][0]["reason"] == "2026-10-13T16:00:00+08:00"   # 不是时间字段，不动
    assert draft["payload"]["applicability"]["valid_from"] == "2026-01-01T08:00:00+08:00"  # 不改调用方的草案
