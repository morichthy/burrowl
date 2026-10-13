"""MCP：工具目录（用户动作不暴露）、真实调用、错误映射、Skill 提示。"""
import json
from pathlib import Path

import anyio
import pytest
from mcp import Client

from burrowl.app import App
from burrowl.interfaces.mcp import build_server

OWNER = "RES-FLOATING-POINT-SUMMATION"
SKILLS = Path(__file__).resolve().parents[2] / "skills"


@pytest.fixture
def server(sample_workspace):
    app = App.open(sample_workspace, fake_vectors=True)
    app.index.reconcile()
    return build_server(app, skills_dir=SKILLS)


def text_of(result) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def test_stdio_launch(sample_workspace):
    """宿主真实的接入方式：以子进程拉起 `burrowl mcp`，经 stdio 握手并调用工具。"""
    import sys

    from mcp import StdioServerParameters

    burrowl = Path(sys.executable).with_name("burrowl.exe" if sys.platform == "win32" else "burrowl")
    params = StdioServerParameters(command=str(burrowl), args=["mcp", "--data-dir", str(sample_workspace)],
                                   env={"PYTHONUTF8": "1", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})

    async def scenario():
        async with Client(params) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert "memory_owners" in names
            result = await client.call_tool("memory_owners", {})
            assert OWNER in text_of(result)
    anyio.run(scenario)


def test_stdio_error_envelope_is_delivered_as_normal_content(sample_workspace):
    """真实子进程与 stdio 验证返回包；不依赖客户端读取日志，也不改变被拒操作的业务状态。"""
    import os
    import sys

    from mcp import StdioServerParameters

    app = App.open(sample_workspace, fake_vectors=True)
    app.config.update({"mcp": {"error_envelope": True}}, expected_revision=app.config.get()["revision"])
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "burrowl.interfaces.cli", "mcp", "--data-dir", str(sample_workspace)],
        env={"PYTHONUTF8": "1", "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")})

    async def scenario():
        async with Client(params) as client:
            result = await client.call_tool("memory_inspect", {"ownerid": "wrong"})
            assert not result.is_error
            body = json.loads(text_of(result))
            assert body["ok"] is False and body["error"]["code"] == "INVALID_ARGUMENT"
            assert body["error"]["errors"]
    anyio.run(scenario)


def test_tools_calls_and_errors(server):
    async def scenario():
        async with Client(server) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert {"memory_inspect", "query_search", "reading_start", "review_queue"} <= tools.keys()
            assert "reading_respond" not in tools and "config_secret_set" not in tools  # 用户动作不给 agent
            # R15 D-03：AI 只能读笔记和回写；交给 AI、执行方案、清除等都是用户专属
            assert {"relations_notes", "relations_note_reply"} <= tools.keys()
            assert not {"relations_note_apply", "relations_note_resolve", "relations_note_clear"} & tools.keys()
            assert tools["memory_inspect"].annotations.read_only_hint is True
            assert "owner_id" in tools["memory_inspect"].input_schema["properties"]
            inspected = await client.call_tool("memory_inspect", {"owner_id": OWNER})
            assert not inspected.is_error and json.loads(text_of(inspected))["head"]["generation"] == 19
            started = await client.call_tool("reading_start", {"question": "浮点求和顺序误差"})
            session_id = json.loads(text_of(started))["session_id"]
            denied = await client.call_tool("reading_recall", {"session_id": session_id, "mode": "fulltext",
                                                                "approval_id": "AP-x"})
            assert denied.is_error and "APPROVAL_REQUIRED" in text_of(denied)
            bad = await client.call_tool("memory_inspect", {"owner_id": ["不是字符串"]})
            assert bad.is_error
    anyio.run(scenario)


def test_prompts_carry_rules_and_contract_tool(server):
    """只经 MCP 接入的宿主也能拿到公共规则、记录标准，并能查询记录契约。"""
    async def scenario():
        async with Client(server) as client:
            prompts = {p.name for p in (await client.list_prompts()).prompts}
            assert {"rules", "recording", "burrowl-work", "burrowl-read", "burrowl-consolidate", "burrowl-dev-check"} <= prompts
            work = await client.get_prompt("burrowl-work")
            text = "".join(getattr(m.content, "text", "") for m in work.messages)
            assert "以下为 RULES.md 全文" in text and "谁可以确认" in text
            tools = {t.name for t in (await client.list_tools()).tools}
            assert {"memory_contract", "sources_register", "maintenance_review", "maintenance_plans", "relations_decide"} <= tools
            assert "maintenance_apply" not in tools  # R16 D-03：维护计划只能由用户在工作台执行
            contract = await client.call_tool("memory_contract", {"kind": "experience"})
            body = json.loads(text_of(contract))
            assert body["level"] == "L3" and body["example_operation"]["draft"]["kind"] == "experience"
    anyio.run(scenario)


def test_quick_knowledge_lookup_is_visible_through_mcp(server):
    """主题知识简查走真实 MCP 会话，工作台同源列表和详情可见进度与笔记。

    先确认低层查询/对象续接不会伪造阅读记录，再验证 quick 的完整交接链。
    所有状态均经工具读回，避免只验证说明文本而漏掉实际工具调用失败。
    """
    async def scenario():
        async with Client(server) as client:
            async def invoke(name, **kwargs):
                result = await client.call_tool(name, kwargs)
                assert not result.is_error, text_of(result)
                # MCP 把列表结果拆为多个内容块；空列表没有文本块。
                if name == "reading_list":
                    return [json.loads(block.text) for block in result.content if getattr(block, "text", "")]
                return json.loads(text_of(result))

            await invoke("query_search", question="浮点求和顺序误差")
            await invoke("memory_resume", owner_id=OWNER)
            assert await invoke("reading_list") == []

            started = await invoke("reading_start", question="查询 浮点求和 相关的知识", strategy="quick",
                                   scope_owner_ids=[OWNER])
            sid = started["session_id"]
            assert sid in {s["session_id"] for s in await invoke("reading_list")}
            packet = next(p for p in started["batch"] if p["owner_id"] == OWNER)
            window = packet["windows"][0]
            await invoke("reading_assess", session_id=sid, judgments=[{
                "owner_id": OWNER, "verdict": "relevant", "reason": "窗口包含浮点求和的已有知识",
                "digest": packet["digest"], "accepted_windows": [window["entry_id"]],
            }])
            note = f"非全文覆盖：采纳窗口提供浮点求和依据（{window['record_id']}）。"
            await invoke("reading_note", session_id=sid, owner_id=OWNER, note=note,
                         sources=[window["record_id"]])
            visible = await invoke("reading_view", session_id=sid)
            assert visible["strategy"] == "quick"
            assert visible["owners"][OWNER]["note"]["text"] == note
            done = await invoke("reading_finish", session_id=sid, outcome="已完成主题知识简查")
            assert note in done["handoff"]["markdown"]
            assert any(g["code"] == "QUICK_MODE" for g in done["handoff"]["gaps"])
            listed = await invoke("reading_list")
            assert sid in {s["session_id"] for s in listed}
            finished = await invoke("reading_view", session_id=sid)
            assert finished["status"] == "finished"
            assert finished["owners"][OWNER]["note"]["text"] == note
    anyio.run(scenario)
