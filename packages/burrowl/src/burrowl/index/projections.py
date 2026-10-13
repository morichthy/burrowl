"""从一个 Owner 的当前记录确定性地生成检索条目（纯函数，不读写文件）。

调用者（服务层）负责：只传当前可读的记录、给出每条结论的复核徽标、标出来源不可用而应隐藏的记录。
"""
import re
from dataclasses import dataclass
from typing import Any

from ..core import predicates
from ..core.hashing import canonical_hash
from ..core.records import claim_hash
from ..core.technical import description_text
from . import graph as graph_projection

# v2（R12 P1）：条目带上下文信封 embed_prefix；技术单元的记录级条目包含全部块正文
# v3（R19 D-04）：发现投影收录结论、已采纳的关联、概念页；关联条目有可检索的文字，只收已采纳的
FULLTEXT_VERSION = "fulltext-v4"  # v4：结论条目的角色读法（R23 D-14）
DISCOVERY_VERSION = "discovery-v4"  # v4：同上
SOURCE_VERSION = "source-v2"
CORPORA = ("fulltext", "discovery", "source")
# 不进入全文检索的类型：L0 原文只作追溯；文稿只是编排；复核/表示/策略等是元数据。
_FULLTEXT_SKIP = {"source", "document", "document_section", "review", "representation", "policy", "feedback"}
_SEARCHABLE_FIELDS = ("question", "objective", "hypothesis", "action", "observation", "decision", "topic",
                      "problem_structure", "recommendation", "applicable", "prohibited", "failure_modes",
                      "retry_conditions", "next_steps", "constraints", "success_criteria", "missing_evidence",
                      "next_step", "stop_reason", "blocker", "reopen_condition", "method", "steps", "results",
                      "limitations", "open_questions", "current_stage", "methods")
UNSAFE_BADGES = {"disputed", "retracted", "superseded", "stale"}


@dataclass(frozen=True)
class OwnerInfo:
    owner_id: str
    owner_type: str
    title: str
    sensitivity: str
    fingerprint: str
    keywords: tuple[str, ...] = ()
    description: str = ""


def review_class(badges: list[str]) -> str:
    """把一组结论徽标归为一类，供检索过滤：none / accepted / unsafe / unreviewed / mixed。"""
    if not badges:
        return "none"
    if any(b in UNSAFE_BADGES for b in badges):
        return "unsafe"
    if all(b == "accepted" for b in badges):
        return "accepted"
    if not any(b == "accepted" for b in badges):
        return "unreviewed"
    return "mixed"


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def _payload_text(payload: dict) -> str:
    """只取作者写的科学字段；引用哈希、路径、审计元数据不参与匹配。"""
    values = [s for key in _SEARCHABLE_FIELDS for s in _strings(payload.get(key))]
    for stage in payload.get("stages", []):
        values.extend(_strings([stage.get(k) for k in ("situation", "action", "reason", "outcome")]))
    for parameter in payload.get("parameters", []):
        if isinstance(parameter, dict):
            values.extend(str(parameter[k]) for k in ("name", "value", "unit", "description") if parameter.get(k) is not None)
    values.extend(f.get("latex", "") for f in payload.get("formulas", []) if isinstance(f, dict))
    values.extend(f.get("caption", "") for f in payload.get("figures", []) if isinstance(f, dict))
    return "\n".join(v for v in values if v and v.strip())


LEVEL_LABELS = {"L0": "L0 原始材料", "L1": "L1 技术单元", "L2": "L2 研究经过", "L3": "L3 经验", "L4": "L4 概览"}
KIND_LABELS = {"goal": "目标", "question": "问题", "route": "路线", "checkpoint": "检查点", "association": "关联",
               "consolidation": "收口", "wiki_page": "Wiki 页", "claim": "结论"}
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.M)


def _heading(markdown: str) -> str | None:
    match = _HEADING.search(markdown)
    return match.group(1).strip() if match else None


