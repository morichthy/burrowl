"""R14 Run 作为执行证据：不参与对象扫描、按编号与位置定位、不持有记录、不进检索投影；关系图的 Run 节点来自引用。"""
import json
import os
import shutil
import time
from datetime import UTC, datetime
from importlib.resources import files

import pytest

from burrowl.app import App
from burrowl.core.config import IndexConfig
from burrowl.core.errors import BurrowlError
from burrowl.interfaces.ops.registry import Ctx, call, load
from burrowl.services.activity import ActivityLog
from burrowl.services.health import validate
from burrowl.services.indexing import IndexService
from burrowl.services.memory import consolidation
from burrowl.services.runs import RunService
from burrowl.storage.owners import OwnerRegistry, RunLocator
from tests.service.helpers import AI, goal_draft, narrative_draft, register_source, request, services

OWNER = "RES-FLOATING-POINT-SUMMATION"
RUN = "RUN-20260908T201214Z-D0AB308F66AD"


def test_scan_reads_no_run_cards(sample_workspace, monkeypatch):
    """AC-P1.1：对象扫描的成本与 Run 数无关——扫描时一张 run.json 都不读。"""
    storage, _, _ = services(sample_workspace)
    read = []
    original = OwnerRegistry._card_owner

    def counting(self, rel, raw):
        read.append(rel)
        return original(self, rel, raw)
    monkeypatch.setattr(OwnerRegistry, "_card_owner", counting)
    index = storage.owners.scan()
    assert not any(rel.endswith("run.json") for rel in read)
    assert [o.owner_type for o in index.owners] == ["research"]


def test_find_by_id_and_parent_by_location(sample_workspace):
    storage, _, _ = services(sample_workspace)
    index = storage.owners.scan()
    run = index.get(RUN)
    assert run.owner_type == "run" and run.native.get("owner_id") is None   # 业务卡没写上级
    assert index.run_parent(run).owner_id == OWNER                           # D-04：按位置
    assert {r.owner_id for r in index.runs_under(index.get(OWNER))} == {r.owner_id for r in index.all_runs()}
    standalone = RunService(storage).create("临时测试")
    fresh = storage.owners.scan()
    found = fresh.get(standalone["run_id"])
    assert fresh.run_parent(found) is None and fresh.runs_under(None) == [found]
    assert fresh.find("RUN-20990101T000000Z-000000000000") is None


def test_nonstandard_dir_name_uses_slow_path_and_is_reported(sample_workspace):
    storage, _, _ = services(sample_workspace)
    runs_dir = sample_workspace / "research/floating-point-summation/runs"
    shutil.move(runs_dir / RUN.lower(), runs_dir / "legacy-name")
    index = storage.owners.scan()
    assert index.get(RUN).native_path.endswith("legacy-name/run.json")
    report = validate(storage, IndexService.open(storage, IndexConfig(), fake_vectors=True))
    assert any(w["code"] == "RUN_DIR_NAME" and w["owner_id"] == RUN for w in report["warnings"])


def test_duplicate_run_identity_is_a_problem(sample_workspace):
    storage, _, _ = services(sample_workspace)
    shutil.copytree(sample_workspace / f"research/floating-point-summation/runs/{RUN.lower()}",
                    sample_workspace / f"runs/{RUN.lower()}")
    report = validate(storage, IndexService.open(storage, IndexConfig(), fake_vectors=True))
    assert any(p["area"] == "runs" and p["code"] == "INTEGRITY_ERROR" for p in report["problems"])


def test_location_mismatch_is_reported(sample_workspace):
    storage, _, _ = services(sample_workspace)
    card_path = sample_workspace / f"research/floating-point-summation/runs/{RUN.lower()}/run.json"
    card = json.loads(card_path.read_text(encoding="utf-8-sig"))
    card_path.write_text(json.dumps({**card, "owner_id": "PRJ-ELSEWHERE"}, ensure_ascii=False), encoding="utf-8")
    report = validate(storage, IndexService.open(storage, IndexConfig(), fake_vectors=True))
    mismatch = [w for w in report["warnings"] if w["code"] == "RUN_LOCATION"]
    assert mismatch and mismatch[0]["details"] == {"declared": "PRJ-ELSEWHERE", "by_location": OWNER}


def test_commit_to_run_is_rejected(sample_workspace):
    _, memory, _ = services(sample_workspace)
    with pytest.raises(BurrowlError) as caught:
        memory.commit(request(RUN, None, {"op": "put_record", "client_key": "g", "draft": goal_draft(RUN)}))
    assert caught.value.code == "INVALID_ARGUMENT" and caught.value.details["parent_id"] == OWNER


def test_reference_to_missing_run_still_fails(sample_workspace):
    """AC-P1.2：引用不存在的 Run 与改前一样报 NOT_FOUND。"""
    _, memory, _ = services(sample_workspace)
    draft = goal_draft(OWNER)
    draft.pop("provenance_gap")
    draft["sources"] = [{"target_kind": "owner", "target_id": "RUN-20990101T000000Z-000000000000", "revision": None,
                         "sha256": "0" * 64, "locator": "", "relation": "references"}]
    head = memory.snapshot(memory.owners().get(OWNER)).commit_id
    with pytest.raises(BurrowlError) as caught:
        memory.commit(request(OWNER, head, {"op": "put_record", "client_key": "g", "draft": draft}))
    assert caught.value.code == "NOT_FOUND"


