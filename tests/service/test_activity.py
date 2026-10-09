"""R13 关键操作记录：只列改变资产的操作、每条一行、可追溯；分页、筛选、受限内容不外泄；
写操作返回值带 activity 行（与列表同一套摘要）。"""
import pytest

from burrowl.app import App
from burrowl.core.errors import BurrowlError
from burrowl.interfaces.ops.registry import Ctx, call
from burrowl.services.activity import ActivityLog
from tests.service.helpers import AI, HUMAN, goal_draft, narrative_draft, register_source


@pytest.fixture
def app(tmp_path):
    return App.open(tmp_path, fake_vectors=True)


def op(app: App, name: str, actor: dict = AI, **arguments):
    return call(Ctx(app=app, actor=actor, channel="mcp"), name, arguments)


def put(app: App, owner_id: str, head, draft: dict, **extra) -> dict:
    return op(app, "memory.commit", owner_id=owner_id, expected_head=head,
              operations=[{"op": "put_record", "client_key": "k", "draft": draft}], **extra)


@pytest.fixture
def history(app):
    created = op(app, "memory.owner_create", owner_type="research", slug="rand", title="随机输入求和")
    assert created["activity"] == ["建立对象 RES-RAND「随机输入求和」"]
    attribution = {"action": "create", "decided_by": "ai", "reason": "读过 RES-SUM 后判定目标不同",
                   "rejected": [{"owner_id": "RES-SUM", "reason": "只研究固定输入"}]}
    first = put(app, "RES-RAND", None, goal_draft("RES-RAND"), attribution=attribution)
    assert first["activity"] == ["归属：新建 RES-RAND（放弃 RES-SUM）：读过 RES-SUM 后判定目标不同", "RES-RAND 保存 1 条（goal×1）"]
    evidence = register_source(app.storage, "SRC-R1", "research/rand/r.txt", "结果\n")
    second = put(app, "RES-RAND", first["commit_id"], narrative_draft("RES-RAND", evidence, claim_id="CLM-R1"))
    assert second["activity"] == ["RES-RAND 保存 1 条（L2×1）"]
    reviewed = op(app, "review.submit", actor=HUMAN, target_claim_id="CLM-R1", state="accepted", reason="可复算",
                  scope="整数输入", evidence_refs=[evidence])
    assert reviewed["activity"] == ["结论 CLM-R1 确认"]
    run = op(app, "runs.create", title="一次实验", owner_id="RES-RAND")
    assert "activity" not in op(app, "runs.update", run_id=run["run_id"], fields={"title": "改名"})
    done = op(app, "runs.update", run_id=run["run_id"], fields={"status": "failed"})
    assert done["activity"] == [f"{run['run_id']} 失败「改名」"]
    app.hub.state.finish_run(app.hub.state.start_run("mdvault", "h"), {"created": 2})
    return run


def test_list_shows_key_operations_newest_first(app, history):
    items = op(app, "memory.activity")["items"]
    kinds = [i["kind"] for i in items]
    assert {"owner", "attribution", "save", "review", "run", "publish"} <= set(kinds)
    assert next(i for i in items if i["kind"] == "run")["owner_id"] == "RES-RAND"
    assert [i["at"] for i in items] == sorted((i["at"] for i in items), reverse=True)
    attribution = next(i for i in items if i["kind"] == "attribution")
    save = next(i for i in items if i["kind"] == "save" and "goal" in i["summary"])
    assert attribution["ref"] == save["ref"] and attribution["ref"]["kind"] == "commit"
    assert items.index(attribution) < items.index(save)  # 同一提交里归属确认在前
    assert next(i for i in items if i["kind"] == "review")["actor"] == "用户"
    assert next(i for i in items if i["kind"] == "publish")["summary"] == "发布到 mdvault：新增 2"


def test_pagination_filter_and_scope(app, history):
    everything = op(app, "memory.activity")["items"]
    first = op(app, "memory.activity", limit=2)
    rest = op(app, "memory.activity", limit=200, before=first["next_before"])
    assert [i["cursor"] for i in first["items"] + rest["items"]] == [i["cursor"] for i in everything]
    assert rest["next_before"] is None
    saves = op(app, "memory.activity", kinds=["save"])["items"]
    assert saves and {i["kind"] for i in saves} == {"save"}
    scoped = op(app, "memory.activity", owner_id="RES-RAND")["items"]
    assert all(i["kind"] != "publish" for i in scoped)
    with pytest.raises(BurrowlError):
        op(app, "memory.activity", owner_id="RES-NOPE")


def test_restricted_records_not_listed(app):
    op(app, "memory.owner_create", owner_type="research", slug="sec", title="保密")
    draft = goal_draft("RES-SEC") | {"sensitivity": "restricted"}
    receipt = put(app, "RES-SEC", None, draft)
    assert "activity" not in receipt
    summaries = [i["summary"] for i in ActivityLog(app.storage).list()["items"]]
    assert not any("RES-SEC 保存" in s for s in summaries)


def test_reads_are_not_listed(app, history):
    before = op(app, "memory.activity")["items"]
    op(app, "memory.owner_route", task="随机输入求和")
    op(app, "memory.inspect", owner_id="RES-RAND")
    assert op(app, "memory.activity")["items"] == before
