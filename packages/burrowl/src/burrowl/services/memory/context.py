"""记忆服务共享的存储句柄，以及固定引用的解析与回源核对。"""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash, sha256_file
from ...core.records import claim_hash, require_ref
from ...storage.layout import Workspace
from ...storage.memory_store import MemoryStore, tombstones
from ...storage.owners import SENSITIVITY_RANK, Owner, OwnerIndex, OwnerRegistry
from ...storage.sources import SourceRegistry


@dataclass
class Storage:
    """一组存储组件；所有服务共享同一个实例。"""

    ws: Workspace
    store: MemoryStore
    owners: OwnerRegistry
    sources: SourceRegistry

    @classmethod
    def open(cls, root, **store_options) -> "Storage":
        ws = Workspace.open(root)
        return cls(ws, MemoryStore(ws.root, **store_options), OwnerRegistry(ws), SourceRegistry(ws))


@dataclass
class RefResolver:
    """一次操作内的引用解析器：解析结果与回源核对清单仅在本次操作内有效。

    pending：本批正在提交的记录（允许引用同批新建记录）。
    checks：发布前需再次核对的依据（对象 HEAD、文件字节、业务卡指纹）。
    """

    storage: Storage
    index: OwnerIndex
    pending: dict[str, dict] = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)
    _cache: dict[str, Any] = field(default_factory=dict)
    claim_lookup: Any = None  # 可选：claim 解析回调（由复核图提供）
    link_warnings: list[dict] = field(default_factory=list)  # 解析正文链接时的提示（R24 D-09），不进回执

    def __call__(self, ref: dict) -> Any:
        require_ref(ref, "引用")
        key = canonical_hash(ref)
        if key not in self._cache:
            self._cache[key] = self._resolve(ref)
        return self._cache[key]

    def _record_owner(self, record_id: str) -> tuple[Owner, Any]:
        matches, removed = [], False
        for owner in self.index.owners:
            if not owner.persisted:
                continue
            snapshot = self.storage.store.read_snapshot(owner)
            if record_id in snapshot.records:
                matches.append((owner, snapshot))
            removed = removed or record_id in tombstones(snapshot.manifest)
        if not matches and removed:  # R22 D-02：已删除的记录不能被新引用
            raise BurrowlError("UNRESOLVED_REFERENCE", "引用的记录已删除；需要时先用 records_restore 恢复",
                               {"target_id": record_id, "removed": True})
        if len(matches) != 1:
            raise BurrowlError("UNRESOLVED_REFERENCE", "记录引用不存在或身份重复", {"target_id": record_id})
        return matches[0]

    def _resolve(self, ref: dict) -> Any:
        target, kind = ref["target_id"], ref["target_kind"]
        if kind == "record":
            pending = self.pending.get(target)
            if pending and pending["revision"] == ref["revision"]:
                if ref.get("sha256") is not None and ref["sha256"] != pending.get("record_hash"):
                    raise BurrowlError("INVALID_SCHEMA", "带 SHA 的引用必须指向已保存的记录，不能指向同批草案",
                                   {"target_id": target})
                return pending
            owner, snapshot = self._record_owner(target)
            owner.require_readable()
            if snapshot.records[target]["sensitivity"] == "restricted":
                raise BurrowlError("ACCESS_DENIED", "引用记录当前已限制访问", {"target_id": target})
            value = self.storage.store.read_record(owner, target, ref["revision"])
            if value["sensitivity"] == "restricted":
                raise BurrowlError("ACCESS_DENIED", "固定引用修订已限制访问", {"target_id": target})
            if ref.get("sha256") is not None and ref["sha256"] != value["record_hash"]:
                raise BurrowlError("STALE_BASIS", "记录固定版本与指纹不一致", {"target_id": target})
            self.checks.append({"kind": "head", "owner_id": owner.owner_id, "head": snapshot.head})
            return value
        if kind == "file":
            source = self.storage.sources.resolve(target)
            actual = sha256_file(source.path)
            self.checks.append({"kind": "file", "ref": deepcopy(ref), "hash": actual})
            if actual != ref["sha256"]:
                raise BurrowlError("STALE_BASIS", "来源文件已变化", {"target_id": target})
            return {"sha256": actual, "sensitivity": source.sensitivity, "path": source.path}
        if kind == "owner":
            owner = self.index.get(target).require_readable()
            self.checks.append({"kind": "owner", "ref": deepcopy(ref), "hash": owner.fingerprint})
            if owner.fingerprint != ref["sha256"]:
                raise BurrowlError("STALE_BASIS", "对象业务卡已变化", {"target_id": target})
            return {"sha256": owner.fingerprint, "owner_type": owner.owner_type, "native": owner.native,
                    "sensitivity": owner.sensitivity}
        if kind == "claim":
            for record in self.pending.values():  # 同批记录里的结论（链接可以指向同批新写的结论）
                for claim in (record.get("payload") or {}).get("claims", []) or []:
                    if claim.get("claim_id") == target:
                        digest = claim_hash(claim)
                        if digest != ref["sha256"]:
                            raise BurrowlError("STALE_BASIS", "结论内容已变化", {"target_id": target})
                        return {"sha256": digest, "claim": deepcopy(claim), "record": record,
                                "owner_id": record.get("owner_id"), "sensitivity": record.get("sensitivity")}
            if self.claim_lookup is None:
                raise BurrowlError("UNRESOLVED_REFERENCE", "当前操作不支持结论引用", {"target_id": target})
            value = self.claim_lookup(target)
            if value["sha256"] != ref["sha256"]:
                raise BurrowlError("STALE_BASIS", "结论内容已变化", {"target_id": target})
            self.checks.append({"kind": "claim", "ref": deepcopy(ref), "hash": value["sha256"]})
            return value
        raise BurrowlError("INVALID_SCHEMA", "未知引用类型", {"target_kind": kind})

    def recheck(self) -> None:
        """发布前逐项回源：依据在预检与发布之间变化则拒绝发布。对象列表必须重新扫描，不用本次操作复用的结果。"""
        fresh = RefResolver(self.storage, self.storage.owners.scan(fresh=True), claim_lookup=self.claim_lookup)
        for check in {canonical_hash(c): c for c in self.checks}.values():
            if check["kind"] == "head":
                owner = fresh.index.get(check["owner_id"]).require_readable()
                if self.storage.store.read_head(owner).head != check["head"]:
                    raise BurrowlError("STALE_BASIS", "引用对象在提交期间发生变化", {"owner_id": check["owner_id"]})
            elif check["kind"] == "owner_access":
                fresh.index.get(check["owner_id"]).require_readable()
            else:
                fresh(check["ref"])


def max_sensitivity(values: list[str]) -> str:
    return max(values, key=lambda v: SENSITIVITY_RANK.get(v, 3)) if values else "public"
