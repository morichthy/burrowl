"""从一个 Owner 的当前记录确定性地生成检索条目（纯函数，不读写文件）。

调用者（服务层）负责：只传当前可读的记录、给出每条结论的复核徽标、标出来源不可用而应隐藏的记录。
"""
from dataclasses import dataclass
from typing import Any

from ..core.hashing import canonical_hash
from ..core.technical import description_text

FULLTEXT_VERSION = "fulltext-v1"
DISCOVERY_VERSION = "discovery-v1"
SOURCE_VERSION = "source-v1"
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


def _ref(record: dict, locator: str = "record") -> dict:
    return {"target_kind": "record", "target_id": record["record_id"], "revision": record["revision"],
            "sha256": record["record_hash"], "locator": locator, "relation": "references"}


def _entry(corpus: str, version: str, owner: OwnerInfo, *, key: str, record_id: str, canonical_id: str,
           kind: str, level: str | None, revision: int | None, content_hash: str, title: str, text: str,
           fixed_ref: dict, discovery: str, sensitivity: str, review: str, block_id: str | None = None) -> dict:
    entry = {"entry_id": f"{corpus}:{owner.owner_id}:{key}", "corpus": corpus, "owner_id": owner.owner_id,
             "owner_type": owner.owner_type, "record_id": record_id, "canonical_id": canonical_id, "kind": kind,
             "level": level, "revision": revision, "content_hash": content_hash, "block_id": block_id,
             "title": title, "text": text, "fixed_ref": fixed_ref, "discovery": discovery,
             "sensitivity": sensitivity, "review": review}
    entry["signature"] = canonical_hash({"version": version, "entry": entry})
    return entry


def _sensitivity(owner: OwnerInfo, record: dict) -> str:
    rank = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
    return max(owner.sensitivity, record["sensitivity"], key=lambda v: rank.get(v, 3))


def fulltext(owner: OwnerInfo, records: list[dict], badges: dict[str, str], hidden: set[str]) -> list[dict]:
    entries = []
    for record in records:
        if record["kind"] in _FULLTEXT_SKIP or record["record_id"] in hidden or record["sensitivity"] == "restricted":
            continue
        payload = record["payload"]
        claim_badges = [badges.get(c["claim_id"], "not-reviewed") for c in payload.get("claims", [])]
        common = {"record_id": record["record_id"], "kind": record["kind"], "level": record["level"],
                  "revision": record["revision"], "discovery": record["discovery"],
                  "sensitivity": _sensitivity(owner, record)}
        if record["kind"] == "detail":
            body = description_text(record)
            extra = _payload_text({k: v for k, v in payload.items() if k not in {"blocks"}})
        else:
            body, extra = record["body_markdown"], _payload_text(payload)
        text = "\n".join(t for t in (body, extra, " ".join(record.get("keywords", []))) if t)
        entries.append(_entry("fulltext", FULLTEXT_VERSION, owner, key=record["record_id"],
                              canonical_id=record["record_id"], content_hash=record["content_hash"],
                              title=record["title"], text=text, fixed_ref=_ref(record),
                              review=review_class(claim_badges), **common))
        if record["kind"] == "detail":
            for block in payload.get("blocks", []):
                entries.append(_entry("fulltext", FULLTEXT_VERSION, owner, key=f"{record['record_id']}#{block['block_id']}",
                                      canonical_id=record["record_id"], content_hash=record["content_hash"],
                                      title=record["title"], text=block["markdown"], block_id=block["block_id"],
                                      fixed_ref=_ref(record, "block:" + block["block_id"]), review="none", **common))
        for claim in payload.get("claims", []):
            badge = badges.get(claim["claim_id"], "not-reviewed")
            entries.append(_entry("fulltext", FULLTEXT_VERSION, owner, key=claim["claim_id"],
                                  canonical_id=claim["claim_id"], content_hash=canonical_hash(claim),
                                  title=claim["statement"], text=f"{claim['statement']}\n{claim['scope']}",
                                  fixed_ref={"target_kind": "claim", "target_id": claim["claim_id"], "revision": None,
                                             "sha256": canonical_hash(claim), "locator": "statement",
                                             "relation": "references"},
                                  review=review_class([badge]), **(common | {"kind": "claim"})))
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


def discovery(owner: OwnerInfo, records: list[dict], badges: dict[str, str], hidden: set[str]) -> list[dict]:
    owner_ref = {"target_kind": "owner", "target_id": owner.owner_id, "revision": None, "sha256": owner.fingerprint,
                 "locator": "", "relation": "references"}
    meta = "\n".join(v for v in [owner.owner_id, owner.owner_type, owner.description, *owner.keywords] if v)
    entries = [_entry("discovery", DISCOVERY_VERSION, owner, key="owner", record_id=owner.owner_id,
                      canonical_id=owner.owner_id, kind="owner", level=None, revision=None,
                      content_hash=owner.fingerprint, title=owner.title, text=meta, fixed_ref=owner_ref,
                      discovery="workspace_summary", sensitivity=owner.sensitivity, review="none")]
    for record in records:
        text = _compact(record)
        if text is None or record["record_id"] in hidden or record["sensitivity"] == "restricted":
            continue
        claim_badges = [badges.get(c["claim_id"], "not-reviewed") for c in record["payload"].get("claims", [])]
        entries.append(_entry("discovery", DISCOVERY_VERSION, owner, key=record["record_id"],
                              record_id=record["record_id"], canonical_id=record["record_id"], kind=record["kind"],
                              level=record["level"], revision=record["revision"], content_hash=record["content_hash"],
                              title=record["title"], text=text,
                              fixed_ref=_ref(record, "retrieval_description" if record["level"] == "L1" else "record"),
                              discovery=record["discovery"], sensitivity=_sensitivity(owner, record),
                              review=review_class(claim_badges)))
    return entries


def source(source_id: str, title: str, sha256: str, sensitivity: str, units: list[dict]) -> list[dict]:
    """来源文件的分段条目。owner_id 用来源 ID 本身；分段标签作为块身份与定位。"""
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
                              block_id=unit["label"]))
    return entries


def signature(entries: list[dict], version: str) -> str:
    return canonical_hash({"version": version, "entries": sorted([e["entry_id"], e["signature"]] for e in entries)})
