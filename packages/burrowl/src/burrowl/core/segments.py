"""原文快照的分段、两版快照的逐段对比、L1 对原文的覆盖度（R21 D-04、D-06、D-09）。纯函数，不读写文件。

分段（segment）是 L1 `origin.locators` 引用的单位：
- 有标题路径的格式（Markdown、docx）按标题路径合并抽取单元，key 就是标题路径，原件改动后多数章节的 key 不变；
- 其他格式（PDF 页、幻灯片、表格行段、无标题文本的行段）沿用抽取标签作 key。
同名标题在 key 后加 " (2)"、" (3)" 区分。

覆盖度是给 AI 和用户看的提示，不是判定：合理的省略、改写或合并都会拉低指标，最终由 AI 写明理由。
"""
import hashlib
import re
from difflib import SequenceMatcher
from typing import Any

NGRAM = 4            # 字符 n-gram 长度：中文按字、英文按字母，都比较稳定
THIN_RATIO = 0.3     # 认领正文字数 / 原文字数低于此值……
THIN_OVERLAP = 0.3   # ……且原文 n-gram 在认领正文中出现的比例也低于此值，标为"偏短"
MIN_CHARS = 80       # 短于此的段落不判偏短（标题、一句话说明）
EXCERPT = 200


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_segments(units: list[dict]) -> list[dict]:
    """抽取单元 [{label, text, path}] → 快照分段 [{key, labels, path, text, sha256, chars}]，保持原文顺序。"""
    merged: list[dict] = []
    for unit in units:
        path = unit.get("path") or ""
        if path and merged and merged[-1]["path"] == path:
            merged[-1]["labels"].append(unit["label"])
            merged[-1]["text"] += "\n\n" + unit["text"]
            continue
        merged.append({"key": path or unit["label"], "labels": [unit["label"]], "path": path, "text": unit["text"]})
    seen: dict[str, int] = {}
    for segment in merged:
        seen[segment["key"]] = seen.get(segment["key"], 0) + 1
        if seen[segment["key"]] > 1:
            segment["key"] = f"{segment['key']} ({seen[segment['key']]})"
        segment["sha256"] = _sha(segment["text"])
        segment["chars"] = len(_normalize(segment["text"]))
    return merged


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _grams(text: str) -> set[str]:
    flat = _normalize(text)
    return {flat[i:i + NGRAM] for i in range(max(0, len(flat) - NGRAM + 1))} if len(flat) >= NGRAM else {flat}


def excerpt(text: str, limit: int = EXCERPT) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= limit else flat[:limit] + "…"


def diff_segments(old: list[dict], new: list[dict]) -> dict[str, Any]:
    """按内容对齐两版快照：unchanged（内容相同，key 可能变了）、changed（同一位置内容改了）、removed、added。"""
    matcher = SequenceMatcher(a=[s["sha256"] for s in old], b=[s["sha256"] for s in new], autojunk=False)
    out: dict[str, list] = {"unchanged": [], "changed": [], "removed": [], "added": []}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out["unchanged"].extend({"old_key": old[i]["key"], "new_key": new[j]["key"]}
                                    for i, j in zip(range(i1, i2), range(j1, j2), strict=True))
        elif tag == "replace":
            out["changed"].append({"old_keys": [s["key"] for s in old[i1:i2]], "new_keys": [s["key"] for s in new[j1:j2]],
                                   "new_excerpt": excerpt("\n".join(s["text"] for s in new[j1:j2]))})
        elif tag == "delete":
            out["removed"].append({"old_keys": [s["key"] for s in old[i1:i2]]})
        else:
            out["added"].append({"new_keys": [s["key"] for s in new[j1:j2]],
                                 "new_excerpt": excerpt("\n".join(s["text"] for s in new[j1:j2]))})
    return out


def claims(records: list[dict], source_id: str) -> dict[str, list[dict]]:
    """各分段被哪些 L1 块认领：{key: [{record, block}]}。块写了 locators 按块认领，否则按记录的 origin.locators 认领。"""
    found: dict[str, list[dict]] = {}
    for record in records:
        origin = record.get("payload", {}).get("origin") if record.get("kind") == "detail" else None
        if not origin or origin["source_ref"]["target_id"] != source_id:
            continue
        for block in record["payload"]["blocks"]:
            for key in block.get("locators") or origin["locators"]:
                found.setdefault(key, []).append({"record": record, "block": block})
    return found


