"""复核、溯源、关联与关系图、语义维护、监测、Run、来源登记。"""
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import Field

from ....core.errors import BurrowlError
from ....services import activity, relations
from ....services.memory import associations, provenance
from .. import shapes
from ..registry import Ctx, op

_FROM = {"target_kind": "record", "target_id": "MEM-4acc73bb-59c7-560d-9415-fd42e9dcaa2d", "revision": 1,
         "sha256": "ba12342c6ca8afa0a1760dc517fd7bff2d1b60c7f082ee9113e419572834df51", "locator": "record",
         "relation": "related_to"}
_TO = {**_FROM, "target_id": "MEM-58fd845a-05a5-516c-8bbe-c88d887cbb91", "revision": 6,
       "sha256": "e5fc7cb2327f0b0412166c545c6ae9e862dad0692c0038775dcb4deb0766e3c2"}
EVIDENCE_REFS = shapes.shaped(list[dict] | None, shapes.REF_LIST, (
    "支持或反驳的固定引用列表（accepted 必填）。" + shapes.REF_NOTE), nullable=True)

ReviewState = Literal["accepted", "disputed", "retracted", "superseded", "not-reviewed"]
QueueState = Literal["not-reviewed", "stale", "disputed", "upstream-blocked", "maturity-requested", "accepted",
                     "retracted", "superseded"]
Maturity = Literal["conjecture", "preliminary", "replicated", "established"]
EvidenceBasis = Literal["engineering_case", "proof", "authoritative_source", "replication"]


# ---------- 复核 ----------
@op("review.queue")
def review_queue(ctx: Ctx, states: Annotated[list[QueueState] | None, Field(description=(
                     "只看这些状态；不给时列出未复核、已失效、有争议、被上游阻断、有升级提议的"))] = None,
                 owner_id: Annotated[str | None, Field(description="只看该对象的结论")] = None,
                 query: Annotated[str, Field(description="按结论陈述或 ID 匹配")] = "",
                 limit: int = 50, offset: int = 0) -> dict:
    """待复核队列：默认列出未复核、已失效、有争议、被上游阻断、有升级提议的结论；
    每项带有效成熟度与派生统计（支持数按原始来源去重，"用得多"不等于"对"）。"""
    return ctx.app.review.queue(states=states, owner_id=owner_id, query=query, limit=limit, offset=offset)


@op("review.submit", effect="write")
def review_submit(ctx: Ctx, target_claim_id: Annotated[str, Field(description="要复核的结论 ID（CLM-…）")],
                  state: Annotated[ReviewState, Field(description=(
                      "accepted 在 scope 内成立；disputed 有争议；retracted 撤回；superseded 被替代；not-reviewed 未复核"))],
                  reason: Annotated[str, Field(min_length=1, description=(
                      "复核理由；AI 代用户登记时写明“用户在对话中确认：…（时间）”"))],
                  scope: Annotated[str | None, Field(description="在什么范围内成立（accepted 必填）")] = None,
                  evidence_refs: EVIDENCE_REFS = None,
                  replacement_claim_id: Annotated[str | None, Field(description=(
                      "superseded 必填：替代它的结论 ID"))] = None,
                  maturity: Annotated[Maturity | None, Field(description=(
                      "成熟度：conjecture 有推理无数据；preliminary 至少一次 Run 或小样本支持；replicated 多份独立依据都成立；"
                      "established 只能由人或授权流程登记（AI 改用 requested_maturity）"))] = None,
                  evidence_basis: Annotated[EvidenceBasis | None, Field(description="established 的证据类型")] = None,
                  requested_maturity: Annotated[Literal["established"] | None, Field(description=(
                      "AI 提议升级为已确立，由用户在复核队列中确认"))] = None,
                  dry_run: Annotated[bool, Field(description="true 只预检、不写入")] = False) -> dict:
    """复核一条结论。accepted 需要适用范围与支持性证据；superseded 需要替代结论。dry_run 只预检。
    成熟度与复核状态分开：可以在 not-reviewed 时登记 conjecture/preliminary；established 必须 accepted。"""
    return activity.annotate(ctx.app.storage, ctx.app.review.submit({"target_claim_id": target_claim_id, "state": state, "reason": reason,
                                  "scope": scope, "evidence_refs": evidence_refs or [],
                                  "replacement_claim_id": replacement_claim_id, "actor": ctx.actor,
                                  "maturity": maturity, "evidence_basis": evidence_basis,
                                  "requested_maturity": requested_maturity, "dry_run": dry_run}))


