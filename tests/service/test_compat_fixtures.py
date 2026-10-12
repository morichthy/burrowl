"""真源兼容性：用当前代码读取 tests/fixtures/compat/ 下每个历史版本冻结的标本（生成见 tools/make_compat_fixture.py）。

标本一经提交不再修改。这里失败说明当前代码读不了某个旧版本写出的数据：
应当补上对旧格式的读取，而不是改标本或放宽断言。
"""
import json
import shutil
import uuid
from pathlib import Path

import pytest

from burrowl.app import App
from burrowl.interfaces.ops.registry import Ctx, call
from tools.make_compat_fixture import format_fingerprint, problems

COMPAT = Path(__file__).parents[1] / "fixtures" / "compat"
AI = {"kind": "ai", "id": "compat-test"}


def _version(path: Path) -> tuple[int, ...]:
    return tuple(int(part) for part in path.name.removeprefix("v").split("."))


FIXTURES = sorted((p for p in COMPAT.glob("v*") if (p / "FIXTURE.json").is_file()), key=_version)


class _Writer:
    """与生成脚本相同的调用方式（problems() 需要 .call）。"""

    def __init__(self, app: App):
        self.app = app

    def call(self, name: str, **arguments):
        return call(Ctx(app=self.app, actor=AI, channel="cli"), name, arguments)


@pytest.fixture(params=FIXTURES, ids=lambda p: p.name)
def opened(request, tmp_path):
    """标本副本：数据目录与工作根一起复制，再把工作根重定向到副本位置（来源迁移本身也在检验之列）。"""
    shutil.copytree(request.param / "data", tmp_path / "data")
    shutil.copytree(request.param / "work", tmp_path / "work")
    meta = json.loads((request.param / "FIXTURE.json").read_text(encoding="utf-8"))
    app = App.open(tmp_path / "data", fake_vectors=True)
    w = _Writer(app)
    w.call("sources.relocate_root", root_id=meta["work_root_id"], new_path=str(tmp_path / "work"))
    return w, meta


def test_every_record_revision_reads_and_verifies(opened):
    """沿每个对象的整条提交链读出全部修订，逐条核对身份与指纹（读取校验在存储层，失败即抛 INTEGRITY_ERROR）。"""
    w, meta = opened
    storage = w.app.storage
    index = storage.owners.scan()
    assert set(meta["owners"]) <= {o.owner_id for o in index.owners}
    kinds, revisions = set(), 0
    for owner in index.owners:
        if not owner.persisted:
            continue
        for manifest in storage.store.iter_manifests(owner):
            for record in storage.store.commit_records(owner, manifest):
                kinds.add(record["kind"])
                revisions += 1
    assert set(meta["kinds"]) <= kinds and revisions > len(meta["kinds"])


def test_runs_sources_and_state_files_load(opened):
    w, meta = opened
    index = w.app.storage.owners.scan()
    owned, standalone, trashed = meta["runs"]
    assert index.run_parent(index.get(owned)).owner_id == "RES-FLOATING-POINT-SUMMATION"   # 归属按位置
    assert index.run_parent(index.get(standalone)) is None
    assert index.find(trashed) is None and [t["run_id"] for t in w.call("runs.trash")] == [trashed]
    for source_id in meta["sources"]:
        assert w.app.storage.sources.resolve(source_id).path.is_file()
    assert w.call("reading.list") and w.call("relations.notes")       # 阅读会话与工作台笔记
    assert w.call("config.get")["config"]["reading"]["strategy"] == "quick"


def test_transcription_state_reads(opened):
    """v0.8.0 起的标本含导入转写：快照、附件与 L1 的 origin 在新代码下照常读取（R21）。"""
    w, meta = opened
    if "transcription" not in meta:
        pytest.skip("该版本没有导入转写的数据")
    owner_id, source_id = meta["transcription"]["owner_id"], meta["transcription"]["source_id"]
    counts = w.call("memory.document", owner_id=owner_id, view="coverage", source_id=source_id)["counts"]
    assert counts["uncovered"] == 0 and counts["omitted"] == 1
    sections = w.call("memory.document", owner_id=owner_id, view="expansion", source_id=source_id)["sections"]
    assert any(b.get("markdown") for s in sections for b in s["blocks"])
    detail = next(r for r in w.call("memory.records", owner_id=owner_id, kinds=["detail"])["items"])
    record = w.call("memory.inspect", owner_id=owner_id, record_id=detail["record_id"])["record"]
    assert w.call("memory.figure", owner_id=owner_id, record_id=record["record_id"], revision=record["revision"],
                  index=0)["data_url"].startswith("data:image/png")


def test_no_new_problems_and_derived_state_rebuilds(opened):
    """升级后不能多出问题；检索索引与关系图投影（不入标本）能从零重建并检索到标本内容。"""
    w, meta = opened
    assert problems(w) == meta["known_problems"]
    w.call("index.reconcile", force=True)
    assert not [p for p in w.call("system.validate")["problems"] if p["area"] == "index"]
    hits = w.call("query.search", question="标本知识", content_source="all",
                  scope={"owner_ids": [meta["owners"][0]]})["items"]
    assert any(h["title"] == "标本经过 CLM-COMPAT-STANDALONE" for h in hits)


def test_old_data_accepts_new_writes(opened):
    """旧数据上继续工作：修订旧记录、在旧对象下建 Run。"""
    w, meta = opened
    owner_id = meta["owners"][0]
    record = next(r for r in w.call("memory.records", owner_id=owner_id, kinds=["narrative"])["items"])
    current = w.call("memory.inspect", owner_id=owner_id)
    draft = {k: current["records"][record["record_id"]][k] for k in ("owner_id", "kind", "title", "body_markdown", "keywords", "payload", "sources",
                                   "provenance_gap", "discovery", "sensitivity")}
    draft |= {"title": draft["title"] + "（升级后修订）", "record_reason": "兼容性测试", "change_reason": "兼容性测试"}
    w.call("memory.commit", owner_id=owner_id, expected_head=current["head"]["commit_id"],
           request_id=str(uuid.uuid4()),
           operations=[{"op": "put_record", "record_id": record["record_id"],
                        "expected_revision": record["revision"], "draft": draft}])
    assert w.call("runs.create", title="升级后新 Run", owner_id="RES-FLOATING-POINT-SUMMATION")["run_id"]


def test_format_change_requires_new_fixture():
    """真源格式指纹与最新标本不同：这次改动可能影响已有数据的读取，须生成新标本并保证旧标本仍能读。"""
    assert FIXTURES, "缺少兼容性标本：运行 uv run python tools/make_compat_fixture.py"
    latest = json.loads((FIXTURES[-1] / "FIXTURE.json").read_text(encoding="utf-8"))
    assert format_fingerprint() == latest["format_fingerprint"], (
        f"真源格式与 {FIXTURES[-1].name} 标本不同：确认旧标本的测试仍通过（补旧格式读取），"
        "然后升版本号并运行 uv run python tools/make_compat_fixture.py 生成新标本")
