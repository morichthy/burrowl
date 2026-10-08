"""材料查询的请求/结果契约（pydantic）。HTTP、MCP 与 CLI 使用同一组模型。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...core.config import Budget, ReviewFilter

ContentSource = Literal["overview_experience", "process", "technical", "all"]
Channel = Literal["identity", "lexical", "dense"]
CONTENT_LEVELS: dict[str, list[str] | None] = {"overview_experience": ["L4", "L3"], "process": ["L2"],
                                                "technical": ["L1"], "all": None}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(_Model):
    """直接候选的范围。不同维度同时满足；同一维度内任一值即可。None 表示不限。"""

    owner_ids: list[str] | None = Field(None, description="限定对象；显式列出的对象才能看到其 owner_only 记录")
    owner_types: list[str] | None = Field(None, description="research / project / core-algorithm / run / knowledge …")
    kinds: list[str] | None = None
    exclude_ids: list[str] = Field(default_factory=list, description="排除的对象/记录/结论 ID；排除始终生效")
    updated_from: str | None = None
    updated_before: str | None = None


class QueryRequest(_Model):
    question: str = Field(min_length=1, max_length=12000)
    keywords: list[str] = Field(default_factory=list, max_length=32)
    content_source: ContentSource = "overview_experience"
    scope: Scope = Field(default_factory=Scope)
    review_filter: ReviewFilter = "all"
    purpose: Literal["exploration", "formal"] = "exploration"
    applicability: str = Field("", description="purpose=formal 时必填：结论需在此范围内已被接受")
    freshness: Literal["current", "allow_stale"] = "current"
    channels: list[Channel] = Field(default_factory=lambda: ["identity", "lexical", "dense"])
    context_reach: Literal["any_readable", "same_scope"] = Field(
        "any_readable", description="组包时读取必要依据的范围：任何可读对象，或限于本次范围")
    result_limit: int | None = Field(None, ge=1, le=100)
    budget: Budget | None = None
    query_id: str | None = Field(None, description="可由调用方生成（UUID），以便在执行中途取消")

    @model_validator(mode="after")
    def _formal(self):
        if self.purpose == "formal" and not self.applicability.strip():
            raise ValueError("正式使用必须填写 applicability（结论的适用范围）")
        return self


class Candidate(_Model):
    candidate_id: str
    kind: str
    owner_id: str
    record_id: str
    title: str
    excerpt: str
    level: str | None
    ref: dict
    review: str = Field(description="none / accepted / unreviewed / mixed / unsafe（来自检索投影）")
    channels: list[str]
    score: float
    matched_blocks: list[str] = Field(default_factory=list)
    stale_revision: bool = False


class QueryPage(_Model):
    query_id: str
    items: list[Candidate]
    next_cursor: str | None
    gaps: list[dict]
    consumed: dict[str, int]
    stop_reason: str | None = None
