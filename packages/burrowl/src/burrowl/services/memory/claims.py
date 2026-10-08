"""结论（claim）与复核（review）的只读图：复核状态、证据有效性、来源可见性、正式投影。

claim 写在 narrative / experience / overview 记录的 payload.claims 中；
复核是独立的不可变 review 记录，同时绑定所在记录的 content_hash 与 claim 本身的指纹，
任一变化即"复核失效"（STALE_BASIS）。

有效性只沿 supports / input 依赖传递；导航关系（references、analogous_to…）不构成科学支持。
图在构造时读取全部可读对象的当前快照，只在本次操作内有效。
"""
from collections import deque
from copy import deepcopy
from typing import Any

from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash, sha256_file
from ...core.jsonschema import validate
from ...core.records import validate_record
from ...core.schema import defs
from ...storage.owners import OwnerIndex
from .context import Storage

DEPENDENT = {"supports", "input"}
STATES = ("not-reviewed", "accepted", "disputed", "retracted", "superseded")
UNSAFE = {"disputed", "retracted", "superseded"}
CLAIM_KINDS = {"narrative", "experience", "overview"}
BLOCKING = {"ACCESS_DENIED", "UNSAFE_PATH"}  # 依据链上出现这些问题时，派生内容也不能交付


def blocked_records(storage: Storage, record_ids, index: OwnerIndex | None = None) -> dict[str, list[dict]]:
    """依据链上有受限或不安全来源的记录（record_id → 问题列表）。用于读取路径过滤，防止派生文本外泄。"""
    ids = list(record_ids)
    if not ids:
        return {}
    graph = ClaimGraph(storage, index)
    found = {}
    for rid in ids:
        problems = [e for e in graph.access_errors(rid) if e["code"] in BLOCKING]
        if problems:
            found[rid] = problems
    return found


def issue(code: str, target_id: str, message: str, path: list[str] | None = None) -> dict:
    return {"code": code, "target_id": target_id, "message": message, "path": path or [target_id]}


def iter_refs(value: Any):
    """只产出声明的引用；missing_refs 中的未解析描述不授予任何证据。"""
    if isinstance(value, dict):
        if "target_kind" in value:
            yield value
        else:
            for key, item in value.items():
                if key != "missing_refs":
                    yield from iter_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_refs(item)


def claim_hash(claim: dict) -> str:
    return canonical_hash(claim)


