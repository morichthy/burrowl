"""统一时间格式：RFC3339 UTC，以 Z 结尾，微秒精度。"""
import re
from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], str]

_UTC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def is_utc(value: object) -> bool:
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
