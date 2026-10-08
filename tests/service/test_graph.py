"""R12 T-P2.7 / T-P2.9：图投影的通用抽边、快捷边与关联；图路线的过滤、排除与默认关闭。"""
import pytest

from burrowl.core.config import GraphRoute, IndexConfig
from burrowl.core.errors import BurrowlError
from burrowl.index import query_plan
from burrowl.services.indexing import IndexService
from burrowl.services.memory.context import Storage
from burrowl.services.memory.service import MemoryService
from burrowl.services.review import claim_lookup_factory
from tests.service.helpers import goal_draft, head_of, narrative_draft, register_source, request


def _commit(memory, owner_id, *operations):
    return memory.commit(request(owner_id, head_of(memory, owner_id), *operations))


@pytest.fixture
def lab(tmp_path):
    storage = Storage.open(tmp_path)
    library = storage.owners.create("knowledge", "concepts", "概念库").owner_id
    owner = storage.owners.create("research", "lab", "补偿求和研究").owner_id
    other = storage.owners.create("research", "other", "排序研究").owner_id
    index = IndexService.open(storage, IndexConfig(), fake_vectors=True, concept_owner=lambda: library)
    memory = MemoryService(storage, index=index, claim_lookup_factory=claim_lookup_factory(storage),
                           concept_owner=lambda: library)
    evidence = register_source(storage, "SRC-LAB-1", "data/lab.txt", "1e16 + 1 = 1e16\n")

    def concept(title):
        return {"owner_id": library, "kind": "wiki_page", "title": title, "body_markdown": f"{title}的定义。",
                "record_reason": "建立概念", "sensitivity": "internal", "sources": [evidence],
                "payload": {"page_type": "concept", "aliases": [], "source_refs": [evidence], "domains": ["数值计算"]}}

    made = _commit(memory, library, {"op": "put_record", "client_key": "a", "draft": concept("补偿求和")},
                   {"op": "put_record", "client_key": "b", "draft": concept("大数抵消")})
    concepts = {r["handle"]: r["record_id"] for r in made["record_results"]}

    claim_record = narrative_draft(owner, evidence, "CLM-LAB-补偿抵消失效", "大数抵消时补偿项会被吞没")
    claim_record["title"] = "第三轮经过"
    claim_record["body_markdown"] = "在 [[大数抵消]] 输入下，[[补偿求和]] 仍可能丢失小量。"
    claim_record["payload"]["claims"][0]["relation"] = {"subject": "[[补偿求和]]", "predicate": "fails_on",
                                                        "object": "[[大数抵消]]"}
    linked = narrative_draft(owner, evidence, "CLM-LAB-2", "排序能减少误差")
    linked["title"] = "排序对照经过"
    linked["body_markdown"] = "对照组只改变求和顺序。"
    result = _commit(memory, owner, {"op": "put_record", "client_key": "c", "draft": claim_record},
                     {"op": "put_record", "client_key": "d", "draft": linked})
    records = {r["handle"]: r for r in result["record_results"]}
    far = goal_draft(other, "评估日志压缩工具的速度")   # 与查询没有任何字面重合，只能经图到达
    far_id = _commit(memory, other, {"op": "put_record", "client_key": "e", "draft": far})["record_results"][0]
    return storage, memory, index, owner, other, concepts, records, far_id


def _association(owner, a, b, status, relation="related_to", certainty="speculative"):
    return {"owner_id": owner, "kind": "association", "title": "关联", "body_markdown": "", "record_reason": "导航",
            "sensitivity": "internal", "sources": [], "provenance_gap": "导航关联，无依据",
            "payload": {"from": a["fixed_ref"], "to": b["fixed_ref"], "relation": relation, "explanation": "值得一起看",
                        "shared_structure": None, "transfer_limits": [], "basis_refs": [], "status": status,
                        "certainty": certainty, "origin": "ai"}}


def test_projection_has_concepts_claims_and_shortcut_edge(lab):
    _storage, _memory, index, _owner, _other, concepts, records, _far = lab
    index.ensure_graph()
    nodes = {n["node_id"]: n for n in index.db.graph_nodes()}
    assert nodes[concepts["补偿求和"]]["node_type"] == "concept"
    assert nodes[concepts["补偿求和"]]["domains"] == ["数值计算"]
    assert nodes["CLM-LAB-补偿抵消失效"]["node_type"] == "claim"
    edges = index.db.graph_all_edges()
    mentions = [e for e in edges if e["type"] == "mentions" and e["src"] == records["第三轮经过"]["record_id"]]
    assert {e["dst"] for e in mentions} == set(concepts.values()) and mentions[0]["category"] == "concept"
    shortcut = next(e for e in edges if e["derived"])
    assert (shortcut["src"], shortcut["dst"], shortcut["type"], shortcut["category"]) == (
        concepts["补偿求和"], concepts["大数抵消"], "fails_on", "claim_relation")
    assert shortcut["declared_by"] == "CLM-LAB-补偿抵消失效" and shortcut["state"] == "not-reviewed"
    assert index.db.graph_dangling() == 0


