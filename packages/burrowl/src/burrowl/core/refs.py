"""固定引用（FixedRef）：对象身份 + 修订 + 内容指纹 + 定位。引用不会悄悄指向新版。"""
from typing import Any

from .hashing import canonical_hash, is_sha256

TARGET_KINDS = ("owner", "record", "claim", "file")


def make_ref(target_kind: str, target_id: str, *, revision: int | None = None, sha256: str | None = None,
             locator: str = "", relation: str = "references") -> dict[str, Any]:
    return {"target_kind": target_kind, "target_id": target_id, "revision": revision,
            "sha256": sha256, "locator": locator, "relation": relation}


def record_ref(record: dict, *, locator: str = "", relation: str = "references") -> dict[str, Any]:
    """指向某条记录当前修订的固定引用（绑定 record_hash）。"""
    return make_ref("record", record["record_id"], revision=record["revision"], sha256=record["record_hash"],
                    locator=locator, relation=relation)


def is_fixed(ref: dict) -> bool:
    if ref.get("target_kind") == "record":
        return type(ref.get("revision")) is int and ref["revision"] >= 1 and is_sha256(ref.get("sha256"))
    return is_sha256(ref.get("sha256"))


def ref_key(ref: dict) -> str:
    """去重用的身份键：不含 relation，同一目标同一版本同一定位视为同一引用。"""
    return canonical_hash({k: ref.get(k) for k in ("target_kind", "target_id", "revision", "sha256", "locator")})


def ref_uri(owner_id: str, ref: dict) -> str:
    """供外部系统（知识中枢、Markdown 导出）回指的稳定 URI。"""
    uri = f"burrowl://{owner_id}/{ref['target_id']}"
    if ref.get("revision") is not None:
        uri += f"@{ref['revision']}"
    if ref.get("sha256"):
        uri += f"#{ref['sha256'][:16]}"
    return uri
