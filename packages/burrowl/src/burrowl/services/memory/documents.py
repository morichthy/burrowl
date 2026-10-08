"""研究文稿的读取：完整文稿、目录、单章上下文包、依据变化影响、图片。

文稿（document）按固定引用编排章节（document_section），章节再引用技术单元（detail）的块。
读取从不更新引用、不改写章节、不调用模型；依据不可用时隐藏对应正文并报告缺口。
影响检查只给出"需要重新审阅"的候选，不是科学结论。
"""
import base64
import time
from copy import deepcopy
from typing import Any, Literal

from ...core.errors import BurrowlError
from ...core.hashing import sha256_bytes
from ...core.jsonschema import validate
from ...core.schema import defs
from ...core.technical import description_text, select_blocks
from .claims import iter_refs
from .context import Storage
from .reader import Reader, fixed_ref, ref_identity

DocumentType = Literal["research_process", "research_report"]
MAX_FIGURE_BYTES = 8 * 1024 * 1024


def _issue(ref: dict, code: str) -> dict:
    return {"target_id": ref["target_id"], "code": code}


class DocumentSession:
    """一次文稿读取：选定文稿、缓存章节、记录新修订提示。"""

    def __init__(self, storage: Storage, owner_id: str, *, document_id: str | None = None,
                 revision: int | None = None, document_type: DocumentType | None = None,
                 exclude_ids: list[str] | None = None):
        self.started = time.perf_counter()
        self.reader = Reader(storage)
        self.owner = self.reader.owner(owner_id)
        self.state = self.reader.state(owner_id)
        self.excluded = set(exclude_ids or [])
        documents = sorted((r for r in self.state.records.values()
                            if r["kind"] == "document" and r["sensitivity"] != "restricted"),
                           key=lambda r: (r["updated_at"], r["revision"], r["record_id"]), reverse=True)
        self.candidates = [{"record_id": r["record_id"], "revision": r["revision"], "title": r["title"],
                            "updated_at": r["updated_at"], "document_type": r["payload"]["document_type"]}
                           for r in documents]
        self.hints: list[dict] = []
        self._sections: dict[tuple, dict] = {}
        if document_id:
            current = self.state.records.get(document_id)
            if current is None or current["kind"] != "document" or current["sensitivity"] == "restricted":
                raise BurrowlError("NOT_FOUND", "所选文稿不存在或不可读取", {"document_id": document_id})
            self.document = self.reader.record({"target_id": document_id, "revision": revision})
            if document_type and document_type != self.document["payload"]["document_type"]:
                raise BurrowlError("INVALID_ARGUMENT", "所选文稿与 document_type 不一致")
        else:
            wanted = document_type or "research_process"
            self.document = next((r for r in documents if r["payload"]["document_type"] == wanted), None)
        if self.document:
            self.common_refs = [*self.document["sources"], *self.document["payload"]["common_refs"]]
            self.common_issues = self.check_refs(self.common_refs)
        else:
            self.common_refs, self.common_issues = [], []

    # ---------- 依据检查 ----------
    def _depends_on_excluded(self, ref: dict, seen: set | None = None) -> bool:
        if not self.excluded:
            return False
        if ref["target_id"] in self.excluded:
            return True
        if ref["target_kind"] != "record":
            return False
        seen = seen if seen is not None else set()
        key = (ref["target_id"], ref.get("revision"))
        if key in seen:
            return False
        seen.add(key)
        record = self.reader.record(ref)
        return record["owner_id"] in self.excluded or any(
            self._depends_on_excluded(child, seen)
            for child in iter_refs({"sources": record["sources"], "payload": record["payload"]}))

    def check_refs(self, refs: list[dict]) -> list[dict]:
        issues = []
        for ref in refs:
            try:
                if self._depends_on_excluded(ref):
                    raise BurrowlError("EXCLUDED", "所需依据被明确排除")
                self.reader.check_ref(ref)
                if ref["target_kind"] == "record":
                    record = self.reader.record(ref)
                    current = self.reader.current(record)
                    if current["revision"] != record["revision"]:
                        hint = {"record_id": record["record_id"], "revision": record["revision"],
                                "current_revision": current["revision"]}
                        if hint not in self.hints:
                            self.hints.append(hint)
            except BurrowlError as exc:
                if (item := _issue(ref, exc.code)) not in issues:
                    issues.append(item)
        return issues

    # ---------- 结构 ----------
    def metadata(self) -> dict | None:
        if self.document is None:
            return None
        payload = self.document["payload"]
        result = {"record_id": self.document["record_id"], "revision": self.document["revision"],
                  "title": self.document["title"],
                  **{k: deepcopy(payload[k]) for k in ("document_type", "purpose", "audience", "scope",
                                                        "common_refs", "section_refs")}}
        if self.common_issues:  # 共同依据不可用时，元数据正文也不能绕过
            result.update(title="依据不可用的文稿", purpose="", audience="", scope="")
        return result

    def section(self, ref: dict) -> dict:
        key = (ref["target_id"], ref["revision"], ref["sha256"])
        if key not in self._sections:
            record = self.reader.record(ref)
            all_defs = defs()
            if record["kind"] != "document_section" or validate(record, all_defs["DocumentSectionRecordV3"], all_defs):
                raise BurrowlError("INVALID_SCHEMA", "文稿章节引用了无效记录", {"target_id": ref["target_id"]})
            self._sections[key] = record
        return self._sections[key]

    def section_rows(self, *, check_units: bool = False) -> list[dict]:
        rows = []
        for ref in self.document["payload"]["section_refs"] if self.document else []:
            row = {"section_id": ref["target_id"], "ref": deepcopy(ref), "section_key": "",
                   "title": "不可读取的章节", "role": "appendix", "unit_refs": [], "source_issues": []}
            if ref["target_id"] in self.excluded:
                row.update(title="已排除章节", source_issues=[_issue(ref, "EXCLUDED")])
            else:
                try:
                    record = self.section(ref)
                    issues = [*self.common_issues, *self.check_refs(record["sources"])]
                    if check_units:
                        issues.extend(self.check_refs(list(iter_refs(record["payload"]["blocks"]))))
                    if not issues:
                        row.update({k: record["payload"][k] for k in ("section_key", "title", "role")})
                    row.update(unit_refs=[deepcopy(b["ref"]) for b in record["payload"]["blocks"]
                                          if b["type"] == "unit"], source_issues=issues)
                except BurrowlError as exc:
                    row["source_issues"] = [_issue(ref, exc.code)]
            rows.append(row)
        return rows

    def resolve_section(self, ref: dict) -> dict:
        row = {"section_id": ref["target_id"], "section_key": "", "title": "不可读取的章节", "role": "appendix",
               "ref": deepcopy(ref), "source_issues": [], "blocks": []}
        if ref["target_id"] in self.excluded:
            row["source_issues"] = [_issue(ref, "EXCLUDED")]
            return row
        try:
            section = self.section(ref)
        except BurrowlError as exc:
            row["source_issues"] = [_issue(ref, exc.code)]
            return row
        base = [*self.common_issues, *self.check_refs([*self.common_refs, *section["sources"]])]
        row["source_issues"] = base
        if not base:
            row.update({k: section["payload"][k] for k in ("section_key", "title", "role")})
        for block in section["payload"]["blocks"]:
            resolved = deepcopy(block)
            issues = [*base, *self.check_refs(block["evidence_refs"] if block["type"] == "prose" else [block["ref"]])]
            if block["type"] == "prose":
                if issues:
                    resolved["markdown"] = ""
            else:
                resolved.update(record=None, resolved_blocks=[], required_block_ids=[])
                if not issues:
                    try:
                        record = self.reader.record(block["ref"])
                        if record["kind"] != "detail":
                            raise BurrowlError("INVALID_SCHEMA", "unit 引用不是技术单元")
                        selected, required = select_blocks(record, block.get("block_ids"))
                        if any(f"{record['record_id']}#{b['block_id']}" in self.excluded for b in selected):
                            raise BurrowlError("EXCLUDED", "所选内容的必要块被明确排除")
                        resolved.update(record=deepcopy(record), resolved_blocks=selected, required_block_ids=required)
                    except BurrowlError as exc:
                        issues.append(_issue(block["ref"], exc.code))
            resolved["source_issues"] = issues
            row["blocks"].append(resolved)
        return row

    def finish(self, result: dict) -> dict:
        self.reader.recheck()
        return {**result, "basis_heads": self.reader.heads(), "writes": 0,
                "metrics": {"elapsed_ms": round((time.perf_counter() - self.started) * 1000, 2),
                            "read_owners": len(self.reader.states), "resolved_sections": len(self._sections)}}


