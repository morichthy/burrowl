"""R20：工具参数的说明、形状与报错——参数只在 registry.call 校验，错误一次列全；意外异常不带堆栈返回并写日志。"""
import json
import re
from pathlib import Path

import anyio
import pytest
from mcp import Client

from burrowl.app import App
from burrowl.core.errors import BurrowlError
from burrowl.core.jsonschema import check_schema, validate
from burrowl.interfaces import mcp as mcp_module
from burrowl.interfaces.ops import errorlog
from burrowl.interfaces.ops.registry import Ctx, call, load

SKILLS = Path(__file__).resolve().parents[2] / "skills"
OWNER = "RES-FLOATING-POINT-SUMMATION"
AI = {"kind": "ai", "id": "test-assistant"}
SELF_EXPLANATORY = {"limit", "offset"}  # 名称已说清用途的参数可以不写说明
FREE_FORM = {"config.update.patch", "memory.summary_save.draft", "memory.summary_save.basis_heads"}


def _skill_ops() -> list:
    text = "".join(p.read_text(encoding="utf-8") for p in SKILLS.rglob("*.md"))
    return [s for s in load().values() if s.effect != "user" and re.search(rf"\b{s.tool_name}\b", text)]


def _descriptions(node):
    if isinstance(node, dict):
        if isinstance(node.get("description"), str):
            yield node["description"]
        for value in node.values():
            yield from _descriptions(value)
    elif isinstance(node, list):
        for value in node:
            yield from _descriptions(value)


def test_skill_tools_describe_every_parameter():
    """D-04：Skill 提到的工具，每个参数都有说明（limit/offset 除外）。"""
    missing = [f"{s.name}.{k}" for s in _skill_ops() for k, v in s.schema().get("properties", {}).items()
               if "description" not in v and k not in SELF_EXPLANATORY]
    assert not missing


def test_structured_parameters_declare_fields():
    """D-03：AI 编写的对象/列表参数都声明了字段（自由格式的白名单除外）。"""
    loose = []
    for spec in _skill_ops():
        for key, prop in spec.schema().get("properties", {}).items():
            if key in spec.shapes or f"{spec.name}.{key}" in FREE_FORM:
                continue
            for option in prop.get("anyOf") or [prop]:
                items = option.get("items") or {}
                if (option.get("type") == "object" and not option.get("properties")) or \
                        (items.get("type") == "object" and not items.get("properties")):
                    loose.append(f"{spec.name}.{key}")
    assert not loose


def test_descriptions_use_mcp_tool_names():
    """D-04：工具说明里提到其他工具一律用 MCP 名（下划线），AI 照着调用不会找不到。"""
    names = sorted({s.name for s in load().values()}, key=len, reverse=True)
    dotted = re.compile(r"(?<![\w.])(" + "|".join(re.escape(n) for n in names) + r")(?!\w)")
    found = {}
    for spec in load().values():
        if spec.effect == "user":
            continue
        for text in [spec.summary, *_descriptions(spec.schema())]:
            if hits := dotted.findall(text):
                found[spec.tool_name] = hits
    assert not found


def test_shapes_are_valid_and_examples_pass():
    """D-09：形状本身合法，附带的示例能通过同一份校验。"""
    for spec in load().values():
        for name, shape in spec.shapes.items():
            check_schema(shape.schema, shape.defs)
            prop = spec.schema()["properties"][name]
            published = (prop.get("anyOf") or [prop])[0]
            for example in published.get("examples", []):
                assert not validate(example, shape.schema, shape.defs), (spec.name, name)


@pytest.fixture
def app(sample_workspace):
    return App.open(sample_workspace, fake_vectors=True)


def _ctx(app) -> Ctx:
    return Ctx(app=app, actor=AI, channel="cli")


def test_all_argument_errors_reported_at_once(app):
    """D-05：顶层参数与结构化参数的问题一次列全，路径从参数根算起，枚举错误带允许值。"""
    with pytest.raises(BurrowlError) as caught:
        call(_ctx(app), "reading.assess", {"judgments": [{"owner_id": OWNER, "verdict": "maybe", "digest": "d",
                                                           "reasn": "拼错的字段"}]})
    errors = {e["path"]: e for e in caught.value.errors}
    assert {"/session_id", "/judgments/0/verdict", "/judgments/0/reason", "/judgments/0/reasn"} <= errors.keys()
    assert "relevant" in errors["/judgments/0/verdict"]["expected"]
    assert "reason" in errors["/judgments/0/reasn"]["expected"]
    assert caught.value.code == "INVALID_ARGUMENT"


def test_unknown_top_level_parameter_lists_allowed(app):
    with pytest.raises(BurrowlError) as caught:
        call(_ctx(app), "memory.inspect", {"ownerid": OWNER})
    errors = {e["path"]: e for e in caught.value.errors}
    assert "owner_id" in errors["/ownerid"]["expected"] and "/owner_id" in errors


def test_internal_error_hides_traceback_and_logs_it(app, monkeypatch):
    """D-06：意外异常返回 INTERNAL_ERROR（类型、摘要、incident_id），堆栈只在日志里。"""
    spec = load()["memory.owners"]

    def boom(ctx, **kwargs):
        raise RuntimeError("磁盘句柄已关闭")
    monkeypatch.setattr(spec, "fn", boom)
    with pytest.raises(BurrowlError) as caught:
        call(_ctx(app), "memory.owners", {})
    details = caught.value.details
    assert caught.value.code == "INTERNAL_ERROR" and not caught.value.retryable
    assert details["exception"] == "RuntimeError" and details["summary"] == "磁盘句柄已关闭"
    log = (app.root / ".burrowl" / "logs" / "errors.jsonl").read_text(encoding="utf-8")
    event = next(json.loads(line) for line in log.splitlines() if details["incident_id"] in line)
    assert "Traceback" in event["traceback"] and "boom" in event["traceback"]