def envelope(owner: OwnerInfo, record: dict, block: dict | None = None, *, claim: dict | None = None) -> str:
    """上下文信封（R12 D-19、D-20、§4.11）：嵌入前拼在文本前面的标题链和简短字段，只进向量文本，不进 FTS。

    行按保留优先级排列，嵌入器按 token 预算（模型窗口的 1/4）从尾部裁掉：
    标题链（含层级说明）→ 检索问题 → 依赖的定义块原文。不复制概览，避免不同块的向量趋同（D-20）。
    """
    label = LEVEL_LABELS.get(record.get("level") or "") or KIND_LABELS.get(record["kind"], record["kind"])
    chain = [owner.title, f"{label}：{record['title']}"]
    if block is not None:
        chain.append(_heading(block["markdown"]) or block["block_id"])
    if claim is not None:
        chain.append("结论")
    lines = [" › ".join(c for c in chain if c)]
    if record["kind"] == "detail":
        question = record["payload"]["retrieval_description"].get("question", "").strip()
        if question:
            lines.append(f"问题：{question}")
        if block is not None:
            by_id = {b["block_id"]: b for b in record["payload"].get("blocks", [])}
            lines.extend(by_id[d]["markdown"].strip() for d in block.get("requires_block_ids", []) if d in by_id)
    return "\n".join(lines)


def _ref(record: dict, locator: str = "record") -> dict:
    return {"target_kind": "record", "target_id": record["record_id"], "revision": record["revision"],
            "sha256": record["record_hash"], "locator": locator, "relation": "references"}


def _entry(corpus: str, version: str, owner: OwnerInfo, *, key: str, record_id: str, canonical_id: str,
           kind: str, level: str | None, revision: int | None, content_hash: str, title: str, text: str,
           fixed_ref: dict, discovery: str, sensitivity: str, review: str, block_id: str | None = None,
           embed_prefix: str | None = None) -> dict:
    entry = {"entry_id": f"{corpus}:{owner.owner_id}:{key}", "corpus": corpus, "owner_id": owner.owner_id,
             "owner_type": owner.owner_type, "record_id": record_id, "canonical_id": canonical_id, "kind": kind,
             "level": level, "revision": revision, "content_hash": content_hash, "block_id": block_id,
             "title": title, "text": text, "fixed_ref": fixed_ref, "discovery": discovery,
             "sensitivity": sensitivity, "review": review, "embed_prefix": embed_prefix or title}
    entry["signature"] = canonical_hash({"version": version, "entry": entry})
    return entry


def _sensitivity(owner: OwnerInfo, record: dict) -> str:
    rank = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
    return max(owner.sensitivity, record["sensitivity"], key=lambda v: rank.get(v, 3))


def _association_ends(record: dict, titles: dict[str, str]) -> list[str]:
    payload = record["payload"]
    return [titles.get(graph_projection.target_node(payload[e])) or payload[e]["target_id"] for e in ("from", "to")]


def _association_title(record: dict, titles: dict[str, str]) -> str:
    ends = _association_ends(record, titles)
    return f"关联：{ends[0]} ↔ {ends[1]}"


def _association_text(record: dict, titles: dict[str, str]) -> str:
    """关联条目的文字（R19 D-04）：读法与确定度、说明、共同结构、迁移边界、差异。说明才是关联承载的知识。"""
    payload = record["payload"]
    head = graph_projection.phrase(payload["relation"], *_association_ends(record, titles), edge_category="navigation")
    lines = [f"{head}（{payload.get('certainty') or 'plausible'}）", payload.get("explanation") or ""]
    if payload.get("shared_structure"):
        lines.append(f"共同结构：{payload['shared_structure']}")
    for label, key in (("迁移边界", "transfer_limits"), ("差异", "differences")):
        if payload.get(key):
            lines.append(f"{label}：" + "；".join(_strings(payload[key])))
    return "\n".join(line for line in lines if line.strip())


def _claim_entry(corpus: str, version: str, owner: OwnerInfo, record: dict, claim: dict, badge: str) -> dict:
    """结论条目：陈述 + 适用范围（+ 有 relation 时的角色读法，R23 D-14）。可见范围与敏感级别跟随所在记录。"""
    relation = claim.get("relation")
    roles = predicates.reading(relation) if relation else ""
    text = "\n".join(t for t in (claim["statement"], claim["scope"], roles) if t)
    return _entry(corpus, version, owner, key=claim["claim_id"], record_id=record["record_id"],
                  canonical_id=claim["claim_id"], kind="claim", level=record["level"], revision=record["revision"],
                  content_hash=claim_hash(claim), title=claim["statement"], text=text,
                  fixed_ref={"target_kind": "claim", "target_id": claim["claim_id"], "revision": None,
                             "sha256": claim_hash(claim), "locator": "statement", "relation": "references"},
                  discovery=record["discovery"], sensitivity=_sensitivity(owner, record),
                  review=review_class([badge]), embed_prefix=envelope(owner, record, claim=claim))


