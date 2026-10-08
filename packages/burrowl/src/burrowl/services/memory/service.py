"""记忆的查看、预检与提交。所有入口（CLI/HTTP/MCP/工作台）共用这一套规则。

提交流程：结构预检 → 解析每个固定引用（权限、版本、指纹）→ 领域规则 →
计算指纹 → 持锁再做一遍 → 发布前回源 → 原子发布 HEAD → 通知检索投影。
内容已保存但索引失败时返回 index_status=pending，不重复创建记录。
"""
import uuid
from copy import deepcopy
from typing import Any

from ...core.clock import Clock, utc_now
from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash, sha256_bytes
from ...core.ports import IndexSink, NullIndex
from ...core.records import content_hash, record_hash, validate_commit_request, validate_record
from ...storage.memory_store import Snapshot
from ...storage.owners import SENSITIVITY_RANK, Owner, OwnerIndex
from . import policy, rules
from .claims import blocked_records
from .context import RefResolver, Storage

SUPPORT_RELATIONS = {"supports", "input"}


def request_hash(request: dict) -> str:
    return canonical_hash({k: v for k, v in request.items() if k not in {"request_id", "dry_run"}})


def _support_targets(value: Any):
    if isinstance(value, dict):
        if value.get("target_kind") == "record" and value.get("relation") in SUPPORT_RELATIONS:
            yield value["target_id"]
        else:
            for key, child in value.items():
                if key != "missing_refs":
                    yield from _support_targets(child)
    elif isinstance(value, list):
        for child in value:
            yield from _support_targets(child)


def _iter_refs(value: Any):
    if isinstance(value, dict):
        if "target_kind" in value and "target_id" in value:
            yield value
        else:
            for key, child in value.items():
                if key != "missing_refs":
                    yield from _iter_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_refs(child)


