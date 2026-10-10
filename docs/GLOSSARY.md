# 术语表

本表是主要术语的**唯一权威短定义**。其他文档首次使用时链接到这里，不另造定义。

每个条目包含三部分：

- 定义：它是什么。
- 不是什么：容易混淆的地方。
- 代码标识：代码与工具中使用的英文名。

字段的完整约束以 `memory.contract(kind)` 和 [配置参考](reference/config.md) 为准。

## 对象与记录

| 术语 | 定义 | 不是什么 / 注意 | 代码标识 |
|---|---|---|---|
| 数据目录 | 存放内容树、记忆和程序状态的文件夹，由用户配置，在代码仓库之外 | 不是代码仓库 | `--data-dir`、`BURROWL_DATA_DIR` |
| Owner（归属对象） | 可以独立拥有记忆记录的稳定身份。类型有 research、project、core-algorithm、knowledge、data、tool、report。一条记录只属于一个 Owner。Run 也有业务卡和编号，但它是执行证据，不保存记录，不参与对象扫描（R14） | 不是记录，也不是可信等级；目录名不决定它 | `owner_id`，如 `RES-…`、`PRJ-…`、`ALG-…`、`RUN-…` |
| 业务卡 | 描述 Owner 的 JSON 文件，如 research.json、run.json | 不是记忆记录；记忆在 `memory_home` 中 | `research.json` 等 |
| 核心算法 | 公司算法/模块文档定义的关键模型算法 | 不是普通脚本、软件模块或通用工具。建立时必须给出 `source_document` | `core-algorithm` |
| Run | 一次真实执行（分析、仿真、训练、报告生成）的证据：输入版本、程序、产物、日志、成功或失败。放在上级对象目录的 `runs/` 下（对象内 Run）或顶层 `runs/` 下（独立 Run），上级按目录位置判断 | 不保存记忆记录、不进检索；执行得到的知识写进引用它的记录；业务卡的 `conclusion` 只是执行摘要。没有执行就不建 Run，没有知识的 Run 丢弃到回收站 | `RUN-…`，`runs.*` |
| 记录（record） | 记忆中的一条带版本的 JSON 内容，有类型（kind）、正文和来源 | 不是原始文件 | `MEM-…` |
| 外壳 / payload | 外壳是所有记录共用的部分：身份、所属对象、类型与层级、标题、正文、来源、修订信息。payload 是随类型变化的结构化字段，供程序读取和校验，例如技术块、适用条件、结论、关联端点 | payload 不会被拼回正文；给人读的内容写在正文（L1 写在 blocks） | `payload`；字段见 `memory.contract(kind)` |
| L0–L4 | 记录的内容层级：L0 原始依据、L1 技术单元、L2 经过、L3 可复用认识、L4 概览。表示内容职责和默认阅读粒度 | 不是可信等级，也不要求逐级补齐 | `level` |
| 技术单元 | L1 记录：实验、方法、推导或分析的完整技术内容。正文写在 `blocks`，检索说明写在 `retrieval_description` | `retrieval_description` 只是检索摘要，不是正文 | `kind=detail` |
| 经过 | L2 记录：研究、实施、排错或决策的实际经过 | 不是操作日志（日志留在 Run） | `kind=narrative` |
| 经验 | L3 记录：可复用的观察、结论、假设或建议，写明适用边界 | `knowledge_type` 是内容分类，不是复核状态 | `kind=experience` |
| 概览 | L4 记录：Owner 的顶层发现入口，说明用途、范围、当前结果和限制 | 不是文稿根，不能代替全文 | `kind=overview` |
| 文稿 / 章节 | 把技术块和衔接文字编排成可读文章的独立记录。research_process 是完整过程稿，research_report 是精简报告 | 不属于 L0–L4（level=null） | `document`、`document_section` |
| 目标 / 路线 / 问题 / 检查点 | 持续工作的状态记录：目标与成功标准；为目标采取的假设与做法；未决问题；交接时点 | 检查点保存交接时点，不是总计划 | `goal`、`route`、`question`、`checkpoint` |
| Wiki 页面 | 宿主 AI 编写、可发布到知识中枢的页面，以本地记录为真源 | 不是外部系统自动生成的页面 | `wiki_page`，slug（可选）为 `<类型>/<名称>` |
| 投入档位 | 按任务决定用多少 burrowl：档 0 直接回答（不调用）；档 1 一次性小任务（开工不检索，结束时沉淀检查有内容才保存）；档 2 持续工作（开工即判定归属、续接） | 不是任务难度评级；用户说"记下来""不用记"可随时调整 | RULES 第 1 节 |
| 归属判定 | 为新任务找出候选对象并附证据（与阅读会话第一轮相同的召回与筛选包、候选的目标与检查点、来源重叠），由 AI 判断沿用、新建或问用户 | 只读；不是阅读会话，也不替 AI 做决定 | `memory.owner_route` |
| 暂定对象 / 归属确认点 | 判定后选中、尚未写入的对象叫暂定对象；本任务第一次往对象里保存的那一刻是归属确认点，该次提交带 `attribution`。确认前可以随时换候选或新建 | 确认后放错只能按纠错处理（新对象重新保存并引用原记录），不能搬运 | `attribution`（提交请求与回执） |
| 关键操作记录 | 从业务卡、提交回执、复核记录、Run 业务卡和中枢执行记录实时汇总的一行一条的视图：建立对象、归属确认、保存、复核、Run 结束、发布 | 派生视图，不另存日志；检索、阅读等不改变资产的操作不在其中 | `memory.activity`；写操作返回值的 `activity` |
| handle | 记录在本对象内的可读短名，供 `[[@handle]]` 链接使用；不填时按标题生成，创建后不可改 | 不是记录 ID；改标题不影响它 | `handle` |

