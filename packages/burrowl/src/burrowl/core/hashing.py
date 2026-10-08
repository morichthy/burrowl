"""规范 JSON 与指纹。所有持久化身份（记录、提交、回执、发布）都用这一套算法。"""
import hashlib
import json
from pathlib import Path
from typing import Any


def _check_json(value: Any) -> None:
    # json.dumps 会把整数键、元组静默转换；跨语言契约里没有这些值，直接拒绝。
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("Canonical JSON object keys must be strings")
            _check_json(child)
    elif isinstance(value, list):
        for child in value:
            _check_json(child)
    elif type(value) not in (str, int, float, bool, type(None)):
        raise ValueError("Canonical hashing accepts JSON values only")


def canonical_bytes(value: Any) -> bytes:
    """UTF-8、键排序、紧凑分隔符、拒绝 NaN；数组顺序与 Unicode 原文保留。"""
    _check_json(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    """流式计算，不把大文件读入内存。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def strict_json_loads(text: str) -> Any:
    """拒绝 NaN/Infinity 等非标准常量，避免读入后无法再规范化。"""
    def reject(constant):
        raise ValueError(f"non-standard JSON constant: {constant}")
    return json.loads(text, parse_constant=reject)
