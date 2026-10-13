"""R12 P3：交换包导出（链接改写、文稿嵌入、图文件与图投影一致、增量）与 Markdown 库按交换格式发布。"""
import json

from burrowl.connectors.mdvault import MarkdownVault
from burrowl.core.config import BurrowlConfig, IndexConfig
from burrowl.services import exchange
from burrowl.services.hub import HubService
from burrowl.services.indexing import IndexService
from burrowl.services.memory.context import Storage
from burrowl.services.memory.service import MemoryService
from burrowl.services.review import ReviewService, claim_lookup_factory
from tests.service.helpers import HUMAN, head_of, narrative_draft, register_source, request

OWNER = "RES-FLOATING-POINT-SUMMATION"


def test_sample_export_matches_graph_and_is_incremental(sample_workspace):
    storage = Storage.open(sample_workspace)
    index = IndexService.open(storage, IndexConfig(), fake_vectors=True)
    first = exchange.export(storage, index, "sample")
    target = exchange.export_path(storage, "sample")
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "burrowl-exchange/2" and first["records"] == len(manifest["items"]) > 0

    # 图文件与图投影一致：本对象的全部边都在，节点与投影中本对象的节点一致
    nodes = [json.loads(line) for line in (target / "graph/nodes.jsonl").read_text(encoding="utf-8").splitlines()]
    edges = [json.loads(line) for line in (target / "graph/edges.jsonl").read_text(encoding="utf-8").splitlines()]
    projected = [e for e in index.db.graph_all_edges() if e["declared_owner"] == OWNER]
    own = {n["node_id"] for n in index.db.graph_nodes() if n["owner_id"] == OWNER and n["node_type"] != "owner"}
    referenced = {x for e in projected for x in (e["src"], e["dst"])}
    ids = {n["id"] for n in nodes}
    assert ids == own | referenced
    assert {e["edge_id"] for e in projected} <= {e["id"] for e in edges}   # 本对象声明的边一条不少
    inside = [e for e in index.db.graph_all_edges() if e["src"] in ids and e["dst"] in ids]
    assert len(edges) == len(inside)                                         # 两端都导出的边全部在图文件里

    # 记录页：YAML 头带 local_key；L1 块以 ## <block_id> 输出；文稿页嵌入技术块而不复制正文
    detail = next(i for i in manifest["items"] if i["record_id"] == "MEM-c4a61c00-1bf1-59b3-a65e-0fa4847db2e1")
    page = (target / detail["path"]).read_text(encoding="utf-8")
    assert page.startswith("---\nburrowl_id: ") and f'local_key: "{detail["local_key"]}"' in page
    assert "\n## kahan\n" in page and "<!-- burrowl:generated:start -->" in page
    documents = [i for i in manifest["items"] if i["path"].startswith("vault/documents/")]
    assert documents
    report = (target / documents[0]["path"]).read_text(encoding="utf-8")
    assert "![[records/" in report

    again = exchange.export(storage, index, "sample")
    assert again["written"] == 0 and again["deleted"] == 0 and again["unchanged"] == len(manifest["files"])