@op("review.batch", effect="write")
def review_batch(ctx: Ctx, items: shapes.shaped(list[dict], shapes.REVIEW_ITEMS, (
        "每项字段同 review_submit：{target_claim_id, state, reason, scope?, evidence_refs?, replacement_claim_id?, "
        "maturity?, evidence_basis?, requested_maturity?}")),
                 dry_run: Annotated[bool, Field(description="true 只预检、不写入")] = False) -> dict:
    """批量复核（逐条串行；单条失败不影响其他）。每项字段同 review_submit。"""
    return activity.annotate(ctx.app.storage, ctx.app.review.batch(items, actor=ctx.actor, dry_run=dry_run))


@op("review.history")
def review_history(ctx: Ctx, claim_id: Annotated[str, Field(description="结论 ID（CLM-…）")]) -> dict:
    """一条结论的复核历史。"""
    return ctx.app.review.history(claim_id)


# ---------- 溯源 ----------
@op("provenance.impact")
def impact(ctx: Ctx, changed_ids: Annotated[list[str], Field(description=(
        "变化了的来源（SRC-…）、记录（MEM-…）或结论（CLM-…）"))]) -> dict:
    """这些内容变化后，哪些下游需要重新审阅（导航边只提示，supports/input 才是正式依赖）。"""
    return provenance.impact(ctx.app.storage, changed_ids)


@op("provenance.lineage")
def lineage(ctx: Ctx, target_ids: Annotated[list[str], Field(description="要追溯的记录（MEM-…）或结论（CLM-…）")]) -> dict:
    """沿依据追到可访问的原件身份（来源数不等于独立支持数）。"""
    return provenance.lineage(ctx.app.storage, target_ids)


@op("provenance.question_validity")
def question_validity(ctx: Ctx, question_id: str, scope: str | None = None) -> dict:
    """已解决问题的答案依据现在是否仍成立。"""
    return provenance.question_validity(ctx.app.storage, question_id, scope=scope)


@op("provenance.formal")
def formal(ctx: Ctx, target_ids: Annotated[list[str], Field(description="结论（CLM-…）或包含结论的记录（MEM-…）")],
           scope: Annotated[str, Field(min_length=1, description="正式使用的范围，只返回在该范围内已接受的结论")]) -> dict:
    """正式使用：只返回在该范围内已接受、有效、来源可见的结论原文，其余列出原因。"""
    return ctx.app.review.graph().formal_projection(target_ids, scope)


# ---------- 关联与关系图 ----------
GraphCategory = Literal["structure", "evidence", "reference", "navigation", "concept", "claim_relation"]
NodeType = Literal["owner", "record", "block", "claim", "concept", "source"]


@op("relations.graph")
def graph(ctx: Ctx, center: str | None = None, hops: int = 2, query: str = "", kinds: list[str] | None = None,
          levels: list[str] | None = None, excluded: list[str] | None = None, limit: int = 400,
          categories: list[GraphCategory] | None = None, domains: list[str] | None = None,
          excluded_domains: list[str] | None = None, node_types: list[NodeType] | None = None) -> dict:
    """材料关系图（子图）：对象、记录、块、结论、概念、来源，以及按类别区分的边（来自图投影）。
    categories 只看某几类边；domains / excluded_domains 按领域筛选或屏蔽概念。"""
    return relations.subgraph(relations.build_graph(ctx.app.storage, ctx.app.index), center=center, hops=hops,
                              query=query, kinds=kinds, levels=levels, excluded=excluded, limit=limit,
                              categories=categories, domains=domains, excluded_domains=excluded_domains,
                              node_types=node_types)


@op("relations.similar")
def similar(ctx: Ctx, seeds: Annotated[list[str], Field(description="种子记录 ID（MEM-…）")],
            method: Annotated[Literal["keyword", "semantic"], Field(description=(
                "keyword=关键词重合；semantic=向量近邻（需要嵌入模型）"))] = "keyword", limit: int = 30) -> dict:
    """为种子记录找关联候选（关键词重合 / 语义近邻）；只是待判断的线索。"""
    return relations.similar(ctx.app.storage, ctx.app.index, seeds, method=method, limit=limit)


