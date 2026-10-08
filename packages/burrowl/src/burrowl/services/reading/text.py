"""对象的完整阅读文本：优先"研究经过"文稿（按章节顺序），没有文稿时按 L4→L2→L3→L1 排列各记录正文。

分页时不把一个段落/技术块拆开（除非单段本身超过一页）。图片只保留引用（figure:n），需要时再展开。
"""
from ...core.errors import BurrowlError
from ...core.technical import render_full
from ..memory import documents
from ..memory.context import Storage
from ..memory.reader import Reader, fixed_ref
from ..query.assemble import record_text

ORDER = {"overview": 0, "narrative": 1, "experience": 2, "detail": 3}
PAGE_CHARS = 8000


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
                        out.append({"heading": f"{section['title']} · {record['title']}", "markdown": item["markdown"],
                                    "ref": fixed_ref(record) | {"locator": "block:" + item["block_id"]},
                                    "ids": [record["record_id"], f"{record['record_id']}#{item['block_id']}",
                                            *[f["ref"]["target_id"] for f in record["payload"].get("figures", [])]]})
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
        out.append({"heading": f"{record['level']} · {record['title']}", "markdown": text, "ref": fixed_ref(record),
                    "ids": [record["record_id"], *[s["target_id"] for s in record["sources"]]]})
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


def page_of(storage: Storage, owner_id: str, number: int, page_chars: int = PAGE_CHARS) -> dict:
    parts, form, gaps = segments(storage, owner_id)
    pages = paginate(parts, page_chars)
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
            "refs": [p["ref"] for p in current], "ids": sorted({i for p in current for i in p["ids"]}),
            "outline": outline if number == 1 else None, "gaps": gaps}