def build_document(storage: Storage, owner_id: str, **selection: Any) -> dict:
    """完整文稿：按章节顺序展开正文、技术块与图号；附带未纳入的技术单元清单。"""
    session = DocumentSession(storage, owner_id, **selection)
    if session.document is None:
        return session.finish({"owner_id": owner_id, "title": session.owner.title, "document_source": "uncomposed",
                               "document": None, "report": None, "candidates": session.candidates,
                               "missing": [{"code": "DOCUMENT_MISSING", "message": "尚未保存该类型的独立文稿"}]})
    sections = [session.resolve_section(ref) for ref in session.document["payload"]["section_refs"]]
    included = {b["ref"]["target_id"] for s in sections for b in s["blocks"] if b["type"] == "unit"}
    # 被文字段落或共同依据引用（而非整块收录）的技术单元也算"已纳入"——覆盖是编辑选择，不是访问门槛。
    authored = [r for section_ref in session.document["payload"]["section_refs"]
                for r in _section_refs(session, section_ref)]
    for ref in session.common_refs + authored:
        if ref["target_kind"] == "record":
            try:
                if session.reader.record(ref)["kind"] == "detail":
                    included.add(ref["target_id"])
            except BurrowlError:
                pass
    uncovered = sorted(r["record_id"] for r in session.state.records.values()
                       if r["kind"] == "detail" and r["sensitivity"] != "restricted" and r["record_id"] not in included)
    complete = not session.common_issues and all(
        not s["source_issues"] and all(not b["source_issues"] for b in s["blocks"]) for s in sections)
    meta = session.metadata()
    return session.finish({
        "owner_id": owner_id, "title": session.owner.title, "document_source": "independent", "document": meta,
        "report": {"record_id": meta["record_id"], "revision": meta["revision"], "title": meta["title"],
                   "sections": sections, "complete": complete},
        "missing": session.common_issues, "candidates": session.candidates,
        "coverage": {"included_detail_ids": sorted(included), "uncovered_detail_ids": uncovered},
        "version_hints": session.hints})


