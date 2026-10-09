"""R13 归属判定：与阅读会话第一轮同一召回、目标与检查点补充、来源重叠与目录亲和、用户指定、
索引缺口、成本边界；提交回执中的 attribution（R13 D-06）。"""
import uuid

import pytest

from burrowl.core.config import BurrowlConfig, IndexConfig
from burrowl.core.errors import BurrowlError
from burrowl.services import routing
from burrowl.services.indexing import IndexService
from burrowl.services.memory.context import Storage
from burrowl.services.memory.service import MemoryService
from burrowl.services.reading.service import ReadingService
from burrowl.services.review import claim_lookup_factory
from burrowl.services.runs import RunService
from burrowl.storage.memory_store import MemoryStore
from tests.service.helpers import AI, goal_draft, narrative_draft, register_source, request

SUM, PUMP, FMT = "RES-SUM", "RES-PUMP", "TOOL-FMT"


def put(memory: MemoryService, owner_id: str, head, draft: dict, key: str = "k", **extra) -> dict:
    return memory.commit(request(owner_id, head, {"op": "put_record", "client_key": key, "draft": draft}) | extra)


def checkpoint_draft(owner_id: str, goal_ref: dict, next_step: str) -> dict:
    return {"owner_id": owner_id, "kind": "checkpoint", "title": "阶段交接", "body_markdown": "",
            "record_reason": "交接", "sensitivity": "internal", "sources": [goal_ref], "provenance_gap": None,
            "payload": {"goal_ref": goal_ref, "route_refs": [], "completed_refs": [], "question_refs": [],
                        "next_step": next_step, "prerequisites": ["确认随机种子与样本量"], "stop_reason": "阶段收口",
                        "budget_remaining": {"value": None, "reason": "not_applicable", "note": "无预算约束"},
                        "missing_refs": []}}


class Workspace:
    def __init__(self, root):
        self.storage = Storage.open(root)
        self.index = IndexService.open(self.storage, IndexConfig(), fake_vectors=True)
        self.memory = MemoryService(self.storage, index=self.index,
                                    claim_lookup_factory=claim_lookup_factory(self.storage))
        self.config = BurrowlConfig()
        self.reading = ReadingService(self.storage, self.index, lambda: self.config)

    def route(self, **kwargs) -> dict:
        return routing.route(self.storage, self.index, self.reading, **kwargs)


@pytest.fixture
def ws(tmp_path):
    w = Workspace(tmp_path)
    w.storage.owners.create("research", "sum", "浮点求和误差研究", {"keywords": ["floating point", "summation"]})
    w.storage.owners.create("research", "pump", "泵站光学标定参数整理", {"keywords": ["calibration"]})
    fmt = w.storage.owners.create("tool", "fmt", "浮点格式转换工具", {"description": "binary64 与十进制字符串互转"})
    assert fmt.owner_id == FMT
    receipt = put(w.memory, SUM, None, goal_draft(SUM, "在随机抵消输入上比较顺序、补偿与成对求和的误差"), "g")
    goal_ref = receipt["record_results"][0]["fixed_ref"]
    receipt = put(w.memory, SUM, receipt["commit_id"],
                  checkpoint_draft(SUM, goal_ref, "设计随机抵消输入的生成方法并比较补偿求和"), "c")
    sum_src = register_source(w.storage, "SRC-SUM-1", "work/sum/results.csv", "a,b\n1,2\n")
    put(w.memory, SUM, receipt["commit_id"], narrative_draft(SUM, sum_src), "n")
    pump_src = register_source(w.storage, "SRC-PUMP-1", "work/pump/calib.txt", "偏置 0.3\n")
    put(w.memory, PUMP, None, narrative_draft(PUMP, pump_src, claim_id="CLM-PUMP-1", statement="偏置稳定"), "n")
    return w


def ids(result: dict) -> list[str]:
    return [c["owner"]["owner_id"] for c in result["candidates"]]


def evidence(result: dict, owner_id: str) -> set[str]:
    found = next(c for c in result["candidates"] if c["owner"]["owner_id"] == owner_id)
    return {e["signal"] for e in found["evidence"]}


def test_same_recall_as_reading_first_round(ws):
    # R13 D-03：候选次序与阅读会话第一轮一致（同一查询、同一配置）。
    result = ws.route(task="浮点求和误差", limit=10)
    session = ws.reading.start("浮点求和误差", actor=AI)
    first_round = [p["owner_id"] for p in session["batch"]]
    assert len(first_round) >= 2 and ids(result)[:len(first_round)] == first_round
    assert result["candidates"][0]["packet"]["windows"] == session["batch"][0]["windows"]
    assert result["candidates"][0]["packet"]["windows"] is not None
    assert "suggestion" not in result  # R13 D-04：程序不给建议动作


