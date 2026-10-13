"""配置、索引维护、能力与操作目录。"""
from typing import Annotated, Literal

from pydantic import Field

from ....core.config import ReviewFilter
from ....core.errors import BurrowlError
from ....services import health
from ....storage import secrets
from .. import errorlog, reqlog
from ..registry import Ctx, catalog, op


# ---------- 配置（MCP 与工作台共用；密钥只能在本机写入） ----------
@op("config.get")
def config_get(ctx: Ctx) -> dict:
    """当前配置、revision 与各连接器密钥是否已设置（不返回密钥内容）。"""
    return ctx.app.config.get()


@op("config.schema")
def config_schema(ctx: Ctx) -> dict:
    """配置的 JSON Schema（字段、默认值与说明）。"""
    return ctx.app.config.schema()


@op("config.update", effect="admin")
def config_update(ctx: Ctx, patch: Annotated[dict, Field(description="JSON Merge Patch：只写要改的字段；null 恢复默认")],
                  expected_revision: Annotated[str, Field(description="config_get 返回的 revision")],
                  dry_run: Annotated[bool, Field(description="true 只校验并返回将要生效的配置、不写入")] = False) -> dict:
    """修改配置（如数据库/索引位置、嵌入模型、向量后端、默认阅读策略、连接器）。返回需要的后续动作。"""
    return ctx.app.config.update(patch, expected_revision=expected_revision, dry_run=dry_run)


@op("config.secret_set", effect="user")
def secret_set(ctx: Ctx, secret_ref: Annotated[str, Field(pattern=r"^keyring:burrowl/")], value: str) -> dict:
    """把密钥写入本机凭据管理器（只在工作台/CLI；不暴露给 agent）。"""
    secrets.store(secret_ref, value)
    return secrets.status(secret_ref)


# ---------- 索引 ----------
@op("index.status")
def index_status(ctx: Ctx, owner_id: Annotated[str, Field(description="对象 ID")]) -> dict:
    """某对象各投影/通道的就绪状态（stale 表示落后于当前 HEAD）。"""
    return ctx.app.index.status(owner_id)


@op("index.reconcile", effect="admin")
def index_reconcile(ctx: Ctx, owner_id: Annotated[str | None, Field(description="只补这个对象；不给时补全部")] = None,
                    force: Annotated[bool, Field(description="true 时即使水位已是最新也重建")] = False,
                    sources: Annotated[bool, Field(description="补全部对象时同时更新来源文件投影")] = True) -> dict:
    """按当前 HEAD 补齐检索投影（不改记忆）；sources=true 同时更新来源文件投影。"""
    result = ctx.app.index.reconcile(owner_id, force=force)
    if sources and owner_id is None:
        result["sources"] = ctx.app.index.sync_sources(ocr_enabled=ctx.app.config.current().index.ocr_enabled)
    return result


@op("index.gaps")
def index_gaps(ctx: Ctx) -> list:
    """确定性缺口：缺 L4 概览、技术单元缺检索说明、L1 块过长、结果块没声明依赖、未解析的概念链接、
    没有入边的概念页、有未处理反例的结论、待确认的成熟度升级。内容是否充分仍需阅读判断。"""
    return ctx.app.index.gaps()


@op("index.search")
def index_search(ctx: Ctx, query: Annotated[str, Field(description="检索文本")], corpus: Literal["fulltext", "discovery", "source"] = "fulltext",
                 owner_ids: list[str] | None = None, levels: list[str] | None = None,
                 review_filter: ReviewFilter = "all", group: Literal["record", "owner"] = "record",
                 limit: int = 20) -> dict:
    """直接检索投影（调试/诊断用；正式查询请用 query_search，它会回到固定版本核对）。"""
    return ctx.app.index.search(query, corpus=corpus, owner_ids=owner_ids, levels=levels,
                                review_filter=review_filter, group=group, limit=limit)


# ---------- 系统 ----------
@op("system.capabilities")
def capabilities(ctx: Ctx) -> dict:
    """当前能力与降级情况（向量模型、重排模型、连接器）。"""
    return ctx.app.capabilities()


SKILL_NAMES = ("burrowl-work", "burrowl-read", "burrowl-consolidate", "burrowl-dev-check")
GUIDES = {"rules": "RULES.md", "recording": "RECORDING.md", **{n: f"{n}/SKILL.md" for n in SKILL_NAMES}}


