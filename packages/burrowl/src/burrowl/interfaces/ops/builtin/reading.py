"""阅读会话。agent 动作（MCP 可见）与用户动作（effect=user，只在工作台/CLI）。

工具说明面向 AI：每个参数写明了什么时候填、填什么；规则由程序强制，违反时返回错误码与原因。
"""
from typing import Annotated, Literal

from pydantic import Field

from ....core.config import ReviewFilter
from ..registry import Ctx, op

SessionId = Annotated[str, Field(description="阅读会话 ID（RS-…）")]
Strategy = Literal["standard", "associative", "quick"]
Verdict = Literal["relevant", "uncertain", "irrelevant"]
RequestId = Annotated[str | None, Field(description="可选 UUID；网络重试时沿用同一个，不会重复执行")]


# ---------- agent ----------
@op("reading.start", effect="write")
def start(ctx: Ctx, question: Annotated[str, Field(description="要回答的问题，一句话，保留型号与数值原文")],
          conditions: Annotated[list[str] | None, Field(description="问题的前提条件（自然语言），会显示给用户")] = None,
          strategy: Annotated[Strategy | None, Field(description=(
              "standard=筛选后完整阅读（默认）；associative=不足时按联想方向补查；quick=只依据片段、不读全文"))] = None,
          review_filter: Annotated[ReviewFilter | None, Field(description=(
              "all（默认，带复核徽标）/ reviewed_only / exclude_unsafe / unreviewed_only"))] = None,
          variants: Annotated[list[dict] | None, Field(description=(
              "最多 2 条等义改写 [{language: zh|en, question, lexical_terms?}]；技术问题建议补一条英文；"
              "型号、数值必须原样保留"))] = None,
          protected_terms: Annotated[list[str] | None, Field(description="必须原样出现的词（型号、编号）")] = None,
          domains: Annotated[list[str] | None, Field(description="术语库领域，用于术语扩展")] = None,
          owner_id: Annotated[str | None, Field(description="本次阅读服务于哪个对象（续接用）")] = None,
          scope_owner_ids: Annotated[list[str] | None, Field(description="只在这些对象中召回")] = None,
          field_conditions: Annotated[list[dict] | None, Field(description=(
              "可选的显式字段条件，最多 16 条：[{id, kind: literal|numeric, field, operator: eq|ne|lt|le|gt|ge, "
              "expected, unit?}]。只在原文的“字段：值”中核对，冲突的对象排后、不删除"))] = None) -> dict:
    """查询主题相关知识、找已有经验的默认入口：创建工作台可见阅读会话并立即召回。
    简查选 quick（采纳片段、写笔记）；完整阅读选 standard。返回第一批对象筛选包
    （每个对象 2–4 个命中窗口，含 digest），继续 assess → read（quick 跳过）→ note → finish。"""
    return ctx.app.reading.start(question, actor=ctx.actor, conditions=conditions, strategy=strategy,
                                 review_filter=review_filter, variants=variants, protected_terms=protected_terms,
                                 domains=domains, owner_id=owner_id, scope_owner_ids=scope_owner_ids,
                                 field_conditions_=field_conditions)


@op("reading.assess", effect="write")
def assess(ctx: Ctx, session_id: SessionId, judgments: Annotated[list[dict], Field(description=(
        "[{owner_id, verdict, reason, digest, accepted_windows?}]。verdict：relevant=窗口显示它能回答问题或"
        "提供必要依据，值得完整阅读；uncertain=信息不足以判断，先 reading_peek 或交给用户；irrelevant=明确无关。"
        "reason 写依据窗口里的什么内容；digest 原样抄筛选包的 digest；quick 策略在 accepted_windows 列出"
        "可作为笔记依据的窗口 entry_id"))], request_id: RequestId = None) -> dict:
    """三态判断一批对象；返回下一批或“可以开始读”的提示。用户的改判优先，不会被覆盖。"""
    return ctx.app.reading.assess(session_id, judgments, request_id=request_id)


@op("reading.peek", effect="write")
def peek(ctx: Ctx, session_id: SessionId, owner_id: str) -> dict:
    """查看某对象更多的命中窗口（用于 uncertain 的对象）。"""
    return ctx.app.reading.peek(session_id, owner_id)


@op("reading.read", effect="write")
def read(ctx: Ctx, session_id: SessionId, owner_id: str, page: int = 1) -> dict:
    """完整阅读判为相关的对象：第 1 页附目录，按 unread_pages 读完全部页。quick 策略不可用。
    对象在阅读期间有新提交时要求从第 1 页重读（STALE_BASIS）。"""
    return ctx.app.reading.read(session_id, owner_id, page)


@op("reading.note", effect="write")
def note(ctx: Ctx, session_id: SessionId, owner_id: str,
         note: Annotated[str, Field(description=(
             "研究笔记（Markdown）：只写材料中的知识——问题如何演进、方法/公式（定义变量与单位）/条件、关键数据、"
             "反例与局限；关键句旁就近标出处 ID；不重复本次问题，不写自己的操作计划"))],
         sources: Annotated[list[str], Field(description=(
             "出处 ID：必须是本会话交付过的（如 MEM-…、MEM-…#块ID、SRC-…）；quick 下只能是已采纳的窗口"))],
         clues: Annotated[list[str] | None, Field(description=(
             "可选的探索线索（材料提到但未核实的方向、可能相关的对象）；只作导航，不会成为结论"))] = None,
         request_id: RequestId = None) -> dict:
    """保存某对象的研究笔记（standard/associative 须先读完全部页）；出处补全为固定引用。"""
    return ctx.app.reading.note(session_id, owner_id, note, sources, clues=clues, request_id=request_id)