def _lab(tmp_path):
    storage = Storage.open(tmp_path)
    library = storage.owners.create("knowledge", "concepts", "概念库").owner_id
    owner = storage.owners.create("research", "lab", "补偿求和研究", {"short_name": "补偿"}).owner_id
    index = IndexService.open(storage, IndexConfig(), fake_vectors=True, concept_owner=lambda: library)
    memory = MemoryService(storage, index=index, claim_lookup_factory=claim_lookup_factory(storage),
                           concept_owner=lambda: library)
    evidence = register_source(storage, "SRC-LAB-1", "data/lab.txt", "1e16 + 1 = 1e16\n")
    concept = {"owner_id": library, "kind": "wiki_page", "title": "补偿求和", "body_markdown": "一种求和方法。",
               "record_reason": "建立概念", "sensitivity": "internal", "sources": [evidence],
               "payload": {"page_type": "concept", "aliases": [], "source_refs": [evidence]}}
    memory.commit(request(library, None, {"op": "put_record", "client_key": "c", "draft": concept}))
    first = narrative_draft(owner, evidence, "CLM-LAB-补偿抵消失效", "大数抵消时 [[补偿求和]] 会失效")
    first["title"] = "第三轮经过"
    second = narrative_draft(owner, evidence, "CLM-LAB-2")
    second["title"] = "复核经过"
    second["body_markdown"] = "沿用 [[@第三轮经过|那一轮]] 的输入，见 [[!补偿抵消失效]]，还提到 [[未建概念]]。"
    secret = narrative_draft(owner, evidence, "CLM-LAB-3")
    secret["title"] = "保密经过"
    secret["sensitivity"] = "confidential"
    memory.commit(request(owner, head_of(memory, owner), {"op": "put_record", "client_key": "a", "draft": first},
                          {"op": "put_record", "client_key": "b", "draft": second},
                          {"op": "put_record", "client_key": "s", "draft": secret}))
    return storage, memory, index, owner, library, evidence


def test_links_rewritten_to_vault_paths_and_sensitive_records_excluded(tmp_path):
    storage, _memory, index, _owner, library, _evidence = _lab(tmp_path)
    result = exchange.export(storage, index, "lab", concept_owner=library)
    target = exchange.export_path(storage, "lab")
    files = {p.relative_to(target).as_posix() for p in target.rglob("*.md")}
    assert "vault/concepts/补偿求和.md" in files
    assert "vault/records/补偿/复核经过.md" in files and "vault/records/补偿/保密经过.md" not in files  # D-25
    page = (target / "vault/records/补偿/复核经过.md").read_text(encoding="utf-8")
    assert "[[records/补偿/第三轮经过|那一轮]]" in page
    assert "[[records/补偿/第三轮经过#^c-" in page and "未建概念" in page and "[[未建概念]]" not in page
    concept = (target / "vault/concepts/补偿求和.md").read_text(encoding="utf-8")
    assert "**被引用**" in concept and "第三轮经过" in concept   # 自动生成的反链
    assert result["records"] == 3   # 概念页 + 两条经过


def test_mdvault_publishes_exchange_layout(tmp_path):
    storage, _memory, _index, owner, library, evidence = _lab(tmp_path)
    review = ReviewService(storage, MemoryService(storage, claim_lookup_factory=claim_lookup_factory(storage)))
    review.submit({"target_claim_id": "CLM-LAB-补偿抵消失效", "state": "accepted", "reason": "可复算", "actor": HUMAN,
                   "scope": "整数输入", "evidence_refs": [evidence]})
    cfg = BurrowlConfig.model_validate({
        "connectors": [{"id": "vault", "type": "mdvault", "options": {"path": str(tmp_path / "vault")},
                        "publish": {"levels": ["L2"], "include_documents": False, "review_filter": "reviewed_only"}}],
        "graph": {"concept_owner_id": library}})
    hub = HubService(storage, lambda: cfg, factory=lambda kind, cid, **s: MarkdownVault(cid, **s))
    plan = hub.plan("vault")
    assert sorted((a["op"], a["channel"]) for a in plan["actions"]) == [("create", "item"), ("create", "wiki")]
    hub.apply("vault", plan["plan_hash"])
    path = tmp_path / "vault" / "burrowl" / "records" / "补偿" / "第三轮经过.md"
    text = path.read_text(encoding="utf-8")
    assert "burrowl_local_key: rec:" in text and "local_key: " in text and "<!-- burrowl:generated:start -->" in text
    assert "[[wiki/concept__burrowl-c-" in text       # 概念页作为 Wiki 一起发布：链接指向库内的 Wiki 文件
    assert hub.plan("vault")["counts"]["unchanged"] == 2
    assert owner
