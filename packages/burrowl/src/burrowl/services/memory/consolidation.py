"""阶段巩固：列出"自上次巩固以来"的变化与受影响记录，记录逐项处置（保留 / 修订 / 撤回 / 暂缓）。

prepare 只读：变化清单 + 受影响下游 + 合并/冲突候选 + 依据指纹。
apply 绑定 prepare 的指纹：依据在两次之间变化即拒绝；暂缓与未处置项留到下次。
实际的语义判断与正文修订由 AI/人完成，这里只保存处置记录。
"""
import uuid
from copy import deepcopy

from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash, sha256_file
from ...core.records import validate_record
from ...storage.owners import Owner, OwnerIndex
from . import provenance
from .claims import FIXING_LEVELS, ClaimGraph, citations, iter_refs
from .reader import fixed_ref
from .service import MemoryService

SKIP = {"consolidation", "feedback", "review", "policy"}


def _key(ref: dict) -> str:
    return canonical_hash({k: ref.get(k) for k in ("target_kind", "target_id")})


def _source_states(memory: MemoryService, refs: list[dict]) -> dict[str, dict]:
    states = {}
    for ref in refs:
        if ref["target_kind"] != "file":
            continue
        try:
            actual = sha256_file(memory.storage.sources.resolve(ref["target_id"]).path)
            states[_key(ref)] = {"ref": ref, "sha256": actual, "error": None}
        except BurrowlError as exc:
            states[_key(ref)] = {"ref": ref, "sha256": None, "error": exc.code}
    return states


def _basis_hash(basis: dict) -> str:
    return canonical_hash({k: basis.get(k) for k in ("owner_id", "basis_heads", "changed", "affected", "remaining",
                                                     "source_states")})


def unfixed_evidence(index: OwnerIndex, owner: Owner, graph: ClaimGraph) -> dict:
    """R14 D-09：本对象位置下的 Run 与本对象的 L0 记录中，没有被全库任何 L1–L4 记录引用的。只报告，不改记录。

    被任何对象的正式记录引用即视为已固定（知识可能按主题写在别的对象里）。graph 是全库复核图，已读入全部记录。"""
    cited = citations(graph, levels=FIXING_LEVELS)
    runs = [{"run_id": r.owner_id, "title": r.title, "status": r.native.get("status"),
             "ended_at": r.native.get("ended_at"), "path": r.native_path}
            for r in (index.runs_under(owner) if owner.owner_type != "run" else [])
            if r.readable and r.owner_id not in cited]
    raw = [fixed_ref(record) for record in graph.records.values()
           if record["owner_id"] == owner.owner_id and record["kind"] == "source"
           and record["sensitivity"] != "restricted" and record["record_id"] not in cited]
    return {"runs": runs, "raw_records": raw}


