"""端到端测试用的工作台服务：复制浮点样例到临时目录，补一条未复核结论、一个待用户回复的阅读会话、
一个指向临时目录的 Markdown 库连接器，然后在固定端口与固定 token 下启动。

用法：uv run python frontend/e2e/server.py <端口>（由 playwright.config.ts 的 webServer 启动）
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from burrowl.app import App  # noqa: E402
from burrowl.interfaces.api import create_api  # noqa: E402
from burrowl.interfaces.ops.registry import Ctx, call  # noqa: E402
from burrowl.services.memory.service import MemoryService  # noqa: E402
from tests.service.helpers import narrative_draft, register_source, request  # noqa: E402

TOKEN = "e2e"


def seed(base: Path) -> dict:
    data, vault = base / "data", base / "vault"
    shutil.copytree(ROOT / "tests" / "fixtures" / "workspace", data)
    vault.mkdir()
    app = App.open(data, fake_vectors=True)
    agent = Ctx(app=app, actor={"kind": "ai", "id": "e2e-agent"}, channel="cli")
    owner = app.storage.owners.create("research", "order-demo", "求和顺序演示")
    evidence = register_source(app.storage, "SRC-ORDER-DEMO", "research/order-demo/data/result.txt", "升序误差 0.0\n")

    # R19 关系图 E2E 种子：准备两页真实概念，让新建结论产生可从概念端点点击的派生关系边。
    # 结论节点默认关闭；该边仍须借助 declared_record 展示完整的原结论。
    concepts = app.storage.owners.create("knowledge", "e2e-concepts", "关系图端到端概念库")
    _config, config_revision = app.config.store.read()
    app.config.update({"graph": {"concept_owner_id": concepts.owner_id}}, expected_revision=config_revision)
    concept_drafts = [
        {"owner_id": concepts.owner_id, "kind": "wiki_page", "title": title,
         "body_markdown": f"{title}的端到端测试定义。", "record_reason": "关系图端到端测试种子",
         "sensitivity": "internal", "sources": [evidence],
         "payload": {"page_type": "concept", "aliases": [], "source_refs": [evidence], "domains": ["端到端测试"]}}
        for title in ("E2E 主体", "E2E 客体")
    ]
    app.memory.commit(request(concepts.owner_id, None,
                              *({"op": "put_record", "client_key": f"concept-{i}", "draft": draft}
                                for i, draft in enumerate(concept_drafts))))
    narrative = narrative_draft(owner.owner_id, evidence)
    narrative["body_markdown"] = "测试关系：[[E2E 主体]] 在 [[E2E 客体]] 上表现出该结论。"
    narrative["payload"]["claims"][0]["relation"] = {"predicate": "fails_on", "participants": [
        {"role": "method", "value": "[[E2E 主体]]"}, {"role": "case", "value": "[[E2E 客体]]"}]}
    app.memory.commit(request(owner.owner_id, None, {"op": "put_record", "client_key": "n",
                                                     "draft": narrative | {"discovery": "workspace_summary"}}))
    # 引用编号验收使用独立对象，避免影响发布测试及用户数据；仍经正式服务保存规范记录。
    citation_owner = app.storage.owners.create("research", "citation-demo", "引用编号演示")
    citation_draft = {
        "owner_id": citation_owner.owner_id, "kind": "overview", "title": "名称较长的原始论文记录用于编号显示验收",
        "handle": "原始论文", "body_markdown": "原论文的引文 [1] 保留。", "sources": [],
        "record_reason": "端到端引用编号种子", "provenance_gap": "测试构造材料",
        "payload": {"question": "引用编号显示", "methods": [], "results": [], "current_stage": "测试",
                    "limitations": ["测试构造"], "open_questions": [], "claims": [], "experience_refs": [],
                    "process_refs": [], "technical_refs": []},
    }
    citation_base = app.memory.commit(request(citation_owner.owner_id, None,
        {"op": "put_record", "client_key": "original", "draft": citation_draft}))
    citation_reading = app.memory.commit(request(citation_owner.owner_id, citation_base["commit_id"],
        {"op": "put_record", "client_key": "reading", "draft": citation_draft | {
            "title": "引用编号阅读测试", "handle": "编号阅读",
            "body_markdown": "原文 [1]。依据：[[@原始论文|很长的显示名称]]。再次引用：[[@原始论文]]。",
            "sources": [citation_base["record_results"][0]["fixed_ref"]],
        }}))
    app.index.reconcile()
    session = call(agent, "reading.start", {"question": "Kahan 补偿求和的误差界是多少"})
    call(agent, "reading.ask_user", {"session_id": session["session_id"], "kind": "fulltext",
                                     "prompt": "是否允许全文补偿召回？"})
    current = call(agent, "config.get", {})
    call(agent, "config.update", {"expected_revision": current["revision"], "patch": {"connectors": [
        {"id": "vault", "type": "mdvault", "options": {"path": str(vault)},
         # 概念页只是关系图画布种子；该连接器的发布测试仅包含 order-demo 的一条记录。
         "wiki": {"mode": "off"},
         "publish": {"levels": ["L2", "L3", "L4"], "include_documents": False,
                     "owner_ids": [owner.owner_id]}}]}})
    # 故障种子只在临时库：模拟旧内容已保存，但升级清空派生索引后尚未 reconcile。
    # 使用没有 IndexSink 的记忆服务保存，避免更改真实数据库或伪造 HTTP 返回。
    missing = app.storage.owners.create("research", "venv-demo", "虚拟环境验证")
    MemoryService(app.storage).commit(request(missing.owner_id, None, {"op": "put_record", "client_key": "overview",
        "draft": {"owner_id": missing.owner_id, "kind": "overview", "title": "虚拟环境验证概览",
                  "body_markdown": "虚拟环境：隔离 Python 依赖，检查解释器与安装位置。",
                  "record_reason": "检索故障回归种子", "sensitivity": "internal", "discovery": "workspace_summary", "sources": [],
                  "provenance_gap": "端到端测试构造材料", "payload": {
                      "question": "如何验证虚拟环境", "methods": ["检查解释器"], "results": ["依赖已隔离"],
                      "current_stage": "完成", "limitations": ["测试构造"], "open_questions": [],
                      "claims": [], "experience_refs": [], "process_refs": [], "technical_refs": []}}}))
    return {"data": str(data), "vault": str(vault), "session_id": session["session_id"], "owner_id": owner.owner_id,
            "citation_owner": citation_owner.owner_id, "citation_record": citation_reading["record_results"][0]["record_id"]}


def main(port: int) -> None:
    base = Path(tempfile.mkdtemp(prefix="burrowl-e2e-"))
    info = seed(base)
    (ROOT / "frontend" / "e2e" / ".state.json").write_text(json.dumps(info), encoding="utf-8")
    app = App.open(info["data"], fake_vectors=True)
    api, _ = create_api(app, token=TOKEN, static_dir=ROOT / "frontend" / "dist", port=port,
                        actor={"kind": "human", "id": "e2e-user"})
    uvicorn.run(api, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(int(sys.argv[1]))
