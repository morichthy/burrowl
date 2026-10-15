"""技术单元（L1 detail）与文稿编排（document / document_section）的领域规则。

检索说明与完整正文块分开保存；选择某些块时只自动补齐其声明的前置块，
不放宽任何读取权限或预算。纯函数，不读写文件。
"""
import re
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from .errors import BurrowlError
from .hashing import is_sha256

Errors = list[tuple[str, str]]
Resolver = Callable[[dict], Any]

DESCRIPTION_LABELS = (
    ("question", "问题"), ("method", "方法"), ("key_findings", "主要发现"),
    ("applicable", "适用条件"), ("not_applicable", "不适用条件"), ("limitations", "限制"))


def description_text(record: dict) -> str:
    """L1 的检索说明全文（含适用边界）；非技术单元返回正文。"""
    if record.get("kind") != "detail":
        return record.get("body_markdown", "")
    description = record["payload"]["retrieval_description"]
    parts = []
    for key, label in DESCRIPTION_LABELS:
        value = description[key]
        text = "\n".join("- " + item for item in value) if isinstance(value, list) else value
        parts.append(f"{label}：\n{text}")
    return "\n\n".join(parts)


# 块正文中显示本单元的图：旧写法 ![图注](figure:序号)（R21），新写法 ![[#锚点名]]（R24 D-03）
_LEGACY_EMBED = re.compile(r"!\[[^\]]*\]\(figure:(\d+)\)")
_ANCHOR_EMBED = re.compile(r"!\[\[#([^\[\]|\n]+?)(?:\|[^\[\]\n]*)?\]\]")
_ANCHOR_FORBIDDEN = re.compile(r"[\[\]|#/\s]")


def figure_anchors(payload: dict) -> dict[str, int]:
    """图锚点名 → 序号（R24 D-01）：显式 label 优先；没有 label 的图用推导名 fig-<序号+1>，只用于解析。
    推导名与显式名冲突时以显式名为准（冲突本身由 figure_errors 报告）。"""
    figures = payload.get("figures") or []
    explicit = {f["label"]: i for i, f in enumerate(figures) if isinstance(f, dict) and f.get("label")}
    derived = {f"fig-{i + 1}": i for i, f in enumerate(figures) if isinstance(f, dict) and not f.get("label")}
    return {**derived, **explicit}


def figure_display_name(payload: dict, index: int) -> str:
    """工作台与引用对照里的图名：显式 label 原样（fig-1 显示为 Figure 1），没有 label 时为"图 N"。"""
    label = ((payload.get("figures") or [])[index] or {}).get("label")
    if not label:
        return f"图 {index + 1}"
    match = re.fullmatch(r"fig-(.+)", label)
    return f"Figure {match[1]}" if match else label


def embedded_figures(payload: dict, markdown: str, *, legacy: bool = True) -> set[int]:
    """一段块正文在原位置显示了本单元的哪些图（两种写法都识别；不存在的锚点忽略，由校验报告）。
    legacy=False 只算新写法：旧写法的方括号里已经带着图注。"""
    anchors = figure_anchors(payload)
    found = {int(m[1]) for m in _LEGACY_EMBED.finditer(markdown)} if legacy else set()
    found |= {anchors[m[1].strip()] for m in _ANCHOR_EMBED.finditer(markdown) if m[1].strip() in anchors}
    return found


def figure_errors(payload: dict) -> Errors:
    """图锚点名的写法、重复，以及与块号的冲突（R24 D-01：图与块共用记录内的 # 命名空间）。"""
    errors: Errors = []
    blocks = {b["block_id"] for b in payload.get("blocks", [])}
    seen: dict[str, int] = {}
    for i, figure in enumerate(payload.get("figures") or []):
        label = figure.get("label")
        if label is not None and (not label or len(label) > 64 or _ANCHOR_FORBIDDEN.search(label)):
            errors.append((f"/figures/{i}/label", "图的锚点名不超过 64 字、不能含空白与 [ ] | # /，建议按原文图号写 fig-1"))
            continue
        name = label or f"fig-{i + 1}"
        if name in seen:
            errors.append((f"/figures/{i}/label", f"锚点名 {name} 与第 {seen[name] + 1} 张图重复"
                           + ("" if label else "（未写 label 的图按序号推导为 fig-N）")))
        elif name in blocks:
            errors.append((f"/figures/{i}/label", f"锚点名 {name} 与块号重复：图和块共用记录内的 # 名称"))
        seen.setdefault(name, i)
    return errors


