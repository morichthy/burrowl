"""组包：把已选的固定材料按作者顺序组合成阅读包，并补齐已登记的必要依据。

只做"已有正文 + 已登记依赖"的展开，不隐式二次检索、不生成新解释。
每个完整块原子加入：预算不够就整块省略并报告缺口，绝不截断公式或定义。
正式用途（formal）只输出在适用范围内已被接受的结论原文。
"""
import base64
from typing import Any

from ...core.errors import BurrowlError
from ...core.technical import render_full, select_blocks
from ..memory.reader import Reader, fixed_ref
from .budget import Ledger

LABELS = {"question": "问题", "method": "方法", "steps": "步骤", "results": "结果", "limitations": "限制",
          "recommendation": "建议", "applicable": "适用条件", "prohibited": "不适用条件",
          "failure_modes": "失败模式", "next_steps": "下一步", "problem_structure": "问题结构"}
MAX_FIGURE_BYTES = 8 * 1024 * 1024


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join("- " + _as_text(v) for v in value)
    return str(value)


def record_text(record: dict) -> str:
    """记录的完整可读正文：v4 记录用作者写的正文；技术单元用全部块；其余记录附上语义字段。"""
    if record["kind"] == "detail":
        return render_full(record)
    if record["kind"] in {"narrative", "experience", "overview"} or not record["payload"]:
        return record["body_markdown"]
    extras = [f"**{LABELS[k]}**\n\n{_as_text(record['payload'][k])}" for k in LABELS if record["payload"].get(k)]
    return "\n\n".join(p for p in [record["body_markdown"], *extras] if p)