@op("relations.associations")
def association_list(ctx: Ctx, status: Annotated[Literal["candidate", "accepted", "rejected", "withdrawn"] | None,
                                               Field(description="只看该状态")] = None,
                     record_id: Annotated[str | None, Field(description="只看一端是该记录的关联")] = None) -> list:
    """已保存的关联记录。"""
    return associations.list_associations(ctx.app.storage, status=status, record_id=record_id)


@op("relations.decide", effect="write")
def association_decide(ctx: Ctx, owner_id: Annotated[str, Field(description="关联记录存放的对象 ID")],
        payload: shapes.shaped(dict, shapes.ASSOCIATION_PAYLOAD, (
            "{from, to, relation, explanation, shared_structure, transfer_limits[], basis_refs[], status, "
            "certainty?(speculative|plausible|supported), origin?, observed_in?}；"
            "快速添加只给 {from, to, explanation}：自动设为 related_to + speculative + accepted。"
            "from/to/basis_refs 都是固定引用对象 {target_kind, target_id, revision, sha256, locator, relation}，不能只写 ID"),
            examples=[{"from": _FROM, "to": _TO, "explanation": "两者都在讨论抵消输入下的求和误差，可以对照着读"}]),
        reason: Annotated[str, Field(description="为什么做这个判断（写进记录的 record_reason）")],
        dry_run: Annotated[bool, Field(description="true 只预检、不写入")] = False,
        discovery: Annotated[Literal["workspace_summary", "owner_only"] | None, Field(description=(
            "可见范围；不给时新关联默认全库可见、修订沿用原值。只有需要保密时才写 owner_only"))] = None) -> dict:
    """保存关联判断（candidate/accepted/rejected/withdrawn）；只能是导航关系。
    "A 会影响 B"这类可以验证的判断写成结论（inference），不要写成关联。"""
    return activity.annotate(ctx.app.storage, associations.decide(ctx.app.memory, owner_id, payload, actor=ctx.actor,
                                                                  reason=reason, dry_run=dry_run, discovery=discovery))


@op("relations.candidates")
def association_candidates(ctx: Ctx, record_id: Annotated[str | None, Field(description="只看涉及该记录的线索")] = None,
                           owner_id: Annotated[str | None, Field(description="只看该对象内的线索")] = None,
                           kinds: Annotated[list[str] | None, Field(description=(
                               "线索类型：concept_mention、similar、external:*"))] = None, limit: int = 50) -> dict:
    """待判断的线索：正文提到概念却没链接（concept_mention）、向量近邻（similar）、外部工具回流（external:*）。
    已判断过的端点对会被过滤。只读。"""
    return relations.candidates(ctx.app.storage, ctx.app.index, record_id=record_id, owner_id=owner_id,
                                kinds=kinds, limit=limit)


@op("relations.view")
def view_get(ctx: Ctx) -> dict:
    """工作台关系图的本机视图偏好。"""
    return ctx.app.prefs.view()


@op("relations.view_save", effect="user")
def view_save(ctx: Ctx, view: dict) -> dict:
    """保存关系图视图偏好（布局、分组、中心、排除项）。"""
    return ctx.app.prefs.save_view(view)


NoteStatus = Literal["open", "queued", "proposed", "needs_user", "handled", "done", "dismissed"]


@op("relations.notes")
def notes(ctx: Ctx, status: Annotated[list[NoteStatus] | None, Field(description=(
              "只看这些状态；AI 处理用户交给你的笔记时用 ['queued']"))] = None,
          owner_id: Annotated[str | None, Field(description="只看引用了该对象或其记录的笔记")] = None) -> list:
    """工作台笔记（R15）：用户在关系图旁写下的分组名、桥接问题、关系设想。queued 表示用户交给 AI 处理，
    用 relations_note_reply 回写方案、提问或说明不处理。笔记不是研究记录。"""
    return ctx.app.notes.items(status=status, owner_id=owner_id)


@op("relations.note_add", effect="user")
def note_add(ctx: Ctx, kind: Literal["cluster-name", "bridge-question", "relation-idea"], title: str, text: str = "",
             refs: list[str] | None = None) -> dict:
    """（仅用户）新建工作台笔记（草稿）。"""
    return ctx.app.notes.add(kind, title, text, refs or [])


@op("relations.note_resolve", effect="user")
def note_resolve(ctx: Ctx, note_id: str, status: Literal["open", "queued", "done", "dismissed"]) -> dict:
    """（仅用户）交给 AI（queued）、收回或重新打开（open）、完成（无需 AI）、放弃。"""
    return ctx.app.notes.resolve(note_id, status)


