"""来源停用或设为 restricted 后，依赖它的记录在检索条目和关系图中也被隐去；恢复后重新出现。

投影的新鲜度只看对象 HEAD，来源登记变化不会让它过期，所以 sources.update 必须主动重新投影依赖者。
复现（2026-10-11 用户数据）：来源设为 restricted 后，以它为 input 的 L1、L4 读取被拒，检索和关系图却仍显示标题与摘录。
"""
import pytest

from burrowl.app import App
from burrowl.core.errors import BurrowlError
from burrowl.interfaces.ops.registry import Ctx, call
from tests.service.helpers import narrative_draft

HUMAN = {"kind": "human", "id": "workbench-user"}
SOURCE = "SRC-LAB-STRATEGY"


@pytest.fixture
def lab(tmp_path):
    app = App.open(tmp_path / "data", create=True, fake_vectors=True)
    ctx = Ctx(app=app, actor=HUMAN, channel="test")
    owner = call(ctx, "memory.owner_create", {"owner_type": "research", "slug": "lab", "title": "检索演进"})["owner_id"]
    (app.root / "notes").mkdir()
    (app.root / "notes" / "strategy.md").write_text("低维护路线讨论\n", encoding="utf-8")
    registered = call(ctx, "sources.register", {"source_id": SOURCE, "path": "notes/strategy.md"})
    evidence = {"target_kind": "file", "target_id": SOURCE, "revision": None, "sha256": registered["sha256"],
                "locator": "", "relation": "input"}
    draft = narrative_draft(owner, evidence, "CLM-LAB-1", "外部知识库接入应保持低维护")
    draft["title"] = "外部知识库接入的低维护路线"
    receipt = call(ctx, "memory.commit", {"owner_id": owner, "expected_head": None,
                                          "operations": [{"op": "put_record", "client_key": "n", "draft": draft}]})
    return ctx, owner, receipt["record_results"][0]["record_id"]


def _visible(ctx, owner, record_id) -> tuple[bool, bool]:
    """(检索能找到, 关系图里有)"""
    found = call(ctx, "query.search", {"question": "外部知识库接入的低维护路线", "content_source": "all",
                                       "scope": {"owner_ids": [owner]}})
    graph = call(ctx, "relations.graph", {"node_types": ["record", "source"], "limit": 400})
    return (record_id in {i["record_id"] for i in found["items"]}, record_id in {n["id"] for n in graph["nodes"]})


def test_restricting_a_source_hides_dependents_from_search_and_graph(lab):
    ctx, owner, record_id = lab
    assert _visible(ctx, owner, record_id) == (True, True)

    changed = call(ctx, "sources.update", {"source_id": SOURCE, "sensitivity": "restricted"})
    assert changed["reindexed_owners"] == [owner]
    with pytest.raises(BurrowlError) as denied:
        call(ctx, "memory.inspect", {"owner_id": owner, "record_id": record_id})
    assert denied.value.code == "ACCESS_DENIED"
    assert _visible(ctx, owner, record_id) == (False, False)      # 读取被拒的记录，检索和关系图也不再显示

    call(ctx, "sources.update", {"source_id": SOURCE, "sensitivity": "internal"})
    assert _visible(ctx, owner, record_id) == (True, True)        # 恢复后重新出现

    call(ctx, "sources.update", {"source_id": SOURCE, "enabled": False})
    assert _visible(ctx, owner, record_id) == (False, False)      # 停用与 restricted 同样处理