def _accepted_association(record: dict) -> bool:
    return record["kind"] == "association" and record["payload"].get("status") == "accepted"


def _concept_text(record: dict) -> str | None:
    """概念页条目（R19 D-04）：active 的 concept / entity 页，标题 + 别名 + 定义 + 领域。"""
    payload = record["payload"]
    if record["kind"] != "wiki_page" or payload.get("page_type") not in graph_projection.CONCEPT_PAGE_TYPES \
            or payload.get("status", "active") != "active":
        return None
    parts = (record["title"], "、".join(_strings(payload.get("aliases"))), record["body_markdown"],
             "、".join(_strings(payload.get("domains"))))
    return "\n".join(t for t in parts if t.strip())


def fulltext(owner: OwnerInfo, records: list[dict], badges: dict[str, str], hidden: set[str],
             titles: dict[str, str] | None = None) -> list[dict]:
    """titles：关联两端节点 → 标题（服务层从全库记录给出；受限记录不给）。"""
    titles = titles or {}
    entries = []
    for record in records:
        if record["kind"] in _FULLTEXT_SKIP or record["record_id"] in hidden or record["sensitivity"] == "restricted":
            continue
        if record["kind"] == "association" and not _accepted_association(record):
            continue  # 候选、已拒绝、已撤回的关联不是知识
        payload = record["payload"]
        claim_badges = [badges.get(c["claim_id"], "not-reviewed") for c in payload.get("claims", [])]
        common = {"record_id": record["record_id"], "kind": record["kind"], "level": record["level"],
                  "revision": record["revision"], "discovery": record["discovery"],
                  "sensitivity": _sensitivity(owner, record)}
        title = record["title"]
        if record["kind"] == "detail":  # 记录级条目 = 检索说明 + 全部块正文（R12 T-P1.4），块级条目另建
            body = "\n\n".join([description_text(record), *(b["markdown"] for b in payload.get("blocks", []))])
            extra = _payload_text({k: v for k, v in payload.items() if k not in {"blocks"}})
        elif record["kind"] == "association":
            body, extra, title = _association_text(record, titles), "", _association_title(record, titles)
        else:
            body, extra = record["body_markdown"], _payload_text(payload)
        text = "\n".join(t for t in (body, extra, " ".join(record.get("keywords", []))) if t)
        entries.append(_entry("fulltext", FULLTEXT_VERSION, owner, key=record["record_id"],
                              canonical_id=record["record_id"], content_hash=record["content_hash"],
                              title=title, text=text, fixed_ref=_ref(record),
                              review=review_class(claim_badges), embed_prefix=envelope(owner, record), **common))
        if record["kind"] == "detail":
            for block in payload.get("blocks", []):
                entries.append(_entry("fulltext", FULLTEXT_VERSION, owner, key=f"{record['record_id']}#{block['block_id']}",
                                      canonical_id=record["record_id"], content_hash=record["content_hash"],
                                      title=record["title"], text=block["markdown"], block_id=block["block_id"],
                                      fixed_ref=_ref(record, "block:" + block["block_id"]), review="none",
                                      embed_prefix=envelope(owner, record, block), **common))
        for claim in payload.get("claims", []):
            entries.append(_claim_entry("fulltext", FULLTEXT_VERSION, owner, record, claim,
                                        badges.get(claim["claim_id"], "not-reviewed")))
    return entries


