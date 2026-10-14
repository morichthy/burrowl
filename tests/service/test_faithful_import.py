"""R21 导入不丢细节：docx 抽取、原文快照、图片附件、L1 的 origin、覆盖报告、原文展开、原件变化后的逐段对比。"""
import io
import zipfile
from pathlib import Path

import pytest

from burrowl.core import segments as seg
from burrowl.core.errors import BurrowlError
from burrowl.core.technical import validate_unit
from burrowl.index import extract
from burrowl.services.maintenance import MaintenanceService
from burrowl.services.memory import consolidation
from burrowl.services.memory.claims import ClaimGraph
from burrowl.services.snapshots import SnapshotService
from tests.service.helpers import AI, request, services

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _png() -> bytes:
    image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    image.new("RGB", (8, 8), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_pdf_reused_resource_name_selects_correct_page_and_rejects_ambiguity(tmp_path):
    """同名 PDF XObject 不同页可指向不同内容；禁止按第一张猜图片。"""
    image = pytest.importorskip("PIL.Image")
    pdf = pytest.importorskip("pypdf")
    buffer = io.BytesIO()
    image.new("RGB", (20, 20), "red").save(buffer, format="PDF", save_all=True,
                                             append_images=[image.new("RGB", (20, 20), "blue")])
    raw = buffer.getvalue()
    original = tmp_path / "input.pdf"
    original.write_bytes(raw)
    (tmp_path / "data").mkdir()
    storage, _memory, _review = services(tmp_path / "data")
    storage.sources.register("SRC-PDF", str(original))
    snapshots = SnapshotService(storage, ocr_enabled=lambda: False)
    taken = snapshots.take("SRC-PDF")
    # Pillow 写出的每页 image.jpg 同名，快照仍存原始标签，API 视图增加页定位。
    assert taken["assets"][0]["label"] == taken["assets"][1]["label"]
    outline = snapshots.outline(taken)
    assert outline["assets"][0]["label"] != outline["assets"][1]["label"]
    with pytest.raises(BurrowlError, match="多页重复") as caught:
        snapshots.attach("SRC-PDF", taken["assets"][0]["label"])
    assert caught.value.details["available"] == [a["label"] for a in outline["assets"]]
    second = snapshots.attach("SRC-PDF", outline["assets"][1]["label"])
    assert storage.sources.resolve(second["source_id"]).path.read_bytes() == pdf.PdfReader(io.BytesIO(raw)).pages[1].images[0].data
    assert second["ref"]["locator"] == outline["assets"][1]["label"]
    assert snapshots.load("SRC-PDF") == taken  # 对外消歧不改写历史快照。


def _p(text: str, style: str = "", numbered: bool = False, image: bool = False) -> str:
    props = (f'<w:pStyle w:val="{style}"/>' if style else "") + ('<w:numPr><w:ilvl w:val="0"/></w:numPr>' if numbered else "")
    blip = f'<w:r><w:drawing><a:blip {A} r:embed="rId9"/></w:drawing></w:r>' if image else ""
    return f"<w:p><w:pPr>{props}</w:pPr><w:r><w:t>{text}</w:t></w:r>{blip}</w:p>"


def make_docx(body: str, png: bytes | None = None) -> bytes:
    styles = (f'<w:styles {W}><w:style w:styleId="H1"><w:name w:val="heading 1"/></w:style>'
              '<w:style w:styleId="H2"><w:name w:val="heading 2"/></w:style></w:styles>')
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="media/image1.png"/></Relationships>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("word/document.xml", f"<w:document {W} {R}><w:body>{body}</w:body></w:document>")
        package.writestr("word/styles.xml", styles)
        package.writestr("word/_rels/document.xml.rels", rels)
        if png:
            package.writestr("word/media/image1.png", png)
    return buffer.getvalue()


BODY = "".join([
    _p("算法说明", "H1"), _p("本文介绍补偿求和。"),
    _p("定义", "H2"), _p("x_i 为输入，S 为和。"), _p("误差项单独累加", numbered=True),
    "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>参数</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>取值</w:t></w:r></w:p></w:tc></w:tr>"
    "<w:tr><w:tc><w:p><w:r><w:t>n</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>1000</w:t></w:r></w:p></w:tc></w:tr></w:tbl>",
    _p("实验", "H2"), _p("在一千个随机数上，补偿求和的误差比顺序求和小两个数量级，见下图。", image=True),
    _p("致谢", "H1"), _p("感谢同事。"),
])


def test_docx_extraction_keeps_headings_tables_lists_and_images(tmp_path):
    out = extract.extract("a.docx", make_docx(BODY, _png()), cache_dir=tmp_path, ocr_enabled=False)
    paths = [u["path"] for u in out["units"]]
    assert paths == ["算法说明", "算法说明 › 定义", "算法说明 › 实验", "致谢"]
    definitions = out["units"][1]["text"]
    assert "## 定义" in definitions and "- 误差项单独累加" in definitions
    assert "| 参数 | 取值 |\n|---|---|\n| n | 1000 |" in definitions
    assert len(out["assets"]) == 1 and out["assets"][0]["unit"] == out["units"][2]["label"]


def test_segments_diff_by_content():
    old = seg.build_segments([{"label": "L1", "text": "甲", "path": "A"}, {"label": "L2", "text": "乙", "path": "A"},
                              {"label": "L3", "text": "丙", "path": "B"}, {"label": "L4", "text": "丁", "path": "C"}])
    assert [s["key"] for s in old] == ["A", "B", "C"] and old[0]["text"] == "甲\n\n乙"
    new = seg.build_segments([{"label": "x", "text": "甲\n\n乙", "path": "A"}, {"label": "y", "text": "丙改", "path": "B"},
                              {"label": "z", "text": "戊", "path": "E"}, {"label": "w", "text": "丁", "path": "C"}])
    diff = seg.diff_segments(old, new)
    assert {"old_key": "A", "new_key": "A"} in diff["unchanged"] and {"old_key": "C", "new_key": "C"} in diff["unchanged"]
    assert diff["changed"][0]["old_keys"] == ["B"] and diff["changed"][0]["new_keys"] == ["B", "E"]


@pytest.mark.parametrize("length", [20, 200])
def test_coverage_does_not_accept_half_transcription_or_padding(length):
    """一小段成立条件也不能因为短而豁免；加整理者注不能掩盖被删掉的原文。"""
    original = "".join(chr(0x4e00 + i) for i in range(length))
    segments = seg.build_segments([{"label": "s1", "text": original}])
    record = {"kind": "detail", "record_id": "test", "payload": {
        "origin": {"source_ref": {"target_id": "SRC-TEST"}, "locators": ["s1"]},
        "blocks": [{"block_id": "b", "markdown": original[:length // 2]}]}}
    assert seg.coverage(segments, [record], "SRC-TEST", [])["segments"][0]["status"] == "thin"
    record["payload"]["blocks"][0]["markdown"] += "整理者注" * length
    assert seg.coverage(segments, [record], "SRC-TEST", [])["segments"][0]["status"] == "thin"


def test_logical_split_retains_coverage_and_original_order():
    """一个快照 key 可分给多个逻辑块，一条 L1 也可跨 key；联合核对，不要求机械一段一记录。"""
    original = "".join(chr(0x4e00 + i) for i in range(200))
    segments = seg.build_segments([{"label": "page1", "text": original}, {"label": "page2", "text": "后续定义"}])
    records = [
        {"kind": "detail", "record_id": "first", "revision": 1, "title": "第一部分", "payload": {
            "origin": {"source_ref": {"target_id": "SRC-TEST"}, "locators": ["page1"]},
            "blocks": [{"block_id": "b1", "markdown": original[:100]}]}},
        {"kind": "detail", "record_id": "second", "revision": 1, "title": "跨页部分", "payload": {
            "origin": {"source_ref": {"target_id": "SRC-TEST"}, "locators": ["page1", "page2"]},
            "blocks": [{"block_id": "b2", "markdown": original[100:], "locators": ["page1"]},
                       {"block_id": "b3", "markdown": "后续定义", "locators": ["page2"]}]}}]
    assert seg.coverage(segments, records, "SRC-TEST", [])["counts"]["covered"] == 2
    expanded = seg.expansion(segments, records, "SRC-TEST", [])
    assert [b["markdown"] for b in expanded[0]["blocks"]] == [original[:100], original[100:]]
    assert expanded[1]["blocks"][0]["markdown"] == "后续定义"


def test_docx_table_images_are_extracted_in_cell_order(tmp_path):
    table = "<w:tbl><w:tr><w:tc>" + _p("图与图注", image=True) + "</w:tc></w:tr></w:tbl>"
    out = extract.extract("table.docx", make_docx(table, _png()), cache_dir=tmp_path, ocr_enabled=False)
    assert len(out["assets"]) == 1
    assert out["assets"][0]["unit"] == out["units"][0]["label"]
    assert "图与图注" in out["units"][0]["text"] and "[Image:" in out["units"][0]["text"]


@pytest.fixture
def imported(tmp_path):
    storage, memory, review = services(tmp_path)
    owner_id = storage.owners.create("knowledge", "kahan", "补偿求和").owner_id
    doc = tmp_path / "originals" / "kahan.docx"
    doc.parent.mkdir()
    doc.write_bytes(make_docx(BODY, _png()))
    storage.sources.register("SRC-KAHAN", "originals/kahan.docx", title="补偿求和说明")
    snapshots = SnapshotService(storage, ocr_enabled=lambda: False)
    return storage, memory, review, snapshots, owner_id, doc


def test_import_preserves_original_image_bytes_and_all_occurrences(imported):
    storage, _memory, _review, snapshots, _owner_id, doc = imported
    image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    image.new("RGBA", (2400, 2), (200, 30, 30, 100)).save(buffer, format="PNG")
    raw = buffer.getvalue()
    doc.write_bytes(make_docx(_p("图片", image=True) * 41, raw))
    storage.sources.register("SRC-MANY", "originals/kahan.docx", title="多图原文")
    taken = snapshots.take("SRC-MANY")
    assert len(taken["assets"]) == 41   # 导入不沿用检索的 40 张上限。
    assert not any("上限" in w for w in taken["warnings"])
    attached = snapshots.attach("SRC-MANY", taken["assets"][0]["label"])
    saved = storage.ws.path(attached["path"]).read_bytes()
    assert saved == raw
    with image.open(io.BytesIO(saved)) as original:
        assert original.size == (2400, 2) and original.mode == "RGBA"


def test_attach_embedded_image_rejects_changed_source(imported):
    _storage, _memory, _review, snapshots, _owner_id, doc = imported
    taken = snapshots.take("SRC-KAHAN")
    doc.write_bytes(make_docx(BODY.replace("一千", "两千"), _png()))
    with pytest.raises(BurrowlError) as caught:
        snapshots.attach("SRC-KAHAN", taken["assets"][0]["label"])
    assert caught.value.code == "STALE_BASIS"


def test_attach_repairs_original_asset_cache_before_preserving_image(imported):
    _storage, _memory, _review, snapshots, _owner_id, doc = imported
    extracted = snapshots._extract(doc.name, doc.read_bytes(), ocr_enabled=False)
    asset = extracted["assets"][0]
    Path(asset["original_path"]).write_bytes(b"broken cache")
    attached = snapshots.attach("SRC-KAHAN", asset["label"])
    assert snapshots.storage.ws.path(attached["path"]).read_bytes() == _png()


def test_markdown_image_links_report_unsaved_assets(tmp_path):
    out = extract.extract("images.md", b"# notes\n![inline](picture.png)\n![reference][]\n![shortcut]",
                          cache_dir=tmp_path, ocr_enabled=False)
    assert not out["assets"]
    assert any("未读取或保存图片" in warning for warning in out["warnings"])


def test_image_audit_distinguishes_missing_body_and_unavailable_attachment(imported):
    storage, _memory, _review, snapshots, owner_id, _doc = imported
    taken = snapshots.take("SRC-KAHAN")
    assert snapshots.coverage(owner_id, "SRC-KAHAN")["asset_counts"]["missing"] == 1
    asset = taken["assets"][0]
    attached = snapshots.attach("SRC-KAHAN", asset["label"])
    record = {"kind": "detail", "record_id": "test", "payload": {
        "origin": {"source_ref": {"target_id": "SRC-KAHAN"}, "locators": [asset["segment"]]},
        "figures": [{"ref": attached["ref"], "caption": "图"}],
        "blocks": [{"block_id": "b", "markdown": "原文"}]}}
    def report():
        return snapshots._asset_coverage(taken["assets"], [record], "SRC-KAHAN", [])
    assert report()[0]["status"] == "unreferenced"
    record["payload"]["blocks"][0]["markdown"] += "\n![图](figure:0)"
    assert report()[0]["status"] == "linked"
    record["payload"]["origin"]["locators"] = ["其他章节"]
    assert report()[0]["status"] == "missing"  # 别处的引用不能掩盖原位置的缺口。
    record["payload"]["origin"]["locators"] = [asset["segment"]]
    storage.ws.path(attached["path"]).write_bytes(b"changed")
    assert report()[0]["status"] == "unavailable"


def _detail(source, figure, title, unit_type, blocks, locators):
    return {"kind": "detail", "title": title, "body_markdown": "", "record_reason": "转写原文", "sources": [source],
            "payload": {"unit_type": unit_type, "run_ref": None, "evidence_refs": [], "missing_refs": [],
                        "figures": [figure] if figure else [], "origin": {"source_ref": source, "locators": locators},
                        "retrieval_description": {"question": "补偿求和", "method": "转写", "key_findings": ["误差更小"],
                                                  "applicable": ["双精度"], "not_applicable": ["未知"],
                                                  "limitations": ["未知"]},
                        "blocks": blocks}}


def test_snapshot_attach_coverage_expansion_and_change(imported):
    storage, memory, review, snapshots, owner_id, doc = imported
    taken = snapshots.take("SRC-KAHAN")
    keys = [s["key"] for s in taken["segments"]]
    assert keys == ["算法说明", "算法说明 › 定义", "算法说明 › 实验", "致谢"]
    view = snapshots.outline(taken, ["算法说明 › 定义"])
    assert view["texts"][0]["key"] == "算法说明 › 定义" and "1000" in view["texts"][0]["text"]
    assert "texts" not in snapshots.outline(taken)  # 目录不冒充全文。
    batch = snapshots.outline(taken, keys, max_chars=1)
    assert batch["truncated_keys"] == keys[1:]
    # 分批限额只跳过整段；后续读这些 key 能取回全部内容，逻辑单元不被切坏。
    assert {s["key"] for s in snapshots.outline(taken, batch["truncated_keys"])["texts"]} == set(keys[1:])
    assert taken["assets"][0]["segment"] == "算法说明 › 实验"
    attached = snapshots.attach("SRC-KAHAN", taken["assets"][0]["label"])
    assert attached["path"].startswith("attachments/") and storage.ws.path(attached["path"]).is_file()
    assert snapshots.attach("SRC-KAHAN", taken["assets"][0]["label"])["source_id"] == attached["source_id"]  # 幂等

    source = {"target_kind": "file", "target_id": "SRC-KAHAN", "revision": None,
              "sha256": taken["sha256"], "locator": "", "relation": "input"}
    figure = {"caption": "补偿求和与顺序求和的误差对比", "ref": attached["ref"] | {"relation": "references"}}
    definitions = _detail(source, None, "补偿求和的定义", "method",
                          [{"block_id": "intro", "role": "introduction", "requires_block_ids": [],
                            "markdown": "本文介绍补偿求和。", "locators": ["算法说明"]},
                           {"block_id": "def", "role": "definitions", "requires_block_ids": [],
                            "markdown": "x_i 为输入，S 为和。误差项单独累加。\n\n| 参数 | 取值 |\n|---|---|\n| n | 1000 |",
                            "locators": ["算法说明 › 定义"]}], ["算法说明", "算法说明 › 定义"])
    experiment = _detail(source, figure, "补偿求和的误差实验", "experiment",   # 原文实验：origin 代替 run_ref
                         [{"block_id": "result", "role": "results", "requires_block_ids": [],
                           "markdown": "在一千个随机数上误差小两个数量级。\n\n![误差对比](figure:0)"}], ["算法说明 › 实验"])
    receipt = memory.commit(request(owner_id, None, {"op": "put_record", "client_key": "d", "draft": definitions},
                                    {"op": "put_record", "client_key": "e", "draft": experiment}))
    assert receipt["save_status"] == "committed"

    report = snapshots.coverage(owner_id, "SRC-KAHAN")
    # 此样例省去了标题并缩写实验原文；旧的三成阈值将它们判为已覆盖，完整转录规则下必须提示核对。
    assert report["counts"] == {"covered": 0, "thin": 3, "uncovered": 1, "omitted": 0}
    assert report["asset_counts"]["linked"] == 1
    assert next(r for r in report["segments"] if r["key"] == "致谢")["excerpt"] == "# 致谢 感谢同事。"

    l0 = {"kind": "source", "title": "补偿求和说明（登记）", "body_markdown": "公司补偿求和说明原件。",
          "record_reason": "登记原件", "sources": [source],
          "payload": {"source_ref": source, "acquisition": "original_link", "completeness": "complete",
                      "acquired_at": None, "omitted": [{"locators": ["致谢"], "reason": "致谢与技术内容无关"}]}}
    memory.commit(request(owner_id, receipt["commit_id"], {"op": "put_record", "client_key": "s", "draft": l0}))
    assert snapshots.coverage(owner_id, "SRC-KAHAN")["counts"]["omitted"] == 1
    sections = snapshots.expansion(owner_id, "SRC-KAHAN")["sections"]
    assert [s["status"] for s in sections] == ["covered", "covered", "covered", "omitted"]
    assert sections[2]["blocks"][0]["markdown"].endswith("![误差对比](figure:0)")

    # 收口：两条 L1 都没有被上层指向；L4 正文写长编号被拒，并给出可直接替换的短名链接
    by_key = {r["client_key"]: r for r in receipt["record_results"]}
    unlinked = {r["target_id"] for r in consolidation.unlinked_details(storage.owners.scan().get(owner_id),
                                                                       ClaimGraph(storage))}
    assert unlinked == {by_key["d"]["record_id"], by_key["e"]["record_id"]}
    head = memory.snapshot(memory.owners().get(owner_id)).commit_id
    overview = {"kind": "overview", "title": "补偿求和概览", "record_reason": "概览", "sources": [source],
                "body_markdown": f"补偿求和的定义见 {by_key['d']['record_id']}。",
                "payload": {"question": "补偿求和是什么", "methods": ["转写"], "results": ["误差更小"],
                            "current_stage": "已导入", "limitations": ["无"], "open_questions": [],
                            "experience_refs": [], "claims": [], "process_refs": [], "technical_refs": []}}
    with pytest.raises(BurrowlError) as caught:
        memory.commit(request(owner_id, head, {"op": "put_record", "client_key": "o", "draft": overview}))
    assert caught.value.code == "UNREADABLE_REF"
    error = caught.value.errors[0]
    assert error["path"] == "/operations/0/draft/body_markdown" and f"[[@{by_key['d']['handle']}]]" in error["hint"]
    overview["body_markdown"] = f"补偿求和的定义见 [[@{by_key['d']['handle']}]]，代码里的 `{by_key['d']['record_id']}` 不算。"
    memory.commit(request(owner_id, head, {"op": "put_record", "client_key": "o", "draft": overview}))
    unlinked = {r["target_id"] for r in consolidation.unlinked_details(storage.owners.scan().get(owner_id),
                                                                       ClaimGraph(storage))}
    assert unlinked == {by_key["e"]["record_id"]}

    doc.write_bytes(make_docx(BODY.replace("小两个数量级", "小三个数量级"), _png()))   # 原件改版
    compared = snapshots.compare("SRC-KAHAN")
    assert compared["status"] == "changed"
    assert compared["diff"]["changed"][0]["old_keys"] == ["算法说明 › 实验"]
    assert len(compared["diff"]["unchanged"]) == 3
    plan = MaintenanceService(memory, review, snapshots).plan(["SRC-KAHAN"], actor=AI)
    touched = {i["target"]["target_id"]: i["origin_changes"] for i in plan["items"] if "origin_changes" in i}
    assert touched[by_key["e"]["record_id"]]["changed"] == ["算法说明 › 实验"]
    assert touched[by_key["d"]["record_id"]]["changed"] == [] and "只把 origin" in touched[by_key["d"]["record_id"]]["hint"]
    assert plan["source_changes"]["SRC-KAHAN"]["status"] == "changed"


def test_unit_rules_for_origin_and_general():
    source = {"target_kind": "file", "target_id": "SRC-KAHAN", "revision": None, "sha256": "a" * 64,
              "locator": "", "relation": "input"}
    block = {"block_id": "a", "role": "discussion", "requires_block_ids": [], "markdown": "text"}
    general = _detail(source, None, "t", "general", [block], ["k"])
    general["sources"] = []
    assert validate_unit(general) == []                       # general + origin, no other evidence: allowed
    general["payload"]["origin"]["source_ref"] = {**source, "target_kind": "owner", "target_id": "RES-X"}
    assert [p for p, _m in validate_unit(general)] == ["/origin/source_ref"]
    experiment = _detail(source, None, "t", "experiment", [block], ["k"])
    experiment["payload"]["origin"] = None
    assert [p for p, _m in validate_unit(experiment)] == ["/run_ref"]   # neither Run nor origin


def test_ref_rewrite_plan_for_legacy_records(imported, monkeypatch):
    storage, memory, review, snapshots, owner_id, _doc = imported
    taken = snapshots.take("SRC-KAHAN")
    source = {"target_kind": "file", "target_id": "SRC-KAHAN", "revision": None, "sha256": taken["sha256"],
              "locator": "", "relation": "input"}
    block = {"block_id": "a", "role": "discussion", "requires_block_ids": [], "markdown": "text"}
    first = memory.commit(request(owner_id, None, {"op": "put_record", "client_key": "d",
                                                   "draft": _detail(source, None, "unit", "general", [block], [])}))
    target = first["record_results"][0]
    legacy = {"kind": "overview", "title": "legacy", "record_reason": "r", "sources": [source],
              "body_markdown": f"see {target['record_id']} and {taken['sha256']}",
              "payload": {"question": "q", "methods": ["m"], "results": ["r"], "current_stage": "s",
                          "limitations": ["l"], "open_questions": [], "experience_refs": [], "claims": [],
                          "process_refs": [], "technical_refs": []}}
    monkeypatch.setattr("burrowl.services.memory.links.LinkResolver.raw_id_errors", lambda *_a, **_k: [])
    memory.commit(request(owner_id, first["commit_id"], {"op": "put_record", "client_key": "o", "draft": legacy}))
    monkeypatch.undo()
    service = MaintenanceService(memory, review, snapshots)
    candidates = service.ref_rewrite_candidates()
    assert len(candidates) == 1 and candidates[0]["fixable"] == 1 and len(candidates[0]["items"]) == 2
    plan = service.plan_ref_rewrite([candidates[0]["record_id"]], actor=AI)
    assert plan["status"] == "reviewed" and plan["preview"][0]["ok"]
    service.apply(plan["plan_id"], actor=AI, expected_revision=1)
    record = memory.inspect(owner_id, record_id=candidates[0]["record_id"])["record"]
    assert record["body_markdown"] == f"see [[@{target['handle']}]] and {taken['sha256']}"
    assert record["links"][0]["ref"]["target_id"] == target["record_id"]