def block_errors(blocks: list[dict]) -> Errors:
    errors: Errors = []
    ids: set[str] = set()
    for position, block in enumerate(blocks):
        key = block["block_id"]
        if "#" in key or key.strip() != key or not key:
            errors.append((f"/blocks/{position}/block_id", "block_id 不能含 #，首尾不能有空白"))
        if key in ids:
            errors.append((f"/blocks/{position}/block_id", "block_id 重复"))
        ids.add(key)
        if len(block["requires_block_ids"]) != len(set(block["requires_block_ids"])):
            errors.append((f"/blocks/{position}/requires_block_ids", "requires_block_ids 中有重复"))
    for position, block in enumerate(blocks):
        if set(block["requires_block_ids"]) - ids:
            errors.append((f"/blocks/{position}/requires_block_ids", "requires_block_ids 指向的块不存在"))
    graph = {b["block_id"]: b["requires_block_ids"] for b in blocks}
    visited: set[str] = set()
    active: set[str] = set()
    for start in graph:  # 迭代 DFS 检测环，避免大单元触发递归上限
        stack = [(start, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                active.discard(node)
                visited.add(node)
            elif node in active:
                return [*errors, ("/blocks", "块之间的依赖形成了环")]
            elif node not in visited and node in graph:
                active.add(node)
                stack.append((node, True))
                stack.extend((child, False) for child in reversed(graph[node]))
    return errors


def select_blocks(record: dict, block_ids: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """返回按原顺序排列的所选块（含自动补齐的前置块）及补齐了哪些。纯选择，不做授权。"""
    if record.get("kind") != "detail":
        if block_ids:
            raise BurrowlError("INVALID_ARGUMENT", "该记录没有可选择的内容块")
        return [], []
    blocks = record["payload"]["blocks"]
    if errors := block_errors(blocks):
        raise BurrowlError("INVALID_SCHEMA", "技术单元块依赖无效",
                       errors=[{"code": "INVALID_SCHEMA", "path": p, "message": m} for p, m in errors])
    by_id = {b["block_id"]: b for b in blocks}
    selected = list(by_id) if block_ids is None else block_ids
    if not isinstance(selected, list) or not selected or any(not isinstance(k, str) for k in selected) \
            or len(set(selected)) != len(selected) or set(selected) - by_id.keys():
        raise BurrowlError("INVALID_ARGUMENT", "所选内容块重复、不存在或为空")
    closure, queue = set(selected), list(selected)
    while queue:
        for dependency in by_id[queue.pop()]["requires_block_ids"]:
            if dependency not in closure:
                closure.add(dependency)
                queue.append(dependency)
    ordered = [deepcopy(b) for b in blocks if b["block_id"] in closure]
    added = [b["block_id"] for b in blocks if b["block_id"] in closure - set(selected)]
    return ordered, added


def render_full(record: dict, block_ids: list[str] | None = None) -> str:
    if record.get("kind") != "detail":
        return record.get("body_markdown", "")
    blocks, _ = select_blocks(record, block_ids)
    return "\n\n".join(b["markdown"] for b in blocks)


def fixed_ref_errors(ref: dict, path: str, *, record_only: bool = False) -> Errors:
    errors: Errors = []
    if record_only and (ref.get("target_kind") != "record" or not str(ref.get("target_id", "")).startswith("MEM-")):
        errors.append((path, "必须是对 MEM 记录的固定引用"))
    if not is_sha256(ref.get("sha256")):
        errors.append((path + "/sha256", "必须写固定的 SHA-256"))
    if ref.get("target_kind") == "record" and (type(ref.get("revision")) is not int or ref["revision"] < 1):
        errors.append((path + "/revision", "必须写固定修订号（正整数）"))
    return errors


def validate_unit(record: dict) -> Errors:
    payload = record["payload"]
    errors = block_errors(payload["blocks"]) + figure_errors(payload)
    anchors = figure_anchors(payload)
    for key, value in payload["retrieval_description"].items():
        if any(not text.strip() for text in (value if isinstance(value, list) else [value])):
            errors.append(("/retrieval_description/" + key, "写出具体条件，或明确写“未知”"))
    for position, block in enumerate(payload["blocks"]):
        if not block["markdown"].strip():
            errors.append((f"/blocks/{position}/markdown", "技术块正文不能为空"))
        for match in _LEGACY_EMBED.finditer(block["markdown"]):
            if int(match[1]) >= len(payload["figures"]):
                errors.append((f"/blocks/{position}/markdown", "正文引用的图号没有登记在本单元的 figures 中"))
        for match in _ANCHOR_EMBED.finditer(block["markdown"]):
            if match[1].strip() not in anchors:
                known = "、".join(sorted(anchors)) or "（本单元没有登记图）"
                errors.append((f"/blocks/{position}/markdown",
                               f"![[#{match[1].strip()}]] 指向的图没有登记在本单元的 figures 中；可用的锚点名：{known}"))
    run, origin = payload["run_ref"], payload.get("origin")
    if payload["unit_type"] == "experiment" and run is None and origin is None:
        errors.append(("/run_ref", "experiment 类型必须用 run_ref 引用实际执行过的 Run；"
                                   "转写外部资料中的实验时改用 origin 指向原文出处"))
    if origin is not None:  # 转写自外部原件（R21 D-02）：覆盖报告、变更对比按 locators 找到这条记录
        ref = origin["source_ref"]
        if ref["target_kind"] != "file" or not ref["target_id"].startswith("SRC-"):
            errors.append(("/origin/source_ref", "origin.source_ref 必须指向已登记的原件（SRC-，target_kind=file）"))
        errors.extend(fixed_ref_errors(ref, "/origin/source_ref"))
    if run is not None:
        if run["target_kind"] != "owner" or not run["target_id"].startswith("RUN-"):
            errors.append(("/run_ref", "run_ref 必须指向 RUN- 对象"))
        errors.extend(fixed_ref_errors(run, "/run_ref"))
    for position, ref in enumerate(payload["evidence_refs"]):
        errors.extend(fixed_ref_errors(ref, f"/evidence_refs/{position}"))
    if not payload["evidence_refs"] and run is None and origin is None and not record["sources"] \
            and not record.get("provenance_gap"):
        errors.append(("/evidence_refs", "非实验单元需要 evidence_refs、sources、origin，或在 provenance_gap 说明缺口"))
    return errors


def validate_watches(watches: list[dict]) -> Errors:
    """观察基线只是变化提醒，不是引用或支持关系。"""
    errors: Errors = []
    seen: set[tuple] = set()
    for position, watch in enumerate(watches):
        path = f"/watch_refs/{position}"
        identity = (watch["watch_type"], watch.get("record_id") or watch.get("owner_id"))
        if identity in seen:
            errors.append((path, "变化监视重复"))
        seen.add(identity)
        if watch["watch_type"] == "revision":
            if not watch["record_id"].startswith("MEM-") or watch["baseline_revision"] < 1:
                errors.append((path, "修订监视需要 MEM- 记录 ID 和正整数的基线修订号"))
            if not is_sha256(watch["baseline_record_hash"]):
                errors.append((path + "/baseline_record_hash", "修订监视需要观察时的 record_hash"))
        elif len(watch["baseline_unit_ids"]) != len(set(watch["baseline_unit_ids"])):
            errors.append((path + "/baseline_unit_ids", "观察的技术单元 ID 重复"))
    return errors


def _section_errors(payload: dict, resolver: Resolver | None) -> Errors:
    errors: Errors = []
    seen: set[tuple] = set()
    for position, block in enumerate(payload["blocks"]):
        if block["type"] == "prose":
            for j, ref in enumerate(block["evidence_refs"]):
                errors.extend(fixed_ref_errors(ref, f"/blocks/{position}/evidence_refs/{j}"))
            continue
        ref = block["ref"]
        bad = fixed_ref_errors(ref, f"/blocks/{position}/ref", record_only=True)
        errors.extend(bad)
        identity = (ref["target_id"], ref["revision"])
        if identity in seen:
            errors.append((f"/blocks/{position}/ref", "同一章节重复收录同一单元；把所选块合并到一处"))
        seen.add(identity)
        if bad or not resolver:
            continue
        target = resolver(ref)
        if not isinstance(target, dict) or target.get("kind") != "detail":
            errors.append((f"/blocks/{position}/ref", "unit 块必须引用 detail 记录"))
            continue
        try:
            select_blocks(target, block.get("block_ids"))
        except BurrowlError as exc:
            errors.append((f"/blocks/{position}/block_ids", exc.message))
    return errors


def _document_errors(payload: dict, resolver: Resolver | None) -> Errors:
    errors: Errors = []
    seen: set[str] = set()
    keys: set[str] = set()
    synthesis = False
    for position, ref in enumerate(payload["section_refs"]):
        path = f"/section_refs/{position}"
        bad = fixed_ref_errors(ref, path, record_only=True)
        errors.extend(bad)
        if ref["target_id"] in seen:
            errors.append((path, "文稿章节重复"))
        seen.add(ref["target_id"])
        if bad or not resolver:
            continue
        target = resolver(ref)
        if not isinstance(target, dict) or target.get("kind") != "document_section":
            errors.append((path, "章节引用必须指向 document_section 记录"))
            continue
        section = target["payload"]
        if section["section_key"] in keys:
            errors.append((path, "section_key 重复"))
        keys.add(section["section_key"])
        synthesis = synthesis or section["role"] in {"discussion", "conclusion"}
        if synthesis and section["role"] == "experiment":
            errors.append((path, "实验章节必须排在讨论和结论之前"))
    for position, ref in enumerate(payload["common_refs"]):
        errors.extend(fixed_ref_errors(ref, f"/common_refs/{position}"))
    return errors


def validate_composition(record: dict, resolver: Resolver | None = None) -> Errors:
    payload = record["payload"]
    errors = validate_watches(payload["watch_refs"])
    required = ("section_key", "title") if record["kind"] == "document_section" else ("purpose", "audience", "scope")
    for key in required:
        if not payload[key].strip():
            errors.append(("/" + key, "文稿元数据不能为空"))
    if record["kind"] == "document_section":
        errors.extend(_section_errors(payload, resolver))
    else:
        errors.extend(_document_errors(payload, resolver))
    return errors
