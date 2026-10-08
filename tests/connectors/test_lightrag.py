"""R12 T-P4.1：LightRAG 连接器（图模式）——发布 → finalize 构建 → 检索 → 回到本地固定版本；
只有新增时追加、有修改时整体重建并删除旧构建；嵌入缓存让未变内容不重算；查询不调用 LLM。"""
import importlib.util
import json
from pathlib import Path

import pytest
from burrowl_connector_lightrag import LightRagConnector
from burrowl_connector_lightrag.connector import default_rag_factory, split_chunks

from burrowl.core.config import BurrowlConfig, IndexConfig
from burrowl.index.embed import FakeEmbedder
from burrowl.services.hub import HubService
from burrowl.services.indexing import IndexService
from burrowl.services.memory.context import Storage
from burrowl.services.memory.service import MemoryService
from burrowl.services.review import claim_lookup_factory
from tests.service.helpers import head_of, narrative_draft, register_source, request


class FakeRag:
    """最小的 LightRAG 替身：把导入的图存成 JSON，按字面匹配返回 chunk（含我们写入的 file_path）。"""

    def __init__(self, working_dir: str, embed, dim: int):
        self.path = Path(working_dir) / "fake-kg.json"
        self.embed = embed

    async def initialize_storages(self):
        self.kg = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else \
            {"chunks": [], "entities": [], "relationships": []}

    async def ainsert_custom_kg(self, kg, full_doc_id=None):
        await self.embed([c["content"] for c in kg["chunks"]] + [e["description"] for e in kg["entities"]])
        for key in self.kg:
            self.kg[key].extend(kg.get(key, []))

    async def aquery_data(self, query, param):
        get = param.get if isinstance(param, dict) else lambda k: getattr(param, k)
        assert get("only_need_context") and get("ll_keywords")       # 查询必须自带关键词：不调用 LLM
        chunks = [c for c in self.kg["chunks"] if any(t in c["content"] for t in get("ll_keywords"))]
        return {"status": "success", "data": {"chunks": chunks, "entities": self.kg["entities"]}}

    async def finalize_storages(self):
        self.path.write_text(json.dumps(self.kg, ensure_ascii=False), encoding="utf-8")


def _setup(tmp_path, rag_factory):
    (tmp_path / "data").mkdir()
    storage = Storage.open(tmp_path / "data")
    library = storage.owners.create("knowledge", "concepts", "概念库").owner_id
    owner = storage.owners.create("research", "lab", "补偿求和研究").owner_id
    index = IndexService.open(storage, IndexConfig(), fake_vectors=True, concept_owner=lambda: library)
    memory = MemoryService(storage, index=index, claim_lookup_factory=claim_lookup_factory(storage),
                           concept_owner=lambda: library)
    evidence = register_source(storage, "SRC-LAB-1", "data/lab.txt", "1e16 + 1 = 1e16\n")
    concept = {"owner_id": library, "kind": "wiki_page", "title": "补偿求和", "body_markdown": "一种求和方法。",
               "record_reason": "建立概念", "sensitivity": "internal", "sources": [evidence],
               "payload": {"page_type": "concept", "aliases": [], "source_refs": [evidence]}}
    memory.commit(request(library, None, {"op": "put_record", "client_key": "c", "draft": concept}))
    draft = narrative_draft(owner, evidence, "CLM-LAB-补偿抵消失效", "大数抵消时补偿项会被吞没")
    draft["title"] = "第三轮经过"
    draft["body_markdown"] = "在大数抵消输入下，[[补偿求和]] 仍可能丢失小量。"
    first = memory.commit(request(owner, None, {"op": "put_record", "client_key": "a", "draft": draft}))
    embedder = FakeEmbedder()
    connector = {"id": "lr", "type": "lightrag", "options": {"path": str(tmp_path / "lightrag")},
                 "publish": {"levels": ["L2"], "include_documents": False, "review_filter": "all"}}
    cfg = BurrowlConfig.model_validate({"connectors": [connector], "graph": {"concept_owner_id": library}})
    conns: dict = {}

    def factory(kind, cid, **settings):
        conns.setdefault(cid, LightRagConnector(cid, **settings, embedder=embedder, rag_factory=rag_factory))
        return conns[cid]
    hub = HubService(storage, lambda: cfg, factory=factory,
                     graph_source=lambda: (index.ensure_graph(), index.db.graph_nodes(), index.db.graph_all_edges())[1:])
    return storage, memory, hub, owner, draft, first["record_results"][0], conns, evidence