@op("relations.note_edit", effect="user")
def note_edit(ctx: Ctx, note_id: str, kind: Literal["cluster-name", "bridge-question", "relation-idea"] | None = None,
              title: str | None = None, text: str | None = None) -> dict:
    """（仅用户）修改笔记内容。已处理的先重新打开；待你确认的方案会作废，笔记退回 AI。"""
    return ctx.app.notes.edit(note_id, kind=kind, title=title, text=text)


@op("relations.note_answer", effect="user")
def note_answer(ctx: Ctx, note_id: str, text: str) -> dict:
    """（仅用户）回答 AI 在笔记上的提问；笔记回到 queued。"""
    return ctx.app.notes.answer(note_id, text)


@op("relations.note_reply", effect="write")
def note_reply(ctx: Ctx, note_id: Annotated[str, Field(description="queued 状态的笔记 ID")], outcome: Annotated[Literal["proposal", "question", "declined"], Field(description=(
                   "proposal：写入方案（先预演，用户在工作台执行后才写入）；question：向用户提问；declined：说明不需要或不能写入"))],
               message: Annotated[str, Field(description="给用户看的一句说明、问题或理由")],
               steps: shapes.shaped(list[dict[str, Any]] | None, shapes.STEPS, (
                   "仅 proposal：最多 10 步，每步 {op:'memory_commit', owner_id, operations, expected_head} 或 "
                   "{op:'relations_decide', owner_id, payload, reason?}；参数与对应工具相同。同一对象只提交一次；"
                   "服务端逐步 dry_run 预检并生成预览，不会写入"), nullable=True) = None) -> dict:
    """回写用户交给你的工作台笔记（只能作用于 queued 的笔记）。写入方案须由用户在工作台执行，不能替用户确认或执行。"""
    return ctx.app.notes.reply(note_id, outcome, message, steps, actor=ctx.actor)


@op("relations.note_apply", effect="user")
def note_apply(ctx: Ctx, note_id: str, proposal_id: str) -> dict:
    """（仅用户）执行笔记上的 AI 方案：核对依据未变后按预览原样写入；依据变化或某步失败时退回 AI。"""
    return ctx.app.notes.apply(note_id, proposal_id)


@op("relations.note_reject", effect="user")
def note_reject(ctx: Ctx, note_id: str, proposal_id: str, comment: str = "") -> dict:
    """（仅用户）退回 AI 方案并附意见；笔记回到 queued。"""
    return ctx.app.notes.reject(note_id, proposal_id, comment)


@op("relations.note_clear", effect="user")
def note_clear(ctx: Ctx) -> dict:
    """（仅用户）删除已处理、已完成和已放弃的笔记。编号不复用。"""
    return ctx.app.notes.clear()


# ---------- 语义维护 ----------
@op("maintenance.plan", effect="write")
def maintenance_plan(ctx: Ctx, changed_ids: Annotated[list[str], Field(description=(
                         "变化了的来源（SRC-…）、记录（MEM-…）或结论（CLM-…）"))],
                     note: Annotated[str, Field(description="给用户看的一句说明（为什么要维护）")] = "",
                     targets: Annotated[list[str] | None, Field(description="只把这些受影响的记录放进计划；省略时放入全部")] = None) -> dict:
    """来源变化或新反证后：列出受影响记录及其完整正文，生成维护计划。"""
    return ctx.app.maintenance.plan(changed_ids, actor=ctx.actor, note=note, targets=targets)


@op("maintenance.plan_from_candidates", effect="user")
def maintenance_plan_from_candidates(ctx: Ctx, target_ids: list[str], note: str = "") -> dict:
    """（仅用户）为所选维护候选生成计划：变化来源取自候选记下的影响路径，计划只含所选记录；候选标为 planned。"""
    candidates = {c["target_id"]: c for c in ctx.app.monitor.candidates(None)}
    missing = [t for t in target_ids if t not in candidates]
    if not target_ids or missing:
        raise BurrowlError("INVALID_ARGUMENT", "请选择现有的维护候选", {"missing": missing})
    # 影响路径的第一项就是变化的来源（provenance_impact 从 changed_ids 出发）
    changed = sorted({path[0] for t in target_ids for path in candidates[t].get("reasons", []) if path})
    if not changed:
        raise BurrowlError("INVALID_ARGUMENT", "所选候选没有记下变化来源，请用 maintenance_plan 手动指定", {"target_ids": target_ids})
    # 结论落到包含它的记录上修订；L0 原件记录等不可修订的不进计划（R16 D-01）
    described = ctx.app.maintenance.describe(target_ids)
    records = sorted({d["record_id"] for d in described.values() if d["editable"]})
    if not records:
        raise BurrowlError("INVALID_ARGUMENT", "所选候选都不能修订（例如 L0 原件记录）；确认后点“无需处理”即可",
                           {"target_ids": target_ids})
    plan = ctx.app.maintenance.plan(changed, actor=ctx.actor, note=note or "由维护候选生成", targets=records)
    for target in target_ids:
        if described[target]["editable"]:  # 不可修订的候选留在 open，由用户确认无需处理
            ctx.app.monitor.resolve(target, "planned", plan["plan_id"])
    return plan