## 版本与引用

| 术语 | 定义 | 不是什么 / 注意 | 代码标识 |
|---|---|---|---|
| 提交（commit） | 一次把一个 Owner 的若干记录变化原子保存的操作，形成不可变历史 | 一次提交只写一个 Owner，没有跨 Owner 事务 | `memory.commit` |
| HEAD | Owner 当前最新提交的指针 | — | `head.commit_id` |
| expected_head | 提交时声明"我看到的是哪一版 HEAD"。不一致说明别人改过，提交会被拒绝（CAS） | 新对象填 null | — |
| 修订号（revision） | 单条记录的版本序号，每次实质修改加 1 | 内容不变不产生新修订 | `revision`、`expected_revision` |
| request_id | 一次提交请求的 UUID。重试时沿用同一个，会得到原来的结果，不会重复写入（幂等） | 不能为了"重新提交"换新 ID | — |
| client_key | 同一批次里给新记录起的临时名字，批内其他记录可以用 `{client_key: "x"}` 引用它 | — | — |
| 固定引用（FixedRef） | 指向某个对象某一版本的引用：`{target_kind, target_id, revision, sha256, locator, relation}`。目标更新后不会悄悄指向新版 | 记录用 record_hash 作 sha256，文件用登记指纹 | `Ref` |
| 关系（relation） | 引用的用途。证据类：supports、input、contradicts；组织类：references、contains、derived_from、prerequisite 等；导航类（只在关联记录中）：same_problem、analogous_to 等 | 导航关系不是证据 | `relation` |
| 来源登记 | 原始文件进入系统的唯一授权入口，记录路径和字节指纹 | 正文里出现的路径不授予读取权限 | `sources.register`、`SRC-…` |
| 数据目录 | burrowl 存放记忆、会话、Run 登记和配置的文件夹，由 MCP 启动参数 `--data-dir` 指定 | 与程序代码、工作目录分开 | `data_dir` |
| 工作目录 / 工作根 | 工作目录是用户和 AI 放原始数据与代码的地方；登记过的工作目录叫工作根，其中的来源记为"根 + 相对路径" | 目录搬家只改根（指纹一致才允许），引用不变 | `--work-dir`、`ROOT-…`、`sources.relocate_root` |
| 运行模式 | auto：任何项目中的实质工作默认按 burrowl-work / burrowl-read / burrowl-consolidate 进行；manual：默认不启用，只在 `burrowl enable` 过的项目中或用户明确要求时使用 | 项目开关优先于模式；简单问答都不进入工作流 | `--mode`、`mcp.mode`、`.burrowl.json` |
| system_guide | MCP 工具，返回 RULES、RECORDING 和各工作流的全文；不支持 Skill 的客户端用它代替 Skill | 内容与 Skill 文件相同 | `system.guide` |
| 敏感级别 | public / internal / confidential / restricted。restricted 不进索引、不发布、不能被引用读取 | 对象和记录各有级别，取较高者 | `sensitivity` |
| 发现可见性 | workspace_summary：全库检索可见；owner_only：只在检索时指定该对象才可见 | 不影响授权 | `discovery` |