@op("reading.synthesize", effect="write")
def synthesize(ctx: Ctx, session_id: SessionId, note: str, sources: list[str], owner_ids: list[str],
               request_id: RequestId = None) -> dict:
    """跨对象综合笔记；所依据的任何单篇笔记改变或陈旧后，综合稿自动失效。"""
    return ctx.app.reading.synthesize(session_id, note, sources, owner_ids, request_id=request_id)


@op("reading.ask_user", effect="write")
def ask_user(ctx: Ctx, session_id: SessionId, kind: Literal["fulltext", "direction", "clarify"], prompt: str,
             options: list[str] | None = None, request_id: RequestId = None) -> dict:
    """向用户提问。fulltext=申请全文补偿（仅当发现召回不可用、不完整或不足，见 fulltext_available；批准后得到
    approval_id）；direction=请用户定联想方向；clarify=澄清问题。用户可在对话或工作台回答。"""
    result = ctx.app.reading.ask_user(session_id, kind, prompt, options, request_id=request_id)
    if ctx.elicit is not None:  # 宿主支持时直接在对话里询问；否则用户在工作台回答
        answer = ctx.elicit(prompt, kind, options or [])
        if answer is not None:
            decision, text = answer
            response = ctx.app.reading.respond(session_id, result["request"]["request_id"], decision=decision,
                                               answer=text)
            result["answered_in_chat"] = response
    return result


@op("reading.recall", effect="write")
def recall(ctx: Ctx, session_id: SessionId, mode: Literal["fulltext", "association"],
           approval_id: Annotated[str | None, Field(description="fulltext 模式必填：用户批准后的 approval_id")] = None,
           association_text: Annotated[str | None, Field(description="association 模式：联想方向（由主 agent 制定）")]
           = None, request_id: RequestId = None) -> dict:
    """补充召回：全文补偿（需用户批准）或联想补查（associative 策略，受轮数上限）。
    想换一种问法重新找，请新开一个 reading_start。"""
    return ctx.app.reading.recall(session_id, mode, approval_id=approval_id, association_text=association_text,
                                  request_id=request_id)


@op("reading.finish", effect="write")
def finish(ctx: Ctx, session_id: SessionId,
           outcome: Annotated[str, Field(description="一句话说明结束原因或结论去向，如“已回答，交给 work 保存”")] = "",
           request_id: RequestId = None) -> dict:
    """结束会话并返回交接文本（综合稿优先；陈旧笔记不交付；超出上限的笔记整篇省略并列出缺口）。"""
    return ctx.app.reading.finish(session_id, outcome, request_id=request_id)


@op("reading.status")
def status(ctx: Ctx, session_id: SessionId) -> dict:
    """会话概况、当前批次、交接预览、预算消耗，以及用户在工作台上的新输入（每条只交付一次）。"""
    return ctx.app.reading.status(session_id)


@op("reading.delegate")
def delegate(ctx: Ctx, session_id: SessionId, host_supports_subagents: bool) -> dict:
    """子 agent 任务包（只描述任务；实际派工由宿主完成）。"""
    return ctx.app.reading.delegate(session_id, host_supports_subagents=host_supports_subagents)


@op("reading.list")
def list_sessions(ctx: Ctx, status: str | None = None, owner_id: str | None = None, limit: int = 50) -> list:
    """阅读会话列表（最新在前）。"""
    return ctx.app.reading.list(status=status, owner_id=owner_id, limit=limit)


@op("reading.view")
def view(ctx: Ctx, session_id: SessionId) -> dict:
    """会话完整状态（时间线、筛选包、笔记、请求）；只读，不消费收件箱。"""
    return ctx.app.reading.view(session_id)


# ---------- 用户（工作台） ----------
@op("reading.respond", effect="user")
def respond(ctx: Ctx, session_id: SessionId, request_id: str, decision: Literal["approve", "deny", "answer"],
            answer: str = "") -> dict:
    """回答 agent 的请求；批准全文补偿会生成 approval_id。"""
    return ctx.app.reading.respond(session_id, request_id, decision=decision, answer=answer)


@op("reading.override", effect="user")
def override(ctx: Ctx, session_id: SessionId, owner_id: str, verdict: Verdict, reason: str = "") -> dict:
    """用户改判某个对象（agent 不能再覆盖）。"""
    return ctx.app.reading.override(session_id, owner_id, verdict, reason)


@op("reading.direct", effect="user")
def direct(ctx: Ctx, session_id: SessionId, text: str) -> dict:
    """补充联想方向（下一轮联想补查使用）。"""
    return ctx.app.reading.direct(session_id, text)


@op("reading.configure", effect="user")
def configure(ctx: Ctx, session_id: SessionId, strategy: Strategy) -> dict:
    """切换阅读策略。"""
    return ctx.app.reading.configure(session_id, strategy=strategy)


@op("reading.abort", effect="user")
def abort(ctx: Ctx, session_id: SessionId, reason: str = "") -> dict:
    """中止会话（agent 之后的写操作会被拒绝）。"""
    return ctx.app.reading.abort(session_id, reason)


@op("reading.archive", effect="user")
def archive(ctx: Ctx, session_id: SessionId, archived: bool = True) -> dict:
    """归档或恢复会话（历史保留）。"""
    return ctx.app.reading.archive(session_id, archived)
