"""WeKnora 连接器：用 httpx.MockTransport 模拟 v0.8 REST；再接入同步引擎走一遍完整链路。

真实服务的验收测试见 test_weknora_live（需要 WEKNORA_URL 与 WEKNORA_API_KEY，标记 live_hub）。
"""
import json
import os
import re

import httpx
import pytest
from burrowl_connector_weknora import WeKnoraConnector

from burrowl.connectors import registry
from burrowl.connectors.spi import ConnectorError, ExternalRef, PublishItem, WikiPage


class FakeWeKnora:
    """最小的 WeKnora v0.8 行为模型：信封、异步解析、custom_metadata、分块检索、Wiki、last_edit_source。"""

    def __init__(self):
        self.kbs: dict[str, dict] = {}
        self.knowledge: dict[str, dict] = {}
        self.wiki: dict[tuple[str, str], dict] = {}
        self.seq = 0
        self.requests: list[tuple[str, str]] = []

    def _id(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}{self.seq}"

    @staticmethod
    def ok(data=None, status=200):
        return httpx.Response(status, json={"success": True, "data": data})

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-API-Key") == "sk-test"
        path = request.url.path.removeprefix("/api/v1")
        method = request.method
        body = json.loads(request.content) if request.content else {}
        self.requests.append((method, path))
        if path == "/knowledge-bases" and method == "GET":
            return self.ok(list(self.kbs.values()))
        if path == "/knowledge-bases" and method == "POST":
            kb = {"id": self._id("kb"), "name": body["name"], "indexing_strategy": {}}
            self.kbs[kb["id"]] = kb
            return self.ok(kb, 201)
        if m := re.fullmatch(r"/knowledge-bases/([^/]+)", path):
            self.kbs[m[1]].update(body)
            return self.ok(self.kbs[m[1]])
        if m := re.fullmatch(r"/knowledge-bases/([^/]+)/knowledge/manual", path):
            k = {"id": self._id("k"), "knowledge_base_id": m[1], "title": body["title"], "content": body["content"],
                 "parse_status": "processing", "custom_metadata": {}}
            self.knowledge[k["id"]] = k
            return self.ok(k, 201)
        if m := re.fullmatch(r"/knowledge-bases/([^/]+)/knowledge", path):
            rows = [k for k in self.knowledge.values() if k["knowledge_base_id"] == m[1]]
            page, size = int(request.url.params["page"]), int(request.url.params["page_size"])
            return self.ok(rows[(page - 1) * size:page * size])
        if m := re.fullmatch(r"/knowledge/manual/([^/]+)", path):
            if m[1] not in self.knowledge:
                return httpx.Response(404, json={"success": False, "error": {"message": "not found"}})
            self.knowledge[m[1]].update(title=body["title"], content=body["content"], parse_status="processing")
            return self.ok(self.knowledge[m[1]])
        if path == "/knowledge/batch":
            ids = request.url.params["ids"].split(",")
            return self.ok([self.knowledge[i] for i in ids if i in self.knowledge])
        if m := re.fullmatch(r"/knowledge/([^/]+)", path):
            if m[1] not in self.knowledge:
                return httpx.Response(404, json={"success": False, "error": {"message": "not found"}})
            if method == "DELETE":
                del self.knowledge[m[1]]
                return self.ok()
            if method == "PUT":
                assert len(body["custom_metadata"]) <= 20
                self.knowledge[m[1]].update(body)
            return self.ok(self.knowledge[m[1]])
        if path == "/knowledge-search":
            hits = []
            for k in self.knowledge.values():
                if k["knowledge_base_id"] in body["knowledge_base_ids"] and k["parse_status"] == "completed" \
                        and body["query"] in k["content"]:
                    hits += [{"id": f"{k['id']}-c{i}", "knowledge_id": k["id"], "knowledge_title": k["title"],
                              "content": k["content"][:50], "score": 0.9 - i / 10} for i in range(2)]
            return self.ok(hits)
        if m := re.fullmatch(r"/knowledgebase/([^/]+)/wiki/pages(?:/(.+))?", path):
            kb, slug = m[1], m[2]
            if slug is None and method == "GET":
                return self.ok({"pages": [p for (k, _), p in self.wiki.items() if k == kb], "total": 0})
            if slug is None and method == "POST":
                page = {**body, "version": 1, "last_edit_source": "agent"}
                self.wiki[(kb, body["slug"])] = page
                return self.ok(page, 201)
            if (kb, slug) not in self.wiki:
                return httpx.Response(404, json={"success": False, "error": {"message": "page not found"}})
            if method == "GET":
                return self.ok(self.wiki[(kb, slug)])
            if method == "PUT":
                page = self.wiki[(kb, slug)]
                page.update(body, version=page["version"] + 1, last_edit_source="agent")
                return self.ok(page)
            if method == "DELETE":
                del self.wiki[(kb, slug)]
                return self.ok()
        return httpx.Response(500, json={"success": False, "error": {"message": f"unexpected {method} {path}"}})

    def finish(self):
        for k in self.knowledge.values():
            k["parse_status"] = "completed"


