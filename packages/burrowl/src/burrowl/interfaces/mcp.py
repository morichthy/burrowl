"""MCP 服务：把操作注册表中的 agent 可用操作（effect ≠ user）暴露为工具。

    burrowl mcp --data-dir D:/BurrowlData/main        stdio（宿主客户端直接拉起）
    burrowl serve 时同时挂载 /<token>/mcp          Streamable HTTP

工具名把点换成下划线（memory.inspect → memory_inspect）；按配置 mcp.toolsets 选择暴露哪些分组。
reading_ask_user 在宿主支持 elicitation 时直接在对话里询问用户；否则请求进入工作台待处理队列。
每个 Skill 的正文作为 prompt 提供，供不支持 Skill 的客户端使用。
"""
import inspect
import json
from pathlib import Path
from typing import Any, Literal

import anyio
from anyio import from_thread, to_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel

from ..app import App
from ..core.errors import BurrowlError
from . import agents
from .ops.registry import Ctx, OpSpec, call, load

COMMON = """工作流说明（burrowl-work / burrowl-read / burrowl-consolidate 及公共规则）：客户端已加载 burrowl 的 Skill 就按 Skill 执行；
否则调用本服务的工具 system_guide 读取——开始实质工作前读 name=rules，再按任务读 burrowl-work / burrowl-read / burrowl-consolidate，
写记忆记录前读 recording，并用 memory_contract(kind) 查该类记录的字段与示例。
用户查询某主题的相关知识、找已有经验，默认先 reading_start 建立可见阅读会话：简查用 strategy=quick，完整阅读用 standard。
只有用户明确只要片段/候选列表，或直接查看已知源码、配置、开发状态时，才直接使用低层查询或读取工具。
query_*、memory_owners、memory_owner_route、memory_resume 不会建立阅读会话，不会出现在工作台的当前阅读或阅读会话中。
写操作返回 activity 行时，在回复末尾以"关键操作"为题原样逐行列出（不展开）。
用户可在工作台把笔记交给 AI：续接或收口时用 relations_notes(status=['queued']) 查看，用 relations_note_reply 回写方案、提问或说明不处理；
方案只预演不写入，由用户在工作台执行（规则第 8 节"工作台笔记"）。
用户交给 AI 的维护计划用 maintenance_plans(status=['queued']) 查看，用 maintenance_review 写处置；执行由用户在工作台完成。
要点：候选不是已读正文；保存≠复核≠索引就绪；引用一律用固定版本（提交回执给出 fixed_ref）；
AI 自查不能登记为已确认；全文补偿必须先 reading_ask_user 获得用户批准。"""

AUTO = """burrowl 已为本对话启用（{why}）：AI 研发工作区的版本化记忆、材料阅读、复核与知识中枢。
- 数据目录：{data}。记忆记录、文稿、阅读会话、Run 登记都存在这里，只能通过本服务的工具读写，不要直接改其中的文件。
- 工作目录：{work}。原始数据和代码留在工作目录（或用户指定的位置），保持原位、不复制进数据目录。
  需要引用时用 sources_register 登记其绝对路径（只记路径和指纹），记录里以 SRC- ID 引用。
本对话中的研究、分析、实验、资料整理、方案讨论等实质工作，默认按工作流进行，不必等用户点名：
  续接或保存成果 → work；查找已有经验、阅读材料 → read；阶段收口、来源变化、复核整理 → consolidate。
按投入分档，取能完成任务的最低一档（规则第 1 节）：
  档 0 简单问答、读源码/配置/命令状态、闲聊：不调用本服务的工具，不记录；
  档 1 一次性小任务：开工不召回、不判定归属；结束时做沉淀检查，有可复用的内容才 memory_owner_route 后保存，否则不写；
  档 2 持续工作（继续/续接、多步研究或实验、导入资料、用户要求留痕）：先 memory_owner_route 选出暂定对象，memory_resume 完整阅读，
  确认是同一目标才第一次保存（带 attribution），不相关就换候选或新建；有用的中间成果主动保存。用户说"不用记"时按档 0/1 处理。
  需要读已有材料时，先确认或新建对象，再 reading_start(owner_id=…)，question 沿用判定时的 task（沿用已有对象时复用那一轮召回）；
  判定阶段觉得可能相关的对象不直接写成关联，实际读完、验证后再写记录和关联。
修改 burrowl 框架本身的代码不进入工作流（那是 burrowl-dev-check）。
""" + COMMON

MANUAL = """burrowl：AI 研发工作区的版本化记忆、材料阅读、复核与知识中枢（{why}）。
数据目录：{data}；工作目录：{work}。
默认不进入 burrowl 工作流。只在以下情况按 burrowl-work / burrowl-read / burrowl-consolidate 工作：用户明确要求使用 burrowl、保存或查找记忆；
或项目说明文件（AGENTS.md 等）写明本项目已启用 burrowl。用户想在本项目长期启用时，告诉用户运行 `burrowl enable <项目目录>`。
""" + COMMON