def coverage(segments: list[dict], records: list[dict], source_id: str, omitted: list[dict]) -> dict[str, Any]:
    """逐段覆盖：status = covered / thin / uncovered / omitted；附认领它的块与原文开头（未覆盖、偏短时）。

    指标：ratio = 认领正文字数 / 原文字数（块同时认领多段时按原文字数比例分摊）；
    overlap = 原文的字符 n-gram 在认领正文中出现的比例。两者都低于阈值才算偏短。"""
    by_key = claims(records, source_id)
    reasons = {key: item["reason"] for item in omitted for key in item["locators"]}
    claimed_total: dict[int, int] = {}  # 块被多少原文字数认领，用来分摊
    for key, hits in by_key.items():
        size = next((s["chars"] for s in segments if s["key"] == key), 0)
        for hit in hits:
            claimed_total[id(hit["block"])] = claimed_total.get(id(hit["block"]), 0) + size
    rows, known = [], {s["key"] for s in segments}
    for segment in segments:
        hits = by_key.get(segment["key"], [])
        row: dict[str, Any] = {"key": segment["key"], "chars": segment["chars"],
                               "claimed_by": [{"record_id": h["record"]["record_id"], "handle": h["record"].get("handle"),
                                               "block_id": h["block"]["block_id"]} for h in hits]}
        if hits:
            text = "\n".join(h["block"]["markdown"] for h in hits)
            share = sum(len(_normalize(h["block"]["markdown"])) * segment["chars"] / max(1, claimed_total[id(h["block"])])
                        for h in hits)
            grams = _grams(segment["text"])
            row["ratio"] = round(share / max(1, segment["chars"]), 2)
            row["overlap"] = round(len(grams & _grams(text)) / max(1, len(grams)), 2)
            thin = segment["chars"] >= MIN_CHARS and row["ratio"] < THIN_RATIO and row["overlap"] < THIN_OVERLAP
            row["status"] = "thin" if thin else "covered"
        elif segment["key"] in reasons:
            row |= {"status": "omitted", "reason": reasons[segment["key"]]}
        else:
            row["status"] = "uncovered"
        if row["status"] in {"thin", "uncovered"}:
            row["excerpt"] = excerpt(segment["text"])
        rows.append(row)
    counts = {status: sum(r["status"] == status for r in rows) for status in ("covered", "thin", "uncovered", "omitted")}
    unknown = sorted((set(by_key) | set(reasons)) - known)  # 写错的或原件改版后已不存在的 locator
    return {"segments": rows, "counts": counts, "unknown_locators": unknown}


def expansion(segments: list[dict], records: list[dict], source_id: str, omitted: list[dict]) -> list[dict]:
    """按原文顺序拼出展开视图：每段列出认领它的块（同一块只在第一次出现时给正文），省略段给理由，其余为缺口。"""
    by_key = claims(records, source_id)
    reasons = {key: item["reason"] for item in omitted for key in item["locators"]}
    shown: set[tuple[str, str]] = set()
    out = []
    for segment in segments:
        entry: dict[str, Any] = {"key": segment["key"], "path": segment["path"], "blocks": []}
        for hit in by_key.get(segment["key"], []):
            ident = (hit["record"]["record_id"], hit["block"]["block_id"])
            block = {"record_id": ident[0], "revision": hit["record"]["revision"], "handle": hit["record"].get("handle"),
                     "title": hit["record"]["title"], "block_id": ident[1]}
            if ident in shown:
                block["see_above"] = True
            else:
                block["markdown"] = hit["block"]["markdown"]
                shown.add(ident)
            entry["blocks"].append(block)
        if not entry["blocks"]:
            entry["status"] = "omitted" if segment["key"] in reasons else "gap"
            if segment["key"] in reasons:
                entry["reason"] = reasons[segment["key"]]
            else:
                entry["excerpt"] = excerpt(segment["text"])
        else:
            entry["status"] = "covered"
        out.append(entry)
    return out
