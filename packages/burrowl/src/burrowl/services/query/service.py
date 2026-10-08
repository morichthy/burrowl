"""材料查询服务：检索、分页、取消，以及把每个候选回到固定版本核对。

查询状态只在进程内保存（默认 15 分钟无访问即过期），它是这次查询的工作状态，不是知识。
"""
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from ...core.clock import is_utc
from ...core.config import BurrowlConfig
from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash
from ...index import query_plan
from ...index.tokenize import tokens
from ..indexing import IndexService
from ..memory import documents as docs
from ..memory.context import Storage
from ..memory.reader import Reader
from .assemble import Packet, record_text
from .budget import Ledger
from .models import CONTENT_LEVELS, Candidate, QueryPage, QueryRequest
from .relations import adopted_neighbors, deepen

TTL_SECONDS = 900
MAX_STATES = 64
EXCERPT_CHARS = 360


@dataclass
class QueryState:
    query_id: str
    request: QueryRequest
    ledger: Ledger
    owner_ids: list[str]
    ranked: list[dict]                       # 融合后的检索命中（尚未逐个核对）
    position: int = 0                        # 已核对到 ranked 的位置
    delivered: list[Candidate] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    touched: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def candidate(self, candidate_id: str) -> Candidate:
        for item in self.delivered:
            if item.candidate_id == candidate_id:
                return item
        raise BurrowlError("NOT_FOUND", "候选不属于这次查询（或尚未翻到）", {"candidate_id": candidate_id})


def candidate_id(ref: dict) -> str:
    return "C-" + canonical_hash({k: ref.get(k) for k in ("target_kind", "target_id", "revision", "locator")})[:16]