def _publish(hub):
    return hub.apply("lr", hub.plan("lr")["plan_hash"])


def test_split_chunks_drops_generated_block():
    text = "# 标题\n正文\n## a\n块 a\n<!-- burrowl:generated:start -->\n反链\n<!-- burrowl:generated:end -->\n## b\n块 b"
    assert split_chunks(text) == ["# 标题\n正文", "## a\n块 a", "## b\n块 b"]


def test_publish_rebuild_append_and_search_maps_back(tmp_path):
    storage, memory, hub, owner, draft, first, conns, evidence = _setup(tmp_path, FakeRag)
    out = _publish(hub)
    assert out["finalize"]["action"] == "rebuilt" and out["finalize"]["items"] == 1
    connector = conns["lr"]
    kg_items = connector.list_items(connector.ensure_collection("burrowl"))
    assert [i.local_key for i in kg_items] == [f"rec:{owner}/{first['record_id']}"]
    # 整理图随条目导入：记录、结论、概念三类实体与 mentions 关系
    build = next((tmp_path / "lightrag" / "burrowl" / "builds").iterdir())
    kg = json.loads((build / "fake-kg.json").read_text(encoding="utf-8"))
    assert {e["entity_type"] for e in kg["entities"]} == {"record", "claim", "concept"}
    assert any(r["keywords"] == "mentions" and r["tgt_id"] == "补偿求和" for r in kg["relationships"])
    from burrowl_connector_lightrag.connector import file_token
    assert all(c["file_path"] == file_token(kg_items[0].local_key) for c in kg["chunks"])

    found = hub.search("补偿求和", connector_ids=["lr"])
    assert found["hits"][0]["fixed_ref"]["target_id"] == first["record_id"]   # 回到本地固定版本

    assert _publish(hub)["finalize"]["action"] == "unchanged"
    # 只新增一条（且没有新的同名实体）→ 追加到当前构建
    second = narrative_draft(owner, evidence, "CLM-LAB-2", "排序后误差更小")
    second["title"] = "排序对照经过"
    memory.commit(request(owner, head_of(memory, owner), {"op": "put_record", "client_key": "b", "draft": second}))
    appended = _publish(hub)["finalize"]
    assert appended["action"] == "appended" and appended["build"] == build.name

    # 修改一条已发布的记录 → 整体重建到新目录，旧构建被删除；未变内容的嵌入来自缓存
    record = memory.inspect(owner, record_id=first["record_id"])["record"]
    changed = dict(draft, body_markdown=draft["body_markdown"] + "补充一句。")
    memory.commit(request(owner, head_of(memory, owner), {"op": "put_record", "record_id": record["record_id"],
                                                          "expected_revision": record["revision"], "draft": changed}))
    rebuilt = _publish(hub)["finalize"]
    assert rebuilt["action"] == "rebuilt" and rebuilt["build"] != build.name and not build.exists()
    assert rebuilt["embeddings"]["cached"] > 0 and rebuilt["embeddings"]["computed"] < rebuilt["embeddings"]["cached"]
    assert storage


@pytest.mark.skipif(importlib.util.find_spec("lightrag") is None, reason="未安装 lightrag-hku（[runtime] 可选依赖）")
def test_real_lightrag_graph_mode_without_llm(tmp_path):
    """真实 LightRAG 1.5.7：导入、查询都不调用 LLM（LLM 函数一被调用就报错）；不联网（不用 tiktoken）。"""
    _storage, _memory, hub, _owner, _draft, first, _conns, _evidence = _setup(tmp_path, default_rag_factory)
    out = _publish(hub)
    assert out["finalize"]["ok"] and out["finalize"]["action"] == "rebuilt", out
    found = hub.search("补偿求和 大数抵消", connector_ids=["lr"])
    assert not found["errors"], found
    assert found["hits"] and found["hits"][0]["fixed_ref"]["target_id"] == first["record_id"]
