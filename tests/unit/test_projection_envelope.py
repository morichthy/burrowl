"""R12 P1：上下文信封、技术单元记录级全文、Markdown 按标题切分、索引结构自动重建。"""
import sqlite3

from burrowl.index import extract, projections
from burrowl.index.db import IndexDB

OWNER = projections.OwnerInfo("RES-X", "research", "浮点求和研究", "internal", "fp")
DETAIL = {
    "record_id": "MEM-1", "revision": 1, "record_hash": "h", "content_hash": "c", "kind": "detail", "level": "L1",
    "title": "Kahan 补偿实验", "keywords": [], "body_markdown": "", "discovery": "workspace_summary",
    "sensitivity": "internal",
    "payload": {"retrieval_description": {"question": "补偿能否消除抵消误差？", "method": "", "key_findings": [],
                                          "applicable": [], "not_applicable": [], "limitations": []},
                "blocks": [{"block_id": "defs", "role": "definitions", "markdown": "## 定义\n$s_i$ 为累加值",
                            "requires_block_ids": []},
                           {"block_id": "res", "role": "results", "markdown": "## 结果与误差\nKahan 5/30 精确",
                            "requires_block_ids": ["defs"]}]}}


def test_envelope_has_title_chain_question_and_required_definitions():
    block = DETAIL["payload"]["blocks"][1]
    text = projections.envelope(OWNER, DETAIL, block)
    lines = text.splitlines()
    assert lines[0] == "浮点求和研究 › L1 技术单元：Kahan 补偿实验 › 结果与误差"
    assert lines[1] == "问题：补偿能否消除抵消误差？"
    assert "$s_i$ 为累加值" in text           # 依赖的定义块原文排在最后，预算不够时最先被裁
    assert "Kahan 5/30" not in text            # 信封不复制本块或概览正文（D-20）


def test_fulltext_record_entry_contains_all_blocks_and_prefix_stays_out_of_text():
    entries = projections.fulltext(OWNER, [DETAIL], {}, set())
    record = next(e for e in entries if e["block_id"] is None)
    assert "Kahan 5/30 精确" in record["text"] and "累加值" in record["text"]
    for entry in entries:
        assert entry["embed_prefix"].startswith("浮点求和研究 › L1 技术单元")
        assert "› L1 技术单元" not in entry["text"]   # 信封只进向量文本，不进 FTS


def test_markdown_split_by_heading_with_path():
    text = "前言\n# 方法\n内容A\n```\n# 不是标题\n```\n## Kahan\n内容B\n# 结果\n内容C\n".encode()
    units = extract.extract("notes.md", text, cache_dir=__import__("pathlib").Path("."))["units"]
    assert [(u["label"], u["path"]) for u in units] == [
        ("Lines 1-1", ""), ("Lines 2-6", "方法"), ("Lines 7-8", "方法 › Kahan"), ("Lines 9-10", "结果")]
    plain = extract.extract("notes.txt", text, cache_dir=__import__("pathlib").Path("."))["units"]
    assert len(plain) == 1 and plain[0]["path"] == ""


def test_source_entry_prefix_uses_heading_path():
    entries = projections.source("SRC-1", "notes.md", "sha", "internal",
                                 [{"label": "Lines 7-8", "text": "内容B", "path": "方法 › Kahan"}])
    assert entries[0]["embed_prefix"] == "notes.md › 方法 › Kahan"


def test_old_index_schema_is_rebuilt_not_rejected(tmp_path):
    path = tmp_path / "index.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("INSERT INTO meta VALUES ('schema_version', '1'), ('tokenizer', 'old')")
        db.execute("CREATE TABLE entries (entry_id TEXT PRIMARY KEY, corpus TEXT)")
        db.execute("INSERT INTO entries VALUES ('x', 'fulltext')")
    index = IndexDB(path)
    assert index.rebuilt_from == {"schema_version": "1", "tokenizer": "old"}
    with index.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0
        assert "embed_prefix" in {r[1] for r in db.execute("PRAGMA table_info(entries)")}
    assert IndexDB(path).rebuilt_from is None
