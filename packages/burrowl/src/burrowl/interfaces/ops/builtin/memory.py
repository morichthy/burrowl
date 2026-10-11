"""记忆：对象、记录、提交、文稿、时间线、原始材料、续接、导出、巩固、总结。"""
import uuid
from typing import Annotated, Any, Literal

from pydantic import Field

from ....core import contract as record_contract
from ....core.errors import BurrowlError
from ....services import activity, routing, workflow
from ....services.memory import consolidation, documents, export, history, raw_materials, structure
from ....storage import recovery
from ..registry import Ctx, op

OwnerId = Annotated[str, Field(description="对象 ID，如 RES-…、PRJ-…、RUN-…")]
DocType = Literal["research_process", "research_report"]


@op("memory.owners")
def owners(ctx: Ctx, owner_types: list[str] | None = None, query: str = "") -> dict:
    """列出可读对象（按类型分组，带各层记录数）。"""
    return structure.owners(ctx.app.storage, owner_types=owner_types, query=query)


@op("memory.owner_route")
def owner_route(ctx: Ctx,
                task: Annotated[str, Field(description="一两句话写清本次任务的目标和交付物，保留型号与数值原文")],
                variants: Annotated[list[dict] | None, Field(description=(
                    "最多 2 条等义改写 [{language: zh|en, question, lexical_terms?}]，与 reading_start 相同"))] = None,
                protected_terms: Annotated[list[str] | None, Field(description="必须原样出现的词（型号、编号）")] = None,
                paths: Annotated[list[str] | None, Field(description="本次任务涉及的文件：绝对路径或 SRC- ID")] = None,
                owner_hint: Annotated[str | None, Field(description="用户指定的对象 ID")] = None,
                limit: Annotated[int, Field(ge=1, le=10)] = 5) -> dict:
    """归属判定（只读）：与 reading_start 第一轮相同的召回和筛选包，附上候选的当前目标、最新检查点与来源重叠证据。

    只在需要读历史或需要保存时调用（简单问答、无复用价值的小任务不调用）。不给建议动作，由你判断：
    选出暂定对象后用 memory_resume 完整阅读，确认是同一目标再第一次保存（memory_commit 带 attribution）；
    不相关就换下一个候选或新建。gaps 有索引缺口时，不要因为"没有候选"就新建对象。不建立阅读会话。"""
    return routing.route(ctx.app.storage, ctx.app.index, ctx.app.reading, task=task, variants=variants,
                         protected_terms=protected_terms, paths=paths, owner_hint=owner_hint, limit=limit)


@op("memory.owner_create", effect="write")
def owner_create(ctx: Ctx, owner_type: Literal["research", "project", "core-algorithm", "data", "tool", "report",
                                               "knowledge"],
                 slug: Annotated[str, Field(description="小写字母/数字/连字符")], title: str,
                 description: str = "", keywords: list[str] | None = None,
                 source_document: Annotated[str | None, Field(description=(
                     "core-algorithm 必填：定义该算法的公司算法/模块文档（路径或文档编号）"))] = None,
                 short_name: Annotated[str | None, Field(description=(
                     "可选的可读短名，供 [[@短名/handle]] 链接其他对象的记录"))] = None,
                 sensitivity: Literal["public", "internal", "confidential", "restricted"] = "internal") -> dict:
    """新建对象（写入业务卡；首次提交记忆时才建立记忆目录）。Run 用 runs.create 建立。

    先用 memory.owners 查找：同一目标的继续或子任务沿用已有对象；主题相似只引用；独立交付目标才新建。
    core-algorithm 只用于公司文档定义的关键算法，普通脚本/模块/工具不要建算法卡。
    knowledge 有两种用途：配置 graph.concept_owner_id 指定的那一个是概念库（只放概念页，建好后把 owner_id 填进该配置）；
    其他 knowledge 对象是通用知识对象，承接不属于任何具体研究、项目、工具、数据的知识，按主题复用，主题不同再新建。"""
    fields = {"description": description, "keywords": keywords or [], "sensitivity": sensitivity}
    if source_document:
        fields["source_document"] = source_document
    if short_name:
        fields["short_name"] = short_name
    owner = ctx.app.storage.owners.create(owner_type, slug, title, fields)
    return activity.with_activity(_indexed(ctx, owner), [activity.owner_line(owner)])


