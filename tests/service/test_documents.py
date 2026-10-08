"""文稿读取：在真实样例（浮点求和研究的两份文稿）上验证完整组装、目录、章节上下文、影响与图片。"""
from burrowl.services.memory import documents
from burrowl.services.memory.context import Storage

OWNER = "RES-FLOATING-POINT-SUMMATION"


def test_full_document_outline_and_context(sample_workspace):
    storage = Storage.open(sample_workspace)
    full = documents.build_document(storage, OWNER)
    assert full["document_source"] == "independent"
    report = full["report"]
    assert report["sections"] and report["complete"], full["missing"]
    unit_blocks = [b for s in report["sections"] for b in s["blocks"] if b["type"] == "unit"]
    assert unit_blocks and all(b["resolved_blocks"] for b in unit_blocks)
    brief = documents.build_document(storage, OWNER, document_type="research_report")
    assert brief["document"]["document_type"] == "research_report"
    toc = documents.outline(storage, OWNER)
    assert len(toc["sections"]) == len(report["sections"])
    first = toc["sections"][0]["section_id"]
    packet = documents.section_context(storage, OWNER, first, max_chars=200_000)
    assert packet["manifest"]["complete"] and packet["context_text"].startswith("# ")
    small = documents.section_context(storage, OWNER, first, max_chars=300)
    assert not small["manifest"]["complete"] and small["manifest"]["required_not_full"]


def test_impact_and_figures(sample_workspace):
    storage = Storage.open(sample_workspace)
    impact = documents.document_impact(storage, OWNER)
    assert impact["scientific_review"] == "not_evaluated" and isinstance(impact["changes"], list)
    full = documents.build_document(storage, OWNER)
    for section in full["report"]["sections"]:
        for block in section["blocks"]:
            record = block.get("record")
            if record and record["payload"].get("figures"):
                image = documents.figure(storage, OWNER, record["record_id"], record["revision"], 0)
                assert image["data_url"].startswith("data:image/")
                return
    raise AssertionError("样例文稿应包含至少一张图片")