def _section_refs(session: DocumentSession, ref: dict) -> list[dict]:
    try:
        return list(iter_refs(session.section(ref)["payload"]["blocks"]))
    except BurrowlError:
        return []


def outline(storage: Storage, owner_id: str, **selection: Any) -> dict:
    session = DocumentSession(storage, owner_id, **selection)
    return session.finish({
        "owner_id": owner_id, "document_source": "independent" if session.document else "uncomposed",
        "document": session.metadata(), "sections": session.section_rows(check_units=True),
        "candidates": session.candidates, "missing": session.common_issues,
        "watch_baseline": {"owner_id": owner_id, "head": session.state.commit_id,
                           "unit_ids": sorted(r["record_id"] for r in session.state.records.values()
                                              if r["kind"] == "detail" and r["sensitivity"] != "restricted")}})


def section_context(storage: Storage, owner_id: str, section_id: str, *, max_chars: int = 20000,
                    include_ids: list[str] | None = None, full_ids: list[str] | None = None,
                    exclude_ids: list[str] | None = None, **selection: Any) -> dict:
    """为改写某一章准备有界上下文：目录 + 所选章节正文 + 共同依据（超预算时退为检索说明并报告）。"""
    if not 1 <= max_chars <= 1_000_000:
        raise BurrowlError("INVALID_ARGUMENT", "max_chars 必须在 1 到 1000000 之间")
    session = DocumentSession(storage, owner_id, exclude_ids=exclude_ids, **selection)
    if session.document is None:
        raise BurrowlError("NOT_FOUND", "尚无独立文稿，不能选择章节")
    include_ids, full_ids = list(dict.fromkeys(include_ids or [])), list(dict.fromkeys(full_ids or []))
    wanted = set(include_ids) | set(full_ids)
    rows = session.section_rows()
    if not any(r["section_id"] == section_id for r in rows):
        raise BurrowlError("NOT_FOUND", "所选章节不在固定文稿中", {"section_id": section_id})
    chosen = {section_id} | {r["section_id"] for r in rows if r["section_id"] in wanted}
    manifest: dict[str, Any] = {"read_refs": [], "omitted": [], "missing": [], "required_not_full": [],
                                "budget": {"max_chars": max_chars, "used_chars": 0}, "complete": True}
    chunks: list[str] = []
    meta = session.metadata()
    packet_document = {k: meta[k] for k in ("record_id", "revision", "document_type")}
    if session.document["record_id"] in session.excluded or owner_id in session.excluded:
        manifest.update(complete=False, missing=[{"target_id": session.document["record_id"], "code": "EXCLUDED"}])
        return session.finish({"owner_id": owner_id, "document": packet_document, "section_id": section_id,
                               "context_text": "", "manifest": manifest})

    def add(text: str, ref: dict | None = None, *, required: bool = True, block_ids: list | None = None) -> bool:
        cost = len(text) + (2 if chunks else 0)
        if manifest["budget"]["used_chars"] + cost > max_chars:
            item = {"target_id": ref["target_id"] if ref else session.document["record_id"], "code": "BUDGET_EXCEEDED"}
            manifest["omitted"].append(item)
            if required:
                manifest["required_not_full"].append(item)
                manifest["complete"] = False
            return False
        chunks.append(text)
        manifest["budget"]["used_chars"] += cost
        if ref:
            item = ref_identity(ref) | ({"block_ids": block_ids} if block_ids is not None else {})
            if item not in manifest["read_refs"]:
                manifest["read_refs"].append(item)
        return True

    add("\n\n".join([f"# {meta['title']}", "目的：" + meta["purpose"], "读者：" + meta["audience"],
                     "范围：" + meta["scope"], "目录：\n" + "\n".join(f"{r['section_id']} {r['title']}" for r in rows)]),
        fixed_ref(session.document))
    seen: set[tuple] = set()
    for row in rows:
        if row["section_id"] not in chosen:
            manifest["omitted"].append({"target_id": row["section_id"], "code": "NOT_SELECTED"})
            continue
        section = session.resolve_section(row["ref"])
        if section["source_issues"]:
            manifest["missing"].extend(section["source_issues"])
            manifest["complete"] = False
        add("## " + section["title"], row["ref"])
        for block in section["blocks"]:
            if block["source_issues"]:
                manifest["missing"].extend(block["source_issues"])
                manifest["complete"] = False
            elif block["type"] == "prose":
                add(block["markdown"], row["ref"])
            else:
                record = block["record"]
                identity = (record["record_id"], record["revision"], tuple(b["block_id"] for b in block["resolved_blocks"]))
                if identity in seen:
                    continue
                seen.add(identity)
                summary = description_text(record)
                full = "\n\n".join(b["markdown"] for b in block["resolved_blocks"])
                if not add(f"### {record['title']}\n\n{record['record_id']} r{record['revision']}\n\n{summary}\n\n{full}",
                           block["ref"], block_ids=[b["block_id"] for b in block["resolved_blocks"]]):
                    # 预算不够时退为检索说明——绝不截断出半个公式。
                    add(f"### {record['title']}（仅检索说明）\n\n{summary}", block["ref"], required=False)
    extras = list(session.document["payload"]["common_refs"])
    for identity in sorted(wanted - chosen):
        rid = identity.split("#", 1)[0]
        if any(ref["target_id"] == rid for ref in extras):
            continue
        try:
            extras.append(fixed_ref(session.reader.record({"target_id": rid, "revision": None})))
        except BurrowlError as exc:
            manifest["missing"].append({"target_id": rid, "code": exc.code})
            manifest["complete"] = False
    for ref in extras:
        if any(item[0] == ref["target_id"] for item in seen):
            continue
        if issues := session.check_refs([ref]):
            manifest["missing"].extend(issues)
            manifest["complete"] = False
            continue
        if ref["target_kind"] != "record":
            add("共同依据：" + ref["target_id"], ref)
            continue
        record = session.reader.record(ref)
        selected_blocks = [i.split("#", 1)[1] for i in full_ids if i.startswith(ref["target_id"] + "#")]
        full = ref["target_id"] in full_ids or bool(selected_blocks)
        if record["kind"] == "detail":
            blocks, _ = select_blocks(record, selected_blocks or None)
            if full and any(f"{ref['target_id']}#{b['block_id']}" in session.excluded for b in blocks):
                manifest["missing"].append(_issue(ref, "EXCLUDED"))
                manifest["complete"] = False
                continue
            text = description_text(record) + ("\n\n" + "\n\n".join(b["markdown"] for b in blocks) if full else "")
        else:
            text = record["body_markdown"] if full else record["title"]
        add(f"### {record['title']}\n\n{text}", ref)
    return session.finish({"owner_id": owner_id, "document": packet_document, "section_id": section_id,
                           "context_text": "\n\n".join(chunks), "manifest": manifest})