def test_rejected_calls_are_counted_without_arguments(app):
    """D-10：被拒绝的调用只记操作名、错误码和路径，按工具汇总。"""
    for _ in range(2):
        with pytest.raises(BurrowlError):
            call(_ctx(app), "reading.assess", {"session_id": "RS-x", "judgments": [{"owner_id": "secret-text"}]})
    summary = call(_ctx(app), "system.tool_errors", {})
    row = next(r for r in summary["by_op"] if r["op"] == "reading.assess")
    assert row["input"] == 2 and "/judgments/0/verdict" in row["top_paths"]
    log = (app.root / ".burrowl" / "logs" / "errors.jsonl").read_text(encoding="utf-8")
    assert "secret-text" not in log
    assert errorlog.summary(app.root)["events"] >= 2


def _problem(result) -> dict:
    text = "".join(getattr(block, "text", "") for block in result.content)
    return json.loads(text[text.index("{"):])


def test_mcp_passes_raw_arguments_to_registry(app):
    """D-08：MCP 层不预校验——类型错误也返回统一结构（而不是 pydantic 原文），JSON 字符串参数照常解析。"""
    server = mcp_module.build_server(app, skills_dir=SKILLS)

    async def scenario():
        async with Client(server) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            operations = tools["memory_commit"].input_schema["properties"]["operations"]
            assert operations["items"]["properties"]["op"]["enum"] and operations["examples"]
            bad = await client.call_tool("memory_commit", {"owner_id": OWNER, "expected_head": None,
                                                            "operations": [1]})
            assert bad.is_error
            problem = _problem(bad)
            assert problem["code"] == "INVALID_ARGUMENT" and problem["errors"][0]["path"] == "/operations/0"
            head = app.memory.inspect(OWNER)["head"]["commit_id"]
            operation = json.loads((Path(__file__).resolve().parents[2] / "packages/burrowl/src/burrowl/core/schema"
                                    / "examples/question.json").read_text(encoding="utf-8"))["operation"]
            ok = await client.call_tool("memory_commit", {"owner_id": OWNER, "expected_head": head, "dry_run": True,
                                                           "operations": json.dumps([operation], ensure_ascii=False)})
            assert not ok.is_error, ok
    anyio.run(scenario)


def test_write_results_cap_warnings(app, monkeypatch):
    """D-06：写操作结果的 warnings 最多 10 条，其余计数；每条带可执行的 action。"""
    spec = load()["runs.update"]
    many = [{"code": "X", "message": f"第 {i} 条", "action": "处理它"} for i in range(15)]
    monkeypatch.setattr(spec, "fn", lambda ctx, **kwargs: {"status": "updated", "warnings": many})
    result = call(_ctx(app), "runs.update", {"run_id": "RUN-x", "fields": {"title": "t"}})
    assert len(result["warnings"]) == 10 and result["warnings_truncated"] == 5


def test_mcp_time_fields_null_ok_and_bad_format_explained_in_message(app):
    """acquired_at 等时间字段：null 可以提交；带时区的写法保存为 UTC（Z）；只写日期时经 MCP 返回的顶层 message
    就指出字段与正确写法，契约也写明格式（不必去翻错误日志）。"""
    server = mcp_module.build_server(app, skills_dir=SKILLS)
    operation = json.loads((Path(__file__).resolve().parents[2] / "packages/burrowl/src/burrowl/core/schema"
                            / "examples/source.json").read_text(encoding="utf-8"))["operation"]
    owner = operation["draft"].get("owner_id") or OWNER

    async def scenario():
        async with Client(server) as client:
            head = app.memory.inspect(owner)["head"]["commit_id"]
            assert operation["draft"]["payload"]["acquired_at"] is None
            ok = await client.call_tool("memory_commit", {"owner_id": owner, "expected_head": head, "dry_run": True,
                                                           "operations": [operation]})
            assert not ok.is_error, ok
            dated = json.loads(json.dumps(operation))
            dated["draft"]["payload"]["acquired_at"] = "2026-10-13"
            bad = await client.call_tool("memory_commit", {"owner_id": owner, "expected_head": head, "dry_run": True,
                                                            "operations": [dated]})
            assert bad.is_error
            problem = _problem(bad)
            path = "/operations/0/draft/payload/acquired_at"
            assert path in problem["message"] and "带时区" in problem["message"]
            assert problem["errors"][0]["path"] == path and "2026-10-13T16:00:00+08:00" in problem["errors"][0]["expected"]
            contract = json.loads("".join(b.text for b in (await client.call_tool(
                "memory_contract", {"kind": "source"})).content))
            assert "带时区" in contract["payload_fields"]["acquired_at"]
            zoned = json.loads(json.dumps(operation))
            zoned["draft"]["payload"]["acquired_at"] = "2026-10-13T16:00:00+08:00"
            saved = await client.call_tool("memory_commit", {"owner_id": owner, "expected_head": head,
                                                              "operations": [zoned]})
            assert not saved.is_error, saved
            receipt = json.loads("".join(b.text for b in saved.content))
            record = app.memory.inspect(owner, record_id=receipt["record_results"][0]["record_id"])["record"]
            assert record["payload"]["acquired_at"] == "2026-10-13T08:00:00Z"     # 保存时统一换算为 UTC
    anyio.run(scenario)