def test_graph_route_off_by_default_and_follows_links(lab):
    _storage, memory, index, owner, other, _concepts, records, far = lab
    plan = query_plan.build("补偿求和 大数抵消 丢失小量")
    scope = {"owner_ids": [owner, other], "channels": ("lexical",)}  # 新对象的记录默认 owner_only，需显式指定
    base = index.search_plan(plan, **scope)
    assert base["items"] and index.search_plan(plan, **scope, graph_route=GraphRoute())["items"] == base["items"]
    assert far["record_id"] not in {i["record_id"] for i in base["items"]}

    # 关联把另一对象的记录接到图上：图路线能把它带进来；rejected 的关联不参与扩展
    _commit(memory, other, {"op": "put_record", "client_key": "x",
                            "draft": _association(other, far, records["第三轮经过"], "rejected")})
    on = GraphRoute(enabled=True, seeds=5)
    assert far["record_id"] not in {i["record_id"] for i in index.search_plan(plan, **scope, graph_route=on)["items"]}
    _commit(memory, other, {"op": "put_record", "client_key": "y",
                            "draft": _association(other, far, records["第三轮经过"], "accepted")})
    found = index.search_plan(plan, **scope, graph_route=on)
    hit = next(i for i in found["items"] if i["record_id"] == far["record_id"])
    assert hit["via"][0]["category"] == "navigation" and found["graph"]["hits"] >= 1
    assert any(r["id"] == "graph" for r in found["routes"])

    # 过滤同样作用于图路线：只看 L4 时，图路线不能带回 L2 记录
    l4_only = index.search_plan(plan, **scope, graph_route=on, levels=["L4"])
    assert far["record_id"] not in {i["record_id"] for i in l4_only["items"]}

    # 预算：最多 1 条边时停止扩展并报告
    tight = index.search_plan(plan, **scope, graph_route=on, graph_limits=(50, 1))
    assert {"channel": "graph", "reason": "graph_budget_truncated"} in tight["gaps"]



def test_quick_association_candidates_and_gaps(lab):
    """T-P2.10 / T-P2.11：快速添加关联的默认值；保存后算出的候选；判断过的端点对不再出现；新增的缺口。"""
    from burrowl.services import relations  # noqa: PLC0415
    from burrowl.services.memory import associations  # noqa: PLC0415
    from tests.service.helpers import AI  # noqa: PLC0415

    storage, memory, index, owner, other, concepts, records, far = lab
    mention = narrative_draft(owner, register_source(storage, "SRC-LAB-2", "data/lab2.txt", "x\n"), "CLM-LAB-3")
    mention["title"] = "提到概念但没链接"
    mention["body_markdown"] = "这里讨论补偿求和，但没有写成链接；还提到 [[还没建的概念]]。"
    rid = _commit(memory, owner, {"op": "put_record", "client_key": "m", "draft": mention})["record_results"][0]["record_id"]
    found = relations.candidates(storage, index, record_id=rid)["candidates"]
    assert {"dst": concepts["补偿求和"], "kind": "concept_mention"}.items() <= next(
        c for c in found if c["kind"] == "concept_mention").items()

    gaps = index.gaps()
    assert any(g["gap"] == "missing_concept" and g["names"] == ["还没建的概念"] for g in gaps)

    quick = {"from": records["排序对照经过"]["fixed_ref"], "to": far["fixed_ref"], "explanation": "可能同一类问题"}
    associations.decide(memory, owner, quick, actor=AI, reason="随手登记")
    saved = associations.list_associations(storage)[0]
    assert (saved["relation"], saved["certainty"], saved["status"], saved["origin"]) == (
        "related_to", "speculative", "accepted", "ai")
    with pytest.raises(BurrowlError):   # 关联不能是证据关系
        associations.decide(memory, owner, {**quick, "relation": "supports", "status": "accepted"}, actor=AI,
                            reason="x")