IMPACT_MESSAGES = {"NEW_REVISION": "固定依据已有新修订，原稿未自动替换",
                   "NEW_UNIT_UNCOVERED": "关注的研究出现了尚未纳入的新技术单元",
                   "DOCUMENT_BASIS_MISMATCH": "两份文稿引用了同一技术单元的不同固定版本"}


def document_impact(storage: Storage, owner_id: str, **selection: Any) -> dict:
    """比较文稿的固定依据、观察基线与当前 HEAD：列出需要重新审阅的章节（文本相似不算依赖）。"""
    session = DocumentSession(storage, owner_id, **selection)
    if session.document is None:
        raise BurrowlError("NOT_FOUND", "尚无独立文稿，不能检查章节影响")
    doc = session.document
    section_ids = [ref["target_id"] for ref in doc["payload"]["section_refs"]]
    changes: list[dict] = []
    included: set[str] = set()
    unit_versions: dict[str, list] = {}

    def add(code, target_id, affected, path, before=None, after=None, relation="evidence"):
        change = {"code": code, "target_id": target_id, "section_ids": sorted(set(affected)), "path": path,
                  "before_ref": ref_identity(before) if before else None,
                  "after_ref": ref_identity(after) if after else None, "relation_type": relation,
                  "requires_review": True, "message": IMPACT_MESSAGES.get(code, "依据或关注对象需要重新检查")}
        if change not in changes:
            changes.append(change)

    def visit(ref, affected, path, seen):
        key = (ref["target_kind"], ref["target_id"], ref.get("revision"), ref.get("sha256"))
        if key in seen:
            return
        seen.add(key)
        here = path + [ref["target_id"]]
        try:
            if ref["target_kind"] != "record":
                session.reader.check_ref(ref)
                return
            old = session.reader.record(ref)
            current = session.reader.current(old)
            if current["revision"] != old["revision"]:
                add("NEW_REVISION", old["record_id"], affected, here, ref, fixed_ref(current))
            if old["kind"] == "detail":
                included.add(old["record_id"])
                unit_versions.setdefault(old["record_id"], []).append((ref, list(affected)))
            for child in iter_refs({"sources": old["sources"], "payload": old["payload"]}):
                visit(child, affected, here, seen)
        except BurrowlError as exc:
            add(exc.code, ref["target_id"], affected, here, ref)

    for ref in session.common_refs:
        visit(ref, section_ids, [doc["record_id"]], set())
    watched = [(doc["payload"]["watch_refs"], section_ids, [doc["record_id"]])]
    for ref in doc["payload"]["section_refs"]:
        visit(ref, [ref["target_id"]], [doc["record_id"]], set())
        try:
            watched.append((session.section(ref)["payload"]["watch_refs"], [ref["target_id"]],
                            [doc["record_id"], ref["target_id"]]))
        except BurrowlError:
            pass
    uncovered: set[str] = set()
    for watches, affected, path in watched:
        for watch in watches:
            if watch["watch_type"] == "revision":
                ref = {"target_kind": "record", "target_id": watch["record_id"], "revision": watch["baseline_revision"],
                       "sha256": watch["baseline_record_hash"], "locator": "", "relation": "references"}
                try:
                    before = session.reader.record(ref)
                    current = session.reader.current(before)
                    if current["revision"] != before["revision"]:
                        add("NEW_REVISION", ref["target_id"], affected, path + [ref["target_id"]], ref,
                            fixed_ref(current), "watch")
                except BurrowlError as exc:
                    add(exc.code, ref["target_id"], affected, path + [ref["target_id"]], ref, relation="watch")
                continue
            try:
                state = session.reader.state(watch["owner_id"])
            except BurrowlError as exc:
                add(exc.code, watch["owner_id"], affected, path + [watch["owner_id"]], relation="watch")
                continue
            known = set(watch["baseline_unit_ids"]) | included
            for record in state.records.values():
                if record["kind"] == "detail" and record["record_id"] not in known \
                        and record["sensitivity"] != "restricted" and not session.check_refs([fixed_ref(record)]):
                    uncovered.add(record["record_id"])
                    add("NEW_UNIT_UNCOVERED", record["record_id"], affected,
                        path + [watch["owner_id"], record["record_id"]], after=fixed_ref(record), relation="watch")
    other_type = "research_report" if doc["payload"]["document_type"] == "research_process" else "research_process"
    others = [r for r in session.state.records.values()
              if r["kind"] == "document" and r["payload"]["document_type"] == other_type and r["sensitivity"] != "restricted"]
    if others:
        other = max(others, key=lambda r: (r["updated_at"], r["revision"], r["record_id"]))
        for section_ref in other["payload"]["section_refs"]:
            try:
                for block in session.section(section_ref)["payload"]["blocks"]:
                    other_ref = block.get("ref") if block["type"] == "unit" else None
                    for own_ref, affected in unit_versions.get(other_ref["target_id"] if other_ref else "", []):
                        if (own_ref["revision"], own_ref["sha256"]) != (other_ref["revision"], other_ref["sha256"]):
                            add("DOCUMENT_BASIS_MISMATCH", own_ref["target_id"], affected,
                                [doc["record_id"], other["record_id"], section_ref["target_id"], own_ref["target_id"]],
                                own_ref, other_ref, "composition")
            except BurrowlError:
                continue
    return session.finish({"owner_id": owner_id, "document": session.metadata(), "changes": changes,
                           "affected_section_ids": sorted({sid for c in changes for sid in c["section_ids"]}),
                           "uncovered_unit_ids": sorted(uncovered), "scientific_review": "not_evaluated"})


