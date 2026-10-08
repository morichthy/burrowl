"""研究笔记的校验与交接文本。

笔记写对搜索问题有用的材料知识：问题如何演进、方法/公式/条件、关键数据、反例与局限，并就近标注出处。
程序只能检查"出处是否真的交付过""依据是否还是当时那一版"，不能判断理解是否正确——那是 AI/人的职责。

固定与陈旧：写笔记时记下对象当时的 HEAD 与出处的固定引用；交接时对象 HEAD 变了（有新提交）或对象已不可读，
该笔记标为陈旧、不再交付；依据它的综合稿同样失效。需要时重新阅读并改写笔记。
"""
import re

from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash
from .session import estimate_tokens

_IDS = re.compile(r"\b(?:MEM|SRC|RUN|CLM|RES|PRJ|ALG|KNW|DAT|TOL|RPT)-[A-Za-z0-9-]+(?:#[A-Za-z0-9_.-]+)?")


def mentioned_ids(text: str) -> set[str]:
    return set(_IDS.findall(text))


def validate(note: str, sources: list[str], allowed: set[str], *, max_tokens: int) -> dict:
    """出处必须是本会话允许引用的身份（已交付；quick 下为已采纳的片段）；正文里提到的 ID 也必须在出处中。"""
    if not isinstance(note, str) or not note.strip():
        raise BurrowlError("INVALID_ARGUMENT", "笔记不能为空")
    if not sources:
        raise BurrowlError("INVALID_ARGUMENT", "笔记必须列出出处（sources）")
    unknown = sorted(set(sources) - allowed)
    if unknown:
        raise BurrowlError("INVALID_ARGUMENT", "出处不是本会话已交付（或 quick 下已采纳）的材料", {"unknown": unknown[:20]})
    unlisted = sorted(mentioned_ids(note) - set(sources))
    if unlisted:
        raise BurrowlError("INVALID_ARGUMENT", "正文提到的身份没有列入 sources", {"unlisted": unlisted[:20]})
    tokens = estimate_tokens(note)
    return {"tokens": tokens, "over_budget": tokens > max_tokens,
            "warning": f"笔记约 {tokens} token，超过上限 {max_tokens}；交接时会整篇省略，请精简" if tokens > max_tokens
            else None}


def basis_digest(session: dict, owner_ids: list[str]) -> str:
    return canonical_hash([[o, session["owners"][o]["note"]["digest"]] for o in sorted(owner_ids)])


def stale_owners(session: dict, heads: dict[str, str | None] | None) -> set[str]:
    """笔记依据的对象 HEAD 已变化或不可读的对象。heads=None 表示调用方未核对（只用于纯展示）。"""
    if heads is None:
        return set()
    return {o for o, s in session["owners"].items()
            if s.get("note") and (o not in heads or heads[o] is None or heads[o] != s["note"].get("basis_head"))}


def handoff(session: dict, heads: dict[str, str | None] | None = None) -> dict:
    """组合交接文本：有效的综合笔记优先，否则逐对象笔记；陈旧笔记不交付，超出上限的整篇省略并列出。"""
    limit = session["context"]["note_max_tokens"]
    owners = session["owners"]
    stale = stale_owners(session, heads)
    noted = [o for o in session["queue"] if owners.get(o, {}).get("note") and o not in stale]
    synthesis = session.get("synthesis")
    gaps = []
    if stale:
        gaps.append({"code": "NOTE_STALE", "message": "对象在写笔记后有新提交或已不可读，相应笔记不再交付；需要时重新阅读",
                     "owner_ids": sorted(stale)})
    if synthesis and (synthesis["basis"] != basis_digest(session, synthesis["owner_ids"])
                      or stale & set(synthesis["owner_ids"])):
        gaps.append({"code": "SYNTHESIS_STALE", "message": "综合笔记所依据的单篇笔记已改变或已陈旧，综合稿不再交付"})
        synthesis = None
    sections, used, included, omitted = [], 0, [], []
    candidates = [("综合", synthesis["note"], synthesis["owner_ids"])] if synthesis else \
        [(owners[o]["title"] or o, owners[o]["note"]["text"], [o]) for o in noted]
    for title, text, ids in candidates:
        tokens = estimate_tokens(text)
        if used + tokens > limit:
            omitted.extend(ids)
            continue
        used += tokens
        included.extend(ids)
        sections.append(f"## {title}\n\n{text}")
    if omitted:
        gaps.append({"code": "NOTE_OMITTED", "message": "超过笔记上限而整篇省略", "owner_ids": omitted})
    unread = [o for o, s in owners.items() if s.get("verdict") == "relevant" and not s.get("note")]
    if unread:
        gaps.append({"code": "RELEVANT_UNREAD", "message": "判为相关但尚未写笔记", "owner_ids": unread})
    uncertain = [o for o, s in owners.items() if s.get("verdict") == "uncertain"]
    if uncertain:
        gaps.append({"code": "UNCERTAIN", "message": "仍有判为不确定的对象", "owner_ids": uncertain})
    if session["strategy"] == "quick":
        gaps.append({"code": "QUICK_MODE", "message": "quick 模式只依据已采纳的召回片段，不代表全文覆盖"})
    clues = [{"owner_id": o, "clue": c} for o in included for c in owners[o]["note"].get("clues", [])]
    if clues:
        sections.append("## 探索线索（未核实，仅供导航）\n\n" + "\n".join(f"- {c['clue']}（{c['owner_id']}）"
                                                                         for c in clues))
    references = [owners[o]["note"].get("fixed_sources") or [{"target_id": s} for s in owners[o]["note"]["sources"]]
                  for o in included]
    flat = {r["target_id"]: r for group in references for r in group}
    for source in (synthesis or {}).get("sources", []):
        flat.setdefault(source, {"target_id": source})
    markdown = f"# {session['question']}\n\n" + "\n\n".join(sections) if sections else ""
    return {"session_id": session["session_id"], "markdown": markdown, "tokens": used, "max_tokens": limit,
            "included_owner_ids": included, "references": sorted(flat.values(), key=lambda r: r["target_id"]),
            "clues": clues, "gaps": gaps, "complete": not gaps, "checked": heads is not None}