@op("memory.owner_adopt", effect="write")
def owner_adopt(ctx: Ctx, path: str, expected_sha256: str) -> dict:
    """把内容树中已有的文档登记为对象（原件不修改；须给出你看到的字节指纹）。"""
    owner = ctx.app.storage.owners.adopt(path, expected_sha256, ctx.actor)
    return activity.with_activity(_indexed(ctx, owner), [activity.owner_line(owner, adopted=True)])


def _indexed(ctx: Ctx, owner) -> dict:
    """新对象立即建检索投影（只有业务卡时投影只含对象元数据）。

    否则它在 index.reconcile 或第一次提交之前一直是 not_indexed 缺口：每次召回都报缺口、阅读会话总是允许全文补偿，
    "判定 → 新建 → 开阅读"时也无从判断新对象是否就绪（R18 实施时发现）。索引失败不影响对象已建立，如实报告。"""
    try:
        ctx.app.index.on_commit(owner.owner_id, None)
        state = ctx.app.index.status(owner.owner_id)
    except Exception as exc:  # noqa: BLE001 - 业务卡已写入：保留业务成功，索引待补偿
        state = {"index_status": "pending", "details": {"reason": type(exc).__name__}}
    result = owner.summary() | {"index": state}
    if state["index_status"] not in {"indexed", "not_configured"}:
        result["warnings"] = ["对象已建立；检索索引待补偿（index.reconcile）"]
    return result


@op("memory.records")
def records(ctx: Ctx, owner_id: OwnerId, levels: list[str] | None = None, kinds: list[str] | None = None,
            query: str = "", main_only: bool = True, offset: int = 0, limit: int = 100) -> dict:
    """对象内的记录目录（按层级，分页；不含正文）。kinds 按类型筛选，query 匹配标题或 ID；kind_counts 是类型筛选前的计数。"""
    return structure.records(ctx.app.storage, owner_id, levels=levels, kinds=kinds, query=query, main_only=main_only,
                             offset=offset, limit=limit)


@op("memory.activity")
def activity_list(ctx: Ctx, owner_id: Annotated[str | None, Field(description="只看这个对象（含其 Run）")] = None,
                  kinds: Annotated[list[Literal["owner", "attribution", "save", "review", "run", "publish"]] | None,
                                   Field(description="只看这些类型；默认全部")] = None,
                  limit: Annotated[int, Field(ge=1, le=200)] = 50,
                  before: Annotated[str | None, Field(description="上一页返回的 next_before")] = None) -> dict:
    """关键操作记录（只读，按时间倒序）：建立对象、归属确认、保存、复核、Run 结束、发布，每条一行，带可追溯的引用。
    检索、阅读、来源登记等不改变资产的操作不在其中。"""
    return activity.ActivityLog(ctx.app.storage, ctx.app.hub.state).list(owner_id=owner_id, kinds=kinds,
                                                                       limit=limit, before=before)


@op("memory.kinds")
def kinds(ctx: Ctx) -> list:
    """全部记录类型：层级、版本、由哪个操作写入、一句话用途。"""
    return record_contract.kinds()


@op("memory.contract")
def contract(ctx: Ctx, kind: Annotated[str, Field(description="记录类型，如 detail、narrative、experience、overview、checkpoint")]) -> dict:
    """某类记录的草案契约：必填字段、payload 结构、写作要点和一份经校验的示例草案。写草案前先调用。"""
    return record_contract.contract(kind)


@op("memory.inspect")
def inspect(ctx: Ctx, owner_id: OwnerId, record_id: str | None = None, revision: int | None = None) -> dict:
    """读取对象的当前快照，或某条记录的当前/指定修订（含完整正文与固定引用）。"""
    return ctx.app.memory.inspect(owner_id, record_id=record_id, revision=revision)


