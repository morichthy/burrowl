"""记忆的查看、预检与提交。所有入口（CLI/HTTP/MCP/工作台）共用这一套规则。

提交流程：结构预检 → 解析每个固定引用（权限、版本、指纹）→ 领域规则 →
计算指纹 → 持锁再做一遍 → 发布前回源 → 原子发布 HEAD → 通知检索投影。
内容已保存但索引失败时返回 index_status=pending，不重复创建记录。
"""
import uuid
from copy import deepcopy
from typing import Any

from ...core import contract as record_contract
from ...core import links as link_syntax
from ...core.clock import Clock, utc_now
from ...core.errors import BurrowlError, rebase, schema_error
from ...core.hashing import canonical_hash, sha256_bytes
from ...core.ports import IndexSink, NullIndex
from ...core.records import content_hash, record_hash, validate_commit_request, validate_record
from ...storage.memory_store import Snapshot, tombstones
from ...storage.owners import SENSITIVITY_RANK, Owner, OwnerIndex
from . import concepts, policy, rules, vocabulary
from .claims import ClaimGraph, blocked_records
from .context import RefResolver, Storage
from .links import LinkResolver
from .removal import Removal

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
                 claim_lookup_factory=None, concept_owner=None):
        self.storage = storage
        self.index = index or NullIndex()
        self.clock = clock
        # 复核图在 review 服务中构建；提交含 claim 引用的记录时按需取用。
        self.claim_lookup_factory = claim_lookup_factory
        # 概念库对象 ID 的读取函数（来自配置 graph.concept_owner_id，R12 D-12）；None 表示未配置
        self.concept_owner = concept_owner or (lambda: None)
        self.removal = Removal(self)  # R22：删除与恢复记录、对象回收站

    # ---------- 读取 ----------
    def owners(self) -> OwnerIndex:
        return self.storage.owners.scan()

    def snapshot(self, owner: Owner) -> Snapshot:
        return self.storage.store.read_snapshot(owner)

    def owner_policy(self, owner: Owner, snapshot: Snapshot) -> dict:
        record = snapshot.pointer("policy")
        return policy.resolve(owner.owner_type, record["payload"] if record else None)

    def inspect(self, owner_id: str, *, record_id: str | None = None, revision: int | None = None) -> dict:
        index = self.owners()
        owner = index.get(owner_id).require_readable()
        if record_id:
            record = self.storage.store.read_record(owner, record_id, revision)
            if record["sensitivity"] == "restricted":
                raise BurrowlError("ACCESS_DENIED", "此记录不在当前可读范围")
            if problems := blocked_records(self.storage, [record_id], index).get(record_id):
                raise BurrowlError("ACCESS_DENIED", "此记录的依据链包含受限或不可用的来源", {"problems": problems})
            return {"owner": owner.summary(), "record": record}
        snapshot = self.snapshot(owner)
        readable = [rid for rid, r in snapshot.records.items() if r["sensitivity"] != "restricted"]
        # 依据链过滤与结论徽标共用一张复核图：每建一张都要读入全部对象
        graph = ClaimGraph(self.storage, index)
        blocked = blocked_records(self.storage, readable, index, graph)
        records = {rid: snapshot.records[rid] for rid in readable if rid not in blocked}
        omitted = [{"record_id": rid, "reason": "依据链包含受限或不可用的来源", "problems": problems}
                   for rid, problems in blocked.items()]
        # R12 T-P2.6：每条结论的复核徽标、有效成熟度与派生统计（派生数据，随时可重算）
        claims = {cid: {"record_id": record["record_id"], "badge": graph.badge(cid), **graph.maturity(cid),
                        "stats": graph.stats(cid)}
                  for cid, (_claim, record) in sorted(graph.claims.items()) if record["record_id"] in records}
        return {"owner": owner.summary(), "head": snapshot.head, "records": records, "omitted": omitted,
                "claims": claims, "policy": self.owner_policy(owner, snapshot),
                "index": self.index.status(owner.owner_id)}

    # ---------- 写入 ----------
    def _check_request(self, request: dict, *, allow_review: bool = False) -> dict:
        if errors := validate_commit_request(request, allow_review=allow_review):
            raise BurrowlError("INVALID_SCHEMA", "提交请求不符合契约", errors=errors)
        try:
            uuid.UUID(request["request_id"])
        except ValueError as exc:
            raise BurrowlError("INVALID_ARGUMENT", "request_id 必须为 UUID") from exc
        request = deepcopy(request)
        # R20 D-01：草案的 owner_id 就是提交的 owner_id，可以省略；写了别的对象才报错（一次提交只写一个对象）
        owner_id = request["owner_id"]
        wrong = [schema_error(f"/operations/{i}/draft/owner_id",
                              f"第 {i + 1} 条草案写的是 {written}，本次提交的对象是 {owner_id}；"
                              "草案的 owner_id 可以省略，写了就必须与提交的 owner_id 相同", expected=[owner_id])
                 for i, operation in enumerate(request["operations"])
                 if (written := operation["draft"].setdefault("owner_id", owner_id)) != owner_id]
        if wrong:
            raise BurrowlError("INVALID_SCHEMA", "草案的归属对象与本次提交不一致（一次提交只写一个对象）",
                               {"owner_id": owner_id}, errors=wrong)
        return request

    @staticmethod
    def _target(index: OwnerIndex, owner_id: str) -> Owner:
        """提交的归属对象：必须可读，且不是 Run（R14 D-01：Run 是执行证据，知识写到上级或合适的知识对象）。"""
        owner = index.get(owner_id).require_readable()
        if owner.owner_type == "run":
            raise BurrowlError("INVALID_ARGUMENT", "Run 不保存记忆记录；把结论写到上级对象或合适的知识对象，并引用该 Run",
                               {"owner_id": owner_id, "parent_id": (p.owner_id if (p := index.run_parent(owner)) else None)})
        return owner

    def _resolver(self, index: OwnerIndex) -> RefResolver:
        lookup = self.claim_lookup_factory(index) if self.claim_lookup_factory else None
        return RefResolver(self.storage, index, claim_lookup=lookup)

    def prepare(self, request: dict, snapshot: Snapshot, index: OwnerIndex, *,
                allow_review: bool = False) -> tuple[list[dict], list[dict], RefResolver]:
        """计算本批要写入的完整记录（纯计算，可在锁内重复执行）。"""
        owner_id = request["owner_id"]
        owner = index.get(owner_id).require_readable()
        resolved = self.owner_policy(owner, snapshot)
        resolver = self._resolver(index)
        resolver.checks.append({"kind": "owner_access", "owner_id": owner_id})
        now = self.clock()
        ids = self._assign_ids(request, snapshot)
        operations = []
        for operation in request["operations"]:
            rid = ids[operation.get("client_key") or operation["record_id"]]
            draft = self._replace_client_keys(deepcopy(operation["draft"]), ids)
            previous = snapshot.records.get(rid)
            if "discovery" not in draft:
                # R19 D-02：修订沿用上一修订的可见范围，默认值变化不能静默改变已有记录；新记录按 D-01 取默认
                draft["discovery"] = previous["discovery"] if previous else policy.discovery_for(draft.get("kind"),
                                                                                                 resolved)
            if "sensitivity" not in draft:
                # R20 D-01：新记录默认 internal；修订沿用原值，不写就不会把 confidential 悄悄降成 internal
                draft["sensitivity"] = previous["sensitivity"] if previous else "internal"
            old = snapshot.records.get(rid)
            if old and draft.get("kind") != old["kind"]:
                raise BurrowlError("INVALID_SCHEMA", "不能改变已有记录的类型", {"record_id": rid})
            resolver.pending[rid] = {**draft, "record_id": rid, "revision": old["revision"] + 1 if old else 1}
            operations.append((operation, rid, draft, old))
        self._assign_handles(operations, snapshot)
        for _operation, rid, draft, _old in operations:
            resolver.pending[rid]["handle"] = draft["handle"]
        link_resolver = LinkResolver(self.storage, index, owner, snapshot.records, resolver.pending,
                                     self.concept_owner())

        def read_source(ref: dict) -> str:
            source = self.storage.sources.resolve(ref["target_id"])
            raw = source.path.read_bytes()
            if sha256_bytes(raw) != ref["sha256"]:
                raise BurrowlError("STALE_BASIS", "逐字来源读取时发生变化")
            return raw.decode("utf-8-sig")

        changed, results = [], []
        positions = {id(operation): i for i, operation in enumerate(request["operations"])}
        for operation, rid, draft, old in operations:
            record, errors = validate_record(draft, resolver=resolver, read_source=read_source,
                                             allow_review=allow_review)
            if errors:
                raise self._draft_error(errors, positions[id(operation)], draft.get("kind"))
            try:
                rules.validate_change(old, record, record_id=rid, resolve=resolver)
                # R12 T-P2.2：解析正文链接并固定版本；没有链接时省略 links（D-27，保持旧记录指纹）
                if raw := link_resolver.raw_id_errors(record, old):  # R21 D-10：正文中的长编号，给出可直接替换的写法
                    raise BurrowlError("UNREADABLE_REF", "正文里有长编号或指纹；按 hint 改写成短名链接后重新提交",
                                       errors=raw)
                resolved = link_resolver.resolve(record, record_id=rid, previous=old.get("links") if old else None,
                                                 repin=bool(operation.get("repin")))
                self._check_sensitivity(record, resolver)
            except BurrowlError as exc:  # R20 D-05：规则层的路径相对草案，补成从提交参数根算起
                exc.errors = rebase(exc.errors, "", f"/operations/{positions[id(operation)]}/draft")
                raise
            if resolved:
                record["links"] = resolved
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
                            "handle": record.get("handle"),
                            "unresolved_concepts": [x["text"] for x in record.get("links", [])
                                                    if x["status"] == "unresolved_concept"],
                            "revision": record["revision"], "content_hash": record["content_hash"],
                            "record_hash": record["record_hash"], "status": status,
                            "fixed_ref": {"target_kind": "record", "target_id": rid, "revision": record["revision"],
                                          "sha256": record["record_hash"], "locator": "record",
                                          "relation": "references"}})
        self._reject_support_cycles(resolver.pending)
        if owner_id == self.concept_owner():  # R12 T-P2.4：概念库内有效概念的名称与别名唯一，合并/拆分目标有效
            concepts.check_library({**snapshot.records, **resolver.pending}, {r["record_id"] for r in changed})
        return changed, results, resolver

    @staticmethod
    def _draft_error(errors: list[dict], position: int, kind: object) -> BurrowlError:
        """R20 D-05：草案错误一次列全，路径从工具参数根算起，缺字段时附上该字段的契约说明。"""
        notes = record_contract.field_notes(kind) if isinstance(kind, str) else {}
        base = f"/operations/{position}/draft"
        detailed = []
        for error in errors:
            field = error["path"].split("/")[1] if error["path"].count("/") == 1 else None
            note = notes.get(field) if field and error["message"].startswith("缺少必填字段") else None
            detailed.append({**error, "path": base + error["path"],
                             "message": error["message"] + (f"：{note}" if note else "")})
        hint = (f"字段要求与示例用 memory_contract(\"{kind}\") 查询" if isinstance(kind, str)
                else "kind 写记录类型，用 memory_kinds 查看可用类型")
        return BurrowlError(errors[0].get("code", "INVALID_SCHEMA"), f"第 {position + 1} 条草案没有通过校验",
                            {"kind": kind, "hint": hint}, errors=detailed)

    @staticmethod
    def _assign_handles(operations: list, snapshot: Snapshot) -> None:
        """R12 D-28 / T-P2.3：handle 在对象内唯一、创建后不可修改；未提供时按标题生成（冲突追加 -2、-3）。
        旧记录没有 handle 时，在它下一次修订时补上（handle 不进内容指纹，不会让复核失效）。"""
        revising = {rid for _op, rid, _d, old in operations if old}
        taken: dict[str, str] = {}  # 规范形 → 占用它的 record_id
        for rid, record in snapshot.records.items():
            if rid not in revising or record.get("handle"):
                handle = record.get("handle") or link_syntax.make_handle(record["title"])
                taken.setdefault(link_syntax.normalize(handle), rid)
        explicit = [(op, rid, draft, old) for op, rid, draft, old in operations if "handle" in draft]
        counters: dict[str, int] = {}  # 生成 handle 的后缀续号，taken 在本循环中只增不减
        for _op, rid, draft, old in explicit + [x for x in operations if "handle" not in x[2]]:
            if old and old.get("handle"):
                if draft.setdefault("handle", old["handle"]) != old["handle"]:
                    raise BurrowlError("INVALID_TRANSITION", "handle 创建后不能修改",
                                       {"record_id": rid, "handle": old["handle"]})
                continue
            if "handle" in draft:
                owner_of = taken.get(link_syntax.normalize(draft["handle"]))
                if owner_of not in (None, rid):
                    raise BurrowlError("INVALID_SCHEMA", "handle 在本对象内已被使用",
                                       {"handle": draft["handle"], "record_id": owner_of},
                                       errors=[schema_error("/handle", f"handle {draft['handle']} 已被 {owner_of} 使用，"
                                                                       "换一个或不写（按标题自动生成）")])
            else:
                # taken 的键已是规范形：按需判断占用（排除本记录自己），批量导入时保持线性
                draft["handle"] = link_syntax.pick_handle(link_syntax.make_handle(draft.get("title", "")),
                                                          lambda key, rid=rid: taken.get(key, rid) != rid,
                                                          counters)
            taken[link_syntax.normalize(draft["handle"])] = rid

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
                    if rid in tombstones(snapshot.manifest):  # R22：已删除的记录要先恢复
                        raise BurrowlError("NOT_FOUND", "要修订的记录已删除；先用 records_restore 恢复",
                                           {"record_id": rid, "removed": True})
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
        owner = self._target(index, request["owner_id"])
        snapshot = self.snapshot(owner)
        if snapshot.commit_id != request["expected_head"]:
            raise BurrowlError("VERSION_CONFLICT", "预检依据的 HEAD 已变化", {"current_head": snapshot.head})
        _changed, results, resolver = self.prepare(request, snapshot, index, allow_review=allow_review)
        resolver.recheck()
        result = {"valid": True, "dry_run": True, "owner_id": owner.owner_id, "head": snapshot.head,
                  "record_results": results, "writes": 0}
        if warnings := vocabulary.relation_warnings(request["operations"]):  # R19 D-09：只提示，不拒绝
            result["warnings"] = warnings
        return result

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
        owner = self._target(index, request["owner_id"])
        digest = hash_override or request_hash(request)
        snapshot = self.snapshot(owner)
        warnings = vocabulary.relation_warnings(request["operations"])  # R19 D-09：只提示，不拒绝
        if prior := self.storage.store.find_receipt(owner, snapshot, request["request_id"], digest):
            return self._with_index(prior, notify=False, warnings=warnings)
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
            source_checks=lambda: deepcopy(holder["resolver"].checks), attribution=request.get("attribution"))
        return self._with_index(receipt, notify=receipt["save_status"] == "committed", warnings=warnings)

    def _with_index(self, receipt: dict, *, notify: bool, warnings: list | None = None) -> dict:
        """HEAD 已发布之后的索引步骤：任何异常都不能把已保存说成失败。"""
        result = deepcopy(receipt)
        try:
            if notify:
                owner = self.owners().get(result["owner_id"])
                self.index.on_commit(owner.owner_id, self.snapshot(owner).head)
            state = self.index.status(result["owner_id"])
        except Exception as exc:  # noqa: BLE001 - 发布后的边界：保留业务成功
            # R20 D-06：附一句摘要，AI 能据此判断要不要告诉用户（例如模型文件缺失）；不带堆栈
            state = {"index_status": "pending",
                     "details": {"reason": type(exc).__name__, "summary": str(exc).strip()[:200]}}
        result["index"] = state
        notes = list(warnings or [])
        if state["index_status"] not in {"indexed", "not_configured"}:
            notes.append({"code": "INDEX_PENDING", "message": "记录已保存；检索索引待补偿",
                          "action": "不要重复提交；稍后调用 index_reconcile 补齐，仍失败时把 index.details 告诉用户"})
        if notes:
            result["warnings"] = notes
        return result
