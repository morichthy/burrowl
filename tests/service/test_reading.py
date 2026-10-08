"""阅读会话：agent 主流程、程序强制规则、用户收件箱与全文补偿批准。"""
import pytest

from burrowl.core.config import BurrowlConfig, IndexConfig
from burrowl.core.errors import BurrowlError
from burrowl.services.indexing import IndexService
from burrowl.services.memory.context import Storage
from burrowl.services.reading.service import ReadingService

OWNER = "RES-FLOATING-POINT-SUMMATION"
AI = {"kind": "ai", "id": "reader"}
QUESTION = "浮点求和中求和顺序与补偿算法对误差的影响"


@pytest.fixture
def reading(sample_workspace):
    storage = Storage.open(sample_workspace)
    index = IndexService.open(storage, IndexConfig(), fake_vectors=True)
    index.reconcile()
    config = BurrowlConfig()
    return ReadingService(storage, index, lambda: config), config


def judge_all(service, session, relevant=(OWNER,)):
    batch = session["batch"]
    return service.assess(session["session_id"], [
        {"owner_id": p["owner_id"], "verdict": "relevant" if p["owner_id"] in relevant else "irrelevant",
         "reason": "测试判断", "digest": p["digest"]} for p in batch])


def test_standard_flow_and_rules(reading):
    service, _ = reading
    session = service.start(QUESTION, actor=AI)
    sid = session["session_id"]
    assert session["batch"][0]["owner_id"] == OWNER and session["batch"][0]["windows"]
    run_id = next(p["owner_id"] for p in session["batch"] if p["owner_id"].startswith("RUN-"))
    with pytest.raises(BurrowlError) as caught:
        service.read(sid, OWNER)
    assert caught.value.code == "INVALID_TRANSITION"  # 未判断不能读
    assessed = judge_all(service, session)
    assert assessed["relevant"] == [OWNER] and assessed["phase"] == "reading"
    with pytest.raises(BurrowlError):
        service.read(sid, run_id)  # 判为不相关
    page = service.read(sid, OWNER)
    assert page["outline"] and page["total_pages"] >= 1 and page["ids"]
    while page["has_more"]:
        page = service.read(sid, OWNER, page["page"] + 1)
    delivered = page["ids"][:2]
    with pytest.raises(BurrowlError):
        service.note(sid, OWNER, "提到未交付的 MEM-00000000-0000-0000-0000-000000000000", delivered)
    saved = service.note(sid, OWNER, f"升序累加误差更小（{delivered[0]}）。", delivered)
    assert not saved["over_budget"]
    done = service.finish(sid, "完成")
    assert OWNER in done["handoff"]["included_owner_ids"] and "升序" in done["handoff"]["markdown"]
    with pytest.raises(BurrowlError):
        service.note(sid, OWNER, "再写", delivered)  # 已结束


def test_fulltext_needs_user_approval_and_inbox(reading):
    service, _ = reading
    sid = service.start(QUESTION, actor=AI)["session_id"]
    with pytest.raises(BurrowlError) as caught:
        service.recall(sid, "fulltext", approval_id="AP-fake")
    assert caught.value.code == "APPROVAL_REQUIRED"
    asked = service.ask_user(sid, "fulltext", "发现索引结果不足，是否允许全文补偿？")
    assert asked["status"] == "waiting_user"
    request_id = asked["request"]["request_id"]
    approved = service.respond(sid, request_id, decision="approve")
    service.override(sid, OWNER, "relevant", "用户确认相关")
    status = service.status(sid)
    kinds = [m["kind"] for m in status["inbox"]]
    assert kinds == ["response", "verdict"] and status["inbox"][0]["payload"]["approval_id"]
    assert service.status(sid)["inbox"] == []  # 每条只交付一次
    recalled = service.recall(sid, "fulltext", approval_id=approved["approval_id"])
    assert recalled["round"] == "R2"
    with pytest.raises(BurrowlError):
        service.recall(sid, "fulltext", approval_id=approved["approval_id"])  # 批准只能用一次
    # agent 不能覆盖用户的改判
    digest = service.view(sid)["owners"][OWNER]["packet"]["digest"]
    service.assess(sid, [{"owner_id": OWNER, "verdict": "irrelevant", "reason": "x", "digest": digest}])
    assert service.view(sid)["owners"][OWNER]["verdict"] == "relevant"
    service.abort(sid, "不再需要")
    with pytest.raises(BurrowlError):
        service.read(sid, OWNER)


def test_quick_mode_and_owner_cap(reading):
    service, config = reading
    session = service.start(QUESTION, actor=AI, strategy="quick")
    sid = session["session_id"]
    judge_all(service, session)
    with pytest.raises(BurrowlError):
        service.read(sid, OWNER)
    window = session["batch"][0]["windows"][0]
    with pytest.raises(BurrowlError) as caught:  # quick 必须先采纳窗口
        service.note(sid, OWNER, f"片段显示：{window['text'][:20]}", [window["record_id"]])
    assert caught.value.code == "INVALID_TRANSITION"
    service.assess(sid, [{"owner_id": OWNER, "verdict": "relevant", "reason": "片段相关",
                          "digest": session["batch"][0]["digest"], "accepted_windows": [window["entry_id"]]}])
    other = next((w for w in session["batch"][0]["windows"][1:] if w["record_id"] != window["record_id"]), None)
    if other:
        with pytest.raises(BurrowlError):  # 未采纳的窗口不能作依据
            service.note(sid, OWNER, "x", [other["record_id"]])
    service.note(sid, OWNER, f"片段显示：{window['text'][:20]}", [window["record_id"]])
    handoff = service.finish(sid)["handoff"]
    assert any(g["code"] == "QUICK_MODE" for g in handoff["gaps"])
    config.reading.context.max_owners = 1
    capped = service.start(QUESTION, actor=AI)
    runs = [p["owner_id"] for p in capped["batch"]][:2]
    judge_all(service, capped, relevant=runs)
    service.read(capped["session_id"], runs[0])
    with pytest.raises(BurrowlError) as caught:
        service.read(capped["session_id"], runs[1])
    assert caught.value.code == "BUDGET_EXCEEDED"
    assert {s["session_id"] for s in service.list()} >= {sid, capped["session_id"]}


