"""阅读会话服务。agent 侧约 6–9 次调用完成一次标准阅读；用户侧通过工作台写入收件箱。

agent：start → assess（可多批）→ read（逐个相关对象，按页读完）→ note → [synthesize] → finish
辅助：peek（看不确定对象的更多窗口）、recall（全文补偿需用户批准 / 联想补查）、ask_user、status、delegate
用户：respond（批准/拒绝/回答）、override（改判对象）、direct（补充联想方向）、configure、abort、archive

程序保证的规则（agent 不需要自己记）：
- assess 必须带筛选包 digest（证明判断的是自己看到的那份包）；用户的改判优先。
- read 只读判为相关的对象；首次读取固定对象 HEAD，之后对象有新提交则要求从第 1 页重读。
- standard/associative 必须读完全部页才能写 note；quick 不读全文，只能引用 assess 时采纳的窗口。
- note 的出处必须是已交付材料，正文提到的 ID 必须列入出处；保存时补全为固定引用。
- 交接时重新核对对象 HEAD：陈旧笔记不交付，依据它的综合稿失效。
- 全文补偿只在发现召回不可用、不完整或不足时可申请，且必须带用户批准的 approval_id。
- 写操作可带 request_id：重试同一个 request_id 返回原结果，不会重复消耗联想轮数或预算。
"""
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from ...core.clock import utc_now
from ...core.config import BurrowlConfig
from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash
from ...index import conditions as field_conditions
from ...index import query_plan
from ...storage import terms as term_store
from ...storage.owners import require_knowledge_scope
from ..indexing import IndexService
from ..memory import concepts
from ..memory.context import Storage
from . import notes, screening, text
from .reuse import ScreenReuse
from .session import STRATEGIES, VERDICTS, SessionStore, new_session

AGENT_CLOSED = {"finished": "已结束", "aborted": "已被用户中止", "archived": "已归档"}
DECISION_WORDS = {"approve": "批准", "deny": "拒绝", "answer": "回答"}
APPLIED_KEEP = 64  # 记住最近多少个 request_id 的结果
BUDGET_KEYS = ("candidates", "rerank_items", "wall_ms")


def _event(session: dict, action: str, summary: str, **extra: Any) -> None:
    session["timeline"].append({"at": utc_now(), "action": action, "summary": summary, **extra})


