"""材料查询：检索 → 翻页 → 组包 / 展开 / 深化 / 完整文稿。"""
from typing import Annotated, Literal

from pydantic import Field

from ....core.config import Budget, ReviewFilter
from ....services.query.models import Channel, ContentSource, QueryRequest, Scope
from ..registry import Ctx, op

QueryId = Annotated[str, Field(description="query.search 返回的 query_id")]
CandidateIds = Annotated[list[str], Field(description="候选 ID（C-…），来自本次查询已返回的结果")]


@op("query.search")
def search(ctx: Ctx, question: str, keywords: list[str] | None = None,
           content_source: Annotated[ContentSource, Field(description=(
               "overview_experience=L4/L3（默认）；process=L2 研究经过；technical=L1 技术内容；all=全部含原始来源"))]
           = "overview_experience",
           scope: Scope | None = None, review_filter: ReviewFilter = "all",
           purpose: Literal["exploration", "formal"] = "exploration",
           applicability: Annotated[str, Field(description="purpose=formal 时必填：结论的适用范围")] = "",
           freshness: Literal["current", "allow_stale"] = "current", channels: list[Channel] | None = None,
           context_reach: Literal["any_readable", "same_scope"] = "any_readable", result_limit: int | None = None,
           budget: Budget | None = None, query_id: str | None = None) -> dict:
    """检索材料并逐个回到固定版本核对，返回第一页候选（候选不是已读正文）。"""
    request = QueryRequest(question=question, keywords=keywords or [], content_source=content_source,
                           scope=scope or Scope(), review_filter=review_filter, purpose=purpose,
                           applicability=applicability, freshness=freshness,
                           channels=channels or ["identity", "lexical", "dense"], context_reach=context_reach,
                           result_limit=result_limit, budget=budget, query_id=query_id)
    return ctx.app.query.search(request)


@op("query.page")
def page(ctx: Ctx, query_id: QueryId, cursor: str | None = None) -> dict:
    """下一页候选（沿用原范围与累计预算）。"""
    return ctx.app.query.page(query_id, cursor)


@op("query.cancel")
def cancel(ctx: Ctx, query_id: QueryId) -> dict:
    """取消查询（已完成部分保留）。"""
    return ctx.app.query.cancel(query_id)


@op("query.assemble")
def assemble(ctx: Ctx, query_id: QueryId, candidate_ids: CandidateIds, associations: bool = False) -> dict:
    """把所选候选组合成阅读包：正文 + 必要依据（+ 已采纳关联补充）；预算不足整块省略并报告。"""
    return ctx.app.query.assemble(query_id, candidate_ids, associations=associations)


@op("query.expand")
def expand(ctx: Ctx, query_id: QueryId, candidate_ids: CandidateIds, target: Literal["process", "technical"]) -> dict:
    """沿作者保存的固定引用展开：经验/概览 → 研究经过或技术依据。"""
    return ctx.app.query.expand(query_id, candidate_ids, target)


@op("query.deepen")
def deepen(ctx: Ctx, query_id: QueryId, candidate_ids: CandidateIds, hops: int = 2,
           relations: list[str] | None = None, include_associations: bool = True) -> dict:
    """沿已保存关系有界深化（节点/边/跳数计入预算）；可达不等于科学支持。"""
    return ctx.app.query.deepen(query_id, candidate_ids, hops=hops, relations=relations,
                                include_associations=include_associations)


@op("query.documents")
def documents(ctx: Ctx, query_id: QueryId, candidate_ids: CandidateIds,
              document_type: Literal["research_process", "research_report"] = "research_process") -> dict:
    """返回候选所属对象的完整文稿（按文稿去重，保持章节顺序）。"""
    return ctx.app.query.documents(query_id, candidate_ids, document_type=document_type)
