"""召回与筛选包：先在 Owner 压缩发现投影里找对象，再给每个对象 2–4 个互补的命中窗口供三态判断。

筛选包只是"值不值得读"的线索，不是完整正文。窗口尽量来自不同层级/记录（互补），
可选的 Cross-encoder 只调整窗口次序、不删除任何对象；模型不可用时如实报告并保持原序
（reranking=required 时则直接报错）。显式字段条件只在命中条目的原文里核对"字段：值"，
结果只影响对象次序并随筛选包展示，不代表适用性已复核。
"""
from typing import Any

from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash
from ...index import conditions as field_conditions
from ...index import rerank
from ...index.tokenize import tokens
from ..indexing import IndexService

WINDOW_CHARS = 480
LEVEL_ORDER = {"L4": 0, "L3": 1, "L2": 2, "L1": 3, None: 4}


def _window(text: str, terms: set[str]) -> str:
    lowered = text.casefold()
    positions = [p for p in (lowered.find(t) for t in terms if len(t) > 1) if p >= 0]
    start = max(0, min(positions) - WINDOW_CHARS // 4) if positions else 0
    snippet = text[start:start + WINDOW_CHARS]
    return ("…" if start else "") + snippet + ("…" if start + WINDOW_CHARS < len(text) else "")


def recall(index: IndexService, plan: dict, *, corpus: str, owner_ids: list[str] | None, review_filter: str,
           exclude: set[str], graph_route=None, readiness: tuple | None = None) -> tuple[list[dict], list[dict]]:
    """返回 (按融合分排序的对象列表, 缺口)。每个对象带命中的条目 ID。graph_route：图路线设置（默认关闭）。
    readiness：调用方刚做过的就绪判断（IndexService.search_state），省掉重复判断。"""
    result = index.search_plan(plan, corpus=corpus, owner_ids=owner_ids, review_filter=review_filter, group="owner",
                               per_route=200, graph_route=graph_route, readiness=readiness)
    owners = [item for item in result["items"] if item["owner_id"] not in exclude]
    return owners, result["gaps"]


def _load_model(reranking: str, reranker_path, diagnostics: dict):
    if reranking == "off":
        return None
    if reranker_path is None:
        if reranking == "required":
            raise BurrowlError("CAPABILITY_UNAVAILABLE", "重排模式为 required，但本机没有重排模型（burrowl models install）")
        diagnostics.update(reranking="unavailable", reason="未安装重排模型")
        return None
    try:
        model = rerank.load(reranker_path)
        diagnostics["reranking"] = "ce"
        return model
    except BurrowlError as exc:
        if reranking == "required":
            raise
        diagnostics.update(reranking="unavailable", reason=exc.message)
        return None


def packets(index: IndexService, plan: dict, owners: list[dict], *, regular: int, maximum: int,
            reranking: str, reranker_path=None, conditions: list[dict] | None = None,
            ) -> tuple[dict[str, dict], dict[str, Any]]:
    """为每个对象挑选窗口：优先不同记录、高层级（L4→L1），再按命中次序。"""
    terms = {t.casefold() for v in plan["variants"] for t in tokens(" ".join(v["lexical_terms"]))}
    question = plan["variants"][0]["question"]
    diagnostics: dict[str, Any] = {"reranking": "off", "rerank_items": 0}
    model = _load_model(reranking, reranker_path, diagnostics)
    result = {}
    for owner in owners:
        entries = index.db.entries([h["entry_id"] for h in owner.get("hits", [])])
        entries.sort(key=lambda e: (LEVEL_ORDER.get(e["level"], 4), e["entry_id"]))
        chosen, seen_records = [], set()
        for entry in entries:  # 先每条记录一个窗口（互补），再补同记录的其他命中
            if entry["record_id"] not in seen_records:
                chosen.append(entry)
                seen_records.add(entry["record_id"])
        chosen += [e for e in entries if e not in chosen]
        windows = [{"entry_id": e["entry_id"], "record_id": e["record_id"], "title": e["title"], "level": e["level"],
                    "kind": e["kind"], "review": e["review"], "text": _window(e["text"], terms), "ref": e["fixed_ref"]}
                   for e in chosen[:maximum]]
        if model and windows:
            try:
                fitting = [w for w in windows if model.count_tokens(question, w["text"]) <= model.max_length]
                scores = model.score_pairs([(question, w["text"]) for w in fitting])
                diagnostics["rerank_items"] += len(fitting)
                for window, score in zip(fitting, scores, strict=True):
                    window["ce_score"] = round(score, 4)
                windows.sort(key=lambda w: -w.get("ce_score", float("-inf")))
            except BurrowlError as exc:
                if reranking == "required":
                    raise
                diagnostics.update(reranking="partial", reason=exc.message)
        packet = {"owner_id": owner["owner_id"], "title": owner.get("title"), "score": round(owner["score"], 6),
                  "channels": sorted({s["channel"] for s in owner.get("query_sources", [])}),
                  "windows": windows[:regular], "extra_windows": windows[regular:maximum]}
        if conditions:
            evidence = [{"text": e["text"], "ref": e["fixed_ref"]} for e in entries]
            packet["conditions"] = field_conditions.evaluate(conditions, evidence)
        packet["digest"] = canonical_hash([w["entry_id"] for w in packet["windows"]])[:16]
        result[owner["owner_id"]] = packet
    return result, diagnostics


def condition_rank(packet: dict) -> tuple[int, int]:
    """条件排序键：有明确冲突的对象排后，满足条件多的排前；其余保持融合次序（稳定排序）。"""
    states = [c["status"] for c in packet.get("conditions", [])]
    return (states.count("conflict"), -states.count("satisfied"))