def _commit_goal(storage):
    """模拟对象在阅读期间有新提交。"""
    from tests.service.helpers import goal_draft, request, services
    _s, memory, _r = services(storage.ws.root)
    head = memory.inspect(OWNER)["head"]["commit_id"]
    memory.commit(request(OWNER, head, {"op": "put_record", "client_key": "g", "draft": goal_draft(OWNER, "新目标")}))


def _read_all(service, sid):
    page = service.read(sid, OWNER)
    while page["has_more"]:
        page = service.read(sid, OWNER, page["page"] + 1)
    return page


def test_note_requires_full_read_and_goes_stale(reading):
    service, _ = reading
    session = service.start(QUESTION, actor=AI)
    sid = session["session_id"]
    judge_all(service, session)
    first = service.read(sid, OWNER)
    if first["total_pages"] > 1:
        with pytest.raises(BurrowlError) as caught:
            service.note(sid, OWNER, "只读了第一页", first["ids"][:1])
        assert caught.value.code == "INVALID_TRANSITION"
    last = _read_all(service, sid)
    source = last["ids"][0]
    saved = service.note(sid, OWNER, f"结论（{source}）", [source], clues=["可对照成对求和"])
    assert saved["owner_id"] == OWNER
    note = service.view(sid)["owners"][OWNER]["note"]
    assert note["basis_head"] and note["fixed_sources"][0]["target_id"] == source.split("#")[0]
    assert service.status(sid)["handoff"]["included_owner_ids"] == [OWNER]
    _commit_goal(service.storage)                             # 对象有了新提交
    handoff = service.status(sid)["handoff"]
    assert handoff["included_owner_ids"] == [] and any(g["code"] == "NOTE_STALE" for g in handoff["gaps"])
    with pytest.raises(BurrowlError) as caught:                   # 翻页时发现对象已更新
        service.read(sid, OWNER, 2) if last["total_pages"] > 1 else service.note(sid, OWNER, "x", [source])
    assert caught.value.code == "STALE_BASIS"
    _read_all(service, sid)                                   # 从第 1 页重读后可以重写笔记
    service.note(sid, OWNER, f"更新后的结论（{source}）", [source])
    assert service.status(sid)["handoff"]["included_owner_ids"] == [OWNER]


def test_digest_reason_required_and_retry_is_idempotent(reading):
    service, config = reading
    session = service.start(QUESTION, actor=AI, strategy="associative")
    sid, packet = session["session_id"], session["batch"][0]
    for bad in ({"owner_id": OWNER, "verdict": "relevant", "reason": "x"},
                {"owner_id": OWNER, "verdict": "relevant", "reason": " ", "digest": packet["digest"]}):
        with pytest.raises(BurrowlError) as caught:
            service.assess(sid, [bad])
        assert caught.value.code == "INVALID_ARGUMENT"
    first = service.recall(sid, "association", association_text="Kahan 补偿", request_id="11111111-1111-1111-1111-111111111111")
    again = service.recall(sid, "association", association_text="Kahan 补偿", request_id="11111111-1111-1111-1111-111111111111")
    assert again["round"] == first["round"]
    assert service.view(sid)["association"]["rounds_used"] == 1      # 重试没有重复消耗轮数
    assert service.status(sid)["consumed"]["candidates"] >= 1


def test_required_reranking_without_model_fails(reading):
    service, config = reading
    config.reading.reranking.mode = "required"
    with pytest.raises(BurrowlError) as caught:
        service.start(QUESTION, actor=AI)
    assert caught.value.code == "CAPABILITY_UNAVAILABLE"


def test_fulltext_only_when_discovery_insufficient(reading):
    service, config = reading
    config.reading.screening.batch_owners = 2                   # 召回数量足够、没有缺口
    session = service.start(QUESTION, actor=AI)
    assert session["fulltext_available"] is False
    with pytest.raises(BurrowlError) as caught:
        service.ask_user(session["session_id"], "fulltext", "要不要全文补偿？")
    assert caught.value.code == "INVALID_TRANSITION"


def test_field_conditions_are_validated_and_reported(reading):
    service, _ = reading
    with pytest.raises(BurrowlError):
        service.start(QUESTION, actor=AI, field_conditions_=[{"id": "t", "kind": "numeric", "field": "温度",
                                                              "operator": "approx", "expected": 1}])
    session = service.start(QUESTION, actor=AI, field_conditions_=[
        {"id": "py", "kind": "literal", "field": "Python", "operator": "eq", "expected": "3.12"}])
    assert all("conditions" in p for p in session["batch"])