@pytest.fixture
def server():
    return FakeWeKnora()


def _connector(server: FakeWeKnora) -> WeKnoraConnector:
    return WeKnoraConnector("wk", base_url="http://weknora.test", api_key="sk-test",
                            transport=httpx.MockTransport(server.handler))


def _item(key="rec:RES-A/MEM-1", text="Kahan 补偿求和的误差界") -> PublishItem:
    return PublishItem(local_key=key, title="补偿求和", markdown=text, content_hash="h1",
                       fixed_ref={"target_id": "MEM-1"}, ref_uri="burrowl://RES-A/MEM-1@1#abc",
                       metadata={"burrowl_review": "accepted"})


def test_entry_point_registered():
    assert "weknora" in registry.factories()


def test_ingest_status_list_search_delete(server):
    conn = _connector(server)
    kb = conn.ensure_collection("burrowl")
    assert conn.ensure_collection("burrowl") == kb                     # 已存在则复用
    ref = conn.upsert(kb, _item(), None)
    assert ref.state == "pending"
    assert conn.status([ref])[0].state == "processing"
    server.finish()
    assert conn.status([ref, ExternalRef("gone")])[1].state == "missing"
    listed = conn.list_items(kb)
    assert listed[0].local_key == "rec:RES-A/MEM-1" and listed[0].content_hash == "h1"
    hits = conn.search([kb], "Kahan", 10)
    assert len(hits) == 1 and hits[0].external_id == ref.external_id  # 分块结果按条目去重
    conn.upsert(kb, _item(text="更新后的 Kahan 正文"), ExternalRef(ref.external_id))
    assert server.knowledge[ref.external_id]["content"] == "更新后的 Kahan 正文"
    conn.delete(kb, ref)
    conn.delete(kb, ref)                                               # 重复删除不报错
    with pytest.raises(ConnectorError) as caught:
        conn.upsert(kb, _item(), ExternalRef(ref.external_id))
    assert caught.value.status == 404


def test_wiki_pages_and_configure(server):
    conn = _connector(server)
    kb = conn.ensure_collection("burrowl")
    written = conn.wiki_write(kb, WikiPage(slug="concept/burrowl-kahan", title="Kahan", markdown="正文", page_type="concept"))
    assert written.version == 1 and written.edited_by == "agent"
    assert conn.wiki_write(kb, WikiPage(slug="concept/burrowl-kahan", title="Kahan", markdown="正文2")).version == 2
    assert [p.slug for p in conn.wiki_list(kb)] == ["concept/burrowl-kahan"]
    assert conn.wiki_read(kb, "concept/none") is None
    assert conn.wiki_configure(kb, auto=True, synthesis_model_id=None)["auto"] is False
    conn.wiki_configure(kb, auto=True, synthesis_model_id="model-1")
    assert server.kbs[kb.id]["indexing_strategy"] == {"wiki_enabled": True}
    assert server.kbs[kb.id]["wiki_config"] == {"synthesis_model_id": "model-1"}