def _compact(record: dict) -> str | None:
    """发现投影的压缩文本；None 表示该记录刻意不进入发现面。"""
    payload, level = record["payload"], record["level"]
    if level == "L1":
        return description_text(record) if record["kind"] == "detail" else None
    if level == "L2":
        parts = [payload.get("question", "")]
        for stage in payload.get("stages", []):  # action 刻意排除：发现只需骨架，完整经过在选中后再读
            parts.extend(_strings([stage.get(k) for k in ("situation", "reason", "outcome")]))
        parts.extend(_strings(payload.get("limitations")))
        return "\n".join(p for p in parts if p.strip())
    if level == "L3":
        return "\n".join(p for p in [record["body_markdown"], *[s for k in (
            "problem_structure", "recommendation", "applicable", "prohibited", "failure_modes", "retry_conditions")
            for s in _strings(payload.get(k))]] if p.strip())
    if level == "L4":
        return "\n".join(p for p in [record["body_markdown"], *[s for k in (
            "question", "methods", "results", "current_stage", "limitations", "open_questions", "topic", "next_steps")
            for s in _strings(payload.get(k))]] if p.strip())
    return None


def discovery(owner: OwnerInfo, records: list[dict], badges: dict[str, str], hidden: set[str],
              titles: dict[str, str] | None = None) -> list[dict]:
    """发现面：对象元数据、L1 检索说明、L2 骨架、L3/L4 正文，以及（R19 D-04）结论、已采纳的关联、概念页。"""
    titles = titles or {}
    owner_ref = {"target_kind": "owner", "target_id": owner.owner_id, "revision": None, "sha256": owner.fingerprint,
                 "locator": "", "relation": "references"}
    meta = "\n".join(v for v in [owner.owner_id, owner.owner_type, owner.description, *owner.keywords] if v)
    entries = [_entry("discovery", DISCOVERY_VERSION, owner, key="owner", record_id=owner.owner_id,
                      canonical_id=owner.owner_id, kind="owner", level=None, revision=None,
                      content_hash=owner.fingerprint, title=owner.title, text=meta, fixed_ref=owner_ref,
                      discovery="workspace_summary", sensitivity=owner.sensitivity, review="none")]
    for record in records:
        if record["record_id"] in hidden or record["sensitivity"] == "restricted" or record["kind"] in _FULLTEXT_SKIP:
            continue
        title, text = record["title"], _compact(record)
        if _accepted_association(record):
            title, text = _association_title(record, titles), _association_text(record, titles)
        elif text is None:
            text = _concept_text(record)
        claim_badges = [badges.get(c["claim_id"], "not-reviewed") for c in record["payload"].get("claims", [])]
        if text is not None:
            locator = "retrieval_description" if record["level"] == "L1" else "record"
            entries.append(_entry("discovery", DISCOVERY_VERSION, owner, key=record["record_id"],
                                  record_id=record["record_id"], canonical_id=record["record_id"], kind=record["kind"],
                                  level=record["level"], revision=record["revision"],
                                  content_hash=record["content_hash"], title=title, text=text,
                                  fixed_ref=_ref(record, locator), discovery=record["discovery"],
                                  sensitivity=_sensitivity(owner, record), review=review_class(claim_badges),
                                  embed_prefix=envelope(owner, record)))
        for claim in record["payload"].get("claims", []) or []:  # 结论随所在记录可见
            entries.append(_claim_entry("discovery", DISCOVERY_VERSION, owner, record, claim,
                                        badges.get(claim["claim_id"], "not-reviewed")))
    return entries


def source(source_id: str, title: str, sha256: str, sensitivity: str, units: list[dict]) -> list[dict]:
    """来源文件的分段条目。owner_id 用来源 ID 本身；分段标签作为块身份与定位。
    信封 = 文件名 › 标题路径（没有标题时用分段标签，如页码、幻灯片号）。"""
    info = OwnerInfo(source_id, "source", title, sensitivity, sha256)
    entries = []
    for unit in units:
        if not unit["text"]:
            continue
        ref = {"target_kind": "file", "target_id": source_id, "revision": None, "sha256": sha256,
               "locator": unit["label"], "relation": "references"}
        entries.append(_entry("source", SOURCE_VERSION, info, key=unit["label"], record_id=source_id,
                              canonical_id=source_id, kind="source_file", level="L0", revision=None,
                              content_hash=sha256, title=title, text=unit["text"], fixed_ref=ref,
                              discovery="workspace_summary", sensitivity=sensitivity, review="none",
                              block_id=unit["label"],
                              embed_prefix=" › ".join([title, unit.get("path") or unit["label"]])))
    return entries


def signature(entries: list[dict], version: str) -> str:
    return canonical_hash({"version": version, "entries": sorted([e["entry_id"], e["signature"]] for e in entries)})
