"""知识复核：待复核队列、一键复核（含预检）、批量复核、复核历史。

复核只有一套存储：每条结论至多一条当前 review 记录，修改复核即产生它的新修订。
"accepted" 需要明确的适用范围和支持性证据，并检查整条依赖链；
其余状态（质疑、撤回、替代、改回未复核）只需理由。
"""
import uuid
from typing import Any

from ..core.errors import BurrowlError
from ..core.hashing import canonical_hash
from ..storage.owners import OwnerIndex
from .memory.claims import ClaimGraph
from .memory.context import Storage
from .memory.service import MemoryService

QUEUE_STATES = ("not-reviewed", "stale", "disputed", "upstream-blocked", "accepted", "retracted", "superseded")
DEFAULT_QUEUE = ("not-reviewed", "stale", "disputed", "upstream-blocked")


def claim_lookup_factory(storage: Storage):
    """给记忆服务用的 claim 解析器工厂：首次遇到 claim 引用时才构建复核图。"""
    def factory(index: OwnerIndex):
        graph: dict[str, ClaimGraph] = {}

        def lookup(claim_id: str) -> dict:
            if "g" not in graph:
                graph["g"] = ClaimGraph(storage, index)
            return graph["g"].resolve_claim(claim_id)
        return lookup
    return factory


class ReviewService:
    def __init__(self, storage: Storage, memory: MemoryService):
        self.storage = storage
        self.memory = memory

    def graph(self) -> ClaimGraph:
        return ClaimGraph(self.storage)

    # ---------- 查询 ----------
    def queue(self, *, states: list[str] | None = None, owner_id: str | None = None, query: str = "",
              limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """跨对象列出结论及其复核状态；默认只列需要处理的（未复核、已失效、有争议、被上游阻断）。"""
        wanted = set(states or DEFAULT_QUEUE)
        if wanted - set(QUEUE_STATES):
            raise BurrowlError("INVALID_ARGUMENT", "未知的队列状态", {"states": sorted(wanted - set(QUEUE_STATES))})
        graph = self.graph()
        items, counts = [], dict.fromkeys(QUEUE_STATES, 0)
        for cid, (claim, record) in sorted(graph.claims.items()):
            if owner_id and record["owner_id"] != owner_id or record["sensitivity"] == "restricted":
                continue
            state = graph.claim_state(cid)
            status = ("stale" if state["stale"] else
                      "upstream-blocked" if state["review_state"] == "accepted" and state["upstream_blocked"]
                      else state["review_state"])
            counts[status] += 1
            text = f"{claim['statement']} {record['title']}".casefold()
            if status not in wanted or (query and query.casefold() not in text):
                continue
            review = graph.reviews.get(cid)
            items.append({"claim_id": cid, "status": status, "statement": claim["statement"], "scope": claim["scope"],
                          "owner_id": record["owner_id"], "record_id": record["record_id"],
                          "record_title": record["title"], "record_kind": record["kind"],
                          "content_hash": record["content_hash"], "claim_sha256": state["sha256"],
                          "evidence_refs": claim["evidence_refs"],
                          "review": review["payload"] | {"revision": review["revision"],
                                                         "updated_at": review["updated_at"]} if review else None,
                          # "尚未复核"本身已由 status 表达，problems 只列其他原因（失效、上游阻断等）。
                          "problems": [e for e in state["errors"]
                                       if not (e["target_id"] == cid and e["message"] == "结论尚未复核")]})
        return {"items": items[offset:offset + limit], "total": len(items), "counts": counts}

    def history(self, claim_id: str) -> dict[str, Any]:
        graph = self.graph()
        graph.resolve_claim(claim_id)
        review = graph.reviews.get(claim_id)
        if review is None:
            return {"claim_id": claim_id, "revisions": []}
        owner = graph.index.get(review["owner_id"])
        revisions = [self.storage.store.read_record(owner, review["record_id"], n)
                     for n in range(review["revision"], 0, -1)]
        return {"claim_id": claim_id, "revisions": [
            {"revision": r["revision"], "state": r["payload"]["state"], "reason": r["payload"]["reason"],
             "scope": r["payload"]["scope"], "reviewer": r["payload"]["reviewer"], "updated_at": r["updated_at"],
             "replacement_claim_id": r["payload"]["replacement_claim_id"]} for r in revisions]}

    # ---------- 写入 ----------
    def submit(self, request: dict) -> dict[str, Any]:
        """提交一条复核。expected_head 省略时基于当前版本（服务端串行化）。dry_run=true 只预检。"""
        request = dict(request)
        request.setdefault("request_id", str(uuid.uuid4()))
        try:
            uuid.UUID(request["request_id"])
        except ValueError as exc:
            raise BurrowlError("INVALID_ARGUMENT", "request_id 必须为 UUID") from exc
        if not isinstance(request.get("actor"), dict):
            raise BurrowlError("INVALID_ARGUMENT", "复核必须给出执行者 actor")
        graph = self.graph()
        prepared = graph.prepare_review({k: v for k, v in request.items() if k != "owner_id"})
        owner = graph.index.get(prepared["owner_id"])
        head = graph.heads.get(owner.owner_id)
        expected = request.get("expected_head", head["commit_id"] if head else None)
        operation: dict[str, Any] = {"op": "put_record", "draft": prepared["draft"]}
        if prepared["record_id"]:
            operation |= {"record_id": prepared["record_id"], "expected_revision": prepared["expected_revision"]}
        else:
            operation["client_key"] = "review"
        commit_request = {"schema_version": 3, "request_id": request["request_id"], "actor": request["actor"],
                          "owner_id": owner.owner_id, "expected_head": expected, "operations": [operation],
                          "dry_run": bool(request.get("dry_run"))}
        # 幂等键绑定原始复核请求（而不是派生出的记录草案）。
        digest = canonical_hash({"operation": "review",
                                 "request": {k: v for k, v in request.items() if k not in {"request_id", "dry_run"}}})
        result = self.memory.commit(commit_request, allow_review=True, hash_override=digest)
        return {**result, "claim_id": prepared["claim_id"], "state": request["state"]}

    def batch(self, items: list[dict], *, actor: dict, dry_run: bool = False) -> dict[str, Any]:
        """逐条串行提交（同一对象的 HEAD 会前进，每条都基于最新版本）。单条失败不影响其他条。"""
        results = []
        for item in items:
            try:
                outcome = self.submit({**item, "actor": actor, "dry_run": dry_run})
                results.append({"claim_id": item.get("target_claim_id"), "ok": True,
                                "save_status": outcome.get("save_status", "dry_run")})
            except BurrowlError as exc:
                results.append({"claim_id": item.get("target_claim_id"), "ok": False, "error": exc.problem()})
        return {"results": results, "succeeded": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results)}