def excerpt(text: str, terms: list[str]) -> str:
    """围绕第一个命中词截取片段；没有命中就取开头。"""
    lowered = text.casefold()
    hit = min((p for p in (lowered.find(t.casefold()) for t in terms if t) if p >= 0), default=0)
    start = max(0, hit - EXCERPT_CHARS // 3)
    return ("…" if start else "") + text[start:start + EXCERPT_CHARS] + ("…" if start + EXCERPT_CHARS < len(text) else "")


class QueryService:
    def __init__(self, storage: Storage, index: IndexService, config: Callable[[], BurrowlConfig], *, hub=None):
        self.storage = storage
        self.index = index
        self.config = config
        self.hub = hub  # 中枢服务（可选）；配置了 index.hub_route.connectors 时作为一路召回
        self._states: OrderedDict[str, QueryState] = OrderedDict()
        self._lock = threading.Lock()

    # ---------- 状态 ----------
    def state(self, query_id: str) -> QueryState:
        with self._lock:
            state = self._states.get(query_id)
            if state is None:
                raise BurrowlError("NOT_FOUND", "查询不存在或已过期；请重新查询", {"query_id": query_id})
            if time.monotonic() - state.touched > TTL_SECONDS:
                del self._states[query_id]
                raise BurrowlError("NOT_FOUND", "查询已过期（15 分钟无操作）；请重新查询", {"query_id": query_id})
            state.touched = time.monotonic()
            self._states.move_to_end(query_id)
            return state

    def _remember(self, state: QueryState) -> None:
        with self._lock:
            self._states[state.query_id] = state
            while len(self._states) > MAX_STATES:
                self._states.popitem(last=False)

    def cancel(self, query_id: str) -> dict:
        self.state(query_id).ledger.cancelled.set()
        return {"query_id": query_id, "cancelled": True}

    # ---------- 范围 ----------
    def _owners(self, request: QueryRequest) -> list[str]:
        scope, index = request.scope, self.storage.owners.scan()
        excluded = set(scope.exclude_ids)
        chosen = [index.get(o) for o in scope.owner_ids] if scope.owner_ids is not None else index.readable()
        return [o.owner_id for o in chosen if o.readable and o.owner_id not in excluded
                and (scope.owner_types is None or o.owner_type in scope.owner_types)]

    # ---------- 检索 ----------
    def search(self, request: QueryRequest) -> QueryPage:
        config = self.config()
        budget = request.budget or config.query.budget
        state = QueryState(query_id=request.query_id or f"Q-{uuid.uuid4()}", request=request,
                           ledger=Ledger(budget), owner_ids=self._owners(request), ranked=[])
        self._remember(state)
        review = "reviewed_only" if request.purpose == "formal" else request.review_filter
        with state.ledger.active():
            plan = query_plan.build(request.question, keywords=request.keywords)
            dense = "dense" in request.channels and self._charge_dense(state, plan)
            explicit = request.scope.owner_ids is not None
            limits = (state.ledger.remaining("graph_nodes"), state.ledger.remaining("graph_edges"))
            hub_hits, hub_gaps = self._hub_hits(request.question, config.index.hub_route,
                                                review)
            result = self.index.search_plan(
                plan, corpus="fulltext", owner_ids=state.owner_ids, explicit_owners=explicit,
                levels=CONTENT_LEVELS[request.content_source], kinds=request.scope.kinds, review_filter=review,
                per_route=min(200, budget.candidates or 1), dense=dense,
                channels=tuple(c for c in request.channels if c != "dense") + (("dense",) if dense else ()),
                graph_route=config.index.graph_route if all(limits) else None, graph_limits=limits,
                hub_hits=hub_hits, hub_weight=config.index.hub_route.weight)
            result["gaps"] = list(result["gaps"]) + hub_gaps
            if graph := result.get("graph"):  # 图路线按实际走过的节点和边记账（R12 §4.11）
                state.ledger.charge("graph_nodes", graph["nodes"])
                state.ledger.charge("graph_edges", graph["edges"])
            state.ranked, state.gaps = result["items"], list(result["gaps"])
            if request.content_source == "all" and request.purpose != "formal":
                sources = self.index.search_plan(plan, corpus="source", per_route=50, dense=dense,
                                                 channels=tuple(request.channels))
                state.ranked = sorted(state.ranked + sources["items"], key=lambda r: -r["score"])
                state.gaps += sources["gaps"]
            if request.freshness == "allow_stale":
                state.ranked += self._history_hits(state, plan)
        return self._page(state)

    def _hub_hits(self, question: str, settings, review_filter: str) -> tuple[list[dict], list[dict]]:
        """中枢路线（R12 T-P4.2、D-23）：外部检索命中 → 本地记录 ID。外部不可用或超时时退回本地并报告缺口；
        对不上本地的命中只作为 external_only 提示，不进入融合。"""
        if not settings.connectors or self.hub is None:
            return [], []
        hits, gaps = [], []
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.hub.search, question, connector_ids=settings.connectors, limit=settings.limit,
                                 review_filter=review_filter)
            try:
                found = future.result(timeout=settings.timeout_s)
            except FutureTimeout:
                return [], [{"channel": "hub", "reason": "timeout", "connectors": settings.connectors}]
            except BurrowlError as exc:
                return [], [{"channel": "hub", "reason": exc.code, "connectors": settings.connectors}]
        gaps.extend({"channel": "hub", "reason": "unavailable", "connector_id": e["connector_id"]}
                    for e in found.get("errors", []))
        external_only = []
        for hit in found["hits"]:
            ref = hit.get("fixed_ref")
            if ref and ref.get("target_kind") == "record":
                hits.append({"record_id": ref["target_id"], "connector_id": hit["connector_id"]})
            else:
                external_only.append({"connector_id": hit["connector_id"], "title": hit["title"],
                                      "external_id": hit["external_id"]})
        if external_only:
            gaps.append({"channel": "hub", "reason": "external_only", "hits": external_only[:10]})
        return list({h["record_id"]: h for h in hits}.values()), gaps

    def _charge_dense(self, state: QueryState, plan: dict) -> bool:
        embedder = self.index.db.embedder
        if embedder is None:
            state.gaps.append({"channel": "dense", "reason": "embedder_unavailable"})
            return False
        total = sum(embedder.count_tokens(v["question"]) for v in plan["variants"])
        calls = len(plan["variants"])
        if not (state.ledger.can("model_calls", calls) and state.ledger.can("model_input_tokens", total)):
            state.gaps.append({"channel": "dense", "reason": "model_budget_insufficient"})
            return False
        state.ledger.charge("model_calls", calls)
        state.ledger.charge("model_input_tokens", total)
        state.ledger.charge("model_tokens", total)
        return True

    def _history_hits(self, state: QueryState, plan: dict) -> list[dict]:
        """包含历史修订：有界遍历范围内对象的旧修订做字面匹配（结果标注为旧版本）。"""
        terms = {t for v in plan["variants"] for t in tokens(" ".join(v["lexical_terms"]))}
        levels = CONTENT_LEVELS[state.request.content_source]
        hits = []
        for owner_id in state.owner_ids:
            owner = self.storage.owners.scan().get(owner_id)
            if not owner.persisted:
                continue
            snapshot = self.storage.store.read_snapshot(owner)
            for record in self.storage.store.read_history(owner, snapshot):
                if snapshot.records[record["record_id"]]["revision"] == record["revision"] \
                        or (levels and record["level"] not in levels) or record["sensitivity"] == "restricted":
                    continue
                state.ledger.checkpoint()
                text = f"{record['title']}\n{record['body_markdown']}"
                matched = len(terms & set(tokens(text)))
                if matched:
                    hits.append({"canonical_id": f"{record['record_id']}@{record['revision']}",
                                 "record_id": record["record_id"], "owner_id": owner_id, "kind": record["kind"],
                                 "level": record["level"], "title": record["title"], "text": text, "review": "none",
                                 "fixed_ref": {"target_kind": "record", "target_id": record["record_id"],
                                               "revision": record["revision"], "sha256": record["record_hash"],
                                               "locator": "record", "relation": "references"},
                                 "score": matched / 1000, "query_sources": [{"channel": "history"}],
                                 "matched_blocks": [], "stale_revision": True})
        return sorted(hits, key=lambda h: -h["score"])[:state.ledger.remaining("candidates")]

    def page(self, query_id: str, cursor: str | None = None) -> QueryPage:
        state = self.state(query_id)
        if cursor is not None and cursor != f"{query_id}:{state.position}":
            raise BurrowlError("VERSION_CONFLICT", "游标已过期，请使用最近一次返回的 next_cursor")
        with state.lock, state.ledger.active():
            return self._page(state)

    def _page(self, state: QueryState) -> QueryPage:
        """从上次位置继续核对，直到凑满一页或预算/候选用完。被过滤的候选也计入消耗。"""
        request, reader = state.request, Reader(self.storage, meter=state.ledger.charge)
        size = request.result_limit or self.config().query.result_limit
        terms = tokens(request.question) + request.keywords
        page: list[Candidate] = []
        stop = None
        formal = request.purpose == "formal"
        graph = reader.claims() if formal else None
        try:
            while len(page) < size and state.position < len(state.ranked):
                hit = state.ranked[state.position]
                state.position += 1
                state.ledger.charge("candidates", 1)
                candidate = self._verify(state, reader, hit, terms, graph)
                if candidate:
                    page.append(candidate)
        except BurrowlError as exc:
            if exc.code != "BUDGET_EXCEEDED":
                raise
            stop = exc.details.get("reason") or exc.details.get("budget") or "budget"
        state.delivered.extend(page)
        more = state.position < len(state.ranked) and stop is None
        return QueryPage(query_id=state.query_id, items=page,
                         next_cursor=f"{state.query_id}:{state.position}" if more else None,
                         gaps=state.gaps, consumed=state.ledger.snapshot(), stop_reason=stop)

    def _verify(self, state: QueryState, reader: Reader, hit: dict, terms: list[str], graph) -> Candidate | None:
        """回到固定版本：确认当前修订、权限、排除项与时间范围；不合格则跳过并记录缺口。"""
        request, ref = state.request, dict(hit["fixed_ref"])
        excluded = set(request.scope.exclude_ids)
        if {hit["owner_id"], hit["record_id"], hit["canonical_id"]} & excluded:
            return None
        text = hit.get("text", "")
        if ref["target_kind"] == "file":
            try:
                self.storage.sources.resolve(ref["target_id"])
            except BurrowlError as exc:
                state.gaps.append({"target_id": ref["target_id"], "reason": exc.code})
                return None
        elif ref["target_kind"] in {"record", "claim"}:
            try:
                record = reader.record({"target_id": hit["record_id"], "revision": None})
            except BurrowlError as exc:
                state.gaps.append({"target_id": hit["record_id"], "reason": exc.code})
                return None
            if not hit.get("stale_revision") and ref["target_kind"] == "record" and record["revision"] != ref["revision"]:
                state.gaps.append({"target_id": hit["record_id"], "reason": "index_behind_current_revision"})
                return None
            if not self._in_window(record, request):
                return None
            if hit.get("stale_revision"):
                record = reader.record(ref)
            if graph is not None:
                projected = graph.formal_projection([record["record_id"]], request.applicability)
                if not projected["claims"]:
                    return None
                text = projected["text"]
        reader.meter("output_chars", min(len(text), EXCERPT_CHARS))
        return Candidate(candidate_id=candidate_id(ref), kind=hit["kind"], owner_id=hit["owner_id"],
                         record_id=hit["record_id"], title=hit["title"], excerpt=excerpt(text, terms),
                         level=hit.get("level"), ref=ref, review=hit.get("review", "none"),
                         channels=sorted({s["channel"] for s in hit.get("query_sources", [])}),
                         score=round(hit["score"], 6), matched_blocks=hit.get("matched_blocks", []),
                         stale_revision=bool(hit.get("stale_revision")))

    # ---------- 选中之后 ----------
    def _packet(self, state: QueryState, reader: Reader, *, association_share: float = 0.25) -> Packet:
        request = state.request
        allowed = set(state.owner_ids) if request.context_reach == "same_scope" else None
        return Packet(reader, state.ledger, allowed_owner_ids=allowed, association_share=association_share,
                      formal_scope=request.applicability if request.purpose == "formal" else None)

    def assemble(self, query_id: str, candidate_ids: list[str], *, associations: bool = False) -> dict:
        """把所选候选组合成阅读包：正文 + 必要依据（+ 可选的已采纳关联补充）。"""
        state = self.state(query_id)
        with state.lock, state.ledger.active():
            reader = Reader(self.storage, meter=state.ledger.charge)
            packet = self._packet(state, reader)
            for cid in candidate_ids:
                candidate = state.candidate(cid)
                packet.add(candidate.ref)
            if associations:
                for ref in adopted_neighbors(reader, [state.candidate(c).record_id for c in candidate_ids]):
                    packet.add(ref, group="association")
            return {"query_id": query_id, **packet.result()}

    def expand(self, query_id: str, candidate_ids: list[str], target: str) -> dict:
        """沿作者保存的固定引用展开：经验/概览 → 研究经过（process）或技术依据（technical）。

        缺少相应引用时报告缺口，不按"同一研究"去猜。展开结果作为新候选加入本次查询。
        """
        field_by_target = {"process": "process_refs", "technical": "technical_refs"}
        if target not in field_by_target:
            raise BurrowlError("INVALID_ARGUMENT", "target 只能是 process 或 technical")
        state = self.state(query_id)
        with state.lock, state.ledger.active():
            reader = Reader(self.storage, meter=state.ledger.charge)
            found, gaps = [], []
            for cid in candidate_ids:
                candidate = state.candidate(cid)
                record = reader.record({"target_id": candidate.record_id, "revision": candidate.ref.get("revision")})
                refs = record["payload"].get(field_by_target[target], [])
                if not refs:
                    gaps.append({"candidate_id": cid, "code": "NO_LINK", "message": "该记录没有保存这类展开引用"})
                for ref in refs:
                    try:
                        child = reader.record(ref)
                    except BurrowlError as exc:
                        gaps.append({"candidate_id": cid, "code": exc.code, "target_id": ref["target_id"]})
                        continue
                    if child["owner_id"] in set(state.request.scope.exclude_ids):
                        continue
                    state.ledger.charge("candidates", 1)
                    text = record_text(child)
                    item = Candidate(candidate_id=candidate_id(ref), kind=child["kind"], owner_id=child["owner_id"],
                                     record_id=child["record_id"], title=child["title"], excerpt=excerpt(text, []),
                                     level=child["level"], ref=ref, review="none", channels=["expand:" + target],
                                     score=0.0, matched_blocks=[ref["locator"][6:]] if str(ref.get("locator", ""))
                                     .startswith("block:") else [])
                    if all(c.candidate_id != item.candidate_id for c in state.delivered):
                        state.delivered.append(item)
                    found.append(item)
            reader.recheck()
            return {"query_id": query_id, "items": [c.model_dump() for c in found], "gaps": gaps,
                    "consumed": state.ledger.snapshot()}

    def documents(self, query_id: str, candidate_ids: list[str], *, document_type: str = "research_process") -> dict:
        """返回候选所属的完整文稿：按文稿去重，保持章节顺序；没有文稿时报告缺口，不临时综合。"""
        state = self.state(query_id)
        owners = list(dict.fromkeys(state.candidate(c).owner_id for c in candidate_ids))
        results, gaps = [], []
        for owner_id in owners:
            built = docs.build_document(self.storage, owner_id, document_type=document_type)
            text = sum(len(b.get("markdown") or "") for s in (built.get("report") or {}).get("sections", [])
                       for b in s["blocks"])
            if built["document"] is None:
                gaps.append({"owner_id": owner_id, "code": "DOCUMENT_MISSING"})
                continue
            if not state.ledger.can("output_chars", text):
                gaps.append({"owner_id": owner_id, "code": "BUDGET_EXCEEDED"})
                continue
            state.ledger.charge("output_chars", text)
            results.append(built)
        return {"query_id": query_id, "documents": results, "gaps": gaps, "consumed": state.ledger.snapshot()}

    def deepen(self, query_id: str, candidate_ids: list[str], *, hops: int = 2,
               relations: list[str] | None = None, include_associations: bool = True) -> dict:
        """从所选候选沿已保存关系有界深化；节点、边、跳数计入本次查询预算。"""
        state = self.state(query_id)
        with state.lock, state.ledger.active():
            reader = Reader(self.storage, meter=state.ledger.charge)
            allowed = set(state.owner_ids) if state.request.context_reach == "same_scope" else None
            graph = deepen(reader, state.ledger, [state.candidate(c).ref for c in candidate_ids], hops=hops,
                           relations=relations, include_associations=include_associations,
                           allowed_owner_ids=allowed)
            reader.recheck()
            return {"query_id": query_id, **graph, "consumed": state.ledger.snapshot()}

    @staticmethod
    def _in_window(record: dict, request: QueryRequest) -> bool:
        updated = record.get("updated_at", "")
        lower, upper = request.scope.updated_from, request.scope.updated_before
        if lower and is_utc(lower) and updated < lower:
            return False
        return not (upper and is_utc(upper) and updated >= upper)
