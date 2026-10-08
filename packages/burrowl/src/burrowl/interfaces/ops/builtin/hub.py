"""知识中枢（WeKnora、Markdown 库……）：发布预览/执行、状态、联邦检索、Wiki。

宿主 AI 编写 Wiki：先用 memory.commit 提交 kind=wiki_page 记录（本地真源），再 hub.plan / hub.apply 发布。
"""
from typing import Annotated

from pydantic import Field

from ....core.config import ReviewFilter
from ..registry import Ctx, op


@op("hub.connectors")
def hub_connectors(ctx: Ctx) -> list:
    """已配置的连接器、发布策略与已发布条目数。"""
    return ctx.app.hub.list_connectors()


@op("hub.health")
def hub_health(ctx: Ctx, connector_id: str) -> dict:
    """测试连接：服务版本、兼容性、能力、密钥是否已设置（不返回密钥）。"""
    return ctx.app.hub.health(connector_id)


@op("hub.plan")
def hub_plan(ctx: Ctx, connector_id: str) -> dict:
    """预览发布：按策略（默认只发已复核的 L4/L3 与成稿文档）列出新增/更新/删除，返回 plan_hash。"""
    return ctx.app.hub.plan(connector_id)


@op("hub.apply", effect="admin")
def hub_apply(ctx: Ctx, connector_id: str,
              plan_hash: Annotated[str, Field(description="hub.plan 返回的 plan_hash；数据变化后需重新预览")],
              force_wiki: Annotated[bool, Field(description="覆盖外部已被修改的 Wiki 页面")] = False) -> dict:
    """执行发布。逐条记录结果，单条失败不影响其他条目。"""
    return ctx.app.hub.apply(connector_id, plan_hash, force_wiki=force_wiki)


@op("hub.status")
def hub_status(ctx: Ctx, connector_id: str, refresh: bool = True) -> dict:
    """发布状态：各状态计数、失败/漂移条目、最近几次执行；refresh 时轮询外部解析进度。"""
    return ctx.app.hub.status(connector_id, refresh=refresh)


@op("hub.search")
def hub_search(ctx: Ctx, query: str, connector_ids: list[str] | None = None, limit: int = 10,
               review_filter: ReviewFilter = "all") -> dict:
    """在外部知识中枢检索；命中能对上本地条目时附 fixed_ref 与按本地当前状态计算的复核类别。"""
    return ctx.app.hub.search(query, connector_ids=connector_ids, limit=limit, review_filter=review_filter)


@op("wiki.list")
def wiki_list(ctx: Ctx, connector_id: str, query: str = "", limit: int = 50) -> dict:
    """列出外部 Wiki 页面（authored_here 表示由本项目发布）。"""
    return ctx.app.hub.wiki_list(connector_id, query, limit)


@op("wiki.read")
def wiki_read(ctx: Ctx, connector_id: str, slug: str) -> dict:
    """读取一页外部 Wiki（含外部自动生成的页面）。"""
    return ctx.app.hub.wiki_read(connector_id, slug)


@op("wiki.configure", effect="admin")
def wiki_configure(ctx: Ctx, connector_id: str) -> dict:
    """按配置 wiki.mode 开启/关闭外部自动 Wiki（auto/both 开启）。"""
    return ctx.app.hub.wiki_configure(connector_id)
