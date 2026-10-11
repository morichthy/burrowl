"""R18 D-02：归属判定之后，同一问题的 reading.start 复用判定的召回与筛选包；任何输入或检索状态变化都重新召回。"""
import pytest

from burrowl.services.reading.service import ReadingService
from tests.service.helpers import AI, narrative_draft, register_source
from tests.service.test_routing import PUMP, SUM, put, seeded_workspace

TASK = "浮点求和误差"


@pytest.fixture
def ws(tmp_path):
    return seeded_workspace(tmp_path)


def fresh_start(ws, question: str = TASK, **kwargs) -> dict:
    """不经复用的对照：新的 ReadingService 没有缓存。"""
    return ReadingService(ws.storage, ws.index, lambda: ws.config).start(question, actor=AI, **kwargs)


def test_start_reuses_route_screening(ws):
    route = ws.route(task=TASK, limit=10)
    assert route["cost"]["reused_from"] is None
    session = ws.reading.start(TASK, actor=AI)
    assert session["reused_from"]["origin"] == "owner_route"
    # 复用的第一批与重新计算的完全相同（含 digest），判断照常需要 digest
    assert session["batch"] == fresh_start(ws)["batch"]
    view = ws.reading.view(session["session_id"])
    assert view["rounds"][0]["reused_from"]["origin"] == "owner_route"
    assert view["consumed"]["rerank_items"] == 0
    assert any("复用 owner_route" in e["summary"] for e in view["timeline"])
    first = session["batch"][0]
    ws.reading.assess(session["session_id"], [{"owner_id": first["owner_id"], "verdict": "relevant",
                                               "reason": "窗口提到求和误差", "digest": first["digest"]}])


def test_reuse_returns_independent_copies(ws):
    route = ws.route(task=TASK, limit=10)
    route["candidates"][0]["packet"]["windows"].clear()
    session = ws.reading.start(TASK, actor=AI)
    assert session["reused_from"] and session["batch"] == fresh_start(ws)["batch"]


def test_different_question_or_scope_does_not_reuse(ws):
    ws.route(task=TASK)
    assert ws.reading.start("求和误差的补偿方法", actor=AI)["reused_from"] is None
    ws.route(task=TASK)
    assert ws.reading.start(TASK, actor=AI, scope_owner_ids=[SUM])["reused_from"] is None
    ws.route(task=TASK)
    assert ws.reading.start(TASK, actor=AI, review_filter="exclude_unsafe")["reused_from"] is None


def test_new_commit_invalidates(ws):
    ws.route(task=TASK)
    head = ws.storage.store.read_head(ws.storage.owners.scan().get(PUMP)).commit_id
    src = register_source(ws.storage, "SRC-PUMP-2", "work/pump/calib2.txt", "偏置 0.4\n")
    put(ws.memory, PUMP, head, narrative_draft(PUMP, src, claim_id="CLM-PUMP-2", statement="偏置漂移"), "n2")
    session = ws.reading.start(TASK, actor=AI)
    assert session["reused_from"] is None
    assert session["batch"] == fresh_start(ws)["batch"]


def test_config_change_invalidates(ws):
    ws.route(task=TASK)
    ws.config = ws.config.model_copy(update={"reading": ws.config.reading.model_copy(update={"review_filter":
                                                                                           "exclude_unsafe"})})
    assert ws.reading.start(TASK, actor=AI)["reused_from"] is None


def test_expired_entry_is_not_reused(ws):
    ws.reading.reuse.seconds = 0
    ws.route(task=TASK)
    assert ws.reading.start(TASK, actor=AI)["reused_from"] is None


def test_unready_owner_does_not_block_reuse(ws):
    # 未就绪的对象（这里直接写业务卡、不建索引，相当于索引同步失败）不进入召回，只多一条缺口，不妨碍复用。
    # 经 memory.owner_create 新建的对象会立即建索引，召回确实不同，不复用（tests/interfaces/test_owner_create_index.py）
    ws.route(task=TASK, limit=10)
    created = ws.storage.owners.create("research", "sum-rand", "随机输入求和误差", {})
    session = ws.reading.start(TASK, actor=AI, owner_id=created.owner_id)
    assert session["reused_from"]["origin"] == "owner_route"
    fresh = fresh_start(ws)
    assert session["batch"] == fresh["batch"] and session["gaps"] == fresh["gaps"]
    assert any(g.get("owner_id") == created.owner_id for g in session["gaps"])


def test_index_watermark_change_invalidates(ws):
    # 索引未就绪时照样可以复用（同一状态得到同一结果、同样的缺口）；水位一变就重新召回
    ws.index.db.mark_pending(PUMP, None)
    route = ws.route(task=TASK)
    assert any(g.get("owner_id") == PUMP for g in route["gaps"])
    session = ws.reading.start(TASK, actor=AI)
    assert session["reused_from"] and session["gaps"] == fresh_start(ws)["gaps"]
    ws.route(task=TASK)
    ws.index.sync_owner(PUMP)
    session = ws.reading.start(TASK, actor=AI)
    assert session["reused_from"] is None
    assert not any(g.get("owner_id") == PUMP for g in session["gaps"])
