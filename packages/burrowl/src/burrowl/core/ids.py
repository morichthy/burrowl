"""服务端生成的身份。持久化文件名只来自这些身份，从不接受调用者给的路径。"""
import uuid

from .errors import BurrowlError

_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def safe_segment(value: object) -> str:
    """校验可作为单个文件名片段的身份；拒绝路径分隔符与特殊字符。"""
    if not isinstance(value, str) or not value or any(c not in _SAFE for c in value):
        raise BurrowlError("UNSAFE_PATH", "规范 ID 不能包含路径或特殊字符", {"value": str(value)[:80]})
    return value


def is_uuid(value: object) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True