class ReadingService:
    def __init__(self, storage: Storage, index: IndexService, config: Callable[[], BurrowlConfig], *,
                 reranker_dir: Path | None = None):
        self.storage = storage
        self.index = index
        self.config = config
        self.sessions = SessionStore(storage.ws)
        self.reranker_dir = reranker_dir
        self.reuse = ScreenReuse()  # R18 D-02：同输入、同检索状态时复用筛选结果（进程内，可丢弃）

    # ---------- 共用 ----------
    def _agent_update(self, session_id: str, change: Callable[[dict], Any],
                      request_id: str | None = None) -> tuple[dict, Any]:
        """agent 的写操作：会话已结束/中止则拒绝；同时取走未读的用户输入（每条只交付一次）。

        带 request_id 时幂等：同一 request_id 已执行过则直接返回当时的结果。
        """
        holder: dict[str, Any] = {}

        def apply(session: dict) -> dict | None:
            applied = session.setdefault("applied", {})
            if request_id and request_id in applied:
                holder["result"] = deepcopy(applied[request_id])
                holder["inbox"] = []
                return None  # 不产生新修订
            if session["status"] in AGENT_CLOSED:
                raise BurrowlError("INVALID_TRANSITION", "会话" + AGENT_CLOSED[session["status"]])
            holder["result"] = change(session)
            if request_id:
                applied[request_id] = deepcopy(holder["result"])
                for old in list(applied)[:-APPLIED_KEEP]:
                    del applied[old]
            holder["inbox"] = [m for m in session["inbox"] if not m["consumed"]]
            for message in session["inbox"]:
                message["consumed"] = True
            return session

        session, revision = self.sessions.update(session_id, apply)
        return session, {"result": holder.get("result"), "inbox": holder.get("inbox", []), "revision": revision}

    @staticmethod
    def _envelope(session: dict, extra: dict) -> dict:
        pending = [r for r in session["requests"] if r["status"] == "pending"]
        return {"session_id": session["session_id"], "status": session["status"], "phase": session["phase"],
                **(extra["result"] or {}), "inbox": extra["inbox"], "pending_requests": pending,
                "revision": extra["revision"]}

    def _batch(self, session: dict) -> list[dict]:
        """下一批待判断对象的筛选包（已判断过的跳过）。"""
        size = session["screening"]["batch_owners"]
        waiting = [o for o in session["queue"] if session["owners"][o].get("verdict") is None]
        return [self._public_packet(session["owners"][o]["packet"]) for o in waiting[:size]]

    @staticmethod
    def _public_packet(packet: dict) -> dict:
        return {k: v for k, v in packet.items() if k != "extra_windows"} | {"more_windows": len(packet["extra_windows"])}

    def _plan(self, question: str, **options: Any) -> dict:
        raw, digest = term_store.load(self.storage.ws)
        entries = query_plan.validate_terms(raw) if raw else []
        # R12 D-26：概念库中的概念名与别名参与术语扩展；两份来源的指纹一起进入计划
        library = self.storage.owners.scan().find(self.config().graph.concept_owner_id or "")
        if library is not None and library.readable and library.persisted:
            records = list(self.storage.store.read_snapshot(library).records.values())
            extra, extra_digest = concepts.term_entries(records, question)
            if extra:
                entries = [*entries, *extra]
                digest = canonical_hash({"terms": digest, "concepts": extra_digest})
        return query_plan.build(question, terms=entries, terms_sha256=digest, **options)

    def _head(self, owner_id: str) -> str | None:
        """对象当前 HEAD；对象不存在或不可读时为 None（视为陈旧）。"""
        owner = self.storage.owners.scan().find(owner_id)
        if owner is None or not owner.readable or not owner.persisted:
            return None
        return self.storage.store.read_head(owner).commit_id

    def _heads(self, session: dict) -> dict[str, str | None]:
        return {o: self._head(o) for o, s in session["owners"].items() if s.get("note")}

    def _handoff(self, session: dict) -> dict:
        return notes.handoff(session, self._heads(session))

    @staticmethod
    def _check_budget(session: dict) -> None:
        limits, used = session.get("budget") or {}, session.get("consumed") or {}
        over = [k for k in BUDGET_KEYS if limits.get(k) is not None and used.get(k, 0) >= limits[k]]
        if over:
            raise BurrowlError("BUDGET_EXCEEDED", "本会话的召回预算已用完（可在设置中调高阅读预算后新开会话）",
                           {"exhausted": over, "consumed": used, "limits": {k: limits[k] for k in over}})

    def screen(self, plan: dict, *, corpus: str = "discovery", owner_ids: list[str] | None = None,
               review_filter: str, exclude: set[str] | frozenset = frozenset(), screening_cfg: dict, reranking: str,
               conditions: list[dict] | None = None, origin: str = "reading") -> dict:
        """一轮召回 + 筛选包，不读写会话。阅读会话的每一轮与归属判定（R13 D-03）共用这一个实现。

        R18 D-02：输入相同、参与检索的对象及其索引都没变时复用上一次的结果，结果带 `reused_from`（来源 origin 与时间）。
        缺口每次现算：未就绪的对象（如刚建的空对象）不影响召回，只多一条缺口。复用时 cost 只记本次实际开销
        （candidates 照计，rerank_items 为 0）。"""
        started = time.monotonic()
        config = self.config()
        key = canonical_hash({"plan_id": plan["plan_id"], "corpus": corpus, "owner_ids": owner_ids,
                              "review_filter": review_filter, "exclude": sorted(exclude),
                              "screening": screening_cfg, "reranking": reranking, "conditions": conditions,
                              "config": config.model_dump(mode="json"), "reranker": self._reranker_marker()})
        state = self.index.search_state(corpus, owner_ids)
        reused = self.reuse.get(key, state["digest"])
        if reused is not None:
            result, source = reused
            # search_plan 的缺口 = 就绪缺口 + 查询相关的缺口（截断、图预算）；前者换成现算的
            result["gaps"] = state["gaps"] + result["gaps"][source.pop("readiness_gaps"):]
            result["reused_from"] = source
            result["cost"] = {"candidates": result["cost"]["candidates"], "rerank_items": 0,
                              "wall_ms": round((time.monotonic() - started) * 1000)}
            return result
        owners, gaps = screening.recall(self.index, plan, corpus=corpus, owner_ids=owner_ids,
                                        review_filter=review_filter, exclude=set(exclude),
                                        graph_route=config.index.graph_route, readiness=state["readiness"])
        packets, diagnostics = screening.packets(self.index, plan, owners, regular=screening_cfg["regular_windows"],
                                                 maximum=screening_cfg["max_windows"], reranking=reranking,
                                                 reranker_path=self.reranker_dir, conditions=conditions)
        ordered = sorted((o["owner_id"] for o in owners), key=lambda o: screening.condition_rank(packets[o]))
        result = {"owners": ordered, "packets": packets, "gaps": gaps, "diagnostics": diagnostics,
                  "cost": {"candidates": len(owners), "rerank_items": diagnostics.get("rerank_items", 0),
                           "wall_ms": round((time.monotonic() - started) * 1000)}, "reused_from": None}
        # 召回期间若有对象被重新索引，之后的 digest 必然不同（水位行带更新时间），这份结果不会被命中
        self.reuse.put(key, state["digest"], result, origin, readiness_gaps=len(state["gaps"]))
        return result

    def _reranker_marker(self) -> list | None:
        """重排模型目录的存在与修改时间：装上或换掉模型后，筛选包的重排结果会不同（R18 D-02）。"""
        try:
            return [str(self.reranker_dir), self.reranker_dir.stat().st_mtime_ns] if self.reranker_dir else None
        except OSError:
            return [str(self.reranker_dir), None]

    def preview(self, question: str, *, variants: list[dict] | None = None,
                protected_terms: list[str] | None = None, domains: list[str] | None = None,
                origin: str = "preview") -> dict:
        """按工作区阅读配置做一次与 reading.start 第一轮相同的发现召回和筛选，不建立会话。
        结果可被随后同一问题的 reading.start 复用（R18 D-02）；origin 标明是谁算的。"""
        cfg = self.config().reading
        plan = self._plan(question, variants=variants, protected_terms=protected_terms, domains=domains)
        return {"plan_id": plan["plan_id"]} | self.screen(
            plan, review_filter=cfg.review_filter, screening_cfg=cfg.screening.model_dump(),
            reranking=cfg.reranking.mode, origin=origin)

    def _round(self, session: dict, plan: dict, kind: str, *, corpus: str = "discovery") -> dict:
        """执行一轮召回，把新对象加入队列并生成筛选包（在会话锁外计算）。"""
        result = self.screen(plan, corpus=corpus, owner_ids=session.get("scope_owner_ids"),
                             review_filter=session["review_filter"],
                             exclude=set(session["owners"]) | set(session.get("skip_owner_ids") or []),
                             screening_cfg=session["screening"], reranking=session["reranking"],
                             conditions=session.get("field_conditions") or None,
                             origin=f"reading.{kind}")
        gaps = result["gaps"]
        if kind == "discovery":
            # 发现召回不可用、不完整或不足时，才允许向用户申请全文补偿
            reasons = [g.get("code", "GAP") for g in gaps] + (
                ["FEW_RESULTS"] if result["cost"]["candidates"] < session["screening"]["batch_owners"] else [])
        else:
            reasons = []
        return {"id": f"R{len(session['rounds']) + 1}", "kind": kind, "plan_id": plan["plan_id"], "corpus": corpus,
                "at": utc_now(), "owners": result["owners"], "packets": result["packets"], "gaps": gaps,
                "fulltext_reasons": reasons, "diagnostics": result["diagnostics"], "cost": result["cost"],
                "reused_from": result["reused_from"]}

    @staticmethod
    def _apply_round(session: dict, round_: dict) -> None:
        packets = round_.pop("packets")
        for rank, owner_id in enumerate(round_["owners"], 1):
            if owner_id in session["owners"]:
                continue
            packet = packets[owner_id]
            refs = {w["record_id"]: w["ref"] for w in packet["windows"] + packet["extra_windows"]}
            session["owners"][owner_id] = {"title": packet["title"], "round": round_["id"], "rank": rank,
                                           "packet": packet, "verdict": None, "reason": "", "judged_by": None,
                                           "delivered": sorted({w["record_id"] for w in packet["windows"]}),
                                           "refs": refs, "accepted": [], "basis_head": None,
                                           "pages_read": [], "total_pages": None, "note": None}
            session["queue"].append(owner_id)
        consumed = session.setdefault("consumed", {})
        for key, value in round_.get("cost", {}).items():
            consumed[key] = consumed.get(key, 0) + value
        session["rounds"].append(round_)
        reused = round_.get("reused_from")
        tail = f"（复用 {reused['origin']} 的结果）" if reused else ""
        _event(session, "recall", f"{round_['kind']} 召回 {len(round_['owners'])} 个对象{tail}", round=round_["id"])

    @staticmethod
    def _fulltext_reasons(session: dict) -> list[str]:
        """可以申请全文补偿的理由；空列表表示发现召回已充分，不应申请。"""
        discovery = [r for r in session["rounds"] if r["kind"] == "discovery"]
        reasons = list(discovery[-1].get("fulltext_reasons", [])) if discovery else ["NO_DISCOVERY"]
        judged = [s for s in session["owners"].values() if s["verdict"] is not None]
        if session["owners"] and len(judged) == len(session["owners"]) and \
                not any(s["verdict"] == "relevant" for s in judged):
            reasons.append("NO_RELEVANT")
        return reasons

    # ---------- agent 动作 ----------
    def start(self, question: str, *, actor: dict, conditions: list[str] | None = None, strategy: str | None = None,
              review_filter: str | None = None, variants: list[dict] | None = None,
              protected_terms: list[str] | None = None, domains: list[str] | None = None,
              owner_id: str | None = None, scope_owner_ids: list[str] | None = None,
              field_conditions_: list[dict] | None = None) -> dict:
        """创建会话并立即召回，返回第一批筛选包。默认值来自工作区配置。"""
        cfg = self.config().reading
        strategy = strategy or cfg.strategy
        if strategy not in STRATEGIES:
            raise BurrowlError("INVALID_ARGUMENT", "strategy 只能是 standard / associative / quick")
        checked = field_conditions.validate_conditions(field_conditions_)
        require_knowledge_scope(scope_owner_ids)
        plan = self._plan(question, variants=variants, protected_terms=protected_terms, domains=domains)
        session = new_session(question=question, conditions=conditions or [], strategy=strategy,
                              review_filter=review_filter or cfg.review_filter, owner_id=owner_id,
                              association=cfg.association.model_dump(), context=cfg.context.model_dump(),
                              screening=cfg.screening.model_dump(), plan=plan, actor=actor)
        session["reranking"] = cfg.reranking.mode
        session["scope_owner_ids"] = scope_owner_ids
        session["field_conditions"] = checked
        session["budget"] = {k: getattr(cfg.budget, k) for k in BUDGET_KEYS}
        session["consumed"] = {}
        # 所属对象还没有任何记录时只有业务卡，没有可读材料，却因标题与问题高度相似排在最前（R18 走查发现），
        # 本会话的所有召回轮次都不收它。已有记录的所属对象照常参与：它的 L1 细节可能是 resume 没有展开的。
        session["skip_owner_ids"] = [owner_id] if owner_id and self._head(owner_id) is None else []
        self._apply_round(session, self._round(session, plan, "discovery"))
        _event(session, "start", f"开始阅读：{question[:60]}")
        if session["skip_owner_ids"]:
            _event(session, "skip_owner", f"所属对象 {owner_id} 尚无记录，不参与召回")
        self.sessions.create(session)
        last = session["rounds"][-1]
        batch = self._batch(session)
        return {"session_id": session["session_id"], "status": "active", "phase": "screening", "strategy": strategy,
                "plan": {"variants": [v["question"] for v in plan["variants"]], "matched_terms": plan["matched_terms"]},
                "batch": batch, "remaining": len(session["queue"]) - len(batch),
                "gaps": last["gaps"], "fulltext_available": bool(last["fulltext_reasons"]),
                "reused_from": last["reused_from"], "skipped_owner_ids": session["skip_owner_ids"],
                "inbox": [], "pending_requests": [], "revision": 1,
                "next": "对每个对象给出 relevant / uncertain / irrelevant、理由和筛选包 digest（reading_assess）"
                        + ("；quick 策略还要用 accepted_windows 标出可依据的窗口" if strategy == "quick" else "")}

    def assess(self, session_id: str, judgments: list[dict], *, request_id: str | None = None) -> dict:
        def change(session: dict) -> dict:
            for item in judgments:
                owner_id, verdict = item.get("owner_id"), item.get("verdict")
                state = session["owners"].get(owner_id)
                if state is None:
                    raise BurrowlError("NOT_FOUND", "该对象不在本会话的筛选结果中", {"owner_id": owner_id})
                if verdict not in VERDICTS:
                    raise BurrowlError("INVALID_ARGUMENT", "verdict 只能是 relevant / uncertain / irrelevant")
                if not item.get("digest"):
                    raise BurrowlError("INVALID_ARGUMENT", "每项判断都要带上筛选包的 digest", {"owner_id": owner_id})
                if item["digest"] != state["packet"]["digest"]:
                    raise BurrowlError("STALE_BASIS", "筛选包已变化，请基于最新筛选包判断", {"owner_id": owner_id})
                if not str(item.get("reason", "")).strip():
                    raise BurrowlError("INVALID_ARGUMENT", "判断要写理由（依据窗口里的什么内容）", {"owner_id": owner_id})
                accepted = item.get("accepted_windows") or []
                if accepted:
                    shown = {w["entry_id"]: w["record_id"]
                             for w in state["packet"]["windows"] + state["packet"]["extra_windows"]
                             if w["record_id"] in state["delivered"]}
                    if unknown := sorted(set(accepted) - shown.keys()):
                        raise BurrowlError("INVALID_ARGUMENT", "只能采纳已交付的窗口", {"unknown": unknown[:10]})
                    state["accepted"] = sorted({shown[e] for e in accepted})
                if state["judged_by"] == "user":
                    continue  # 用户的改判优先，agent 不能覆盖
                state.update(verdict=verdict, reason=str(item["reason"]), judged_by="ai")
            _event(session, "assess", f"判断 {len(judgments)} 个对象")
            batch = self._batch(session)
            relevant = [o for o in session["queue"] if session["owners"][o]["verdict"] == "relevant"]
            if not batch:
                session["phase"] = "reading" if session["strategy"] != "quick" else "noting"
            reasons = self._fulltext_reasons(session)
            hint = ("继续判断下一批" if batch else
                    "quick 模式：依据 accepted_windows 采纳的窗口写笔记（reading_note）" if session["strategy"] == "quick"
                    else "依次完整阅读相关对象（reading_read，读到 has_more=false）" if relevant else
                    "没有相关对象：可向用户申请全文补偿（reading_ask_user kind=fulltext）或结束" if reasons else
                    "没有相关对象；发现召回已充分，直接结束并说明未找到")
            return {"batch": batch, "relevant": relevant, "fulltext_available": bool(reasons), "next": hint}
        session, extra = self._agent_update(session_id, change, request_id)
        return self._envelope(session, extra)

    def peek(self, session_id: str, owner_id: str) -> dict:
        """查看某个对象的更多命中窗口（用于 uncertain 的对象）。"""
        def change(session: dict) -> dict:
            state = session["owners"].get(owner_id)
            if state is None:
                raise BurrowlError("NOT_FOUND", "该对象不在本会话中", {"owner_id": owner_id})
            extra = state["packet"]["extra_windows"]
            state["delivered"] = sorted(set(state["delivered"]) | {w["record_id"] for w in extra})
            return {"owner_id": owner_id, "windows": extra}
        session, extra = self._agent_update(session_id, change)
        return self._envelope(session, extra)

    def read(self, session_id: str, owner_id: str, page: int = 1) -> dict:
        session, _revision = self.sessions.read(session_id)
        state = session["owners"].get(owner_id)
        if state is None:
            raise BurrowlError("NOT_FOUND", "该对象不在本会话中", {"owner_id": owner_id})
        if session["strategy"] == "quick":
            raise BurrowlError("INVALID_TRANSITION", "quick 模式只依据召回片段，不读全文")
        if state["verdict"] != "relevant":
            raise BurrowlError("INVALID_TRANSITION", "只能完整阅读判为相关的对象（可先 assess 或请用户改判）")
        opened = [o for o, s in session["owners"].items() if s["pages_read"]]
        if owner_id not in opened and len(opened) >= session["context"]["max_owners"]:
            raise BurrowlError("BUDGET_EXCEEDED", "已达到本会话可完整阅读的对象上限", {"max_owners": session["context"]["max_owners"]})
        head = self._head(owner_id)
        if head is None:
            raise BurrowlError("ACCESS_DENIED", "该对象当前不可读", {"owner_id": owner_id})
        restarted = state["basis_head"] is not None and state["basis_head"] != head
        if restarted and page != 1:
            raise BurrowlError("STALE_BASIS", "对象在阅读期间有新提交，请从第 1 页重新阅读", {"owner_id": owner_id})
        content = text.page_of(self.storage, owner_id, page)

        def change(current: dict) -> dict:
            owner = current["owners"][owner_id]
            if owner["basis_head"] != head:  # 首次阅读或对象更新后从头读：固定新的依据版本
                owner.update(basis_head=head, pages_read=[])
            owner["pages_read"] = sorted(set(owner["pages_read"]) | {page})
            owner["total_pages"] = content["total_pages"]
            owner["delivered"] = sorted(set(owner["delivered"]) | set(content["ids"]))
            owner["refs"].update({r["target_id"]: r for r in content["refs"]})
            current["phase"] = "reading"
            _event(current, "read", f"阅读 {owner_id} 第 {page}/{content['total_pages']} 页")
            remaining = sorted(set(range(1, content["total_pages"] + 1)) - set(owner["pages_read"]))
            return content | {"restarted": restarted, "unread_pages": remaining,
                              "next": f"继续阅读第 {remaining[0]} 页" if remaining else "已读完，写研究笔记（reading_note）"}
        session, extra = self._agent_update(session_id, change)
        return self._envelope(session, extra)

    def note(self, session_id: str, owner_id: str, note: str, sources: list[str], *,
             clues: list[str] | None = None, request_id: str | None = None) -> dict:
        head = self._head(owner_id)

        def change(session: dict) -> dict:
            state = session["owners"].get(owner_id)
            if state is None or state["verdict"] != "relevant":
                raise BurrowlError("INVALID_TRANSITION", "只能为判为相关的对象写笔记")
            if session["strategy"] == "quick":
                if not state["accepted"]:
                    raise BurrowlError("INVALID_TRANSITION", "quick 模式先在 assess 时用 accepted_windows 采纳可依据的窗口")
                allowed = set(state["accepted"])
                basis = head
            else:
                total = state["total_pages"] or 0
                if not total or len(state["pages_read"]) < total:
                    raise BurrowlError("INVALID_TRANSITION", "请先读完该对象的全部页再写笔记",
                                   {"pages_read": state["pages_read"], "total_pages": total})
                if state["basis_head"] != head:
                    raise BurrowlError("STALE_BASIS", "对象在阅读后有新提交，请重新阅读再写笔记", {"owner_id": owner_id})
                allowed, basis = set(state["delivered"]), state["basis_head"]
            check = notes.validate(note, sources, allowed, max_tokens=session["context"]["note_max_tokens"])
            fixed = [state["refs"].get(s.split("#")[0]) or {"target_id": s} for s in sorted(set(sources))]
            state["note"] = {"text": note, "sources": sorted(set(sources)), "fixed_sources": fixed,
                             "clues": [c for c in (clues or []) if c.strip()], "basis_head": basis,
                             "tokens": check["tokens"], "digest": canonical_hash(note)[:16], "updated_at": utc_now()}
            _event(session, "note", f"保存 {owner_id} 的笔记（约 {check['tokens']} token）")
            return {"owner_id": owner_id, **check}
        session, extra = self._agent_update(session_id, change, request_id)
        return self._envelope(session, extra)

    def synthesize(self, session_id: str, note: str, sources: list[str], owner_ids: list[str], *,
                   request_id: str | None = None) -> dict:
        """跨对象综合：固定所依据的单篇笔记；任一单篇改变或陈旧后综合稿自动失效。"""
        heads = {o: self._head(o) for o in owner_ids}

        def change(session: dict) -> dict:
            if not owner_ids or any(not session["owners"].get(o, {}).get("note") for o in owner_ids):
                raise BurrowlError("INVALID_TRANSITION", "综合只能基于已保存笔记的对象")
            if stale := notes.stale_owners({"owners": {o: session["owners"][o] for o in owner_ids}}, heads):
                raise BurrowlError("STALE_BASIS", "部分单篇笔记已陈旧，请先重新阅读", {"owner_ids": sorted(stale)})
            delivered = {s for o in owner_ids for s in session["owners"][o]["note"]["sources"]}
            check = notes.validate(note, sources, delivered, max_tokens=session["context"]["note_max_tokens"])
            session["synthesis"] = {"note": note, "sources": sorted(set(sources)), "owner_ids": sorted(owner_ids),
                                    "basis": notes.basis_digest(session, owner_ids), "updated_at": utc_now()}
            session["phase"] = "synthesizing"
            _event(session, "synthesize", f"综合 {len(owner_ids)} 个对象的笔记")
            return check
        session, extra = self._agent_update(session_id, change, request_id)
        return self._envelope(session, extra)

    def ask_user(self, session_id: str, kind: str, prompt: str, options: list[str] | None = None, *,
                 request_id: str | None = None) -> dict:
        """向用户提问。fulltext：申请全文补偿；direction：请用户定方向；clarify：澄清问题。"""
        if kind not in {"fulltext", "direction", "clarify"}:
            raise BurrowlError("INVALID_ARGUMENT", "kind 只能是 fulltext / direction / clarify")

        def change(session: dict) -> dict:
            if kind == "fulltext" and not (reasons := self._fulltext_reasons(session)):
                raise BurrowlError("INVALID_TRANSITION", "发现召回已充分（没有缺口、结果数量足够、已有相关对象），不需要全文补偿")
            request = {"request_id": f"UR-{uuid.uuid4().hex[:12]}", "kind": kind, "prompt": prompt,
                       "options": options or [], "status": "pending", "answer": None, "approval_id": None,
                       "created_at": utc_now(), **({"reasons": reasons} if kind == "fulltext" else {})}
            session["requests"].append(request)
            session["status"] = "waiting_user"
            _event(session, "ask_user", f"等待用户：{prompt[:60]}")
            return {"request": request}
        session, extra = self._agent_update(session_id, change, request_id)
        return self._envelope(session, extra)

    def recall(self, session_id: str, mode: str, *, approval_id: str | None = None,
               association_text: str | None = None, request_id: str | None = None) -> dict:
        """补充召回。fulltext：在全文投影上补偿，必须带用户批准的 approval_id；
        association：按主 agent 给定的联想方向补查（仅 associative 策略，受轮数上限）。"""
        session, _ = self.sessions.read(session_id)
        if request_id and request_id in session.get("applied", {}):
            return self._envelope(*self._agent_update(session_id, lambda s: None, request_id))
        self._check_budget(session)
        if mode == "fulltext":
            if not any(r["approval_id"] == approval_id and r["status"] == "approved" and not r.get("used")
                       for r in session["requests"]):
                raise BurrowlError("APPROVAL_REQUIRED", "全文补偿需要用户批准；先用 reading_ask_user(kind=fulltext) 提出申请")
            plan, kind, corpus = session["plan"], "fulltext", "fulltext"
        elif mode == "association":
            association = session["association"]
            if session["strategy"] != "associative" or not association["enabled"]:
                raise BurrowlError("INVALID_TRANSITION", "只有 associative 策略可以联想补查")
            if association["rounds_used"] >= association["max_rounds"]:
                raise BurrowlError("BUDGET_EXCEEDED", "联想补查轮数已用完")
            direction = association_text or association["text"]
            if not direction.strip():
                raise BurrowlError("INVALID_ARGUMENT", "需要联想方向文本（由主 agent 或用户给出）")
            plan, kind, corpus = self._plan(direction), "association", "discovery"
        else:
            raise BurrowlError("INVALID_ARGUMENT", "mode 只能是 fulltext 或 association")
        round_ = self._round(session, plan, kind, corpus=corpus)

        def change(current: dict) -> dict:
            if mode == "fulltext":
                for request in current["requests"]:
                    if request["approval_id"] == approval_id:
                        request["used"] = True
            else:
                current["association"]["rounds_used"] += 1
            self._apply_round(current, deepcopy(round_))
            current["phase"] = "screening"
            batch = self._batch(current)
            return {"round": round_["id"], "batch": batch, "gaps": round_["gaps"]}
        session, extra = self._agent_update(session_id, change, request_id)
        return self._envelope(session, extra)

    def finish(self, session_id: str, outcome: str = "", *, request_id: str | None = None) -> dict:
        session, _ = self.sessions.read(session_id)
        heads = self._heads(session)

        def change(current: dict) -> dict:
            current["status"], current["phase"] = "finished", "done"
            _event(current, "finish", outcome[:120] or "完成")
            return {"handoff": notes.handoff(current, heads)}
        session, extra = self._agent_update(session_id, change, request_id)
        return self._envelope(session, extra)

    def status(self, session_id: str) -> dict:
        """会话概况 + 未读的用户输入（agent 续接时调用）。已结束的会话也可查看。"""
        session, revision = self.sessions.read(session_id)
        inbox = [m for m in session["inbox"] if not m["consumed"]]
        if inbox and session["status"] not in AGENT_CLOSED:
            session, extra = self._agent_update(session_id, lambda s: None)
            inbox, revision = extra["inbox"], extra["revision"]
        owners = [{"owner_id": o, "title": s["title"], "verdict": s["verdict"], "judged_by": s["judged_by"],
                   "pages_read": len(s["pages_read"]), "total_pages": s["total_pages"], "has_note": bool(s["note"])}
                  for o, s in ((o, session["owners"][o]) for o in session["queue"])]
        return {"session_id": session_id, "question": session["question"], "status": session["status"],
                "phase": session["phase"], "strategy": session["strategy"], "owners": owners,
                "batch": self._batch(session), "handoff": self._handoff(session), "inbox": inbox,
                "fulltext_available": bool(self._fulltext_reasons(session)),
                "consumed": session.get("consumed", {}), "budget": session.get("budget", {}),
                "pending_requests": [r for r in session["requests"] if r["status"] == "pending"], "revision": revision}

    def delegate(self, session_id: str, *, host_supports_subagents: bool) -> dict:
        """子 agent 任务包（只描述任务；实际派工由宿主完成）。"""
        session, revision = self.sessions.read(session_id)
        collaboration = self.config().collaboration
        route = "subagent" if collaboration.subagents == "auto" and host_supports_subagents else "single-agent"
        brief = (f"用 read 技能续接阅读会话 {session_id}（修订 {revision}）。问题：{session['question']}。"
                 f"策略：{session['strategy']}。先 reading_status 取当前批次与用户输入；判断、阅读、写笔记后只回报"
                 f"会话 ID、阶段与缺口，不要转述全文。")
        return {"route": route, "dispatched": False, "brief": brief,
                "model_preference": collaboration.subagent_requirements}

    def list(self, *, status: str | None = None, owner_id: str | None = None, limit: int = 50) -> list[dict]:
        items = [s for s in self.sessions.list() if (status is None or s["status"] == status)
                 and (owner_id is None or s["owner_id"] == owner_id)]
        return items[:limit]

    def view(self, session_id: str) -> dict:
        """工作台详情：完整状态（含时间线、筛选包、笔记、请求）。只读，不消费收件箱。"""
        session, revision = self.sessions.read(session_id)
        return {**session, "revision": revision, "handoff": self._handoff(session)}

    # ---------- 用户动作（工作台） ----------
    def _user_update(self, session_id: str, kind: str, payload: dict, change: Callable[[dict], Any]) -> dict:
        def apply(session: dict) -> dict:
            if session["status"] in {"aborted", "archived"} and kind not in {"unarchive"}:
                raise BurrowlError("INVALID_TRANSITION", "会话已中止或归档")
            change(session)
            session["inbox"].append({"id": f"UI-{uuid.uuid4().hex[:10]}", "at": utc_now(), "kind": kind,
                                     "payload": payload, "consumed": False})
            _event(session, "user:" + kind, payload.get("summary") or kind)
            return session
        session, revision = self.sessions.update(session_id, apply)
        return {"session_id": session_id, "status": session["status"], "revision": revision}

    def respond(self, session_id: str, request_id: str, *, decision: str, answer: str = "") -> dict:
        """回答 agent 的请求。fulltext 批准后生成 approval_id，agent 用它执行全文补偿。"""
        if decision not in {"approve", "deny", "answer"}:
            raise BurrowlError("INVALID_ARGUMENT", "decision 只能是 approve / deny / answer")
        approval_id = f"AP-{uuid.uuid4().hex[:12]}" if decision == "approve" else None

        def change(session: dict) -> None:
            request = next((r for r in session["requests"] if r["request_id"] == request_id), None)
            if request is None or request["status"] != "pending":
                raise BurrowlError("NOT_FOUND", "请求不存在或已处理", {"request_id": request_id})
            request.update(status={"approve": "approved", "deny": "denied", "answer": "answered"}[decision],
                           answer=answer, answered_at=utc_now(), approval_id=approval_id)
            if not any(r["status"] == "pending" for r in session["requests"]):
                session["status"] = "active"
        payload = {"request_id": request_id, "decision": decision, "answer": answer, "approval_id": approval_id,
                   "summary": f"用户{DECISION_WORDS[decision]}：{answer[:40]}"}
        return self._user_update(session_id, "response", payload, change) | {"approval_id": approval_id}

    def override(self, session_id: str, owner_id: str, verdict: str, reason: str = "") -> dict:
        if verdict not in VERDICTS:
            raise BurrowlError("INVALID_ARGUMENT", "verdict 只能是 relevant / uncertain / irrelevant")

        def change(session: dict) -> None:
            state = session["owners"].get(owner_id)
            if state is None:
                raise BurrowlError("NOT_FOUND", "该对象不在本会话中", {"owner_id": owner_id})
            state.update(verdict=verdict, reason=reason, judged_by="user")
        return self._user_update(session_id, "verdict", {"owner_id": owner_id, "verdict": verdict, "reason": reason,
                                                         "summary": f"用户把 {owner_id} 改判为 {verdict}"}, change)

    def direct(self, session_id: str, text_: str) -> dict:
        """补充联想方向（associative 策略的下一轮补查会使用）。"""
        def change(session: dict) -> None:
            session["association"]["text"] = text_
        return self._user_update(session_id, "direction", {"text": text_, "summary": f"联想方向：{text_[:40]}"}, change)

    def configure(self, session_id: str, *, strategy: str | None = None) -> dict:
        if strategy not in STRATEGIES:
            raise BurrowlError("INVALID_ARGUMENT", "strategy 只能是 standard / associative / quick")

        def change(session: dict) -> None:
            session["strategy"] = strategy
        return self._user_update(session_id, "strategy", {"strategy": strategy, "summary": f"切换策略为 {strategy}"},
                                 change)

    def abort(self, session_id: str, reason: str = "") -> dict:
        def change(session: dict) -> None:
            session["status"] = "aborted"
        return self._user_update(session_id, "abort", {"reason": reason, "summary": "用户中止会话"}, change)

    def archive(self, session_id: str, archived: bool = True) -> dict:
        def change(session: dict) -> None:
            session["status"] = "archived" if archived else "finished"
        return self._user_update(session_id, "archive" if archived else "unarchive", {"archived": archived}, change)