@op("memory.commit", effect="write")
def commit(ctx: Ctx, owner_id: OwnerId,
           operations: Annotated[list[dict[str, Any]], Field(description=(
               "操作列表：新建 {op:'put_record', client_key, draft}；修订 {op:'put_record', record_id, "
               "expected_revision, draft}。draft 的字段与示例用 memory.contract(kind) 查询；批内引用可写 {client_key: 'x'}"))],
           expected_head: Annotated[str | None, Field(description="提交前看到的 HEAD commit_id；新对象为 null")],
           request_id: Annotated[str | None, Field(description="UUID；重试时用同一个保证幂等")] = None,
           dry_run: bool = False,
           attribution: Annotated[dict[str, Any] | None, Field(description=(
               "归属确认点（本任务第一次往该对象保存）时必填："
               "{action: continue|create, decided_by: user|ai, reason, rejected?: [{owner_id, reason}]}；"
               "rejected 写完整阅读后放弃的对象。之后的保存不再填写"))] = None) -> dict:
    """保存一批记录（同一对象内原子提交）。dry_run=true 只预检不写入。"""
    request = {"schema_version": 3, "request_id": request_id or str(uuid.uuid4()), "actor": ctx.actor,
               "owner_id": owner_id, "expected_head": expected_head, "operations": operations, "dry_run": dry_run}
    if attribution is not None:
        request["attribution"] = attribution
    return activity.annotate(ctx.app.storage, ctx.app.memory.commit(request))


@op("memory.document")
def document(ctx: Ctx, owner_id: OwnerId, view: Literal["full", "outline", "section", "impact"] = "full",
             document_type: DocType = "research_process", document_id: str | None = None,
             section_id: str | None = None, max_chars: int = 20000) -> dict:
    """读取研究文稿：完整文稿 / 目录 / 单章上下文包（改写某章用）/ 依据变化影响。"""
    selection = {"document_type": document_type, "document_id": document_id}
    if view == "full":
        return documents.build_document(ctx.app.storage, owner_id, **selection)
    if view == "outline":
        return documents.outline(ctx.app.storage, owner_id, **selection)
    if view == "impact":
        return documents.document_impact(ctx.app.storage, owner_id, **selection)
    if not section_id:
        raise BurrowlError("INVALID_ARGUMENT", "view=section 需要 section_id")
    return documents.section_context(ctx.app.storage, owner_id, section_id, max_chars=max_chars, **selection)


@op("memory.figure")
def figure(ctx: Ctx, owner_id: OwnerId, record_id: str, revision: int, index: int) -> dict:
    """技术单元登记的图片（data URL）。"""
    return documents.figure(ctx.app.storage, owner_id, record_id, revision, index)


@op("memory.timeline")
def timeline(ctx: Ctx, owner_id: OwnerId, offset: int = 0, limit: int = 50) -> dict:
    """研究时间线：全部记录的全部修订与关联 Run（不含正文）。"""
    return history.timeline(ctx.app.storage, owner_id, offset=offset, limit=limit)


@op("memory.raw_materials")
def raw_list(ctx: Ctx, owner_id: OwnerId) -> dict:
    """L0 原始材料清单（不读原件正文）。"""
    return raw_materials.listing(ctx.app.storage, owner_id)


@op("memory.raw_material")
def raw_read(ctx: Ctx, owner_id: OwnerId, material_id: str, max_chars: int = 20000) -> dict:
    """回读一个原始材料（按清单中的 ID；核对固定字节）。"""
    return raw_materials.read(ctx.app.storage, owner_id, material_id, max_chars=max_chars)


@op("memory.resume")
def resume(ctx: Ctx, owner_id: OwnerId) -> dict:
    """续接对象的工作上下文：目标→检查点→最新概览/经过/技术→路线/问题/经验（只读）。
    不建立阅读会话；用户查询主题相关知识、找已有经验时默认先 reading_start。
    workbench_notes 列出与该对象相关、尚未结束的工作台笔记；queued 的由你用 relations_note_reply 回写。"""
    result = workflow.resume(ctx.app.memory, owner_id)
    # R15 D-09：续接时带出用户交给 AI、或等待用户处理的笔记（笔记不是记录，不进材料包预算）
    result["workbench_notes"] = [
        {"id": n["id"], "status": n["status"], "kind": n["kind"], "title": n["title"], "text": n["text"],
         "thread": n["thread"][-3:]}
        for n in ctx.app.notes.items(status=["queued", "needs_user", "proposed"], owner_id=owner_id)]
    return result


