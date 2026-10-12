"""材料查询：检索 → 翻页 → 组包 / 展开 / 深化 / 完整文稿。"""
from typing import Annotated, Literal

from pydantic import Field

from ....core.config import Budget, ReviewFilter
from ....services.query.models import Channel, ContentSource, QueryRequest, Scope
from ..registry import Ctx, op

QueryId = Annotated[str, Field(description="query_search 返回的 query_id")]
CandidateIds = Annotated[list[str], Field(description="候选 ID（C-…），来自本次查询已返回的结果")]


@op("query.search")
def search(ctx: Ctx, question: Annotated[str, Field(description="要查的问题，一句话，保留型号与数值原文")],
           keywords: Annotated[list[str] | None, Field(description="词法检索用的关键词；不给时按问题切词")] = None,
           content_source: Annotated[ContentSource, Field(description=(
               "overview_experience=L4/L3（默认）；process=L2 研究经过；technical=L1 技术内容；all=全部含原始来源"))]
           = "overview_experience",
           scope: Annotated[Scope | None, Field(description="限定检索范围（对象、层级、类型等）；不给时全库")] = None,
           review_filter: Annotated[ReviewFilter, Field(description=(
               "all（默认，带复核徽标）/ reviewed_only / exclude_unsafe / unreviewed_only"))] = "all",
           purpose: Annotated[Literal["exploration", "formal"], Field(description=(
               "exploration 探索；formal 正式使用（只返回在 applicability 范围内已接受的结论）"))] = "exploration",
           applicability: Annotated[str, Field(description="purpose=formal 时必填：结论的适用范围")] = "",
           freshness: Annotated[Literal["current", "allow_stale"], Field(description=(
               "current 只用与当前版本一致的结果；allow_stale 允许索引落后时的结果（会标注）"))] = "current",
           channels: Annotated[list[Channel] | None, Field(description=(
               "检索通道，默认 identity + lexical + dense"))] = None,
           context_reach: Annotated[Literal["any_readable", "same_scope"], Field(description=(
               "组包时依据可以来自任何可读对象，或只来自同一范围"))] = "any_readable",
           result_limit: Annotated[int | None, Field(description="每页候选数上限")] = None,
           budget: Annotated[Budget | None, Field(description="本次查询的预算（字符、节点等）；不给时用配置默认")] = None,
           query_id: Annotated[str | None, Field(description="可选：自定查询 ID（重试时沿用）")] = None) -> dict:
    """独立检索材料并核对固定版本，返回第一页候选；不建立阅读会话、不进入工作台阅读页。
    用户查询主题相关知识、找已有经验时默认先 reading_start（简查 quick、完整阅读 standard）；
    只有明确只要片段/候选列表或低层查询时直接使用本工具。候选不是已读正文。"""
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
def assemble(ctx: Ctx, query_id: QueryId, candidate_ids: CandidateIds,
             associations: Annotated[bool, Field(description="true 时补充已采纳关联指向的内容")] = False) -> dict:
    """把所选候选组合成阅读包：正文 + 必要依据（+ 已采纳关联补充）；预算不足整块省略并报告。"""
    return ctx.app.query.assemble(query_id, candidate_ids, associations=associations)


@op("query.expand")
def expand(ctx: Ctx, query_id: QueryId, candidate_ids: CandidateIds,
           target: Annotated[Literal["process", "technical"], Field(description=(
               "process 展开到研究经过（L2）；technical 展开到技术依据（L1）"))]) -> dict:
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
              document_type: Annotated[Literal["research_process", "research_report"], Field(description=(
                  "research_process 完整过程稿；research_report 精简报告"))] = "research_process") -> dict:
    """返回候选所属对象的完整文稿（按文稿去重，保持章节顺序）。"""
    return ctx.app.query.documents(query_id, candidate_ids, document_type=document_type)
