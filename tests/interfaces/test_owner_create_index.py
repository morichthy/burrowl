"""新对象立即建检索投影：只有业务卡的对象不再一直是 not_indexed 缺口，判定能找到它（避免重复新建）。

R18 实施时发现：memory.owner_create / owner_adopt 不触发索引同步，新对象在 index.reconcile 或第一次提交之前
在每次召回里都报缺口，阅读会话因此总是允许全文补偿。修复后新建路径上的阅读会重新召回（新对象进入了索引，
结果确实不同），沿用已有对象的路径照常复用判定的召回（用户 2026-10-11 选定）。
"""
import hashlib

import pytest

from burrowl.app import App
from burrowl.interfaces.ops.registry import Ctx, call
from tests.service.helpers import narrative_draft

AI = {"kind": "ai", "id": "test-agent"}
TASK = "浮点求和误差的比较"


@pytest.fixture
def ctx(tmp_path):
    app = App.open(tmp_path / "data", create=True, fake_vectors=True)
    ctx = Ctx(app=app, actor=AI, channel="test")
    owner = call(ctx, "memory.owner_create", {"owner_type": "research", "slug": "sum", "title": "浮点求和误差研究"})
    (app.root / "notes").mkdir()
    (app.root / "notes" / "sum.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    registered = call(ctx, "sources.register", {"source_id": "SRC-SUM", "path": "notes/sum.csv"})
    evidence = {"target_kind": "file", "target_id": "SRC-SUM", "revision": None, "sha256": registered["sha256"],
                "locator": "", "relation": "input"}
    draft = narrative_draft(owner["owner_id"], evidence, "CLM-SUM-1", "补偿求和误差更小")
    call(ctx, "memory.commit", {"owner_id": owner["owner_id"], "expected_head": None,
                                "operations": [{"op": "put_record", "client_key": "n", "draft": draft}]})
    return ctx


def owner_gaps(result: dict, owner_id: str) -> list[dict]:
    return [g for g in result["gaps"] if g.get("owner_id") == owner_id]


def test_created_owner_is_indexed_and_routable(ctx):
    created = call(ctx, "memory.owner_create", {"owner_type": "research", "slug": "pump",
                                                "title": "泵站光学标定参数整理", "keywords": ["calibration"]})
    assert created["index"]["index_status"] == "indexed" and "warnings" not in created
    route = call(ctx, "memory.owner_route", {"task": "泵站光学标定参数整理"})
    assert not owner_gaps(route, created["owner_id"])
    assert created["owner_id"] in {c["owner"]["owner_id"] for c in route["candidates"]}


def test_adopted_owner_is_indexed(ctx):
    doc = ctx.app.root / "research" / "notes" / "calib.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# 标定说明\n", encoding="utf-8")
    adopted = call(ctx, "memory.owner_adopt", {"path": "research/notes/calib.md",
                                               "expected_sha256": hashlib.sha256(doc.read_bytes()).hexdigest()})
    assert adopted["index"]["index_status"] == "indexed"
    assert not owner_gaps(call(ctx, "memory.owner_route", {"task": "标定说明"}), adopted["owner_id"])


def test_reuse_on_continue_path_but_not_after_create(ctx):
    # 沿用：判定 → resume（只读）→ 开阅读，复用判定的召回
    call(ctx, "memory.owner_route", {"task": TASK})
    assert call(ctx, "reading.start", {"question": TASK})["reused_from"]["origin"] == "owner_route"
    # 新建：判定 → 新建 → 开阅读。新对象进入了索引，召回确实不同，重新召回且不报它的缺口
    call(ctx, "memory.owner_route", {"task": TASK})
    created = call(ctx, "memory.owner_create", {"owner_type": "research", "slug": "sum-rand", "title": "随机输入求和"})
    session = call(ctx, "reading.start", {"question": TASK, "owner_id": created["owner_id"]})
    assert session["reused_from"] is None
    assert not owner_gaps(session, created["owner_id"])
