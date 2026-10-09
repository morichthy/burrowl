"""知识中枢（WeKnora、Markdown 库……）：发布预览/执行、状态、联邦检索、Wiki。

宿主 AI 编写 Wiki：先用 memory.commit 提交 kind=wiki_page 记录（本地真源），再 hub.plan / hub.apply 发布。
"""
from typing import Annotated

from pydantic import Field

from ....core.config import ReviewFilter
from ....services import activity, exchange, render
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
    result = ctx.app.hub.apply(connector_id, plan_hash, force_wiki=force_wiki)
    return activity.with_activity(result, [activity.publish_line(connector_id, result.get("summary"))])


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


@op("hub.candidates_pull", effect="write")
def hub_candidates_pull(ctx: Ctx, connector_id: str, limit: int = 200) -> dict:
    """把外部工具发现的关系线索拉回为关联候选（origin=tool:<连接器>，只写派生的候选表，不写记录）。
    之后用 relations.candidates 查看并判断。"""
    pulled = ctx.app.hub.external_candidates(connector_id, limit)
    by_owner: dict[str, list[dict]] = {}
    for row in pulled["rows"]:
        by_owner.setdefault(row["owner_id"], []).append(row)
    for owner_id, rows in by_owner.items():
        ctx.app.index.db.add_candidates(owner_id, rows, origin=f"tool:{connector_id}")
    return {"connector_id": connector_id, "added": len(pulled["rows"]), "unmatched": pulled["unmatched"],
            "supported": pulled["supported"]}


# ---------- 交换包（R12 T-P3.2，格式见 docs/reference/exchange-format.md）----------
@op("exchange.export", effect="write")
def exchange_export(ctx: Ctx, name: Annotated[str, Field(description="导出名称：字母、数字、点、下划线、连字符")],
                    owner_ids: list[str] | None = None, owner_types: list[str] | None = None,
                    exclude_sensitivity: Annotated[list[str] | None, Field(description=(
                        "不导出的敏感级别；默认 [confidential]，restricted 永远不导出"))] = None) -> dict:
    """把记录导出为交换包（Markdown 库 + 图 JSONL + manifest）到数据目录的 exports/<名称>/。
    派生数据，可删除重建；增量写入，内容没变的文件不改写。可以用 Obsidian 直接打开其中的 vault 目录。"""
    return exchange.export(ctx.app.storage, ctx.app.index, name, owner_ids=owner_ids, owner_types=owner_types,
                           exclude_sensitivity=exclude_sensitivity,
                           concept_owner=ctx.app.config.current().graph.concept_owner_id)


@op("memory.render")
def memory_render(ctx: Ctx, owner_id: str, record_id: str) -> dict:
    """按交换格式把一条记录渲染为 Markdown（YAML 头 + 正文 + 自动生成的反链与关联）。只读。"""
    ctx.app.index.ensure_graph()
    return render.render_one(ctx.app.storage, owner_id, record_id,
                             concept_owner=ctx.app.config.current().graph.concept_owner_id,
                             edges=ctx.app.index.db.graph_all_edges())
