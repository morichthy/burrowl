"""材料查询（样例数据）：检索、翻页、组包、展开、完整文稿、深化、正式用途与预算。"""
import pytest

from burrowl.core.config import Budget, BurrowlConfig, IndexConfig
from burrowl.core.errors import BurrowlError
from burrowl.services.indexing import IndexService
from burrowl.services.memory.context import Storage
from burrowl.services.query.models import QueryRequest, Scope
from burrowl.services.query.service import QueryService

OWNER = "RES-FLOATING-POINT-SUMMATION"


@pytest.fixture
def query(sample_workspace):
    storage = Storage.open(sample_workspace)
    index = IndexService.open(storage, IndexConfig(), fake_vectors=True)
    index.reconcile()
    index.sync_sources(ocr_enabled=False)
    return QueryService(storage, index, lambda: BurrowlConfig())


def ask(service, **fields):
    return service.search(QueryRequest(**{"question": "Kahan 补偿求和 误差", "scope": Scope(owner_ids=[OWNER]),
                                          **fields}))


def test_search_page_and_assemble(query):
    page = ask(query, result_limit=1)  # 样例里只有 1 条 L4 与 1 条 L3
    assert len(page.items) == 1 and page.next_cursor
    assert all(item.level in {"L4", "L3"} for item in page.items)
    more = query.page(page.query_id, page.next_cursor)
    assert {c.candidate_id for c in more.items}.isdisjoint({c.candidate_id for c in page.items})
    with pytest.raises(BurrowlError):
        query.page(page.query_id, page.next_cursor)  # 旧游标作废
    packet = query.assemble(page.query_id, [page.items[0].candidate_id])
    assert packet["parts"] and packet["parts"][0]["group"] == "direct" and packet["consumed"]["output_chars"] > 0


def test_technical_and_process_expand_documents_deepen(query):
    page = ask(query, content_source="overview_experience", result_limit=20)
    with_links = next(c for c in page.items if c.kind == "overview")
    expanded = query.expand(page.query_id, [with_links.candidate_id], "technical")
    assert expanded["items"] and all(i["level"] == "L1" for i in expanded["items"])
    technical_id = expanded["items"][0]["candidate_id"]
    packet = query.assemble(page.query_id, [technical_id])
    assert any(p["ref"]["locator"].startswith("block:") for p in packet["parts"])
    docs = query.documents(page.query_id, [with_links.candidate_id])
    assert docs["documents"] and docs["documents"][0]["report"]["sections"]
    graph = query.deepen(page.query_id, [with_links.candidate_id], hops=1)
    assert graph["edges"] and all(e["from"]["target_id"] == with_links.record_id for e in graph["edges"])


def test_technical_source_and_formal_purpose(query):
    technical = ask(query, content_source="technical")
    assert technical.items and {c.level for c in technical.items} == {"L1"}
    formal = ask(query, purpose="formal", applicability="整数输入")
    assert formal.items == []  # 样例中没有已接受的结论
    everything = ask(query, content_source="all", question="fsum sequential")
    assert any(c.kind == "source_file" for c in everything.items)


def test_budget_limits_assembly(query):
    page = ask(query, budget=Budget(output_chars=1200), result_limit=2)
    packet = query.assemble(page.query_id, [c.candidate_id for c in page.items])
    assert not packet["complete"] and any(g["code"] == "BUDGET_EXCEEDED" for g in packet["gaps"])
    query.cancel(page.query_id)
    with pytest.raises(BurrowlError):
        query.assemble(page.query_id, [page.items[0].candidate_id])
