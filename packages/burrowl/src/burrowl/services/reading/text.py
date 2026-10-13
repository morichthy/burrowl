"""对象的完整阅读文本：优先"研究经过"文稿（按章节顺序），没有文稿时按 L4→L2→L3→L1 排列各记录正文。

分页时不把一个段落/技术块拆开（除非单段本身超过一页）。图片只保留引用（figure:n），需要时再展开。

R19 D-12：交付记录时，其中 L1 块（MEM-…#块）与结论（CLM-…）也是可引用身份，笔记出处可以细到块和结论。
R19 D-11：图扩展带来的对象默认定点阅读（target_page_of）：只交付被关系指到的节点及其必要上下文。
"""
from ...core.errors import BurrowlError
from ...core.records import claim_hash
from ...core.technical import description_text, render_full
from ..memory import documents
from ..memory.context import Storage
from ..memory.reader import Reader, fixed_ref
from ..query.assemble import record_text

ORDER = {"overview": 0, "narrative": 1, "experience": 2, "detail": 3}
PAGE_CHARS = 8000


def claim_ref(claim: dict) -> dict:
    return {"target_kind": "claim", "target_id": claim["claim_id"], "revision": None, "sha256": claim_hash(claim),
            "locator": "statement", "relation": "references"}


def _record_ids(record: dict, block_ids: list[str] | None = None) -> tuple[list[str], list[dict]]:
    """一段交付内容中可引用的身份与额外的固定引用：记录、（给定或全部）L1 块、结论、记录引用的材料。"""
    payload = record["payload"]
    blocks = block_ids if block_ids is not None else [b["block_id"] for b in payload.get("blocks", [])]
    claims = [c for c in payload.get("claims", []) or []
              if block_ids is None or not c.get("block_id") or c["block_id"] in block_ids]
    ids = [record["record_id"], *[f"{record['record_id']}#{b}" for b in blocks], *[c["claim_id"] for c in claims],
           *[s["target_id"] for s in record["sources"]]]
    return ids, [claim_ref(c) for c in claims]


def segments(storage: Storage, owner_id: str) -> tuple[list[dict], str, list[dict]]:
    """返回 (段落列表, 文本形态, 缺口)。每段：{heading, markdown, ref, ids}；ids 是本段交付的可引用身份。"""
    built = documents.build_document(storage, owner_id, document_type="research_process")
    gaps = list(built.get("missing", []))
    if built["document"] is not None:
        out = []
        for section in built["report"]["sections"]:
            gaps.extend(section["source_issues"])
            for block in section["blocks"]:
                if block["source_issues"]:
                    gaps.extend(block["source_issues"])
                elif block["type"] == "prose":
                    out.append({"heading": section["title"], "markdown": block["markdown"], "ref": section["ref"],
                                "ids": [section["section_id"], *[r["target_id"] for r in block["evidence_refs"]]]})
                else:
                    record = block["record"]
                    for item in block["resolved_blocks"]:
                        ids, extra = _record_ids(record, [item["block_id"]])
                        out.append({"heading": f"{section['title']} · {record['title']}", "markdown": item["markdown"],
                                    "ref": fixed_ref(record) | {"locator": "block:" + item["block_id"]},
                                    "ids": [*ids, *[f["ref"]["target_id"] for f in record["payload"].get("figures", [])]],
                                    "extra_refs": extra})
        return out, "document", gaps
    reader = Reader(storage)
    records = [r for r in reader.state(owner_id).records.values()
               if r["kind"] in ORDER and r["sensitivity"] != "restricted"]
    records.sort(key=lambda r: (ORDER[r["kind"]], r["created_at"]))
    out = []
    for record in records:
        issues = reader.try_check([fixed_ref(record)])
        if issues:
            gaps.extend(issues)
            continue
        text = render_full(record) if record["kind"] == "detail" else record_text(record)
        ids, extra = _record_ids(record)
        out.append({"heading": f"{record['level']} · {record['title']}", "markdown": text, "ref": fixed_ref(record),
                    "ids": ids, "extra_refs": extra})
    reader.recheck()
    if not out:
        gaps.append({"code": "NO_READABLE_TEXT", "target_id": owner_id})
    return out, "records", gaps


def paginate(parts: list[dict], page_chars: int = PAGE_CHARS) -> list[list[dict]]:
    pages: list[list[dict]] = [[]]
    size = 0
    for part in parts:
        pieces = [part] if len(part["markdown"]) <= page_chars else [
            part | {"markdown": chunk} for chunk in _split(part["markdown"], page_chars)]
        for piece in pieces:
            if size and size + len(piece["markdown"]) > page_chars:
                pages.append([])
                size = 0
            pages[-1].append(piece)
            size += len(piece["markdown"])
    return [p for p in pages if p] or [[]]


