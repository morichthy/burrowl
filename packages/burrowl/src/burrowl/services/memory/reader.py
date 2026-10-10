"""一次请求内的只读集合：对象清单只扫描一次，正文按固定引用读取，返回前统一回源核对。

文稿组装、材料查询、阅读会话共用这一个读取器（旧系统里有两套）。
meter 回调可选：查询/阅读用它把读取字节计入累计预算。
"""
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from ...core.errors import BurrowlError
from ...core.hashing import sha256_bytes, sha256_file
from ...storage.memory_store import Snapshot
from ...storage.owners import Owner, OwnerIndex
from .claims import ClaimGraph, iter_refs
from .context import Storage

Meter = Callable[[str, int], None]


class Reader:
    def __init__(self, storage: Storage, *, index: OwnerIndex | None = None, meter: Meter | None = None):
        self.storage = storage
        self.index = index or storage.owners.scan()
        self.meter = meter or (lambda _kind, _amount: None)
        self.states: dict[str, Snapshot] = {}
        self.record_owner: dict[str, str] = {}
        self.used: set[str] = set()
        self.checked: set[tuple] = set()
        self.file_checks: dict[tuple, tuple[dict, str]] = {}
        self._claims: ClaimGraph | None = None

    # ---------- 读取 ----------
    def owner(self, owner_id: str) -> Owner:
        owner = self.index.find(owner_id)
        if owner is None:
            raise BurrowlError("NOT_FOUND", "对象不存在", {"target_id": owner_id})
        owner.require_readable()
        self.used.add(owner.owner_id)
        return owner

    def state(self, owner_id: str) -> Snapshot:
        owner = self.owner(owner_id)
        if owner.owner_id not in self.states:
            snapshot = self.storage.store.read_snapshot(owner)
            self.states[owner.owner_id] = snapshot
            self.record_owner.update(dict.fromkeys(snapshot.records, owner.owner_id))
        return self.states[owner.owner_id]

    def locate(self, record_id: str) -> str:
        """找出记录属于哪个对象：先查已读快照，再只读各对象的 HEAD 清单（不读正文）。"""
        if record_id in self.record_owner:
            return self.record_owner[record_id]
        found = None
        for owner in self.index.owners:
            if not owner.readable or not owner.persisted or owner.owner_id in self.states:
                continue
            snapshot = self.state(owner.owner_id)
            if record_id in snapshot.records:
                if found:
                    raise BurrowlError("INTEGRITY_ERROR", "重复的记录身份", {"target_id": record_id})
                found = owner.owner_id
        if found is None:
            raise BurrowlError("NOT_FOUND", "记录不存在", {"target_id": record_id})
        return found

    def record(self, ref: dict) -> dict:
        """读取固定修订（revision 为 None 表示当前修订）。权限按当前状态判断。"""
        rid = ref["target_id"]
        owner_id = self.locate(rid)
        current = self.state(owner_id).records[rid]
        if current["sensitivity"] == "restricted":
            raise BurrowlError("ACCESS_DENIED", "记录当前已限制访问", {"target_id": rid})
        revision = ref.get("revision")
        record = current if revision in (None, current["revision"]) else \
            self.storage.store.read_record(self.owner(owner_id), rid, revision)
        if record["sensitivity"] == "restricted":
            raise BurrowlError("ACCESS_DENIED", "该修订已限制访问", {"target_id": rid})
        if ref.get("sha256") and ref["sha256"] != record["record_hash"]:
            raise BurrowlError("STALE_BASIS", "固定记录指纹不匹配", {"target_id": rid})
        return record

    def current(self, record: dict) -> dict:
        return self.state(record["owner_id"]).records[record["record_id"]]

    def claims(self) -> ClaimGraph:
        if self._claims is None:
            self._claims = ClaimGraph(self.storage, self.index)
        return self._claims

    def read_file(self, ref: dict, *, max_bytes: int | None = None) -> bytes:
        """读取已登记来源文件（计入预算）并核对固定指纹。"""
        source = self.storage.sources.resolve(ref["target_id"])
        size = source.path.stat().st_size
        if max_bytes is not None and size > max_bytes:
            raise BurrowlError("BUDGET_EXCEEDED", "来源文件超过读取上限", {"target_id": ref["target_id"]})
        self.meter("read_bytes", size)
        raw = source.path.read_bytes()
        if ref.get("sha256") and sha256_bytes(raw) != ref["sha256"]:
            raise BurrowlError("STALE_BASIS", "固定来源已变化", {"target_id": ref["target_id"]})
        return raw

    # ---------- 依据可用性 ----------
    def check_ref(self, ref: dict) -> None:
        """递归检查依据的当前授权与固定版本。失败时清空缓存，避免留下"看似已验证"的条目。"""
        try:
            self._check(ref)
        except BurrowlError:
            self.checked.clear()
            raise

    def _check(self, ref: dict) -> None:
        key = (ref["target_kind"], ref["target_id"], ref.get("revision"), ref.get("sha256"))
        if key in self.checked:
            return
        self.checked.add(key)
        kind = ref["target_kind"]
        if kind == "record":
            record = self.record(ref)
            for child in iter_refs({"sources": record["sources"], "payload": record["payload"]}):
                self._check(child)
        elif kind == "file":
            source = self.storage.sources.resolve(ref["target_id"])
            actual = sha256_file(source.path)
            self.file_checks[key] = (deepcopy(ref), actual)
            if actual != ref.get("sha256"):
                raise BurrowlError("STALE_BASIS", "固定来源已变化", {"target_id": ref["target_id"]})
        elif kind == "owner":
            owner = self.owner(ref["target_id"])
            if ref.get("sha256") and owner.fingerprint != ref["sha256"]:
                raise BurrowlError("STALE_BASIS", "对象业务卡已变化", {"target_id": ref["target_id"]})
        elif kind == "claim":
            graph = self.claims()
            if any(e["code"] in {"ACCESS_DENIED", "UNSAFE_PATH"} for e in graph.access_errors(ref["target_id"])):
                raise BurrowlError("ACCESS_DENIED", "结论的来源当前不可读取", {"target_id": ref["target_id"]})
            if graph.resolve_claim(ref["target_id"])["sha256"] != ref.get("sha256"):
                raise BurrowlError("STALE_BASIS", "固定结论已变化", {"target_id": ref["target_id"]})
        else:
            raise BurrowlError("INVALID_SCHEMA", "未知引用类型", {"target_kind": kind})

    def try_check(self, refs: list[dict]) -> list[dict]:
        """逐个检查，返回问题列表（不抛出）。"""
        issues = []
        for ref in refs:
            try:
                self.check_ref(ref)
            except BurrowlError as exc:
                item = {"target_id": ref["target_id"], "code": exc.code}
                if item not in issues:
                    issues.append(item)
        return issues

    def heads(self) -> dict[str, str]:
        return {oid: s.commit_id or "none" for oid, s in self.states.items()}

    def recheck(self) -> None:
        """返回前回源：本次读过的对象业务卡、HEAD 与来源文件都必须未变化。对象列表重新扫描，不用本次操作复用的结果。"""
        fresh = self.storage.owners.scan(fresh=True)
        for owner_id in self.used:
            before, after = self.index.get(owner_id), fresh.find(owner_id)
            if after is None or after.fingerprint != before.fingerprint or not after.readable:
                raise BurrowlError("STALE_BASIS", "读取期间对象登记发生变化", {"owner_id": owner_id})
            if owner_id in self.states and self.storage.store.read_head(after).head != self.states[owner_id].head:
                raise BurrowlError("STALE_BASIS", "读取期间对象有新提交", {"owner_id": owner_id})
        for ref, digest in self.file_checks.values():
            if sha256_file(self.storage.sources.resolve(ref["target_id"]).path) != digest:
                raise BurrowlError("STALE_BASIS", "读取期间来源发生变化", {"target_id": ref["target_id"]})


def fixed_ref(record: dict, relation: str = "references") -> dict[str, Any]:
    return {"target_kind": "record", "target_id": record["record_id"], "revision": record["revision"],
            "sha256": record["record_hash"], "locator": "", "relation": relation}


def ref_identity(ref: dict) -> dict[str, Any]:
    """清单中只放身份字段：自由 locator 可能夹带原文。"""
    return {k: ref.get(k) for k in ("target_kind", "target_id", "revision", "sha256", "relation")}