@op("maintenance.visibility_candidates")
def maintenance_visibility_candidates(ctx: Ctx) -> list:
    """R19 之前按旧默认存成 owner_only 的知识记录（L1–L4、已采纳的关联、概念页）。对象策略显式要求保密的对象不列出。"""
    return ctx.app.maintenance.visibility_candidates()


@op("maintenance.plan_visibility", effect="user")
def maintenance_plan_visibility(ctx: Ctx, target_ids: list[str], note: str = "") -> dict:
    """（仅用户）为勾选的记录生成可见范围补齐计划：处置已写好（修订为全库可见，正文不变），预览后执行。"""
    return ctx.app.maintenance.plan_visibility(target_ids, actor=ctx.actor, note=note)


@op("maintenance.plans")
def maintenance_plans(ctx: Ctx, status: Annotated[
        list[Literal["planned", "queued", "reviewed", "applied", "partial"]] | None,
        Field(description="只看这些状态；AI 处理用户交给你的计划时用 ['queued']")] = None) -> list:
    """维护计划概况（最近有变化的在前）。queued 表示用户交给 AI 起草处置。"""
    return ctx.app.maintenance.plans(status)


@op("maintenance.handoff", effect="user")
def maintenance_handoff(ctx: Ctx, plan_id: str, note: str = "") -> dict:
    """（仅用户）把计划交给 AI 起草处置，或把待确认 / 部分完成的计划退回 AI（附意见）。"""
    return ctx.app.maintenance.handoff(plan_id, note, actor=ctx.actor)


@op("maintenance.review", effect="write")
def maintenance_review(ctx: Ctx, plan_id: Annotated[str, Field(description="维护计划 ID")],
        decisions: shapes.shaped(list[dict], shapes.DECISIONS, (
            "[{target_id, action: retain|revise|resynthesize|retract|defer, reason, draft?}]；"
            "revise / resynthesize 必须附完整的新草案 draft（owner_id 可省略，change_reason 由计划补上）")),
        expected_revision: Annotated[int | None, Field(description="maintenance_status 返回的计划修订号")] = None,
        message: Annotated[str, Field(description="给用户看的一句说明（改了什么、为什么）")] = "") -> dict:
    """记录逐项处置与修订草案，并逐对象预检（不写记忆）。之后由用户在工作台预览并执行。"""
    return ctx.app.maintenance.review(plan_id, decisions, expected_revision=expected_revision, actor=ctx.actor,
                                      message=message)


@op("maintenance.apply", effect="user")
def maintenance_apply(ctx: Ctx, plan_id: str, expected_revision: int) -> dict:
    """（仅用户）执行已写好处置的计划：按对象逐个提交修订与撤回；可重试，部分完成会如实报告。"""
    return activity.annotate(ctx.app.storage, ctx.app.maintenance.apply(plan_id, actor=ctx.actor, expected_revision=expected_revision))


@op("maintenance.status")
def maintenance_status(ctx: Ctx, plan_id: Annotated[str, Field(description="维护计划 ID")]) -> dict:
    """维护计划的当前状态。"""
    return ctx.app.maintenance.status(plan_id)


# ---------- 监测 ----------
@op("monitor.run", effect="write")
def monitor_run(ctx: Ctx, dry_run: Annotated[bool, Field(description="只比较，不写观察记录、不更新基线")] = False) -> dict:
    """扫描一次：对象 HEAD 与来源文件指纹的变化，并给出维护候选（观察不改变任何证据或复核状态）。"""
    return ctx.app.monitor.run(dry_run=dry_run)


