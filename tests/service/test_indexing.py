"""统一检索：投影同步、水位新鲜度、关键词/向量/身份召回、复核过滤、提交后自动更新。"""
from burrowl.core.config import IndexConfig
from burrowl.services.indexing import IndexService
from burrowl.services.memory.context import Storage
from burrowl.services.memory.service import MemoryService
from burrowl.services.review import ReviewService, claim_lookup_factory
from tests.service.helpers import HUMAN, narrative_draft, register_source, request

OWNER = "RES-FLOATING-POINT-SUMMATION"


def open_index(storage: Storage) -> IndexService:
    return IndexService.open(storage, IndexConfig(), fake_vectors=True)


def test_sample_reconcile_and_search(sample_workspace):
    storage = Storage.open(sample_workspace)
    index = open_index(storage)
    result = index.reconcile()
    assert {r["owner_id"] for r in result["synced"]} >= {OWNER}
    assert index.status(OWNER)["index_status"] == "indexed"
    assert index.reconcile()["synced"] == []  # 全部新鲜时不重复工作
    hits = index.search("Kahan 补偿 求和 误差", channels=("lexical",))
    assert hits["items"] and hits["items"][0]["owner_id"] == OWNER
    dense = index.search("补偿求和的误差界", channels=("dense",))
    assert dense["items"]
    owners = index.search("浮点 求和", corpus="discovery", group="owner")
    assert owners["items"][0]["owner_id"] == OWNER
    by_id = index.search(OWNER, channels=("identity",), corpus="discovery")
    assert by_id["items"][0]["kind"] == "owner"
    blocks = index.search("Kahan", channels=("lexical",), levels=["L1"])
    assert all(item["level"] == "L1" for item in blocks["items"])


def test_commit_updates_index_and_review_filter(tmp_path):
    storage = Storage.open(tmp_path)
    index = open_index(storage)
    memory = MemoryService(storage, index=index, claim_lookup_factory=claim_lookup_factory(storage))
    review = ReviewService(storage, memory)
    owner_id = storage.owners.create("research", "order", "顺序").owner_id
    evidence = register_source(storage, "SRC-ORDER-1", "research/order/r.txt", "结果\n")
    receipt = memory.commit(request(owner_id, None, {"op": "put_record", "client_key": "n",
                                                     "draft": narrative_draft(owner_id, evidence)}))
    assert receipt["index"]["index_status"] == "indexed"
    query, scope = "升序 误差", [owner_id]
    # 默认策略 discovery=owner_only：只有显式限定到本对象的检索才能看到这些记录。
    assert not index.search(query)["items"]
    assert index.search(query, owner_ids=scope, review_filter="unreviewed_only")["items"]
    assert not index.search(query, owner_ids=scope, review_filter="reviewed_only")["items"]
    review.submit({"target_claim_id": "CLM-ORDER-1", "state": "accepted", "reason": "可复算", "actor": HUMAN,
                   "scope": "整数输入", "evidence_refs": [evidence]})
    accepted = index.search(query, owner_ids=scope, review_filter="reviewed_only")["items"]
    assert accepted and {i["review"] for i in accepted} == {"accepted"}
    review.submit({"target_claim_id": "CLM-ORDER-1", "state": "disputed", "reason": "样本太少", "actor": HUMAN})
    assert not index.search(query, owner_ids=scope, review_filter="exclude_unsafe")["items"]


def test_plan_search_groups_by_owner(sample_workspace):
    from burrowl.index import query_plan

    storage = Storage.open(sample_workspace)
    index = open_index(storage)
    index.reconcile()
    plan = query_plan.build("浮点求和的顺序误差", variants=[{"language": "en", "question": "floating point summation order error"}])
    result = index.search_plan(plan, corpus="discovery", group="owner")
    top = result["items"][0]
    assert top["owner_id"] == OWNER and {s["channel"] for s in top["query_sources"]} >= {"lexical", "dense"}
    records = index.search_plan(plan, corpus="fulltext")
    assert records["items"] and len({r["canonical_id"] for r in records["items"]}) == len(records["items"])


def test_source_files_projection(sample_workspace):
    storage = Storage.open(sample_workspace)
    index = open_index(storage)
    first = index.sync_sources(ocr_enabled=False)
    assert len(first["synced"]) == 44 and not first["skipped"]
    assert index.sync_sources(ocr_enabled=False)["synced"] == []  # 未变化不重复抽取
    hits = index.search("fsum sequential", corpus="source", channels=("lexical",))
    assert hits["items"] and hits["items"][0]["fixed_ref"]["target_kind"] == "file"
    assert hits["items"][0]["fixed_ref"]["locator"].startswith("Lines ")
    index.reconcile()  # 对象投影的维护不能删掉来源投影
    assert index.search("fsum sequential", corpus="source", channels=("lexical",))["items"]


def test_stale_owner_reported_as_gap(sample_workspace, tmp_path):
    storage = Storage.open(sample_workspace)
    index = open_index(storage)
    index.reconcile()
    other = storage.owners.create("research", "new-topic", "新课题")
    hits = index.search("浮点", channels=("lexical",))
    assert any(g["owner_id"] == other.owner_id and g["reason"] == "not_indexed" for g in hits["gaps"])


def test_model_change_makes_vectors_stale_and_reconcile_rebuilds(sample_workspace):
    """R12 T-P0.1：换模型或切窗规则（指纹变化）后，旧向量水位不能冒充就绪；reconcile 补建并清掉旧窗口。"""
    from burrowl.index.embed import FakeEmbedder  # noqa: PLC0415

    storage = Storage.open(sample_workspace)
    open_index(storage).reconcile()
    changed = FakeEmbedder()
    changed.fingerprint += "-rules-v2"
    index = IndexService.open(storage, IndexConfig(), embedder=changed)
    assert index.status(OWNER)["details"]["fulltext.vector"] == "stale"
    stale = index.search("补偿求和的误差界", channels=("dense",))
    assert not stale["items"] and any(g.get("owner_id") == OWNER for g in stale["gaps"])
    assert {r["owner_id"] for r in index.reconcile()["synced"]} >= {OWNER}
    assert index.search("补偿求和的误差界", channels=("dense",))["items"]
    with index.db.connect() as db:
        fps = {r[0] for r in db.execute("SELECT DISTINCT model_fp FROM vectors WHERE owner_id=?", (OWNER,))}
    assert fps == {changed.fingerprint}


def test_query_over_model_limit_reports_gap(sample_workspace):
    from burrowl.index.embed import FakeEmbedder  # noqa: PLC0415

    storage = Storage.open(sample_workspace)
    small = FakeEmbedder()
    small.max_tokens = 8
    index = IndexService.open(storage, IndexConfig(), embedder=small)
    index.reconcile()
    hits = index.search("浮点求和在大数抵消时为什么会丢失小量", channels=("dense",))
    assert {"channel": "dense", "reason": "query_truncated"} in hits["gaps"]
    assert hits["items"]