def figure(storage: Storage, owner_id: str, record_id: str, revision: int, index: int) -> dict:
    """只返回技术单元登记的、数据目录内的栅格图片（PNG/JPEG/WebP），拒绝 SVG/HTML 与任意路径。"""
    reader = Reader(storage)
    reader.state(owner_id)
    record = reader.record({"target_id": record_id, "revision": revision})
    if record["owner_id"] != owner_id or record["kind"] != "detail":
        raise BurrowlError("ACCESS_DENIED", "图片不属于所选技术单元")
    figures = record["payload"].get("figures", [])
    if not 0 <= index < len(figures):
        raise BurrowlError("NOT_FOUND", "图片序号不存在")
    for ref in iter_refs({"sources": record["sources"], "payload": record["payload"]}):
        reader.check_ref(ref)
    ref = figures[index]["ref"]
    if ref["target_kind"] != "file":
        raise BurrowlError("INVALID_ARGUMENT", "图片必须引用已登记的固定文件")
    source = storage.sources.resolve(ref["target_id"])
    if not source.path.is_relative_to(storage.ws.root):
        raise BurrowlError("ACCESS_DENIED", "研究图片必须是数据目录内的受控附件")
    if source.path.stat().st_size > MAX_FIGURE_BYTES:
        raise BurrowlError("INVALID_ARGUMENT", "图片超过 8 MiB 展示上限")
    raw = source.path.read_bytes()
    mime = ("image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if raw.startswith(b"\xff\xd8\xff")
            else "image/webp" if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" else None)
    if mime is None or sha256_bytes(raw) != ref["sha256"]:
        raise BurrowlError("INTEGRITY_ERROR", "图片格式或固定指纹不匹配")
    reader.recheck()
    return {"data_url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
            "caption": figures[index]["caption"], "sha256": ref["sha256"]}