def _split(text: str, size: int) -> list[str]:
    chunks, current = [], ""
    for paragraph in text.split("\n\n"):
        if current and len(current) + len(paragraph) + 2 > size:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
    return [*chunks, current] if current else chunks


def render(page: list[dict]) -> str:
    return "\n\n".join(f"### {p['heading']}\n\n{p['markdown']}" for p in page)


def target_segments(storage: Storage, owner_id: str, targets: list[str]) -> tuple[list[dict], list[dict]]:
    """定点阅读的分段（R19 D-11）：目标是记录 → 该记录正文；L1 块 → 所在记录的检索说明、该块与它依赖的定义块；
    结论 → 所在记录正文。同一记录只交付一次；目标不存在或不可读时记为缺口。"""
    reader = Reader(storage)
    records = {rid: r for rid, r in reader.state(owner_id).records.items() if r["sensitivity"] != "restricted"}
    by_claim = {c["claim_id"]: rid for rid, r in records.items() for c in r["payload"].get("claims", []) or []}
    wanted: dict[str, set[str] | None] = {}  # 记录 → 要交付的块（None 表示整条记录）
    gaps = []
    for target in targets:
        record_id, _, block = target.partition("#")
        record_id = by_claim.get(record_id, record_id)
        if record_id not in records:
            gaps.append({"code": "TARGET_UNAVAILABLE", "target_id": target})
            continue
        if block and wanted.get(record_id, set()) is not None:
            wanted.setdefault(record_id, set()).add(block)
        elif not block:
            wanted[record_id] = None
    out = []
    for record_id, blocks in wanted.items():
        record = records[record_id]
        if issues := reader.try_check([fixed_ref(record)]):
            gaps.extend(issues)
            continue
        if blocks is None or record["kind"] != "detail":
            text = render_full(record) if record["kind"] == "detail" else record_text(record)
            ids, extra = _record_ids(record)
        else:
            by_id = {b["block_id"]: b for b in record["payload"].get("blocks", [])}
            chosen = [b for b in blocks if b in by_id]
            required = [d for b in chosen for d in by_id[b].get("requires_block_ids", []) if d in by_id]
            order = list(dict.fromkeys([*required, *chosen]))  # 定义块在前
            text = "\n\n".join([description_text(record), *(by_id[b]["markdown"] for b in order)])
            ids, extra = _record_ids(record, order)
        out.append({"heading": f"{record['level']} · {record['title']}", "markdown": text, "ref": fixed_ref(record),
                    "ids": ids, "extra_refs": extra})
    reader.recheck()
    if not out:
        gaps.append({"code": "NO_READABLE_TEXT", "target_id": owner_id})
    return out, gaps


def target_page_of(storage: Storage, owner_id: str, targets: list[str], number: int, max_pages: int,
                   page_chars: int = PAGE_CHARS) -> dict:
    """定点阅读的一页；超过 max_pages 的部分不交付，列为缺口（需要时升级为整读）。"""
    parts, gaps = target_segments(storage, owner_id, targets)
    pages = paginate(parts, page_chars)
    if len(pages) > max_pages:
        gaps.append({"code": "TARGET_PAGES_TRUNCATED", "target_id": owner_id,
                     "message": f"定点内容超过 {max_pages} 页，只交付前 {max_pages} 页；需要时用 scope=full 升级为整读"})
        pages = pages[:max_pages]
    return _page(owner_id, "targets", pages, number, gaps)


def page_of(storage: Storage, owner_id: str, number: int, page_chars: int = PAGE_CHARS) -> dict:
    parts, form, gaps = segments(storage, owner_id)
    return _page(owner_id, form, paginate(parts, page_chars), number, gaps)


def _page(owner_id: str, form: str, pages: list[list[dict]], number: int, gaps: list[dict]) -> dict:
    if not 1 <= number <= len(pages):
        raise BurrowlError("INVALID_ARGUMENT", "页码超出范围", {"page": number, "total_pages": len(pages)})
    outline, seen = [], set()
    for index, page in enumerate(pages, 1):
        for part in page:
            if part["heading"] not in seen:
                seen.add(part["heading"])
                outline.append({"heading": part["heading"], "page": index})
    current = pages[number - 1]
    return {"owner_id": owner_id, "form": form, "page": number, "total_pages": len(pages),
            "has_more": number < len(pages), "text": render(current),
            "refs": [p["ref"] for p in current] + [r for p in current for r in p.get("extra_refs", [])],
            "ids": sorted({i for p in current for i in p["ids"]}),
            "outline": outline if number == 1 else None, "gaps": gaps}