## 复核与依据

| 术语 | 定义 | 不是什么 / 注意 | 代码标识 |
|---|---|---|---|
| 结论（claim） | 写在记录 `payload.claims` 中、需要复核的一句陈述，带适用范围和证据。L1 只写 fact / calculation，可用 `block_id` 指向结果块；可选 `relation`（主体、谓词、对象） | 记录正文本身不是 claim | `claim_id`、`CLM-…` |
| 适用范围（scope） | 结论成立的条件和对象范围，写得越具体越好 | "一般情况下"不是范围 | `scope` |
| 复核 | 对一条结论的判断，绑定当时的记录内容与结论指纹 | 保存、索引、回读都不是复核 | `review.*` |
| 复核状态 | not-reviewed 未复核；accepted 已确认（需范围与证据）；disputed 有争议；retracted 已撤回；superseded 已被替代 | — | `state` |
| 已失效（stale） | 复核之后结论或所在记录被修改，原复核不再适用 | 不等于结论错了 | `stale` |
| 成熟度 | 结论在证据阶梯上的位置：conjecture 设想、preliminary 初步、replicated 重复验证、established 已确立。存在复核记录中；AI 不能登记 established，只能提出升级 | 不是复核状态，也不是主观概率；`knowledge_facets.confidence` 是作者自评，不是成熟度 | `maturity`、`requested_maturity`、`evidence_basis` |
| 派生统计 | 独立支持数（按原件去重）、反例数与未处理反例、被引用次数、被采纳的反馈数 | 可随时重算，不是第二份真源；"用得多"不等于"对" | `review.queue` 的 `stats` |
| 依据受阻（upstream-blocked） | 已确认的结论，其依据后来变化、失效或不可读 | — | `upstream-blocked` |
| 复核类别 | 检索时对一条记录全部结论的汇总：none 无结论、accepted 全部已确认、unreviewed 都未复核、mixed 部分确认、unsafe 有争议/撤回/替代/失效 | — | `review` |
| 复核过滤 | 检索时按复核类别筛选：all / reviewed_only / exclude_unsafe / unreviewed_only | — | `review_filter` |
| 正式用途 | 只输出在指定范围内已确认、有效的结论原文 | — | `purpose=formal`、`provenance.formal` |
| 关联（association） | 两条内容之间的导航关系（相似、同一问题、类比、related_to…），状态 candidate/accepted/rejected/withdrawn，带确定度 speculative/plausible/supported（缺省按 plausible 解释）和提出者 | 采纳关联不构成科学支持；"A 会影响 B"这类可验证的判断写成结论 | `kind=association`、`certainty`、`origin` |

## 检索与阅读

| 术语 | 定义 | 不是什么 / 注意 | 代码标识 |
|---|---|---|---|
| 投影 | 从记录派生的可重建检索条目。fulltext 全文；discovery 发现（L4/L3、L2 摘要、L1 检索说明）；source 原件；另有图投影（见下节） | 不是事实来源，可以删除重建 | `index.*` |
| 上下文信封 | 嵌入前由程序拼在文本前面的标题链（对象 › 层级：标题 › 块）和简短字段（检索问题、依赖的定义块），最多占模型窗口的 1/4 | 只进向量文本，不进关键词索引，不写入记录 | `embed_prefix` |
| 索引状态 | indexed 已就绪；pending 待更新；stale 落后于当前 HEAD | pending 时结果可能不完整，不能写成"没有材料" | `index_status` |
| 阅读会话（RS） | 围绕一个问题保存的阅读工作：召回、筛选、阅读进度、笔记和用户请求 | 不是知识，笔记要成为知识须另行提交 | `RS-…`，`reading.*` |
| 阅读策略 | standard：筛选后完整阅读；associative：不足时按联想方向补查；quick：只依据召回片段 | — | `strategy` |
| 筛选包 | 为判断一个 Owner 是否相关而交付的 2–4 个互补命中窗口，带 digest | 不是完整正文 | `packet`、`digest` |
| 三态判断 | relevant：值得完整阅读；uncertain：信息不足，先 peek 或问用户；irrelevant：明确无关 | uncertain 不会自动扩大阅读范围 | `verdict` |
| 研究笔记（note） | 阅读后对一个 Owner 写的材料知识，带固定出处 | 不是规范记录或已复核结论 | `reading.note` |
| 全文补偿 | 发现召回不可用、不完整或不足时，在全文投影上补充召回；必须由用户批准 | 不能自动触发 | `recall(mode=fulltext)`、`approval_id` |
| 收件箱（inbox） | 用户在工作台对会话的操作（批准、改判、方向、中止），随 agent 下一次调用交付一次 | — | `inbox` |
| 预算 | 一次查询或会话的工程保护上限（时间、读取字节、候选数…） | 不是宿主模型的 token 计费 | `budget` |