class ClaimGraph:
    def __init__(self, storage: Storage, index: OwnerIndex | None = None):
        self.storage = storage
        self.index = index or storage.owners.scan()
        self.records: dict[str, dict] = {}
        self.claims: dict[str, tuple[dict, dict]] = {}
        self.reviews: dict[str, dict] = {}
        self.heads: dict[str, dict | None] = {}
        self.basis_checks: list[dict] = []
        self._revisions: dict[tuple[str, int], dict] = {}
        self._sources: dict[str, BurrowlError | None] = {}
        for owner in self.index.owners:
            if not owner.persisted:
                continue
            snapshot = storage.store.read_snapshot(owner)
            self.heads[owner.owner_id] = snapshot.head
            self.basis_checks.append({"kind": "head", "owner_id": owner.owner_id, "head": snapshot.head})
            for rid, record in snapshot.records.items():
                if rid in self.records:
                    raise BurrowlError("INTEGRITY_ERROR", "重复的记录身份", {"target_id": rid})
                self.records[rid] = record
                if record["kind"] in CLAIM_KINDS:
                    for claim in record["payload"].get("claims", []):
                        if claim["claim_id"] in self.claims:
                            raise BurrowlError("INTEGRITY_ERROR", "重复的结论身份", {"target_id": claim["claim_id"]})
                        self.claims[claim["claim_id"]] = (claim, record)
                elif record["kind"] == "review":
                    cid = record["payload"]["target_claim_id"]
                    if cid in self.reviews:
                        raise BurrowlError("INTEGRITY_ERROR", "同一结论有多条当前复核", {"target_id": cid})
                    self.reviews[cid] = record

    # ---------- 解析 ----------
    def resolve_claim(self, claim_id: str) -> dict:
        if claim_id not in self.claims:
            raise BurrowlError("UNRESOLVED_REFERENCE", "结论不存在", {"target_id": claim_id})
        claim, record = self.claims[claim_id]
        self.index.get(record["owner_id"]).require_readable()
        if record["sensitivity"] == "restricted":
            raise BurrowlError("ACCESS_DENIED", "结论不在可读范围", {"target_id": claim_id})
        return {"sha256": claim_hash(claim), "claim": deepcopy(claim), "record": deepcopy(record),
                "owner_id": record["owner_id"], "sensitivity": record["sensitivity"]}

    def _reference(self, ref: dict) -> str | None:
        """解析一个固定依据，返回图节点身份；文件是叶子，返回 None。"""
        all_defs = defs()
        if errors := validate(ref, all_defs["Ref"], all_defs):
            raise BurrowlError("INVALID_SCHEMA", "依据引用不完整", errors=errors)
        target, kind = ref["target_id"], ref["target_kind"]
        if kind == "record":
            record = self.records.get(target)
            if record is None:
                raise BurrowlError("UNRESOLVED_REFERENCE", "引用的记录不存在", {"target_id": target})
            if record["sensitivity"] == "restricted":
                raise BurrowlError("ACCESS_DENIED", "引用的记录不在可读范围", {"target_id": target})
            if record["revision"] != ref["revision"] or (ref["sha256"] is not None
                                                        and ref["sha256"] != record["record_hash"]):
                raise BurrowlError("STALE_BASIS", "引用的记录已有新修订", {"target_id": target})
            return target
        if kind == "claim":
            if self.resolve_claim(target)["sha256"] != ref["sha256"]:
                raise BurrowlError("STALE_BASIS", "引用的结论内容已变化", {"target_id": target})
            return target
        if kind == "file":
            source = self.storage.sources.resolve(target)
            actual = sha256_file(source.path)
            if actual != ref["sha256"]:
                raise BurrowlError("STALE_BASIS", "来源文件字节已变化", {"target_id": target})
            self.basis_checks.append({"kind": "file", "ref": deepcopy(ref), "hash": actual})
            return None
        owner = self.index.get(target).require_readable()
        if owner.fingerprint != ref["sha256"]:
            raise BurrowlError("STALE_BASIS", "对象业务卡已变化", {"target_id": target})
        return target

    # ---------- 复核与有效性 ----------
    def review_state(self, claim_id: str, scope: str | None = None) -> tuple[str, list[dict]]:
        """返回 (已存复核状态, 该结论自身的问题)。"""
        claim, record = self.claims[claim_id]
        review = self.reviews.get(claim_id)
        if review is None:
            return "not-reviewed", [issue("EVIDENCE_INELIGIBLE", claim_id, "结论尚未复核")]
        p, errors = review["payload"], []
        bindings = [r for r in review["sources"] if r.get("target_kind") == "claim" and r.get("target_id") == claim_id]
        if p["target_content_hash"] != record["content_hash"] or len(bindings) != 1 \
                or bindings[0].get("sha256") != claim_hash(claim):
            errors.append(issue("STALE_BASIS", claim_id, "复核与结论或其所在记录的当前内容不再一致"))
        if p["state"] != "accepted":
            errors.append(issue("EVIDENCE_INELIGIBLE", claim_id, "复核状态为 " + p["state"]))
        else:
            wanted = scope if scope is not None else claim["scope"]
            if not p["scope"] or p["scope"] != claim["scope"] or p["scope"] != wanted:
                errors.append(issue("EVIDENCE_INELIGIBLE", claim_id, "复核范围与请求范围不一致"))
            if not any(r.get("relation") in DEPENDENT for r in p["evidence_refs"]):
                errors.append(issue("EVIDENCE_INELIGIBLE", claim_id, "复核没有支持性证据"))
        return p["state"], errors

    def analyze(self, target: str, scope: str | None = None) -> list[dict]:
        """沿 supports/input 依赖迭代检查；菱形依赖不重复计算，依赖环判为无效。"""
        queue, paths, deps, local = deque([target]), {target: [target]}, {}, {}
        while queue:
            current = queue.popleft()
            if current in deps:
                continue
            deps[current], local[current], refs = set(), [], []
            if current in self.claims:
                claim, record = self.claims[current]
                _state, failures = self.review_state(current, scope if current == target else None)
                local[current].extend(failures)
                if not any(r.get("relation") in DEPENDENT for r in claim["evidence_refs"]):
                    local[current].append(issue("EVIDENCE_INELIGIBLE", current, "结论没有支持性证据"))
                refs = list(claim["evidence_refs"]) + list(record["sources"])
                if current in self.reviews:
                    refs += self.reviews[current]["payload"]["evidence_refs"]
            elif current in self.records:
                record = self.records[current]
                refs = list(record["sources"])
                if record["kind"] == "source":
                    refs.append(dict(record["payload"]["source_ref"], relation="input"))
                elif record["kind"] in CLAIM_KINDS:
                    deps[current].update(c["claim_id"] for c in record["payload"]["claims"])
                    if not record["payload"]["claims"]:
                        local[current].append(issue("EVIDENCE_INELIGIBLE", current, "记录中没有可独立复核的结论"))
                else:
                    refs += list(iter_refs(record["payload"]))
            elif self.index.find(current):
                deps[current].update(cid for cid, (_c, r) in self.claims.items() if r["owner_id"] == current)
                if not deps[current]:
                    local[current].append(issue("EVIDENCE_INELIGIBLE", current, "对象中没有可独立复核的结论"))
            else:
                local[current].append(issue("UNRESOLVED_REFERENCE", current, "依据目标不存在"))
            for ref in refs:
                if ref.get("relation") not in DEPENDENT:
                    continue
                try:
                    if dependency := self._reference(ref):
                        deps[current].add(dependency)
                except BurrowlError as exc:
                    local[current].append(issue(exc.code, ref["target_id"], exc.message))
                except OSError:
                    local[current].append(issue("UNRESOLVED_REFERENCE", ref["target_id"], "依据来源无法读取"))
            for dependency in sorted(deps[current]):
                paths.setdefault(dependency, paths[current] + [dependency])
                if dependency not in deps:
                    queue.append(dependency)
        pending = {node: set(edges) for node, edges in deps.items()}
        while leaves := {node for node, edges in pending.items() if not edges}:
            pending = {node: edges - leaves for node, edges in pending.items() if node not in leaves}
        for node in pending:
            local[node].append(issue("EVIDENCE_INELIGIBLE", node, "支持依据中存在循环"))
        output, seen = [], set()
        for node in sorted(local):
            for failure in local[node]:
                key = (failure["code"], failure["target_id"], failure["message"])
                if key not in seen:
                    seen.add(key)
                    path = list(reversed(paths[node]))
                    if failure["target_id"] != node:
                        path.insert(0, failure["target_id"])
                    output.append(dict(failure, path=path))
        return output

    def claim_state(self, claim_id: str, scope: str | None = None) -> dict:
        value = self.resolve_claim(claim_id)
        state, own = self.review_state(claim_id, scope)
        errors = self.analyze(claim_id, scope or value["claim"]["scope"])
        stale = any(e["code"] == "STALE_BASIS" and e["target_id"] == claim_id for e in own)
        blocked = any(e["target_id"] != claim_id for e in errors)
        return {"claim_id": claim_id, "owner_id": value["owner_id"], "record_id": value["record"]["record_id"],
                "statement": value["claim"]["statement"], "scope": value["claim"]["scope"],
                "review_state": state, "stale": stale, "upstream_blocked": blocked,
                "effective_validity": not errors, "errors": errors, "sha256": value["sha256"]}

    def badge(self, claim_id: str) -> str:
        """供界面与检索结果使用的单一徽标：accepted / not-reviewed / disputed / … / stale。"""
        state, own = self.review_state(claim_id)
        if state != "not-reviewed" and any(e["code"] == "STALE_BASIS" for e in own):
            return "stale"
        return state

    # ---------- 来源可见性 ----------
    def _revision(self, record_id: str, revision: int | None) -> dict:
        current = self.records.get(record_id)
        if current is None:
            raise BurrowlError("UNRESOLVED_REFERENCE", "来源记录不存在", {"target_id": record_id})
        owner = self.index.get(current["owner_id"])
        if current["sensitivity"] == "restricted" or not owner.readable:
            raise BurrowlError("ACCESS_DENIED", "来源记录不在可读范围", {"target_id": record_id})
        if revision is None or revision == current["revision"]:
            return current
        key = (record_id, revision)
        if key not in self._revisions:
            self._revisions[key] = self.storage.store.read_record(owner, record_id, revision)
        return self._revisions[key]

    def _source_failure(self, source_id: str) -> BurrowlError | None:
        """来源能否解析；同一张图（一次操作）内只解析一次。"""
        if source_id not in self._sources:
            try:
                self.storage.sources.resolve(source_id)
                self._sources[source_id] = None
            except BurrowlError as exc:
                self._sources[source_id] = exc
        return self._sources[source_id]

    def access_errors(self, target_id: str, revision: int | None = None) -> list[dict]:
        """沿全部声明引用检查可见性（含背景、类比）：限制来源的派生文本也不能外泄。"""
        queue = deque([({"target_id": target_id, "revision": revision}, [target_id])])
        visited, errors = set(), []
        while queue:
            pinned, path = queue.popleft()
            current = pinned["target_id"]
            key = (current, pinned.get("revision"), pinned.get("sha256"))
            if key in visited:
                continue
            visited.add(key)
            if current in self.claims:
                _claim, record = self.claims[current]
                queue.append(({"target_id": record["record_id"], "revision": record["revision"]},
                              path + [record["record_id"]]))
                continue
            if current in self.records:
                try:
                    record = self._revision(current, pinned.get("revision"))
                except BurrowlError as exc:
                    errors.append(issue(exc.code, current, exc.message, list(reversed(path))))
                    continue
                for ref in iter_refs({"sources": record["sources"], "payload": record["payload"]}):
                    if ref["target_kind"] == "file":
                        if failure := self._source_failure(ref["target_id"]):
                            errors.append(issue(failure.code, ref["target_id"], failure.message,
                                                [ref["target_id"], *reversed(path)]))
                    else:
                        queue.append((ref, path + [ref["target_id"]]))
            elif owner := self.index.find(current):
                if not owner.readable:
                    errors.append(issue("ACCESS_DENIED", current, "对象不在可读范围", list(reversed(path))))
            else:
                errors.append(issue("UNRESOLVED_REFERENCE", current, "来源目标不存在", list(reversed(path))))
        return errors

    # ---------- 复核草案 ----------
    def prepare_review(self, request: dict) -> dict:
        """生成可信的复核记录草案（服务在锁内会再调用一次）。"""
        allowed = {"request_id", "expected_head", "dry_run", "actor", "target_claim_id", "state", "reason", "scope",
                   "evidence_refs", "replacement_claim_id", "expected_content_hash"}
        if set(request) - allowed:
            raise BurrowlError("INVALID_SCHEMA", "复核请求含未知字段", {"fields": sorted(set(request) - allowed)})
        cid = request.get("target_claim_id")
        value = self.resolve_claim(cid)
        claim, record = self.claims[cid]
        state, scope = request.get("state"), request.get("scope")
        if state not in STATES:
            raise BurrowlError("INVALID_SCHEMA", "未知复核状态", {"state": state})
        if request.get("expected_content_hash", record["content_hash"]) != record["content_hash"]:
            raise BurrowlError("STALE_BASIS", "复核前结论所在记录已变化")
        replacement = request.get("replacement_claim_id")
        if state == "superseded":
            if not replacement or replacement == cid:
                raise BurrowlError("EVIDENCE_INELIGIBLE", "替代结论必须是另一条已存在的结论")
            self.resolve_claim(replacement)
        elif replacement:
            raise BurrowlError("INVALID_SCHEMA", "只有 superseded 状态可以指定替代结论")
        evidence_refs = deepcopy(request.get("evidence_refs", []))
        draft = {"owner_id": record["owner_id"], "kind": "review", "title": "结论复核：" + cid, "body_markdown": "",
                 "record_reason": request.get("reason", ""), "change_reason": request.get("reason", ""),
                 "sources": [{"target_kind": "claim", "target_id": cid, "revision": None, "sha256": value["sha256"],
                              "locator": "claim:" + cid, "relation": "references"},
                             {"target_kind": "record", "target_id": record["record_id"], "revision": record["revision"],
                              "sha256": None, "locator": "payload.claims", "relation": "references"}],
                 "provenance_gap": None, "discovery": record["discovery"], "sensitivity": record["sensitivity"],
                 "payload": {"target_claim_id": cid, "target_content_hash": record["content_hash"], "state": state,
                             "reviewer": request.get("actor"), "reason": request.get("reason", ""),
                             "evidence_refs": evidence_refs, "scope": scope, "replacement_claim_id": replacement}}
        normalized, errors = validate_record(draft, allow_review=True)
        if errors:
            code = "EVIDENCE_INELIGIBLE" if state == "accepted" else "INVALID_SCHEMA"
            raise BurrowlError(code, "复核不满足契约", errors=errors)
        previous = self.reviews.get(cid)
        self.reviews[cid] = normalized  # 临时代入，检验"若接受"时整条依赖链是否成立
        try:
            for ref in evidence_refs:
                self._reference(ref)
            if state == "accepted" and (failures := self.analyze(cid, scope)):
                raise BurrowlError("EVIDENCE_INELIGIBLE", "该结论在此范围内不能被接受", errors=failures)
        finally:
            if previous is None:
                self.reviews.pop(cid, None)
            else:
                self.reviews[cid] = previous
        return {"owner_id": record["owner_id"], "draft": normalized, "claim_id": cid,
                "record_id": previous["record_id"] if previous else None,
                "expected_revision": previous["revision"] if previous else None}

    # ---------- 正式投影 ----------
    def formal_projection(self, refs: list, scope: str) -> dict:
        """只返回在给定范围内已接受、有效、来源可见的结论正文；其余列入 rejected 并说明原因。"""
        if not isinstance(scope, str) or not scope.strip():
            raise BurrowlError("INVALID_ARGUMENT", "正式使用必须给出明确的适用范围")
        selected, rejected, queue, visited = set(), [], deque(), set()
        for ref in refs:
            try:
                if isinstance(ref, str):
                    queue.append(ref)
                elif target := self._reference(ref):
                    queue.append(target)
            except BurrowlError as exc:
                rejected.append({"canonical_id": ref.get("target_id"), "errors": [issue(exc.code, ref.get("target_id"),
                                                                                       exc.message)]})
        while queue:
            target = queue.popleft()
            if target in visited:
                continue
            visited.add(target)
            if target in self.claims:
                selected.add(target)
            elif target in self.records:
                record = self.records[target]
                if record["kind"] in CLAIM_KINDS:
                    selected.update(c["claim_id"] for c in record["payload"]["claims"])
                elif record["kind"] == "association":
                    rejected.append({"canonical_id": target, "errors": [issue(
                        "EVIDENCE_INELIGIBLE", target, "导航关联不能作为正式的科学支持")]})
                else:
                    for ref in [*iter_refs(record["payload"]), *record["sources"]]:
                        try:
                            if dependency := self._reference(ref):
                                queue.append(dependency)
                        except BurrowlError as exc:
                            rejected.append({"canonical_id": target, "errors": [issue(exc.code, ref["target_id"],
                                                                                       exc.message)]})
            elif self.index.find(target):
                selected.update(cid for cid, (_c, r) in self.claims.items() if r["owner_id"] == target)
            else:
                rejected.append({"canonical_id": target, "errors": [issue("UNRESOLVED_REFERENCE", target,
                                                                           "目标不存在")]})
        claims = []
        for cid in sorted(selected):
            try:
                if access := self.access_errors(cid):
                    rejected.append({"canonical_id": cid, "errors": access})
                    continue
                state = self.claim_state(cid, scope)
                if not state["effective_validity"]:
                    rejected.append({"canonical_id": cid, "errors": state["errors"]})
                    continue
                claims.append({"claim_id": cid, "owner_id": state["owner_id"], "record_id": state["record_id"],
                               "sha256": state["sha256"], "statement": state["statement"], "scope": scope,
                               "review_state": "accepted"})
            except BurrowlError as exc:
                rejected.append({"canonical_id": cid, "errors": [issue(exc.code, cid, exc.message)]})
        return {"claims": claims, "rejected": rejected, "text": "\n\n".join(c["statement"] for c in claims)}