def test_restricted_standalone_run_materials_are_blocked(sample_workspace):
    """AC-P1.3：独立 Run 不在对象列表中，它的敏感级别仍按路径找到并生效。"""
    storage, _, _ = services(sample_workspace)
    created = RunService(storage).create("保密测试")
    folder = sample_workspace / created["path"].rsplit("/", 1)[0]
    card = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    (folder / "run.json").write_text(json.dumps({**card, "sensitivity": "restricted"}), encoding="utf-8")
    (folder / "out.txt").write_text("x", encoding="utf-8")
    relative = f"{created['path'].rsplit('/', 1)[0]}/out.txt"
    with pytest.raises(BurrowlError) as caught:
        storage.owners.scan().require_path_readable(relative)
    assert caught.value.code == "ACCESS_DENIED"


def test_runs_are_not_projected_but_referenced_runs_are_graph_nodes(sample_workspace):
    storage, _, _ = services(sample_workspace)
    index = IndexService.open(storage, IndexConfig(), fake_vectors=True)
    index.reconcile()
    states = index.db.states()
    assert states and not any(s["owner_id"].startswith("RUN-") for s in states)       # D-01：不建投影
    nodes = {n["node_id"]: n for n in index.db.graph_nodes()}
    assert nodes[RUN]["owner_id"] == OWNER and nodes[RUN]["kind"] == "run"            # D-11：由引用者投影
    assert nodes[RUN]["title"] == storage.owners.scan().get(RUN).title
    assert not any(e["type"] == "produces" and e["dst"].startswith("RUN-") for e in index.db.graph_all_edges())
    assert index.db.graph_dangling() == 0


def test_slug_runs_is_reserved(tmp_path):
    storage, _, _ = services(tmp_path)
    with pytest.raises(BurrowlError):
        storage.owners.create("research", "runs", "会和 Run 目录冲突")


# ---------- P2：检索范围与解决依据 ----------
def test_query_and_reading_reject_run_scope(sample_workspace):
    """D-07：查询与阅读的对象范围不接受 Run，提示用 runs.search。"""
    app = App.open(sample_workspace, fake_vectors=True)
    for name, arguments in (("query.search", {"question": "求和顺序", "scope": {"owner_ids": [RUN]}}),
                            ("reading.start", {"question": "求和顺序", "scope_owner_ids": [RUN]})):
        with pytest.raises(BurrowlError) as caught:
            call(Ctx(app=app, actor=AI, channel="mcp"), name, arguments)
        assert caught.value.code == "INVALID_ARGUMENT" and caught.value.details["use"] == "runs.search"


def test_run_conclusion_cannot_resolve_a_question(sample_workspace):
    """D-08：Run 卡的 conclusion 只是执行摘要，不能作为问题的解决依据。"""
    storage, memory, _ = services(sample_workspace)
    example = json.loads((files("burrowl.core.schema") / "examples" / "question.json").read_text(encoding="utf-8"))
    draft = example["operation"]["draft"]
    run = storage.owners.scan().get(RUN)
    assert run.native.get("conclusion")                          # 样例 Run 卡里确实写了结论
    draft["payload"] |= {"status": "resolved", "resolution_refs": [
        {"target_kind": "owner", "target_id": RUN, "revision": None, "sha256": run.fingerprint, "locator": "",
         "relation": "references"}]}
    head = memory.snapshot(memory.owners().get(OWNER)).commit_id
    with pytest.raises(BurrowlError) as caught:
        memory.commit(request(OWNER, head, {"op": "put_record", "client_key": "q", "draft": draft}))
    assert caught.value.code == "INVALID_TRANSITION"


# ---------- P3：未固定证据与丢弃 ----------
def test_unfixed_evidence_reported_until_cited(sample_workspace):
    """AC-P3.1：样例的 Run 都已被引用；新 Run 不写记录时被列出，引用后消失。"""
    storage, memory, _ = services(sample_workspace)
    assert consolidation.prepare(memory, OWNER)["unfixed_evidence"]["runs"] == []
    created = RunService(storage).create("补充实验", owner_id=OWNER)
    listed = consolidation.prepare(memory, OWNER)["unfixed_evidence"]["runs"]
    assert [r["run_id"] for r in listed] == [created["run_id"]]
    assert [r["run_id"] for r in RunService(storage).search(unfixed_only=True)] == [created["run_id"]]
    run_ref = _run_ref(storage, created["run_id"])
    goal = goal_draft(OWNER, "补充实验之后的目标")
    goal.pop("provenance_gap")
    goal["sources"] = [run_ref]
    _commit(memory, goal)
    # goal 不是正式记录（没有层级），引用了也不算固定
    assert [r["run_id"] for r in consolidation.prepare(memory, OWNER)["unfixed_evidence"]["runs"]] == [created["run_id"]]
    evidence = register_source(storage, "SRC-EXTRA", "research/floating-point-summation/extra.txt", "结果\n")
    narrative = narrative_draft(OWNER, evidence, claim_id="CLM-EXTRA-1")
    narrative["sources"].append(run_ref)
    _commit(memory, narrative)
    assert consolidation.prepare(memory, OWNER)["unfixed_evidence"]["runs"] == []


