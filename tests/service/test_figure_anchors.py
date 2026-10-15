"""R24 图作为记录内的锚点：登记与嵌入、跨记录引用与显示、漏写符号的提示、取图、阅读与图投影的消费方。"""
import io

import pytest

from burrowl.core import links as syntax
from burrowl.core import segments as seg
from burrowl.core.config import Budget
from burrowl.core.errors import BurrowlError
from burrowl.core.technical import embedded_figures, figure_anchors, figure_display_name
from burrowl.index.graph import target_node
from burrowl.services.memory import documents, targets
from burrowl.services.memory.reader import Reader
from burrowl.services.query.assemble import Packet
from burrowl.services.query.budget import Ledger
from burrowl.services.reading import text
from tests.service.helpers import head_of, narrative_draft, register_source, request, services


def _png() -> bytes:
    image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    image.new("RGB", (4, 4), (20, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _detail(source: dict, figures: list[dict], markdown: str, block_id: str = "results") -> dict:
    return {"kind": "detail", "title": "实验一原文", "body_markdown": "", "record_reason": "转写原文",
            "sources": [source, *[f["ref"] for f in figures]],
            "payload": {"unit_type": "general", "run_ref": None, "evidence_refs": [], "missing_refs": [],
                        "figures": figures, "origin": {"source_ref": source, "locators": ["实验一"]},
                        "retrieval_description": {"question": "实验一", "method": "转写", "key_findings": ["成绩提升"],
                                                  "applicable": ["原文条件"], "not_applicable": ["未知"],
                                                  "limitations": ["未知"]},
                        "blocks": [{"block_id": block_id, "role": "results", "requires_block_ids": [],
                                    "markdown": markdown}]}}


@pytest.fixture
def paper(tmp_path):
    storage, memory, _review = services(tmp_path)
    owner_id = storage.owners.create("research", "kawaii", "可爱图片实验").owner_id
    source = register_source(storage, "SRC-PAPER", "paper.md", "# 实验一\n\n成绩提升。Figure 1. 成绩对比。")
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "fig1.png").write_bytes(_png())
    entry = storage.sources.register("SRC-ATT-FIG1", "attachments/fig1.png", title="Figure 1")
    figure_ref = {"target_kind": "file", "target_id": "SRC-ATT-FIG1", "revision": None, "sha256": entry["sha256"],
                  "locator": "figure-1.png", "relation": "references"}
    figures = [{"label": "fig-1", "caption": "Figure 1. 成绩对比。", "ref": figure_ref}]
    receipt = memory.commit(request(owner_id, None, {"op": "put_record", "client_key": "d",
                                                     "draft": _detail(source, figures, "成绩提升。\n\n![[#fig-1]]")}))
    detail = receipt["record_results"][0]
    return storage, memory, owner_id, source, detail


def test_syntax_embed_anchor_and_display_name():
    found = {link.text: link for link in syntax.parse("见 [[@实验一#fig-1|Figure 1]]，![[@实验一#fig-1]]；![[#fig-2]]")}
    assert found["@实验一#fig-1"].embed and found["@实验一#fig-1"].block == "fig-1"  # 同一原文任一处嵌入即嵌入
    assert found["#fig-2"].kind == "anchor" and found["#fig-2"].embed
    payload = {"figures": [{"label": "fig-1", "caption": "a"}, {"caption": "b"}],
               "blocks": [{"block_id": "x", "markdown": "![[#fig-1]] ![b](figure:1)"}]}
    assert figure_anchors(payload) == {"fig-2": 1, "fig-1": 0}  # 没写 label 的图按序号推导
    assert embedded_figures(payload, payload["blocks"][0]["markdown"]) == {0, 1}
    assert embedded_figures(payload, payload["blocks"][0]["markdown"], legacy=False) == {0}
    assert [figure_display_name(payload, i) for i in (0, 1)] == ["Figure 1", "图 2"]
    ref = {"target_kind": "record", "target_id": "MEM-1", "locator": "figure:fig-1"}
    assert target_node(ref) == "MEM-1#fig-1"


def test_caption_counts_as_block_text_for_coverage():
    """R24 D-07：图注只存在 caption 时，覆盖统计仍把它算作嵌图所在块的正文；旧写法不重复计入。"""
    payload = {"figures": [{"label": "fig-1", "caption": "Figure 1. 成绩对比。"}]}
    hit = {"record": {"payload": payload}, "block": {"markdown": "成绩提升。\n\n![[#fig-1]]"}}
    assert seg.block_text(hit).endswith("Figure 1. 成绩对比。")
    legacy = {"record": {"payload": payload}, "block": {"markdown": "![Figure 1. 成绩对比。](figure:0)"}}
    assert seg.block_text(legacy) == legacy["block"]["markdown"]


def test_own_anchor_is_validated_and_not_stored(paper):
    storage, memory, owner_id, source, detail = paper
    record = memory.inspect(owner_id, record_id=detail["record_id"])["record"]
    assert "links" not in record  # 归属记录显示自己的图不是引用
    image = documents.figure(storage, owner_id, detail["record_id"], detail["revision"], label="fig-1")
    assert image["data_url"].startswith("data:image/png") and image["name"] == "Figure 1" and image["index"] == 0
    with pytest.raises(BurrowlError) as caught:
        documents.figure(storage, owner_id, detail["record_id"], detail["revision"], label="fig-9")
    assert caught.value.code == "NOT_FOUND"
    bad = _detail(source, record["payload"]["figures"], "![[#fig-9]]", block_id="other")
    with pytest.raises(BurrowlError) as caught:
        memory.commit(request(owner_id, head_of(memory, owner_id), {"op": "put_record", "client_key": "x", "draft": bad}))
    assert "fig-9" in str(caught.value.errors)
    clash = _detail(source, record["payload"]["figures"], "![[#fig-1]]", block_id="fig-1")
    with pytest.raises(BurrowlError) as caught:
        memory.commit(request(owner_id, head_of(memory, owner_id), {"op": "put_record", "client_key": "y", "draft": clash}))
    assert "块号重复" in str(caught.value.errors)


def test_other_records_cite_and_embed_figure(paper):
    storage, memory, owner_id, source, detail = paper
    handle = detail["handle"]
    draft = narrative_draft(owner_id, source)
    draft["body_markdown"] = f"成绩提升见 [[@{handle}#fig-1|Figure 1]]。\n\n![[@{handle}#fig-1]]"
    receipt = memory.commit(request(owner_id, head_of(memory, owner_id),
                                    {"op": "put_record", "client_key": "n", "draft": draft}))
    rid = receipt["record_results"][0]["record_id"]
    record = memory.inspect(owner_id, record_id=rid)["record"]
    (link,) = record["links"]
    assert link["ref"]["locator"] == "figure:fig-1" and link["ref"]["target_id"] == detail["record_id"]
    assert link["ref"]["revision"] == detail["revision"]  # D-04：与块一样按修订固定
    table = targets.link_table(record, targets.ref_targets(storage, owner_id, record))
    assert table == [{"link": f"[[@{handle}#fig-1]]", "title": "实验一原文 › fig-1", "target_id": detail["record_id"],
                      "revision": detail["revision"], "kind": "record"}]
    page = text.page_of(storage, owner_id, 1)  # 阅读分页同样附引用对照（D-08）
    assert table[0] in page["link_targets"]
    packet = Packet(Reader(storage), Ledger(Budget()), allowed_owner_ids=None)
    packet.add(link["ref"])  # 定位到图：交付显示这张图的块与图
    (part,) = packet.result()["parts"]
    assert "![[#fig-1]]" in part["markdown"] and part["figures"][0]["name"] == "Figure 1"


@pytest.mark.parametrize("body, message", [
    ("![[@{h}]]", "只有图可以"),        # 记录不能显示
    ("![[@{h}#results]]", "只有图可以"),  # 块不能显示
    ("见 [[#fig-1]]", "前面要加 !"),      # 本记录锚点只用于嵌入
    ("![[#fig-1]]", "没有这个图的锚点名"),  # 非 L1 没有 figures
])
def test_invalid_embeds_are_rejected(paper, body, message):
    _storage, memory, owner_id, source, detail = paper
    draft = narrative_draft(owner_id, source)
    draft["body_markdown"] = body.format(h=detail["handle"])
    with pytest.raises(BurrowlError) as caught:
        memory.commit(request(owner_id, head_of(memory, owner_id), {"op": "put_record", "client_key": "n", "draft": draft}))
    assert caught.value.code == "UNRESOLVED_LINK"
    assert message in caught.value.errors[0]["message"] and "引用写法" in caught.value.errors[0]["hint"]


def test_missing_sigil_warns_but_saves(paper):
    _storage, memory, owner_id, source, detail = paper
    draft = narrative_draft(owner_id, source)
    draft["body_markdown"] = f"见 [[{detail['handle']}]]；结论见 [[ORDER-1]]。"
    receipt = memory.commit(request(owner_id, head_of(memory, owner_id),
                                    {"op": "put_record", "client_key": "n", "draft": draft}))
    assert receipt["save_status"] == "committed"
    hints = [w for w in receipt["warnings"] if w["code"] == "LINK_SIGIL_MISSING"]
    assert [h["link"] for h in hints] == [f"[[{detail['handle']}]]", "[[ORDER-1]]"]
    assert f"[[@{detail['handle']}]]" in hints[0]["action"] and "[[!ORDER-1]]" in hints[1]["action"]
    assert hints[0]["path"] == "/operations/0/draft/links"
