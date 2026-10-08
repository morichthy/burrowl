"""导航关联：提出候选（关键词重合 / 语义相近）与保存判断（采纳、拒绝、撤回）。

关联只是导航关系，不能是 supports/input；采纳导航不等于科学支持。
同一对端点+关系只有一条规范记录：修改判断即产生它的新修订。
"""
import uuid
from copy import deepcopy

from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash
from .claims import ClaimGraph
from .context import Storage
from .reader import fixed_ref
from .service import MemoryService

SYMMETRIC = {"analogous_to", "same_problem", "same_failure_mode"}
SKIP_KINDS = {"association", "review", "policy", "consolidation", "feedback"}


def keyword_overlap(left: list[str], right: list[str]) -> float:
    a, b = {k.casefold() for k in left}, {k.casefold() for k in right}
    return len(a & b) / len(a | b) if a and b else 0.0


def propose(storage: Storage, seeds: list[str], *, threshold: float = 0.3, limit: int = 50) -> dict:
    """以关键词重合度为种子记录找候选关联（只读）。语义近邻由检索服务另行提供。"""
    graph = ClaimGraph(storage)
    rows = [r for r in graph.records.values() if r["kind"] not in SKIP_KINDS and r["sensitivity"] != "restricted"
            and not graph.access_errors(r["record_id"])]
    by_id = {r["record_id"]: r for r in rows}
    missing = [s for s in seeds if s not in by_id]
    candidates, seen = [], set()
    for seed in seeds:
        if seed not in by_id:
            continue
        for other in rows:
            pair = tuple(sorted((seed, other["record_id"])))
            if other["record_id"] == seed or pair in seen:
                continue
            seen.add(pair)
            score = keyword_overlap(by_id[seed]["keywords"], other["keywords"])
            if score >= threshold:
                candidates.append({"from": fixed_ref(by_id[seed]), "to": fixed_ref(other), "score": round(score, 4),
                                   "method": "keyword_overlap", "title": other["title"]})
    candidates.sort(key=lambda c: (-c["score"], c["to"]["target_id"]))
    return {"candidates": candidates[:limit], "missing": missing,
            "note": "候选只是待判断的导航线索，保存时须说明共同结构与不可迁移的边界"}


def list_associations(storage: Storage, *, status: str | None = None, record_id: str | None = None) -> list[dict]:
    graph = ClaimGraph(storage)
    out = []
    for record in graph.records.values():
        if record["kind"] != "association" or record["sensitivity"] == "restricted":
            continue
        payload = record["payload"]
        if status and payload["status"] != status:
            continue
        if record_id and record_id not in {payload["from"]["target_id"], payload["to"]["target_id"]}:
            continue
        out.append({"record_id": record["record_id"], "revision": record["revision"], "owner_id": record["owner_id"],
                    **{k: payload[k] for k in ("from", "to", "relation", "status", "explanation",
                                               "shared_structure", "transfer_limits")}})
    return out


def _identity(payload: dict) -> tuple:
    ends = (payload["from"]["target_id"], payload["to"]["target_id"])
    return (*(sorted(ends) if payload["relation"] in SYMMETRIC else ends), payload["relation"])


def decide(memory: MemoryService, owner_id: str, payload: dict, *, actor: dict, reason: str, title: str = "关联判断",
           request_id: str | None = None, dry_run: bool = False) -> dict:
    """保存一条关联判断（candidate / accepted / rejected / withdrawn）。"""
    payload = deepcopy(payload)
    if payload.get("relation") in {"supports", "input"}:
        raise BurrowlError("INVALID_ARGUMENT", "关联只保存导航关系；科学依据请写在记录的 sources/evidence_refs 中")
    graph = ClaimGraph(memory.storage)
    matches = [r for r in graph.records.values() if r["kind"] == "association" and _identity(r["payload"]) == _identity(payload)]
    foreign = [r for r in matches if r["owner_id"] != owner_id]
    if foreign:
        raise BurrowlError("VERSION_CONFLICT", "这条关联已归属其他对象，请在原对象中修订",
                       {"owner_id": foreign[0]["owner_id"], "record_id": foreign[0]["record_id"]})
    draft = {"owner_id": owner_id, "kind": "association", "title": title, "body_markdown": "", "keywords": [],
             "payload": payload, "sources": deepcopy(payload.get("basis_refs", [])),
             "provenance_gap": None if payload.get("basis_refs") else "仅导航关系，尚缺独立依据",
             "record_reason": reason, "change_reason": reason, "sensitivity": "internal", "discovery": "owner_only"}
    operation: dict = {"op": "decide_association", "draft": draft}
    if matches:
        operation |= {"record_id": matches[0]["record_id"], "expected_revision": matches[0]["revision"]}
    else:
        operation["client_key"] = "association"
    owner = memory.owners().get(owner_id)
    head = memory.snapshot(owner).commit_id if owner.persisted else None
    request = {"schema_version": 3, "request_id": request_id or str(uuid.uuid4()), "actor": actor, "owner_id": owner_id,
               "expected_head": head, "operations": [operation], "dry_run": dry_run}
    digest = canonical_hash({"operation": "association", "payload": payload, "reason": reason, "owner_id": owner_id})
    return memory.commit(request, hash_override=digest)