@op("memory.export")
def export_(ctx: Ctx, owner_id: OwnerId, fmt: Literal["markdown", "json", "html"] = "markdown",
            record_ids: list[str] | None = None, exclude_ids: list[str] | None = None,
            include_raw_materials: bool = False) -> dict:
    """导出对象的记忆（不产生新记录、不复制原件）。"""
    return export.export(ctx.app.memory, owner_id, fmt=fmt, record_ids=record_ids, exclude_ids=exclude_ids,
                         include_raw_materials=include_raw_materials)


@op("memory.consolidate_prepare")
def consolidate_prepare(ctx: Ctx, owner_id: OwnerId, since_commit: str | None = None) -> dict:
    """阶段巩固的待处理清单：自上次巩固以来的变化、受影响下游与合并/冲突候选。"""
    return consolidation.prepare(ctx.app.memory, owner_id, since_commit=since_commit)


@op("memory.consolidate_apply", effect="write")
def consolidate_apply(ctx: Ctx, owner_id: OwnerId, basis: dict, decisions: list[dict], reason: str,
                      operations: list[dict] | None = None, dry_run: bool = False) -> dict:
    """保存巩固处置（retain/revise/retract/defer…），可与本批实际修订一起提交。"""
    return activity.annotate(ctx.app.storage, consolidation.apply(ctx.app.memory, owner_id, basis, decisions, actor=ctx.actor, reason=reason,
                               operations=operations, dry_run=dry_run))


@op("memory.summary_materials")
def summary_materials(ctx: Ctx, owner_ids: list[str], exclude_ids: list[str] | None = None) -> dict:
    """跨对象总结的固定材料包（不生成总结）。"""
    return workflow.summary_materials(ctx.app.memory, owner_ids, exclude_ids=exclude_ids)


@op("memory.summary_save", effect="write")
def summary_save(ctx: Ctx, owner_id: OwnerId, draft: dict, basis_heads: dict[str, str | None],
                 record_id: str | None = None, expected_revision: int | None = None, dry_run: bool = False) -> dict:
    """把写好的总结保存为本对象的 L3/L4；依据对象有新提交则拒绝。"""
    return activity.annotate(ctx.app.storage, workflow.save_summary(ctx.app.memory, owner_id, draft, actor=ctx.actor, basis_heads=basis_heads,
                                 record_id=record_id, expected_revision=expected_revision, dry_run=dry_run))


@op("memory.feedback", effect="write")
def feedback(ctx: Ctx, owner_id: OwnerId, query_id: str,
             target: Annotated[dict, Field(description="被反馈的固定引用对象（检索命中的 ref），不能只写 ID")],
             label: Literal["missing", "irrelevant", "invalid_analogy", "lost_boundary", "stale", "adopted", "outcome"],
             note: str = "", adopted_in: dict | None = None,
             request_id: Annotated[str | None, Field(description="UUID；重试时沿用同一个，不会重复记录")] = None) -> dict:
    """记录检索结果的使用反馈（采用次数不是复核）。label=outcome 时 adopted_in 须指向实际应用它的 Run 或记录。"""
    return export.feedback(ctx.app.memory, owner_id, query_id=query_id, target=target, label=label, note=note,
                           adopted_in=adopted_in, actor=ctx.actor, request_id=request_id)


@op("memory.recovery", effect="admin")
def recovery_inspect(ctx: Ctx, owner_id: OwnerId) -> dict:
    """检查记忆目录健康状况（写锁、孤立提交、暂存残留）；只报告不修改。"""
    return recovery.inspect(ctx.app.storage.store, ctx.app.storage.owners.scan().get(owner_id))
