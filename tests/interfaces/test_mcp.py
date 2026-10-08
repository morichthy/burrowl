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


def test_tools_calls_and_errors(server):
    async def scenario():
        async with Client(server) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert {"memory_inspect", "query_search", "reading_start", "review_queue"} <= tools.keys()
            assert "reading_respond" not in tools and "config_secret_set" not in tools  # 用户动作不给 agent
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
            assert {"memory_contract", "sources_register", "maintenance_apply", "relations_decide"} <= tools
            contract = await client.call_tool("memory_contract", {"kind": "experience"})
            body = json.loads(text_of(contract))
            assert body["level"] == "L3" and body["example_operation"]["draft"]["kind"] == "experience"
    anyio.run(scenario)