## 知识图

| 术语 | 定义 | 不是什么 / 注意 | 代码标识 |
|---|---|---|---|
| 知识节点 | 可以被单独引用、判断和修订的图节点：结论、概念，以及作为容器的对象、记录、L1 块、来源 | 与 L0–L4 正交：层级是容纳知识节点的容器 | `node_type` |
| 概念库 / 概念页 | 概念库是配置 `graph.concept_owner_id` 指定的那一个 knowledge 对象；其他 knowledge 对象是通用知识对象，承接不属于具体研究、项目、工具、数据的知识；其中 page_type 为 concept / entity 的 wiki_page 是概念页，标题加别名就是概念的身份。可按领域（`domains`）筛选或屏蔽；合并、拆分用修订表达，从不删除 | 不是证据，也不是结论 | `graph.concept_owner_id`、`KNW-…` |
| 链接 | 正文里的 `[[概念]]`、`[[@handle]]`、`[[@对象短名/handle]]`、`[[@handle#块]]`、`[[!结论短名]]` 写法；提交时解析并固定版本，存入记录的 `links` | 不是证据，除非另写在依据字段里；未解析的概念不阻塞提交 | `links`、`UNRESOLVED_LINK` |
| 图投影 | 从记录派生的节点表和边表，存在 index.sqlite 中，带水位，可以重建 | 不是真源；要修改关系应修改记录 | `graph_nodes`、`graph_edges` |
| 边类别 | 由程序按关系名查表得出：structure 结构、evidence 证据、reference 引用、navigation 关联、concept 概念、claim_relation 结论关系 | AI 不填写类别 | `category` |
| 快捷边 | 由结论的 `relation` 派生的 主体 → 对象 边，继承该结论的复核状态和成熟度；结论失效时快捷边失效 | 不独立存在，条件留在结论的 scope 里 | `derived=true` |
| 图路线 | 检索融合中的一路：从关键词与向量的前几名出发沿图扩展，热门节点按度数降权；默认关闭 | 只是导航，不是证据；命中仍要回到固定版本核对 | `index.graph_route`、`via` |
| 关联候选 | 保存后程序算出的线索：正文提到概念却没链接、向量近邻；外部工具也可回流候选 | 不会自动写成记录，需要判断 | `relations.candidates`、`link_candidates` |

## 知识中枢

| 术语 | 定义 | 不是什么 / 注意 | 代码标识 |
|---|---|---|---|
| 知识中枢（Hub） | 接收本地挑选知识的外部知识库，如 WeKnora 或 Markdown 库 | 不是权威数据，可以从本地重新发布 | `hub.*` |
| 连接器 | 对接某个外部系统的插件 | — | `connectors[]`，入口点 `burrowl.connectors` |
| 发布策略 | 哪些内容发布：层级、复核过滤、敏感级别、撤回时删除还是标记 | 默认只发布已复核内容 | `publish` |
| plan_hash | 发布预览的指纹。执行时必须一致，防止预览后数据变化 | — | `hub.apply(plan_hash)` |
| 漂移（drift） | 外部 Wiki 页面在上次发布后被人修改；发布时不覆盖 | — | `drift` |
