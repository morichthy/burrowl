"""统一时间格式：RFC3339 UTC，以 Z 结尾，微秒精度。

保存的时间一律是 UTC、以 Z 结尾：字符串顺序就是时间顺序（关键操作记录、工作台重启都按字符串比较），
同一时刻只有一种写法（记录指纹不因时区写法而变）。AI 写入时可以带任意时区，由 to_utc 换算；
没有时区的（只写日期、只写到本地时刻）无法换算，照旧拒绝。
"""
import re
from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], str]

_UTC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z")
_ZONED = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?[+-]\d\d:\d\d")
TIME_KEYS = {"valid_from", "valid_until"}  # 另外名称以 _at 结尾的字段都是时间
TIME_EXAMPLES = ["2026-10-13T16:00:00+08:00", "2026-10-13T08:00:00Z"]
TIME_RULE = "时间要带时区（如 2026-10-13T16:00:00+08:00 或 2026-10-13T08:00:00Z），保存时统一换算为 UTC"


def is_time_key(name: object) -> bool:
    return isinstance(name, str) and (name.endswith("_at") or name in TIME_KEYS)


def to_utc(value: object) -> object:
    """带时区的 RFC3339 时间换算成以 Z 结尾的 UTC；已是 UTC 的原样返回（已有记录的指纹不变），
    其他值（null、只写日期、没有时区）原样返回，由校验报错。"""
    if not isinstance(value, str) or is_utc(value) or not _ZONED.fullmatch(value):
        return value
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    spec = "microseconds" if "." in value else "seconds"
    return moment.astimezone(UTC).isoformat(timespec=spec).replace("+00:00", "Z")


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