def prepare(memory: MemoryService, owner_id: str, *, since_commit: str | None = None, trigger: str = "manual") -> dict:
    owner = memory.owners().get(owner_id).require_readable()
    snapshot = memory.snapshot(owner)
    store = memory.storage.store
    previous_runs = [r for r in snapshot.records.values() if r["kind"] == "consolidation"]
    latest = max(previous_runs, key=lambda r: (r["updated_at"], r["record_id"]), default=None)
    base_commit = since_commit or (snapshot.manifest["record_heads"][latest["record_id"]]["commit_id"] if latest else None)
    before = store.snapshot_at(owner, base_commit).records if base_commit else {}
    changed, external = [], []
    for rid, record in snapshot.records.items():
        if record["kind"] == "review" and (rid not in before or before[rid]["content_hash"] != record["content_hash"]):
            changed.append({"target_kind": "claim", "target_id": record["payload"]["target_claim_id"], "revision": None,
                            "sha256": None, "locator": "复核变化", "relation": "references"})
        if record["kind"] in SKIP:
            continue
        external.extend(r for r in iter_refs({"sources": record["sources"], "payload": record["payload"]})
                        if r["target_kind"] == "file")
        if rid not in before or before[rid]["content_hash"] != record["content_hash"]:
            changed.append(fixed_ref(record))
    states = _source_states(memory, external)
    for state in states.values():
        if state["error"] or state["sha256"] != state["ref"].get("sha256"):
            changed.append(state["ref"] | {"sha256": state["sha256"] or state["ref"].get("sha256")})
    changed = list({canonical_hash(r): r for r in changed}.values())
    graph = ClaimGraph(memory.storage)
    affected, basis_heads = [], {owner_id: snapshot.commit_id}
    if changed:
        for item in provenance.impact(memory.storage, [r["target_id"] for r in changed])["affected"]:
            record = graph.records.get(item["canonical_id"])
            if record and record["kind"] not in SKIP and record["sensitivity"] != "restricted":
                affected.append(fixed_ref(record))
                head = graph.heads.get(record["owner_id"])
                basis_heads[record["owner_id"]] = head["commit_id"] if head else None
    candidates = []
    rows = [r for r in snapshot.records.values() if r["kind"] in {"experience", "narrative", "overview"}]
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            shared = {k.casefold() for k in a["keywords"]} & {k.casefold() for k in b["keywords"]}
            if shared and {_key(r) for r in a["sources"]} == {_key(r) for r in b["sources"]} and a["sources"]:
                candidates.append({"kind": "merge_candidate", "refs": [fixed_ref(a), fixed_ref(b)],
                                   "reason": "关键词重合且来源相同，需比较适用边界后再决定是否合并"})
            if any(r["relation"] == "contradicts" for r in [*a["sources"], *b["sources"]]):
                candidates.append({"kind": "conflict_candidate", "refs": [fixed_ref(a), fixed_ref(b)],
                                   "reason": "存在显式反证关系，需复核范围；不会自动撤回"})
    result = {"owner_id": owner_id, "trigger": trigger, "since_commit": base_commit, "basis_heads": basis_heads,
              "changed": changed, "affected": affected,
              "remaining": deepcopy(latest["payload"]["remaining_refs"]) if latest else [],
              "candidates": candidates, "source_states": states,
              "unfixed_evidence": unfixed_evidence(memory.owners(), owner, graph), "writes": 0}
    result["basis_hash"] = _basis_hash(result)
    return result


def apply(memory: MemoryService, owner_id: str, basis: dict, decisions: list[dict], *, actor: dict, reason: str,
          operations: list[dict] | None = None, request_id: str | None = None, dry_run: bool = False) -> dict:
    """保存处置记录（可与本批的实际修订一起原子提交）。decisions: [{target, action, reason, ...}]。"""
    if _basis_hash(basis) != basis.get("basis_hash") or basis["owner_id"] != owner_id:
        raise BurrowlError("STALE_BASIS", "巩固清单指纹不一致")
    if _source_states(memory, [s["ref"] for s in basis["source_states"].values()]) != basis["source_states"]:
        raise BurrowlError("STALE_BASIS", "准备之后外部来源已变化，请重新准备")
    targets = {_key(r): r for r in [*basis["changed"], *basis["affected"], *basis["remaining"]]}
    handled, seen = set(), set()
    for decision in decisions:
        key = _key(decision["target"])
        if key not in targets or key in seen:
            raise BurrowlError("INVALID_ARGUMENT", "处置目标不在清单中或重复", {"target_id": decision["target"]["target_id"]})
        seen.add(key)
        if decision["action"] != "defer":
            handled.add(key)
    remaining = [r for k, r in targets.items() if k not in handled]
    sources = list({canonical_hash(r): r for r in [*targets.values(), *[
        s["ref"] | {"sha256": s["sha256"]} for s in basis["source_states"].values() if s["sha256"]]]}.values())
    draft = {"owner_id": owner_id, "kind": "consolidation", "title": "阶段巩固", "body_markdown": "", "keywords": [],
             "sources": sources, "provenance_gap": None if sources else "当前没有待巩固的来源",
             "record_reason": reason, "change_reason": reason, "sensitivity": "internal", "discovery": "owner_only",
             "payload": {"basis_heads": basis["basis_heads"], "trigger": basis["trigger"],
                         "affected_refs": list(targets.values()), "decisions": decisions, "remaining_refs": remaining}}
    _normalized, errors = validate_record(draft)
    if errors:
        raise BurrowlError("INVALID_SCHEMA", "巩固记录不符合契约", errors=errors)
    request = {"schema_version": 3, "request_id": request_id or str(uuid.uuid4()), "actor": actor,
               "owner_id": owner_id, "expected_head": basis["basis_heads"][owner_id],
               "operations": [*deepcopy(operations or []), {"op": "put_record", "client_key": "consolidation",
                                                            "draft": draft}], "dry_run": dry_run}
    others = {k: v for k, v in basis["basis_heads"].items() if k != owner_id}
    return memory.commit(request, basis_heads=others or None)