@op("monitor.candidates")
def monitor_candidates(ctx: Ctx, status: Annotated[Literal["open", "planned", "dismissed"] | None, Field(description=(
        "open 待处理（默认）；planned 已放进计划；dismissed 已确认无需处理；null 列出全部"))] = "open") -> list:
    """来源变化影响到的维护候选；用 maintenance_plan 把它们纳入语义维护。
    每项附 kind、title、record_id（结论落到包含它的记录）与 editable（能否放进计划修订）。"""
    items = ctx.app.monitor.candidates(status)
    described = ctx.app.maintenance.describe([c["target_id"] for c in items])
    return [{**c, **described[c["target_id"]]} for c in items]


@op("monitor.resolve", effect="user")
def monitor_resolve(ctx: Ctx, target_id: str, status: Literal["open", "planned", "dismissed"], note: str = "") -> dict:
    """（仅用户）处置维护候选。"""
    return ctx.app.monitor.resolve(target_id, status, note)


@op("monitor.observations")
def monitor_observations(ctx: Ctx, limit: int = 50) -> list:
    """最近的监测观察记录。"""
    return ctx.app.monitor.observations(limit)


# ---------- Run ----------
@op("runs.create", effect="write")
def run_create(ctx: Ctx, title: Annotated[str, Field(description="这次执行做什么，一句话")],
               owner_id: Annotated[str | None, Field(description="上级对象 ID；不给时是独立 Run")] = None,
               question: Annotated[str, Field(description="这次执行要回答的问题")] = "",
               keywords: Annotated[list[str] | None, Field(description="查找 Run 用的关键词")] = None) -> dict:
    """新建 Run（一次实际执行的证据：输入、程序、输出与失败）。给 owner_id 时放在该对象目录下，否则是独立 Run。

    Run 不保存记忆记录、不进检索；执行得到的知识写进上级对象或合适的知识对象的记录，并引用该 Run。
    一次性小任务默认不建 Run，只有执行结果要被记录引用时才建；没有知识的 Run 用 runs_discard 丢弃。"""
    return ctx.app.runs.create(title, owner_id=owner_id, question=question, keywords=keywords)


@op("runs.register", effect="write")
def run_register(ctx: Ctx, run_id: Annotated[str, Field(description="Run ID（RUN-…）")],
                 inputs: Annotated[list[str] | None, Field(description="输入文件：数据目录内相对路径或 SRC- ID")] = None,
                 artifacts: Annotated[list[str] | None, Field(description="产物文件：数据目录内相对路径或 SRC- ID")] = None,
                 preview: Annotated[bool, Field(description="true 只预览登记结果、不写入")] = False) -> dict:
    """登记已有文件为 Run 的输入/产物（固定字节指纹，可回滚）。"""
    return ctx.app.runs.register(run_id, inputs=inputs or [], artifacts=artifacts or [], preview=preview)


@op("runs.execute", effect="write")
def run_execute(ctx: Ctx, run_id: Annotated[str, Field(description="Run ID（RUN-…）")],
                script: Annotated[str, Field(description="要运行的 Python 脚本路径")],
                inputs: Annotated[list[str] | None, Field(description="输入文件：数据目录内相对路径或 SRC- ID")] = None,
                args: Annotated[list[str] | None, Field(description="脚本参数，可用 {input0}、{output_dir} 占位")] = None,
                timeout_seconds: Annotated[float, Field(description="超时秒数")] = 3600,
                preview: Annotated[bool, Field(description="true 只预览命令与登记、不执行")] = False) -> dict:
    """前台运行 Python 脚本并登记输入、输出与日志（失败也登记）。参数可用 {input0}、{output_dir} 占位。"""
    result = ctx.app.runs.execute(run_id, script=script, inputs=inputs or [], args=args or [],
                                  timeout_seconds=timeout_seconds, preview=preview)
    return activity.with_activity(result, activity.run_lines(ctx.app.storage, run_id))


@op("runs.update", effect="write")
def run_update(ctx: Ctx, run_id: Annotated[str, Field(description="Run ID（RUN-…）")],
               fields: shapes.shaped(dict, shapes.RUN_FIELDS, (
                   "只写要改的字段：title, question, conclusion, limitations, parameters, status, keywords, parent_run_ids。"
                   "conclusion 只写执行摘要（成功或失败、产物、异常），知识写成记忆记录并引用本 Run")),
               expected_sha256: Annotated[str | None, Field(description=(
                   "读取时 run.json 的指纹（上次修改返回的 sha256）；不一致说明别人改过，拒绝覆盖"))] = None) -> dict:
    """修改 Run 的执行摘要、限制、状态等（不改输入/产物登记）。"""
    result = ctx.app.runs.update(run_id, fields, expected_sha256=expected_sha256)
    changed = result.get("status") == "updated" and "status" in fields
    return activity.with_activity(result, activity.run_lines(ctx.app.storage, run_id) if changed else [])