def effective_mode(app: App) -> tuple[str, str]:
    """(auto|manual, 原因)。工作目录下的 .burrowl.json 可以在 manual 下启用、在 auto 下关闭本项目。"""
    configured = app.mcp_mode or app.config.current().mcp.mode
    project = agents.project_state(app.work_dir)
    if project is True:
        return "auto", "本项目已启用"
    if project is False:
        return "manual", "本项目已关闭 burrowl 工作流"
    return configured, "全局自动模式" if configured == "auto" else "手动模式，本项目未启用"


def instructions(app: App) -> str:
    mode, why = effective_mode(app)
    work = str(app.work_dir) if app.work_dir else "未指定（引用原件时用绝对路径）"
    return (AUTO if mode == "auto" else MANUAL).format(data=app.root, work=work, why=why)


class _Approval(BaseModel):
    decision: Literal["approve", "deny"]
    note: str = ""


class _Answer(BaseModel):
    answer: str


def _elicitor(context: Context | None):
    """返回同步的 elicit 回调（在工作线程里调用，回到事件循环执行）。宿主不支持时返回 None。"""
    if context is None:
        return None

    def elicit(prompt: str, kind: str, options: list[str]) -> tuple[str, str] | None:
        schema = _Approval if kind == "fulltext" else _Answer
        message = prompt + (f"\n可选：{'、'.join(options)}" if options else "")
        try:
            result = from_thread.run(context.elicit, message, schema)
        except Exception:  # noqa: BLE001 - 客户端不支持 elicitation：退回工作台队列
            return None
        if getattr(result, "action", None) != "accept" or result.data is None:
            return ("deny", "用户在对话中拒绝或取消") if kind == "fulltext" else None
        if kind == "fulltext":
            return result.data.decision, result.data.note
        return "answer", result.data.answer
    return elicit


def _tool(app: App, spec: OpSpec, actor: dict):
    params = list(inspect.signature(spec.fn).parameters.values())[1:]

    async def tool(context: Context | None = None, **kwargs: Any) -> Any:
        ctx = Ctx(app=app, actor=actor, channel="mcp", elicit=_elicitor(context))
        try:
            return await to_thread.run_sync(lambda: call(ctx, spec.name, kwargs))
        except BurrowlError as exc:
            raise ToolError(json.dumps(exc.problem(), ensure_ascii=False)) from exc

    context_param = inspect.Parameter("context", inspect.Parameter.KEYWORD_ONLY, annotation=Context | None,
                                      default=None)
    tool.__signature__ = inspect.Signature([*params, context_param], return_annotation=Any)
    tool.__annotations__ = {p.name: p.annotation for p in params} | {"context": Context | None, "return": Any}
    tool.__name__ = spec.tool_name
    tool.__doc__ = spec.summary
    return tool


def build_server(app: App, *, actor: dict | None = None, skills_dir: Path | None = None) -> MCPServer:
    app.skills_dir = app.skills_dir or skills_dir
    server = MCPServer(name="burrowl", instructions=instructions(app))
    toolsets = set(app.config.current().mcp.toolsets) | {"system"}
    actor = actor or {"kind": "ai", "id": "mcp-client"}
    for spec in load().values():
        if spec.effect == "user" or spec.group not in toolsets:
            continue
        server.add_tool(_tool(app, spec, actor), name=spec.tool_name, description=spec.summary,
                        annotations=ToolAnnotations(readOnlyHint=spec.effect == "read", destructiveHint=False,
                                                    idempotentHint=spec.effect == "read"),
                        structured_output=False)
    if skills_dir and skills_dir.is_dir():
        # 公共文档（RULES.md、RECORDING.md）作为独立 prompt；每个 Skill prompt 附上 RULES，
        # 这样只通过 MCP 接入的宿主也能拿到 Skill 引用的公共规则。
        shared = {p.stem.lower(): p.read_text(encoding="utf-8") for p in sorted(skills_dir.glob("*.md"))
                  if p.name != "README.md"}
        for name, text in shared.items():
            _add_prompt(server, name, text, f"公共文档：{name}")
        rules = shared.get("rules", "")
        for skill in sorted(skills_dir.glob("*/SKILL.md")):
            text = skill.read_text(encoding="utf-8")
            if rules:
                text += f"\n\n---\n\n<!-- 以下为 RULES.md 全文，Skill 中的“先读 RULES.md”即指此处 -->\n\n{rules}"
            _add_prompt(server, skill.parent.name, text, f"工作流：{skill.parent.name}")
    return server


def _add_prompt(server: MCPServer, name: str, text: str, description: str) -> None:
    def prompt() -> str:
        return text
    prompt.__name__ = name.replace("-", "_")
    prompt.__doc__ = description
    server.prompt(name=name, description=description)(prompt)


def run_stdio(app: App, *, skills_dir: Path | None = None) -> None:
    server = build_server(app, skills_dir=skills_dir)
    anyio.run(server.run_stdio_async)