class MemoryService:
    def __init__(self, storage: Storage, *, index: IndexSink | None = None, clock: Clock = utc_now,
                 claim_lookup_factory=None):
        self.storage = storage
        self.index = index or NullIndex()
        self.clock = clock
        # 复核图在 review 服务中构建；提交含 claim 引用的记录时按需取用。
        self.claim_lookup_factory = claim_lookup_factory

    # ---------- 读取 ----------
    def owners(self) -> OwnerIndex:
        return self.storage.owners.scan()

    def snapshot(self, owner: Owner) -> Snapshot:
        return self.storage.store.read_snapshot(owner)

    def owner_policy(self, owner: Owner, snapshot: Snapshot) -> dict:
        record = snapshot.pointer("policy")
        return policy.resolve(owner.owner_type, record["payload"] if record else None)

    def inspect(self, owner_id: str, *, record_id: str | None = None, revision: int | None = None) -> dict:
        owner = self.owners().get(owner_id).require_readable()
        index = self.owners()
        if record_id:
            record = self.storage.store.read_record(owner, record_id, revision)
            if record["sensitivity"] == "restricted":
                raise BurrowlError("ACCESS_DENIED", "此记录不在当前可读范围")
            if problems := blocked_records(self.storage, [record_id], index).get(record_id):
                raise BurrowlError("ACCESS_DENIED", "此记录的依据链包含受限或不可用的来源", {"problems": problems})
            return {"owner": owner.summary(), "record": record}
        snapshot = self.snapshot(owner)
        readable = [rid for rid, r in snapshot.records.items() if r["sensitivity"] != "restricted"]
        blocked = blocked_records(self.storage, readable, index)
        records = {rid: snapshot.records[rid] for rid in readable if rid not in blocked}
        omitted = [{"record_id": rid, "reason": "依据链包含受限或不可用的来源", "problems": problems}
                   for rid, problems in blocked.items()]
        return {"owner": owner.summary(), "head": snapshot.head, "records": records, "omitted": omitted,
                "policy": self.owner_policy(owner, snapshot), "index": self.index.status(owner.owner_id)}

    # ---------- 写入 ----------
    def _check_request(self, request: dict, *, allow_review: bool = False) -> dict:
        if errors := validate_commit_request(request, allow_review=allow_review):
            raise BurrowlError("INVALID_SCHEMA", "提交请求不符合契约", errors=errors)
        try:
            uuid.UUID(request["request_id"])
        except ValueError as exc:
            raise BurrowlError("INVALID_ARGUMENT", "request_id 必须为 UUID") from exc
        return deepcopy(request)

    def _resolver(self, index: OwnerIndex) -> RefResolver:
        lookup = self.claim_lookup_factory(index) if self.claim_lookup_factory else None
        return RefResolver(self.storage, index, claim_lookup=lookup)

    def prepare(self, request: dict, snapshot: Snapshot, index: OwnerIndex, *,
                allow_review: bool = False) -> tuple[list[dict], list[dict], RefResolver]:
        """计算本批要写入的完整记录（纯计算，可在锁内重复执行）。"""
        owner_id = request["owner_id"]
        owner = index.get(owner_id).require_readable()
        effective = self.owner_policy(owner, snapshot)["values"]
        resolver = self._resolver(index)
        resolver.checks.append({"kind": "owner_access", "owner_id": owner_id})
        now = self.clock()
        ids = self._assign_ids(request, snapshot)
        operations = []
        for operation in request["operations"]:
            rid = ids[operation.get("client_key") or operation["record_id"]]
            draft = self._replace_client_keys(deepcopy(operation["draft"]), ids)
            draft.setdefault("discovery", effective["discovery"])
            if draft.get("owner_id") != owner_id:
                raise BurrowlError("INVALID_SCHEMA", "单批只允许一个归属对象",
                               errors=[{"path": "/owner_id", "message": "归属不匹配"}])
            old = snapshot.records.get(rid)
            if old and draft.get("kind") != old["kind"]:
                raise BurrowlError("INVALID_SCHEMA", "不能改变已有记录的类型", {"record_id": rid})
            resolver.pending[rid] = {**draft, "record_id": rid, "revision": old["revision"] + 1 if old else 1}
            operations.append((operation, rid, draft, old))

        def read_source(ref: dict) -> str:
            source = self.storage.sources.resolve(ref["target_id"])
            raw = source.path.read_bytes()
            if sha256_bytes(raw) != ref["sha256"]:
                raise BurrowlError("STALE_BASIS", "逐字来源读取时发生变化")
            return raw.decode("utf-8-sig")

        changed, results = [], []
        for operation, rid, draft, old in operations:
            record, errors = validate_record(draft, resolver=resolver, read_source=read_source,
                                             allow_review=allow_review)
            if errors:
                raise BurrowlError(errors[0].get("code", "INVALID_SCHEMA"), "记录草案验证失败", errors=errors)
            rules.validate_change(old, record, record_id=rid, resolve=resolver)
            self._check_sensitivity(record, resolver)
            digest = content_hash(record)
            if old and digest == old["content_hash"]:
                record, status = old, "no_change"
            else:
                record.update(record_id=rid, revision=old["revision"] + 1 if old else 1,
                              previous_revision=old["revision"] if old else None, content_hash=digest,
                              created_at=old["created_at"] if old else now,
                              created_by=old["created_by"] if old else request["actor"],
                              updated_at=now, updated_by=request["actor"])
                record["change_reason"] = record.get("change_reason") or record["record_reason"]
                record["record_hash"] = record_hash(record)
                changed.append(record)
                status = "would_update" if old else "would_create"
            resolver.pending[rid] = record
            # record_hash 与现成的固定引用直接给出，AI 引用刚保存的记录时不必再读一次
            results.append({"client_key": operation.get("client_key"), "record_id": rid,
                            "revision": record["revision"], "content_hash": record["content_hash"],
                            "record_hash": record["record_hash"], "status": status,
                            "fixed_ref": {"target_kind": "record", "target_id": rid, "revision": record["revision"],
                                          "sha256": record["record_hash"], "locator": "record",
                                          "relation": "references"}})
        self._reject_support_cycles(resolver.pending)
        return changed, results, resolver

    @staticmethod
    def _assign_ids(request: dict, snapshot: Snapshot) -> dict[str, str]:
        """新记录 ID 由 request_id 与 client_key 确定性派生：预检与提交结果一致，重试也一致。"""
        ids: dict[str, str] = {}
        seen: set[str] = set()
        for operation in request["operations"]:
            if "record_id" in operation:
                rid = operation["record_id"]
                old = snapshot.records.get(rid)
                if not old:
                    raise BurrowlError("NOT_FOUND", "要修订的记录不属于此对象", {"record_id": rid})
                if old["revision"] != operation["expected_revision"]:
                    raise BurrowlError("VERSION_CONFLICT", "记录修订已经变化",
                                   {"record_id": rid, "current_revision": old["revision"]})
                key = rid
            else:
                key = operation["client_key"]
                if key in ids:
                    raise BurrowlError("INVALID_SCHEMA", "单批 client_key 重复", {"client_key": key})
                rid = "MEM-" + str(uuid.uuid5(uuid.UUID(request["request_id"]), f"{request['owner_id']}:{key}"))
            if rid in seen:
                raise BurrowlError("INVALID_SCHEMA", "单批记录 ID 重复", {"record_id": rid})
            seen.add(rid)
            ids[key] = rid
        return ids

    @staticmethod
    def _replace_client_keys(value: Any, ids: dict[str, str]) -> Any:
        """草案中 {"client_key": "x", ...} 形式的批内引用替换为真实记录引用。"""
        if isinstance(value, list):
            return [MemoryService._replace_client_keys(v, ids) for v in value]
        if not isinstance(value, dict):
            return value
        if "client_key" in value:
            if value["client_key"] not in ids or set(value) - {"client_key", "target_kind", "revision", "sha256",
                                                               "locator", "relation"}:
                raise BurrowlError("UNRESOLVED_REFERENCE", "未知或格式错误的批内引用", {"client_key": value["client_key"]})
            return {"target_kind": "record", "target_id": ids[value["client_key"]], "revision": 1, "sha256": None,
                    "locator": value.get("locator", ""), "relation": value.get("relation", "references")}
        return {k: MemoryService._replace_client_keys(v, ids) for k, v in value.items()}

    @staticmethod
    def _check_sensitivity(record: dict, resolver: RefResolver) -> None:
        """每个引用都实际解析；记录敏感级别不得低于其来源。"""
        for ref in _iter_refs(record):
            resolved = resolver(ref)
            level = resolved.get("sensitivity", "internal") if isinstance(resolved, dict) else "internal"
            if SENSITIVITY_RANK.get(level, 3) > SENSITIVITY_RANK[record["sensitivity"]]:
                raise BurrowlError("ACCESS_DENIED", "不能降低来源的敏感级别", {"target_id": ref["target_id"]})

    @staticmethod
    def _reject_support_cycles(pending: dict[str, dict]) -> None:
        """同批记录之间的 supports/input 不能形成自证环（导航环是允许的）。"""
        edges = {rid: set(_support_targets(record)) & pending.keys() for rid, record in pending.items()}
        visited: set[str] = set()
        for start in edges:
            stack, active = [(start, iter(edges[start]))], {start}
            while stack:
                node, children = stack[-1]
                child = next(children, None)
                if child is None:
                    stack.pop()
                    active.discard(node)
                    visited.add(node)
                elif child in active:
                    raise BurrowlError("INVALID_SCHEMA", "supports/input 不能形成自证环")
                elif child not in visited:
                    active.add(child)
                    stack.append((child, iter(edges[child])))

    def validate_draft(self, request: dict, *, allow_review: bool = False) -> dict:
        request = self._check_request(request, allow_review=allow_review)
        index = self.owners()
        owner = index.get(request["owner_id"]).require_readable()
        snapshot = self.snapshot(owner)
        if snapshot.commit_id != request["expected_head"]:
            raise BurrowlError("VERSION_CONFLICT", "预检依据的 HEAD 已变化", {"current_head": snapshot.head})
        _changed, results, resolver = self.prepare(request, snapshot, index, allow_review=allow_review)
        resolver.recheck()
        return {"valid": True, "dry_run": True, "owner_id": owner.owner_id, "head": snapshot.head,
                "record_results": results, "writes": 0}

    def basis_checks(self, basis_heads: dict[str, str | None]) -> list[dict]:
        """材料包依据：列出的对象 HEAD 必须与准备材料时一致，否则拒绝（不能用旧材料覆盖新结果）。"""
        index, checks, changed = self.owners(), [], []
        for owner_id, expected in basis_heads.items():
            owner = index.get(owner_id)
            head = self.snapshot(owner).head if owner.persisted else None
            if (head["commit_id"] if head else None) != expected:
                changed.append({"owner_id": owner_id, "expected": expected,
                                "actual": head["commit_id"] if head else None})
            checks.append({"kind": "head", "owner_id": owner_id, "head": head})
        if changed:
            raise BurrowlError("STALE_BASIS", "材料包依据的对象已有新提交，请重新准备", {"changed_owners": changed})
        return checks

    def commit(self, request: dict, *, allow_review: bool = False, hash_override: str | None = None,
               basis_heads: dict[str, str | None] | None = None) -> dict:
        """提交一批记录。dry_run=true 时只预检。返回存储回执 + 当前索引状态。

        basis_heads：本次写入所依据材料的对象 HEAD；任何一个在提交前变化都会拒绝。
        """
        request = self._check_request(request, allow_review=allow_review)
        if basis_heads:
            self.basis_checks(basis_heads)
        if request.get("dry_run"):
            return self.validate_draft(request, allow_review=allow_review)
        index = self.owners()
        owner = index.get(request["owner_id"]).require_readable()
        digest = hash_override or request_hash(request)
        snapshot = self.snapshot(owner)
        if prior := self.storage.store.find_receipt(owner, snapshot, request["request_id"], digest):
            return self._with_index(prior, notify=False)
        # 先在锁外完整预检：无效输入不会创建任何目录或锁。
        self.validate_draft(request, allow_review=allow_review)
        owner = self.storage.owners.persist(owner, request["actor"])
        holder: dict[str, RefResolver] = {}

        def prepare(current: Snapshot):
            changed, results, resolver = self.prepare(request, current, self.owners(), allow_review=allow_review)
            if basis_heads:
                resolver.checks.extend(self.basis_checks(basis_heads))
            holder["resolver"] = resolver
            return changed, results

        receipt = self.storage.store.commit(
            owner, expected_head=request["expected_head"], request_id=request["request_id"], request_hash=digest,
            prepare=prepare, prepublish=lambda: holder["resolver"].recheck(),
            source_checks=lambda: deepcopy(holder["resolver"].checks))
        return self._with_index(receipt, notify=receipt["save_status"] == "committed")

    def _with_index(self, receipt: dict, *, notify: bool) -> dict:
        """HEAD 已发布之后的索引步骤：任何异常都不能把已保存说成失败。"""
        result = deepcopy(receipt)
        try:
            if notify:
                owner = self.owners().get(result["owner_id"])
                self.index.on_commit(owner.owner_id, self.snapshot(owner).head)
            state = self.index.status(result["owner_id"])
        except Exception as exc:  # noqa: BLE001 - 发布后的边界：保留业务成功
            state = {"index_status": "pending", "details": {"reason": type(exc).__name__}}
        result["index"] = state
        if state["index_status"] not in {"indexed", "not_configured"}:
            result["warnings"] = ["记录已保存；检索索引待补偿（index.reconcile），不要重复提交"]
        return result