@op("runs.rollback", effect="write")
def run_rollback(ctx: Ctx, receipt: str, preview: bool = False) -> dict:
    """按登记回执恢复 run.json（不删除任何文件）。"""
    return ctx.app.runs.rollback(receipt, preview=preview)


@op("runs.search")
def run_search(ctx: Ctx, query: Annotated[str, Field(description="按标题、问题、关键词匹配；空串列出全部")] = "",
               owner_id: Annotated[str | None, Field(description="只列该对象目录下的 Run")] = None, limit: int = 50,
               unfixed_only: Annotated[bool, Field(description=(
                   "只列没有被任何 L1–L4 记录引用的 Run（执行知识尚未写成记录）"))] = False) -> list:
    """按标题/问题/关键词查找 Run。Run 不进检索与阅读，这是显式查找 Run 的入口；给 owner_id 时只列该对象目录下的 Run。"""
    return ctx.app.runs.search(query=query, owner_id=owner_id, limit=limit, unfixed_only=unfixed_only)


@op("runs.discard", effect="write")
def run_discard(ctx: Ctx, run_id: Annotated[str, Field(description="Run ID（RUN-…）")], reason: Annotated[str, Field(description=(
        "为什么没有可写成记录的内容，例如：临时测试，沉淀检查无可复用的方法、边界或失败教训"))]) -> dict:
    """丢弃没有知识的 Run：当前没有任何记录引用时，把整个 Run 目录移入回收站（可用 runs_restore 恢复）。

    失败本身有教训时不要丢弃，写成记录并引用它。被记录引用的 Run 不能丢弃。"""
    return ctx.app.runs.discard(run_id, reason=reason, actor=ctx.actor)


@op("runs.trash")
def run_trash(ctx: Ctx) -> list:
    """回收站中已丢弃的 Run（原位置、理由、时间、执行者）。"""
    return ctx.app.runs.trash()


@op("runs.restore", effect="write")
def run_restore(ctx: Ctx, run_id: str) -> dict:
    """把回收站中的 Run 移回原位置（原位置已被占用时拒绝）。"""
    return ctx.app.runs.restore(run_id)


@op("runs.empty_trash", effect="user")
def run_empty_trash(ctx: Ctx) -> dict:
    """永久删除回收站中的全部 Run。用户专属，只在工作台与 CLI 提供。"""
    return ctx.app.runs.empty_trash()


# ---------- 来源登记 ----------
@op("sources.list")
def sources_list(ctx: Ctx) -> list:
    """已登记的来源文件（file 引用的唯一授权入口），附当前解析出的位置 location。"""
    return ctx.app.storage.sources.describe()


@op("sources.roots")
def sources_roots(ctx: Ctx) -> dict:
    """已登记的工作根（工作目录）与本次会话的工作目录。工作根下的来源记为"根 + 相对路径"，目录搬家只改根。"""
    return {"roots": ctx.app.storage.sources.roots(),
            "work_dir": str(ctx.app.work_dir) if ctx.app.work_dir else None}


@op("sources.add_root", effect="write")
def sources_add_root(ctx: Ctx, path: Annotated[str, Field(description="工作目录的绝对路径（不能在数据目录内）")],
                     title: Annotated[str, Field(description="给用户看的名称")] = "") -> dict:
    """登记一个工作根。通常不必手动调用：登记本次工作目录里的文件时会自动建根。"""
    return ctx.app.storage.sources.add_root(path, title=title)


@op("sources.relocate_root", effect="write")
def sources_relocate_root(ctx: Ctx, root_id: Annotated[str, Field(description="sources_roots 返回的根 ID")],
                          new_path: Annotated[str, Field(description="新位置的绝对路径")],
                          allow_missing: Annotated[bool, Field(description=(
                              "true 时允许新位置缺少部分已登记文件（缺的会列出）"))] = False) -> dict:
    """工作目录整体搬家：逐个核对新位置下已登记文件的指纹，全部一致才改根的位置；已有固定引用不变。"""
    return ctx.app.storage.sources.relocate_root(root_id, new_path, allow_missing=allow_missing)