def test_goal_and_checkpoint_attached(ws):
    result = ws.route(task="浮点求和误差")
    top = next(c for c in result["candidates"] if c["owner"]["owner_id"] == SUM)
    assert top["goal"]["objective"].startswith("在随机抵消输入")
    assert top["checkpoint"]["next_step"].startswith("设计随机抵消输入")
    other = next(c for c in result["candidates"] if c["owner"]["owner_id"] != SUM)
    assert other["goal"] is None and other["checkpoint"] is None


def test_source_overlap_and_directory_affinity(ws):
    root = ws.storage.ws.root
    result = ws.route(task="量子退火调度综述", paths=[str(root / "work" / "sum" / "results.csv")])
    assert SUM in ids(result) and "source_overlap" in evidence(result, SUM)  # 不在召回里也补入
    by_id = ws.route(task="整理数据", paths=["SRC-PUMP-1"])
    assert "source_overlap" in evidence(by_id, PUMP)
    new_file = root / "work" / "pump" / "calib-2.txt"
    new_file.write_text("偏置 0.4\n", encoding="utf-8")
    near = ws.route(task="看看新的标定文件", paths=[str(new_file)])
    assert "directory_affinity" in evidence(near, PUMP) and "source_overlap" not in evidence(near, PUMP)
    missing = ws.route(task="无关", paths=[str(root / "elsewhere" / "x.txt"), "relative.txt", "SRC-NOPE"])
    reasons = {g["reason"] for g in missing["gaps"] if g.get("channel") == "paths"}
    assert reasons == {"unregistered", "not_absolute", "unknown_source"}


def test_owner_hint_first_and_run_maps_to_parent(ws):
    result = ws.route(task="浮点求和误差", owner_hint=PUMP)
    assert ids(result)[0] == PUMP and "user_specified" in evidence(result, PUMP)
    run = RunService(ws.storage).create("一次标定", owner_id=PUMP)
    assert ids(ws.route(task="补一次标定", owner_hint=run["run_id"]))[0] == PUMP
    with pytest.raises(BurrowlError):
        ws.route(task="x", owner_hint="RES-NOPE")


def test_index_gap_reported(ws):
    ws.index.db.drop_owner(SUM)
    result = ws.route(task="浮点求和误差")
    assert any(g.get("owner_id") == SUM for g in result["gaps"])


def test_cost_bounded(ws, monkeypatch):
    # R13 D-07：判定不读任何对象的全部记录；指针读取只针对返回的候选。
    def forbidden(*_args, **_kwargs):
        raise AssertionError("不应读取完整快照")

    monkeypatch.setattr(MemoryStore, "read_snapshot", forbidden)
    result = ws.route(task="浮点求和误差", paths=["SRC-SUM-1"], limit=1)
    assert result["cost"]["pointer_reads"] <= len(result["candidates"])
    assert len(result["candidates"]) <= 2  # limit=1，加上来源重叠补入的对象


def test_invalid_arguments(ws):
    for kwargs in ({"task": " "}, {"task": "x", "limit": 0}, {"task": "x", "limit": 11}):
        with pytest.raises(BurrowlError):
            ws.route(**kwargs)


def test_attribution_saved_in_receipt_and_idempotent(ws):
    owner_id = ws.storage.owners.create("research", "rand", "随机输入求和").owner_id
    attribution = {"action": "create", "decided_by": "ai", "reason": "读过 RES-SUM 后判定目标不同",
                   "rejected": [{"owner_id": SUM, "reason": "RES-SUM 只研究固定输入"}]}
    req = request(owner_id, None, {"op": "put_record", "client_key": "g", "draft": goal_draft(owner_id)}) \
        | {"attribution": attribution}
    receipt = ws.memory.commit(req)
    assert receipt["attribution"] == attribution
    assert ws.memory.commit(req)["commit_id"] == receipt["commit_id"]  # 同一请求重放得到原回执
    changed = req | {"attribution": attribution | {"reason": "换了理由"}}
    with pytest.raises(BurrowlError):
        ws.memory.commit(changed)  # 同一 request_id、内容不同 → 拒绝


@pytest.mark.parametrize("bad", [
    {"action": "merge", "decided_by": "ai", "reason": "x"},
    {"action": "create", "decided_by": "robot", "reason": "x"},
    {"action": "create", "decided_by": "ai", "reason": ""},
    {"action": "create", "decided_by": "ai", "reason": "x", "rejected": [{"owner_id": SUM}]},
    {"action": "create", "decided_by": "ai", "reason": "x", "extra": 1},
])
def test_attribution_validated(ws, bad):
    req = request(SUM, None, {"op": "put_record", "client_key": "g", "draft": goal_draft(SUM)}) | {"attribution": bad}
    req["request_id"] = str(uuid.uuid4())
    with pytest.raises(BurrowlError):
        ws.memory.commit(req)