def test_errors_are_classified():
    def handler(request):
        return httpx.Response(503, text="busy")
    conn = WeKnoraConnector("wk", base_url="http://weknora.test", transport=httpx.MockTransport(handler))
    with pytest.raises(ConnectorError) as caught:
        conn.health()
    assert caught.value.status == 503 and caught.value.retryable
    with pytest.raises(ConnectorError):
        WeKnoraConnector("wk", base_url=None)


def test_sync_engine_end_to_end(server, tmp_path):
    """发布已复核 → 异步解析 → 检索命中回到固定引用 → 映射丢失后认领 → 撤回后删除 → 重跑无变化。"""
    from burrowl.core.config import BurrowlConfig
    from burrowl.services.hub import HubService
    from tests.service.helpers import HUMAN, narrative_draft, register_source, request, services

    (tmp_path / "data").mkdir()
    storage, memory, review = services(tmp_path / "data")
    owner_id = storage.owners.create("research", "order", "顺序").owner_id
    evidence = register_source(storage, "SRC-1", "research/order/data/r.txt", "升序误差 0.0\n")
    rid = memory.commit(request(owner_id, None, {"op": "put_record", "client_key": "n",
                                                 "draft": narrative_draft(owner_id, evidence)}))["record_results"][0]
    review.submit({"target_claim_id": "CLM-ORDER-1", "state": "accepted", "reason": "可复算", "actor": HUMAN,
                   "scope": "整数输入", "evidence_refs": [evidence]})
    cfg = BurrowlConfig.model_validate({"connectors": [{"id": "wk", "type": "weknora", "base_url": "http://weknora.test",
                                                    "publish": {"levels": ["L2"], "include_documents": False}}]})
    conn = _connector(server)
    hub = HubService(storage, lambda: cfg, factory=lambda *a, **k: conn)
    assert hub.apply("wk", hub.plan("wk")["plan_hash"])["summary"] == {"created": 1}
    assert hub.status("wk")["counts"] == {"processing": 1}
    server.finish()
    assert hub.status("wk")["counts"] == {"completed": 1}
    hit = hub.search("排序后求和误差更小")["hits"][0]
    assert hit["fixed_ref"]["target_id"] == rid["record_id"] and hit["review"] == "accepted"
    for key in list(hub.state.mappings("wk")):
        hub.state.remove("wk", key)
    hub.apply("wk", hub.plan("wk")["plan_hash"])
    assert len(server.knowledge) == 1                                  # 认领，未重复创建
    server.finish()
    hub.status("wk")
    assert hub.plan("wk")["actions"] == []
    review.submit({"target_claim_id": "CLM-ORDER-1", "state": "retracted", "reason": "错", "actor": HUMAN})
    assert hub.apply("wk", hub.plan("wk")["plan_hash"])["summary"] == {"deleted": 1}
    assert server.knowledge == {} and hub.plan("wk")["actions"] == []


@pytest.mark.live_hub
@pytest.mark.skipif(not os.environ.get("WEKNORA_URL"), reason="需要 WEKNORA_URL / WEKNORA_API_KEY")
def test_weknora_live():
    conn = WeKnoraConnector("live", base_url=os.environ["WEKNORA_URL"], api_key=os.environ.get("WEKNORA_API_KEY"))
    assert conn.health()["ok"]
    kb = conn.ensure_collection("burrowl-live-test")
    ref = conn.upsert(kb, _item(key="rec:LIVE/MEM-1"), None)
    try:
        assert conn.status([ref])[0].state in {"pending", "processing", "completed"}
        assert any(i.local_key == "rec:LIVE/MEM-1" for i in conn.list_items(kb))
    finally:
        conn.delete(kb, ref)