Sensitivity = Literal["public", "internal", "confidential", "restricted"]


def _check_owner_access(ctx: Ctx, path: str) -> str:
    """数据目录内的文件若属于受限对象，不能被登记成可读来源。返回规范化后的路径。"""
    normalized = ctx.app.storage.sources.normalize(path)
    relative = PurePosixPath(normalized)
    if relative.parts and not relative.is_absolute() and ":" not in relative.parts[0]:
        ctx.app.storage.owners.scan().require_path_readable(relative.as_posix())
    return normalized  # 数据目录外的原件不属于任何对象


@op("sources.register", effect="write")
def sources_register(ctx: Ctx, source_id: Annotated[str, Field(pattern=r"^SRC-", description=(
        "自定的来源 ID，SRC- 开头，如 SRC-SUMMATION-R01；内容变了用新 ID"))],
                     path: Annotated[str, Field(description="工作目录等外部位置用绝对路径；数据目录内可用相对路径")],
                     title: Annotated[str, Field(description="给人看的名称")] = "",
                     sensitivity: Annotated[Sensitivity, Field(description=(
                         "restricted 的来源不进索引、不能被引用"))] = "internal") -> dict:
    """登记一个来源文件并记录当前字节指纹：L0 原件的唯一授权入口。原件留在原处，不复制、不修改。
    工作目录里的文件自动记为"工作根 + 相对路径"，迁移时随根重定向。
    同一 ID 重复登记同一文件是幂等的；内容变了请用新 ID；文件搬家用 sources_relocate / sources_relocate_root。"""
    path = _check_owner_access(ctx, path)
    entry = ctx.app.storage.sources.register(source_id, path, title=title, sensitivity=sensitivity,
                                             work_dir=ctx.app.work_dir)
    if sensitivity == "restricted":  # 重复登记可能把已被引用的来源提升为 restricted，同样要从投影中隐去依赖者
        ctx.app.index.on_access_change([source_id])
    return entry


@op("sources.register_many", effect="write")
def sources_register_many(ctx: Ctx, items: shapes.shaped(list[dict], shapes.SOURCE_ITEMS, (
        "[{source_id, path, title?, sensitivity?}]，逐条登记；单条失败不影响其他"))) -> dict:
    """批量登记来源（导入已有资料时使用）。"""
    results = []
    for item in items:
        try:
            path = _check_owner_access(ctx, item["path"])
            entry = ctx.app.storage.sources.register(item["source_id"], path, title=item.get("title", ""),
                                                     sensitivity=item.get("sensitivity", "internal"),
                                                     work_dir=ctx.app.work_dir)
            results.append({"source_id": item["source_id"], "ok": True, "sha256": entry["sha256"]})
        except (BurrowlError, KeyError, OSError) as exc:
            results.append({"source_id": item.get("source_id"), "ok": False,
                            "error": exc.problem() if isinstance(exc, BurrowlError) else str(exc)})
    restricted = [i["source_id"] for i, r in zip(items, results, strict=True) if r["ok"] and i.get("sensitivity") == "restricted"]
    if restricted:  # 同 sources_register：提升为 restricted 的来源要从投影中隐去依赖者
        ctx.app.index.on_access_change(restricted)
    return {"succeeded": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
            "results": results}


@op("sources.relocate", effect="write")
def sources_relocate(ctx: Ctx, source_id: Annotated[str, Field(description="已登记的来源 ID（SRC-…）")],
                     new_path: Annotated[str, Field(description="新位置的绝对路径")]) -> dict:
    """原件搬家：新位置的字节必须与登记时一致，旧路径记入 relocated_from，已有固定引用继续有效。"""
    new_path = _check_owner_access(ctx, new_path)
    return ctx.app.storage.sources.relocate(source_id, new_path, work_dir=ctx.app.work_dir)


@op("sources.update", effect="user")
def sources_update(ctx: Ctx, source_id: str, enabled: bool | None = None, sensitivity: Sensitivity | None = None) -> dict:
    """（仅用户）启用/停用来源或调整敏感级别。停用或设为 restricted 后，读取被拒的记录在检索和关系图中也同时隐去。"""
    entry = ctx.app.storage.sources.update(source_id, enabled=enabled, sensitivity=sensitivity)
    # 投影不会因来源登记变化而过期，必须在这里重新投影依赖它的对象，否则检索和关系图仍显示被隐去的记录
    return {**entry, "reindexed_owners": ctx.app.index.on_access_change([source_id])}