@op("system.guide")
def guide(ctx: Ctx, name: Annotated[Literal["rules", "recording", "burrowl-work", "burrowl-read", "burrowl-consolidate", "burrowl-dev-check"], Field(
        description="rules 公共规则；recording 记忆记录写作标准；burrowl-work / burrowl-read / burrowl-consolidate 工作流；burrowl-dev-check 框架开发")]) -> dict:
    """读取 burrowl 工作流说明全文（与 Skill 文件相同）。客户端没有加载 burrowl Skill 时，用它代替 Skill。"""
    if ctx.app.skills_dir is None or not (ctx.app.skills_dir / GUIDES[name]).is_file():
        raise BurrowlError("NOT_FOUND", "本进程没有可用的工作流说明目录", {"name": name})
    return {"name": name, "text": (ctx.app.skills_dir / GUIDES[name]).read_text(encoding="utf-8")}


@op("system.validate")
def validate(ctx: Ctx) -> dict:
    """全工作区只读校验：提交链与指纹、写锁与残留、来源文件是否变化或缺失、索引是否落后。不修复。"""
    return health.validate(ctx.app.storage, ctx.app.index)


@op("system.ops")
def ops(ctx: Ctx, groups: Annotated[list[str] | None, Field(description="只列这些分组，如 memory、reading")] = None) -> list:
    """全部操作的目录（名称、分组、读写类型、说明、参数 schema）。"""
    return catalog(groups)


@op("system.tool_errors")
def tool_errors(ctx: Ctx, limit: Annotated[int, Field(ge=1, le=20000, description="最多统计最近多少条")] = 2000) -> dict:
    """最近被拒绝的工具调用与内部错误，按工具汇总（R20 D-10）：参数错误（input）、其他拒绝（other）、内部错误（internal）
    的次数和最常出错的参数路径，以及最近的内部错误 incident_id。参数错误多的工具，说明说明或示例需要改；不含参数内容。"""
    return errorlog.summary(ctx.app.root, limit=limit)


@op("system.request_log")
def request_log(ctx: Ctx, limit: Annotated[int, Field(ge=1, le=500, description="最多返回最近多少条")] = 20,
                op_name: Annotated[str | None, Field(description="只看这个工具，如 memory_commit")] = None,
                errors_only: Annotated[bool, Field(description="true 只看出错的调用")] = False,
                request_id: Annotated[str | None, Field(description="报错 details 里的 request_id")] = None) -> dict:
    """请求日志（新的在前）：每次工具调用收到的参数、MCP 层预处理后的参数（json_decoded 列出被从 JSON 字符串解析回来的）、
    返回内容与耗时。用来排查"传过来的和服务看到的不一样"这类问题；报错里带 request_id 时直接按它查。
    默认关闭（level=off 时为空），由用户在工作台设置或 MCP 启动参数 --request-log 打开。"""
    active = reqlog.level(ctx.app)
    # 关闭后既不写也不展示历史内容，保留磁盘文件便于再次启用；不把关闭操作当作清空日志。
    return {"level": active, "path": str(reqlog.path(ctx.app.root)),
            "items": [] if active == "off" else reqlog.read(
                ctx.app.root, limit=limit, op=op_name, errors_only=errors_only, request_id=request_id)}


# ---------- 工作台进程（R21 D-14；只在工作台提供） ----------
def _control(ctx: Ctx):
    if ctx.app.server is None:
        raise BurrowlError("CAPABILITY_UNAVAILABLE", "只有经 burrowl serve / start 运行的工作台能查看或控制自己的进程")
    return ctx.app.server


@op("server.info", effect="user")
def server_info(ctx: Ctx) -> dict:
    """工作台进程：运行中的版本、已安装的版本（不同表示需要重启才能用上新版本）、进程号、启动时间、网址、日志。"""
    return _control(ctx).info()


@op("server.stop", effect="user")
def server_stop(ctx: Ctx) -> dict:
    """停止工作台（处理完当前请求后退出）。AI 客户端里经 stdio 运行的 MCP 是另外的进程，不受影响。"""
    return _control(ctx).stop()


@op("server.restart", effect="user")
def server_restart(ctx: Ctx) -> dict:
    """重启工作台：新进程加载当前安装的版本，接管同一网址；页面稍后自动恢复。AI 客户端的 MCP 需在客户端里重启。"""
    return _control(ctx).restart()
