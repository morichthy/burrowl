"""关系图、监测与结构导航（样例数据）。"""
from burrowl.core.config import IndexConfig
from burrowl.services import monitor, relations
from burrowl.services.indexing import IndexService
from burrowl.services.memory import structure
from burrowl.services.memory.context import Storage

OWNER = "RES-FLOATING-POINT-SUMMATION"


def test_graph_and_subgraph(sample_workspace):
    storage = Storage.open(sample_workspace)
    graph = relations.build_graph(storage, IndexService.open(storage, IndexConfig(), fake_vectors=True))
    types = {n["type"] for n in graph["nodes"]}
    assert types == {"owner", "record", "source", "block"}   # 样例没有结论和概念页
    assert any(e["type"] == "belongs_to" and e["category"] == "structure" for e in graph["edges"])
    evidence = relations.subgraph(graph, categories=["evidence"], node_types=["record", "source"])
    assert evidence["edges"] and {e["category"] for e in evidence["edges"]} == {"evidence"}
    near = relations.subgraph(graph, center=OWNER, hops=1)
    assert OWNER in near["centers"] and len(near["nodes"]) > 1
    found = relations.subgraph(graph, query="Kahan")
    assert found["centers"]
    prefs = relations.WorkbenchPrefs(storage)
    prefs.save_view({"name": "默认", "mode": "radial", "positions": {}})
    assert prefs.view()["name"] == "默认"


def test_monitor_detects_source_change(sample_workspace):
    storage = Storage.open(sample_workspace)
    watch = monitor.Monitor(storage)
    first = watch.run()
    assert first["changes"] == [] and first["baseline_updated"]
    source = storage.sources.resolve("SRC-SUMMATION-R01")
    source.path.write_text(source.path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    preview = watch.run(dry_run=True)                      # 预览：有变化但什么都不写
    assert preview["changes"] and not preview["baseline_updated"]
    assert len(watch.observations()) == 1 and watch.candidates() == []
    second = watch.run()
    assert any(c["target_id"] == "SRC-SUMMATION-R01" for c in second["changes"])
    assert second["maintenance_candidates"]
    assert watch.observations()[0]["at"] == second["at"]
    open_items = watch.candidates()
    assert {i["target_id"] for i in open_items} == set(second["maintenance_candidates"])
    watch.resolve(open_items[0]["target_id"], "dismissed", "确认不影响结论")
    assert len(watch.candidates()) == len(open_items) - 1


def test_structure(sample_workspace):
    storage = Storage.open(sample_workspace)
    tree = structure.owners(storage)
    research = next(g for g in tree["groups"] if g["owner_type"] == "research")
    assert research["owners"][0]["counts"]["L1"] >= 1
    page = structure.records(storage, OWNER, limit=5)
    assert page["items"][0]["level"] == "L4" and page["next_offset"] == 5


def test_records_filter_by_kind_and_query(sample_workspace):
    storage = Storage.open(sample_workspace)
    full = structure.records(storage, OWNER, limit=500)
    counts = full["kind_counts"]
    assert sum(counts.values()) == full["total"]
    details = structure.records(storage, OWNER, kinds=["detail"], limit=500)
    assert details["total"] == counts["detail"] and {r["kind"] for r in details["items"]} == {"detail"}
    assert details["kind_counts"] == counts                # 计数在类型筛选之前，筛选项不会随勾选消失
    first = full["items"][0]
    found = structure.records(storage, OWNER, query=first["title"][:4].upper(), limit=500)
    assert first["record_id"] in {r["record_id"] for r in found["items"]}
    assert all("excerpt" in r and len(r["excerpt"]) <= structure.EXCERPT_CHARS for r in full["items"])