def _run_ref(storage, run_id: str) -> dict:
    run = storage.owners.scan().get(run_id)
    return {"target_kind": "owner", "target_id": run.owner_id, "revision": None, "sha256": run.fingerprint,
            "locator": "", "relation": "references"}


def _commit(memory, draft: dict) -> dict:
    head = memory.snapshot(memory.owners().get(draft["owner_id"])).commit_id
    return memory.commit(request(draft["owner_id"], head, {"op": "put_record", "client_key": "x", "draft": draft}))


def test_discard_restore_and_trash(sample_workspace):
    """D-12：只有无引用的 Run 能丢弃；移入回收站后查不到，可恢复；被引用的不能丢弃。"""
    storage, _, _ = services(sample_workspace)
    runs = RunService(storage)
    with pytest.raises(BurrowlError) as caught:
        runs.discard(RUN, reason="试图丢弃已被引用的 Run", actor=AI)
    assert caught.value.code == "VERSION_CONFLICT" and caught.value.details["cited_by"]
    created = runs.create("临时测试")
    folder = sample_workspace / created["path"].rsplit("/", 1)[0]
    with pytest.raises(BurrowlError):
        runs.discard(created["run_id"], reason=" ", actor=AI)          # 必须说明理由
    result = runs.discard(created["run_id"], reason="沉淀检查无可复用内容", actor=AI)
    assert result["status"] == "discarded" and result["files_deleted"] == 0 and not folder.exists()
    assert storage.owners.scan().find(created["run_id"]) is None
    assert [t["run_id"] for t in runs.trash()] == [created["run_id"]]
    runs.restore(created["run_id"])
    assert folder.is_dir() and storage.owners.scan().get(created["run_id"]).title == "临时测试"
    assert runs.trash() == [] and list((folder / ".run-captures").glob("discard-*.json"))   # 丢弃说明留作历史


def test_empty_trash_is_user_only():
    specs = load()
    assert specs["runs.empty_trash"].effect == "user"
    assert specs["runs.discard"].effect == "write" and specs["runs.restore"].effect == "write"


# ---------- 惰性读取：全局关键操作记录与 runs.search 不读全部 Run 卡 ----------
@pytest.fixture
def card_loads(monkeypatch):
    loads = []
    original = RunLocator._load

    def counting(self, folder, scope=None):
        loads.append(folder)
        return original(self, folder, scope)
    monkeypatch.setattr(RunLocator, "_load", counting)
    return loads


def test_global_activity_reads_only_needed_run_cards(tmp_path, card_loads):
    storage, _, _ = services(tmp_path)
    storage.owners.create("research", "a", "甲")
    runs = RunService(storage)
    ids = [runs.create(f"实验{i}", owner_id="RES-A")["run_id"] for i in range(12)]
    start = time.time() - 86400
    for i, run_id in enumerate(ids):         # 每隔一分钟结束一个；目录修改时间在结束后一秒
        runs.update(run_id, {"status": "succeeded"})
        _set_end(storage, run_id, start + 60 * i, start + 60 * i + 1)
    runs.update(ids[0], {"title": "最早结束、最后改名"})   # 目录修改时间最新（现在），但 ended_at 最早
    log = ActivityLog(storage)
    card_loads.clear()
    page = log.list(kinds=["run"], limit=3)
    assert [item["ref"]["run_id"] for item in page["items"]] == ids[-1:-4:-1]
    assert len(card_loads) < len(ids)                                   # 没有读全部 Run 卡
    everything = log.list(kinds=["run"], limit=200)["items"]            # 全部取出时顺序仍与 ended_at 一致
    assert [item["ref"]["run_id"] for item in everything] == list(reversed(ids))


def _set_end(storage, run_id: str, ended: float, mtime: float) -> None:
    """模拟真实时间线：改写 ended_at，再把目录修改时间设为结束之后（结束时改写 run.json 会更新目录时间）。"""
    folder = storage.ws.path(storage.owners.scan().get(run_id).native_path).parent
    card = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    card["ended_at"] = datetime.fromtimestamp(ended, UTC).isoformat().replace("+00:00", "Z")
    (folder / "run.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    os.utime(folder, (mtime, mtime))


def test_runs_search_stops_after_limit(tmp_path, card_loads):
    storage, _, _ = services(tmp_path)
    runs = RunService(storage)
    ids = [runs.create(f"测试{i}")["run_id"] for i in range(8)]
    card_loads.clear()
    found = runs.search(limit=3)
    assert [r["run_id"] for r in found] == sorted(ids, reverse=True)[:3]
    assert len(card_loads) == 3