class Packet:
    """一次组包的累积结果。group：direct（所选）/ context（必要依据）/ association（关联补充）。"""

    def __init__(self, reader: Reader, ledger: Ledger, *, allowed_owner_ids: set[str] | None,
                 formal_scope: str | None = None, association_share: float = 0.25, defer_evidence: bool = False,
                 include_figures: bool = True):
        self.reader, self.ledger = reader, ledger
        self.allowed = allowed_owner_ids  # None：必要依据可读取任何可读对象
        self.formal_scope = formal_scope
        self.defer_evidence = defer_evidence
        self.include_figures = include_figures
        self.parts: list[dict] = []
        self.gaps: list[dict] = []
        self.visited: set[tuple] = set()
        self.association_limit = int(ledger.remaining("output_chars") * association_share)
        self.association_used = 0

    def gap(self, code: str, message: str, ref: dict | None = None) -> None:
        item = {"code": code, "message": message, "target_id": ref["target_id"] if ref else None}
        if item not in self.gaps:
            self.gaps.append(item)

    def _emit(self, group: str, heading: str, text: str, ref: dict, figures: list[dict] | None = None) -> None:
        for existing in self.parts:  # 同一块被多个命中共享时只交付一次；若本次是直接选中则升为 direct
            if existing["ref"] == ref and existing["markdown"] == text:
                if group == "direct":
                    existing["group"] = "direct"
                return
        if not text.strip():
            self.gap("EMPTY", "所选材料没有可组合的正文", ref)
            return
        if group == "association" and self.association_used + len(text) > self.association_limit:
            self.gap("BUDGET_EXCEEDED", "关联补充达到篇幅比例上限，已整块省略", ref)
            return
        if not self.ledger.can("output_chars", len(text)):
            self.gap("BUDGET_EXCEEDED", f"输出预算不足，已整块省略：{heading}", ref)
            return
        self.ledger.charge("output_chars", len(text))
        if group == "association":
            self.association_used += len(text)
        part = {"group": group, "heading": heading, "markdown": text, "ref": ref}
        if figures:
            part["figures"] = figures
        self.parts.append(part)

    def _figures(self, record: dict, text: str, whole: bool) -> list[dict]:
        """随正文交付文中引用到的已登记栅格图（计入读取预算）。"""
        if not self.include_figures:
            return []
        out = []
        for number, figure in enumerate(record["payload"].get("figures", [])):
            if not whole and f"](figure:{number})" not in text:
                continue
            ref = figure["ref"]
            try:
                source = self.reader.storage.sources.resolve(ref["target_id"])
                if not source.path.is_relative_to(self.reader.storage.ws.root) \
                        or source.path.stat().st_size > MAX_FIGURE_BYTES:
                    raise BurrowlError("ACCESS_DENIED", "图示不是数据目录内的受控附件或超过 8 MiB")
                raw = self.reader.read_file(ref, max_bytes=MAX_FIGURE_BYTES)
                mime = ("image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
                        if raw.startswith(b"\xff\xd8\xff") else "image/webp" if raw[8:12] == b"WEBP" else None)
                if mime is None:
                    raise BurrowlError("INVALID_ARGUMENT", "图示不是可展示的栅格图片")
                out.append({"index": number, "caption": figure["caption"], "ref": ref,
                            "data_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"})
            except BurrowlError as exc:
                if exc.code == "BUDGET_EXCEEDED":
                    raise
                self.gap(exc.code, exc.message, ref)
        return out

    def _permitted(self, record: dict, group: str, parent: str | None) -> bool:
        if group != "direct" and self.allowed is not None and record["owner_id"] not in self.allowed:
            self.gap("ACCESS_DENIED", "必要依据位于本次范围之外（context_reach=same_scope）",
                     {"target_id": record["record_id"]})
            if parent:  # 缺了必要依据的父材料不能当作完整内容交付
                for part in self.parts:
                    if part["ref"]["target_id"] == parent:
                        part["incomplete"] = True
                self.gap("CONTEXT_MISSING", "该材料的必要依据未能加入，阅读时需注意前提缺失", {"target_id": parent})
            return False
        return True

    def add(self, ref: dict, *, group: str = "direct", parent: str | None = None) -> None:
        """加入一个固定引用的内容，并递归补齐它声明的必要依据。"""
        key = (ref["target_kind"], ref["target_id"], ref.get("revision"), ref.get("locator") or "")
        if key in self.visited:
            return
        self.visited.add(key)
        self.ledger.checkpoint()
        try:
            if ref["target_kind"] == "file":
                raw = self.reader.read_file(ref, max_bytes=self.ledger.remaining("read_bytes"))
                self._emit(group, "原始材料：" + ref["target_id"], raw.decode("utf-8-sig", errors="replace"), ref)
                return
            if ref["target_kind"] == "claim":
                claim, record = self.reader.claims().claims[ref["target_id"]]
                self._emit(group, record["title"], f"{claim['statement']}\n\n适用范围：{claim['scope']}", ref)
                return
            if ref["target_kind"] == "owner":
                self.gap("UNSUPPORTED", "对象本身不能直接组包，请选择其中的记录", ref)
                return
            record = self.reader.record(ref)
            self.reader.check_ref(fixed_ref(record))  # 依据当前仍可读、固定字节未变
        except BurrowlError as exc:
            if exc.code == "BUDGET_EXCEEDED":
                raise
            self.gap(exc.code, exc.message, ref)
            return
        if not self._permitted(record, group, parent):
            return
        if self.formal_scope is not None:
            projected = self.reader.claims().formal_projection([record["record_id"]], self.formal_scope)
            if projected["claims"]:
                self._emit(group, record["title"], projected["text"], fixed_ref(record))
            else:
                self.gap("EVIDENCE_INELIGIBLE", "所选内容没有在该适用范围内已接受的结论", ref)
            return
        self._content(record, ref, group)
        if not self.defer_evidence:
            for source in record["sources"]:
                if source.get("relation") == "prerequisite" and source["target_kind"] == "record":
                    self.add(source, group="context", parent=record["record_id"])

    def _content(self, record: dict, ref: dict, group: str) -> None:
        locator = ref.get("locator") or ""
        if record["kind"] == "detail":
            selected = [locator[6:]] if locator.startswith("block:") else None
            try:
                blocks, _added = select_blocks(record, selected)
            except BurrowlError as exc:
                self.gap(exc.code, exc.message, ref)
                return
            for block in blocks:
                part_group = group if selected is None or block["block_id"] in selected else "context"
                block_ref = fixed_ref(record) | {"locator": "block:" + block["block_id"]}
                self._emit(part_group, f"{record['title']} · {block.get('title') or block['block_id']}",
                           block["markdown"], block_ref, self._figures(record, block["markdown"], False))
        elif record["kind"] == "document":
            for section_ref in record["payload"]["section_refs"]:
                self.add(section_ref, group=group)
        elif record["kind"] == "document_section":
            for block in record["payload"]["blocks"]:
                if block["type"] == "prose":
                    self._emit(group, record["payload"]["title"], block["markdown"], fixed_ref(record))
                else:
                    for block_id in block.get("block_ids") or [None]:
                        target = dict(block["ref"])
                        if block_id:
                            target["locator"] = "block:" + block_id
                        self.add(target, group=group)
        else:
            self._emit(group, record["title"], record_text(record), fixed_ref(record))

    def result(self) -> dict:
        self.reader.recheck()
        groups = {g: sum(p["group"] == g for p in self.parts) for g in ("direct", "context", "association")}
        return {"parts": self.parts, "gaps": self.gaps, "complete": not self.gaps, "counts": groups,
                "incomplete_parts": [p["ref"]["target_id"] for p in self.parts if p.get("incomplete")],
                "basis_heads": self.reader.heads(), "consumed": self.ledger.snapshot()}
