# 工具参考（自动生成，勿手改）

HTTP：`POST /api/v1/ops/<name>`；MCP：工具名把 `.` 换成 `_`；CLI：`burrowl call <name> --json '{...}'`。
带 * 的参数必填。

## config

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `config.get` | 只读 | 当前配置、revision 与各连接器密钥是否已设置（不返回密钥内容）。 | 无 |
| `config.schema` | 只读 | 配置的 JSON Schema（字段、默认值与说明）。 | 无 |
| `config.secret_set` | 仅用户（不暴露给 agent） | 把密钥写入本机凭据管理器（只在工作台/CLI；不暴露给 agent）。 | `secret_ref`*: string，`value`*: string |
| `config.update` | 管理 | 修改配置（如数据库/索引位置、嵌入模型、向量后端、默认阅读策略、连接器）。返回需要的后续动作。 | `patch`*: object，`expected_revision`*: string，`dry_run`: boolean |

## exchange

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `exchange.export` | 写入 | 把记录导出为交换包（Markdown 库 + 图 JSONL + manifest）到数据目录的 exports/<名称>/。 派生数据，可删除重建；增量写入，内容没变的文件不改写。可以用 Obsidian 直接打开其中的 vault 目录。 | `name`*: string，`owner_ids`: array|null，`owner_types`: array|null，`exclude_sensitivity`: array|null |

## hub

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `hub.apply` | 管理 | 执行发布。逐条记录结果，单条失败不影响其他条目。 | `connector_id`*: string，`plan_hash`*: string，`force_wiki`: boolean |
| `hub.candidates_pull` | 写入 | 把外部工具发现的关系线索拉回为关联候选（origin=tool:<连接器>，只写派生的候选表，不写记录）。 之后用 relations.candidates 查看并判断。 | `connector_id`*: string，`limit`: integer |
| `hub.connectors` | 只读 | 已配置的连接器、发布策略与已发布条目数。 | 无 |
| `hub.health` | 只读 | 测试连接：服务版本、兼容性、能力、密钥是否已设置（不返回密钥）。 | `connector_id`*: string |
| `hub.plan` | 只读 | 预览发布：按策略（默认只发已复核的 L4/L3 与成稿文档）列出新增/更新/删除，返回 plan_hash。 | `connector_id`*: string |
| `hub.search` | 只读 | 在外部知识中枢检索；命中能对上本地条目时附 fixed_ref 与按本地当前状态计算的复核类别。 | `query`*: string，`connector_ids`: array|null，`limit`: integer，`review_filter`: string |
| `hub.status` | 只读 | 发布状态：各状态计数、失败/漂移条目、最近几次执行；refresh 时轮询外部解析进度。 | `connector_id`*: string，`refresh`: boolean |

## index

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `index.gaps` | 只读 | 确定性缺口：缺 L4 概览、技术单元缺检索说明、L1 块过长、结果块没声明依赖、未解析的概念链接、 没有入边的概念页、有未处理反例的结论、待确认的成熟度升级。内容是否充分仍需阅读判断。 | 无 |
| `index.reconcile` | 管理 | 按当前 HEAD 补齐检索投影（不改记忆）；sources=true 同时更新来源文件投影。 | `owner_id`: string|null，`force`: boolean，`sources`: boolean |
| `index.search` | 只读 | 直接检索投影（调试/诊断用；正式查询请用 query.search，它会回到固定版本核对）。 | `query`*: string，`corpus`: string，`owner_ids`: array|null，`levels`: array|null，`review_filter`: string，`group`: string，`limit`: integer |
| `index.status` | 只读 | 某对象各投影/通道的就绪状态（stale 表示落后于当前 HEAD）。 | `owner_id`*: string |

## maintenance

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `maintenance.apply` | 写入 | 按对象逐个提交计划中的修订与撤回；可重试，部分完成会如实报告。 | `plan_id`*: string，`expected_revision`*: integer |
| `maintenance.plan` | 写入 | 来源变化或新反证后：列出受影响记录及其完整正文，生成维护计划。 | `changed_ids`*: array，`note`: string |
| `maintenance.review` | 写入 | 记录逐项处置与修订草案，并逐对象预检（不写记忆）。 | `plan_id`*: string，`decisions`*: array，`expected_revision`: integer|null |
| `maintenance.status` | 只读 | 维护计划的当前状态。 | `plan_id`*: string |

## memory

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `memory.commit` | 写入 | 保存一批记录（同一对象内原子提交）。dry_run=true 只预检不写入。 | `owner_id`*: string，`operations`*: array，`expected_head`*: string|null，`request_id`: string|null，`dry_run`: boolean |
| `memory.consolidate_apply` | 写入 | 保存巩固处置（retain/revise/retract/defer…），可与本批实际修订一起提交。 | `owner_id`*: string，`basis`*: object，`decisions`*: array，`reason`*: string，`operations`: array|null，`dry_run`: boolean |
| `memory.consolidate_prepare` | 只读 | 阶段巩固的待处理清单：自上次巩固以来的变化、受影响下游与合并/冲突候选。 | `owner_id`*: string，`since_commit`: string|null |
| `memory.contract` | 只读 | 某类记录的草案契约：必填字段、payload 结构、写作要点和一份经校验的示例草案。写草案前先调用。 | `kind`*: string |
| `memory.document` | 只读 | 读取研究文稿：完整文稿 / 目录 / 单章上下文包（改写某章用）/ 依据变化影响。 | `owner_id`*: string，`view`: string，`document_type`: string，`document_id`: string|null，`section_id`: string|null，`max_chars`: integer |
| `memory.export` | 只读 | 导出对象的记忆（不产生新记录、不复制原件）。 | `owner_id`*: string，`fmt`: string，`record_ids`: array|null，`exclude_ids`: array|null，`include_raw_materials`: boolean |
| `memory.feedback` | 写入 | 记录检索结果的使用反馈（采用次数不是复核）。label=outcome 时 adopted_in 须指向实际应用它的 Run 或记录。 | `owner_id`*: string，`query_id`*: string，`target`*: object，`label`*: string，`note`: string，`adopted_in`: object|null，`request_id`: string|null |
| `memory.figure` | 只读 | 技术单元登记的图片（data URL）。 | `owner_id`*: string，`record_id`*: string，`revision`*: integer，`index`*: integer |
| `memory.inspect` | 只读 | 读取对象的当前快照，或某条记录的当前/指定修订（含完整正文与固定引用）。 | `owner_id`*: string，`record_id`: string|null，`revision`: integer|null |
| `memory.kinds` | 只读 | 全部记录类型：层级、版本、由哪个操作写入、一句话用途。 | 无 |
| `memory.owner_adopt` | 写入 | 把内容树中已有的文档登记为对象（原件不修改；须给出你看到的字节指纹）。 | `path`*: string，`expected_sha256`*: string |
| `memory.owner_create` | 写入 | 新建对象（写入业务卡；首次提交记忆时才建立记忆目录）。Run 用 runs.create 建立。 先用 memory.owners 查找：同一目标的继续或子任务沿用已有对象；主题相似只引用；独立交付目标才新建。 core-algorithm 只用于公司文档定义的关键算法，普通脚本/模块/工具不要建算法卡。 knowledge 用于全库唯一的概念库（建好后把 owner_id 填进配置 graph.concept_owner_id）。 | `owner_type`*: string，`slug`*: string，`title`*: string，`description`: string，`keywords`: array|null，`source_document`: string|null，`short_name`: string|null，`sensitivity`: string |
| `memory.owners` | 只读 | 列出可读对象（按类型分组，带各层记录数）。 | `owner_types`: array|null，`query`: string |
| `memory.raw_material` | 只读 | 回读一个原始材料（按清单中的 ID；核对固定字节）。 | `owner_id`*: string，`material_id`*: string，`max_chars`: integer |
| `memory.raw_materials` | 只读 | L0 原始材料清单（不读原件正文）。 | `owner_id`*: string |
| `memory.records` | 只读 | 对象内的记录目录（按层级，分页；不含正文）。kinds 按类型筛选，query 匹配标题或 ID；kind_counts 是类型筛选前的计数。 | `owner_id`*: string，`levels`: array|null，`kinds`: array|null，`query`: string，`main_only`: boolean，`offset`: integer，`limit`: integer |
| `memory.recovery` | 管理 | 检查记忆目录健康状况（写锁、孤立提交、暂存残留）；只报告不修改。 | `owner_id`*: string |
| `memory.render` | 只读 | 按交换格式把一条记录渲染为 Markdown（YAML 头 + 正文 + 自动生成的反链与关联）。只读。 | `owner_id`*: string，`record_id`*: string |
| `memory.resume` | 只读 | 续接对象的工作上下文：目标→检查点→最新概览/经过/技术→路线/问题/经验（只读）。 不建立阅读会话；用户查询主题相关知识、找已有经验时默认先 reading_start。 | `owner_id`*: string |
| `memory.summary_materials` | 只读 | 跨对象总结的固定材料包（不生成总结）。 | `owner_ids`*: array，`exclude_ids`: array|null |
| `memory.summary_save` | 写入 | 把写好的总结保存为本对象的 L3/L4；依据对象有新提交则拒绝。 | `owner_id`*: string，`draft`*: object，`basis_heads`*: object，`record_id`: string|null，`expected_revision`: integer|null，`dry_run`: boolean |
| `memory.timeline` | 只读 | 研究时间线：全部记录的全部修订与关联 Run（不含正文）。 | `owner_id`*: string，`offset`: integer，`limit`: integer |

## monitor

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `monitor.candidates` | 只读 | 来源变化影响到的维护候选；用 maintenance_plan 把它们纳入语义维护。 | `status`: string|null |
| `monitor.observations` | 只读 | 最近的监测观察记录。 | `limit`: integer |
| `monitor.resolve` | 仅用户（不暴露给 agent） | （仅用户）处置维护候选。 | `target_id`*: string，`status`*: string，`note`: string |
| `monitor.run` | 写入 | 扫描一次：对象 HEAD 与来源文件指纹的变化，并给出维护候选（观察不改变任何证据或复核状态）。 | `dry_run`: boolean |

## provenance

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `provenance.formal` | 只读 | 正式使用：只返回在该范围内已接受、有效、来源可见的结论原文，其余列出原因。 | `target_ids`*: array，`scope`*: string |
| `provenance.impact` | 只读 | 这些内容变化后，哪些下游需要重新审阅（导航边只提示，supports/input 才是正式依赖）。 | `changed_ids`*: array |
| `provenance.lineage` | 只读 | 沿依据追到可访问的原件身份（来源数不等于独立支持数）。 | `target_ids`*: array |
| `provenance.question_validity` | 只读 | 已解决问题的答案依据现在是否仍成立。 | `question_id`*: string，`scope`: string|null |

## query

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `query.assemble` | 只读 | 把所选候选组合成阅读包：正文 + 必要依据（+ 已采纳关联补充）；预算不足整块省略并报告。 | `query_id`*: string，`candidate_ids`*: array，`associations`: boolean |
| `query.cancel` | 只读 | 取消查询（已完成部分保留）。 | `query_id`*: string |
| `query.deepen` | 只读 | 沿已保存关系有界深化（节点/边/跳数计入预算）；可达不等于科学支持。 | `query_id`*: string，`candidate_ids`*: array，`hops`: integer，`relations`: array|null，`include_associations`: boolean |
| `query.documents` | 只读 | 返回候选所属对象的完整文稿（按文稿去重，保持章节顺序）。 | `query_id`*: string，`candidate_ids`*: array，`document_type`: string |
| `query.expand` | 只读 | 沿作者保存的固定引用展开：经验/概览 → 研究经过或技术依据。 | `query_id`*: string，`candidate_ids`*: array，`target`*: string |
| `query.page` | 只读 | 下一页候选（沿用原范围与累计预算）。 | `query_id`*: string，`cursor`: string|null |
| `query.search` | 只读 | 独立检索材料并核对固定版本，返回第一页候选；不建立阅读会话、不进入工作台阅读页。 用户查询主题相关知识、找已有经验时默认先 reading_start（简查 quick、完整阅读 standard）； 只有明确只要片段/候选列表或低层查询时直接使用本工具。候选不是已读正文。 | `question`*: string，`keywords`: array|null，`content_source`: string，`scope`: ?|null，`review_filter`: string，`purpose`: string，`applicability`: string，`freshness`: string，`channels`: array|null，`context_reach`: string，`result_limit`: integer|null，`budget`: ?|null，`query_id`: string|null |

## reading

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `reading.abort` | 仅用户（不暴露给 agent） | 中止会话（agent 之后的写操作会被拒绝）。 | `session_id`*: string，`reason`: string |
| `reading.archive` | 仅用户（不暴露给 agent） | 归档或恢复会话（历史保留）。 | `session_id`*: string，`archived`: boolean |
| `reading.ask_user` | 写入 | 向用户提问。fulltext=申请全文补偿（仅当发现召回不可用、不完整或不足，见 fulltext_available；批准后得到 approval_id）；direction=请用户定联想方向；clarify=澄清问题。用户可在对话或工作台回答。 | `session_id`*: string，`kind`*: string，`prompt`*: string，`options`: array|null，`request_id`: string|null |
| `reading.assess` | 写入 | 三态判断一批对象；返回下一批或“可以开始读”的提示。用户的改判优先，不会被覆盖。 | `session_id`*: string，`judgments`*: array，`request_id`: string|null |
| `reading.configure` | 仅用户（不暴露给 agent） | 切换阅读策略。 | `session_id`*: string，`strategy`*: string |
| `reading.delegate` | 只读 | 子 agent 任务包（只描述任务；实际派工由宿主完成）。 | `session_id`*: string，`host_supports_subagents`*: boolean |
| `reading.direct` | 仅用户（不暴露给 agent） | 补充联想方向（下一轮联想补查使用）。 | `session_id`*: string，`text`*: string |
| `reading.finish` | 写入 | 结束会话并返回交接文本（综合稿优先；陈旧笔记不交付；超出上限的笔记整篇省略并列出缺口）。 | `session_id`*: string，`outcome`: string，`request_id`: string|null |
| `reading.list` | 只读 | 阅读会话列表（最新在前）。 | `status`: string|null，`owner_id`: string|null，`limit`: integer |
| `reading.note` | 写入 | 保存某对象的研究笔记（standard/associative 须先读完全部页）；出处补全为固定引用。 | `session_id`*: string，`owner_id`*: string，`note`*: string，`sources`*: array，`clues`: array|null，`request_id`: string|null |
| `reading.override` | 仅用户（不暴露给 agent） | 用户改判某个对象（agent 不能再覆盖）。 | `session_id`*: string，`owner_id`*: string，`verdict`*: string，`reason`: string |
| `reading.peek` | 写入 | 查看某对象更多的命中窗口（用于 uncertain 的对象）。 | `session_id`*: string，`owner_id`*: string |
| `reading.read` | 写入 | 完整阅读判为相关的对象：第 1 页附目录，按 unread_pages 读完全部页。quick 策略不可用。 对象在阅读期间有新提交时要求从第 1 页重读（STALE_BASIS）。 | `session_id`*: string，`owner_id`*: string，`page`: integer |
| `reading.recall` | 写入 | 补充召回：全文补偿（需用户批准）或联想补查（associative 策略，受轮数上限）。 想换一种问法重新找，请新开一个 reading_start。 | `session_id`*: string，`mode`*: string，`approval_id`: string|null，`association_text`: string|null，`request_id`: string|null |
| `reading.respond` | 仅用户（不暴露给 agent） | 回答 agent 的请求；批准全文补偿会生成 approval_id。 | `session_id`*: string，`request_id`*: string，`decision`*: string，`answer`: string |
| `reading.start` | 写入 | 查询主题相关知识、找已有经验的默认入口：创建工作台可见阅读会话并立即召回。 简查选 quick（采纳片段、写笔记）；完整阅读选 standard。返回第一批对象筛选包 （每个对象 2–4 个命中窗口，含 digest），继续 assess → read（quick 跳过）→ note → finish。 | `question`*: string，`conditions`: array|null，`strategy`: string|null，`review_filter`: string|null，`variants`: array|null，`protected_terms`: array|null，`domains`: array|null，`owner_id`: string|null，`scope_owner_ids`: array|null，`field_conditions`: array|null |
| `reading.status` | 只读 | 会话概况、当前批次、交接预览、预算消耗，以及用户在工作台上的新输入（每条只交付一次）。 | `session_id`*: string |
| `reading.synthesize` | 写入 | 跨对象综合笔记；所依据的任何单篇笔记改变或陈旧后，综合稿自动失效。 | `session_id`*: string，`note`*: string，`sources`*: array，`owner_ids`*: array，`request_id`: string|null |
| `reading.view` | 只读 | 会话完整状态（时间线、筛选包、笔记、请求）；只读，不消费收件箱。 | `session_id`*: string |

## relations

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `relations.associations` | 只读 | 已保存的关联记录。 | `status`: string|null，`record_id`: string|null |
| `relations.candidates` | 只读 | 待判断的线索：正文提到概念却没链接（concept_mention）、向量近邻（similar）、外部工具回流（external:*）。 已判断过的端点对会被过滤。只读。 | `record_id`: string|null，`owner_id`: string|null，`kinds`: array|null，`limit`: integer |
| `relations.decide` | 写入 | 保存关联判断（candidate/accepted/rejected/withdrawn）；只能是导航关系。 "A 会影响 B"这类可以验证的判断写成结论（inference），不要写成关联。 | `owner_id`*: string，`payload`*: object，`reason`*: string，`dry_run`: boolean |
| `relations.graph` | 只读 | 材料关系图（子图）：对象、记录、块、结论、概念、来源，以及按类别区分的边（来自图投影）。 categories 只看某几类边；domains / excluded_domains 按领域筛选或屏蔽概念。 | `center`: string|null，`hops`: integer，`query`: string，`kinds`: array|null，`levels`: array|null，`excluded`: array|null，`limit`: integer，`categories`: array|null，`domains`: array|null，`excluded_domains`: array|null，`node_types`: array|null |
| `relations.note_add` | 仅用户（不暴露给 agent） | 添加关系图笔记。 | `kind`*: string，`title`*: string，`text`: string，`refs`: array|null |
| `relations.note_resolve` | 仅用户（不暴露给 agent） | 更新关系图笔记状态。 | `note_id`*: string，`status`*: string |
| `relations.notes` | 只读 | 关系图上的随手笔记（分组名、桥接问题、关系设想）。 | 无 |
| `relations.similar` | 只读 | 为种子记录找关联候选（关键词重合 / 语义近邻）；只是待判断的线索。 | `seeds`*: array，`method`: string，`limit`: integer |
| `relations.view` | 只读 | 工作台关系图的本机视图偏好。 | 无 |
| `relations.view_save` | 仅用户（不暴露给 agent） | 保存关系图视图偏好（布局、分组、中心、排除项）。 | `view`*: object |

## review

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `review.batch` | 写入 | 批量复核（逐条串行；单条失败不影响其他）。每项字段同 review.submit。 | `items`*: array，`dry_run`: boolean |
| `review.history` | 只读 | 一条结论的复核历史。 | `claim_id`*: string |
| `review.queue` | 只读 | 待复核队列：默认列出未复核、已失效、有争议、被上游阻断、有升级提议的结论； 每项带有效成熟度与派生统计（支持数按原始来源去重，"用得多"不等于"对"）。 | `states`: array|null，`owner_id`: string|null，`query`: string，`limit`: integer，`offset`: integer |
| `review.submit` | 写入 | 复核一条结论。accepted 需要适用范围与支持性证据；superseded 需要替代结论。dry_run 只预检。 成熟度与复核状态分开：可以在 not-reviewed 时登记 conjecture/preliminary；established 必须 accepted。 | `target_claim_id`*: string，`state`*: string，`reason`*: string，`scope`: string|null，`evidence_refs`: array|null，`replacement_claim_id`: string|null，`maturity`: string|null，`evidence_basis`: string|null，`requested_maturity`: string|null，`dry_run`: boolean |

## runs

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `runs.create` | 写入 | 新建 Run（一次实际分析/执行的记录容器），默认放在所属对象目录下。 | `title`*: string，`owner_id`: string|null，`question`: string，`keywords`: array|null |
| `runs.execute` | 写入 | 前台运行 Python 脚本并登记输入、输出与日志（失败也登记）。参数可用 {input0}、{output_dir} 占位。 | `run_id`*: string，`script`*: string，`inputs`: array|null，`args`: array|null，`timeout_seconds`: number，`preview`: boolean |
| `runs.register` | 写入 | 登记已有文件为 Run 的输入/产物（固定字节指纹，可回滚）。 | `run_id`*: string，`inputs`: array|null，`artifacts`: array|null，`preview`: boolean |
| `runs.rollback` | 写入 | 按登记回执恢复 run.json（不删除任何文件）。 | `receipt`*: string，`preview`: boolean |
| `runs.search` | 只读 | 按标题/问题/关键词查找 Run。 | `query`: string，`owner_id`: string|null，`limit`: integer |
| `runs.update` | 写入 | 修改 Run 的结论、限制、状态等（不改输入/产物登记）。 | `run_id`*: string，`fields`*: object，`expected_sha256`: string|null |

## sources

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `sources.add_root` | 写入 | 登记一个工作根。通常不必手动调用：登记本次工作目录里的文件时会自动建根。 | `path`*: string，`title`: string |
| `sources.list` | 只读 | 已登记的来源文件（file 引用的唯一授权入口），附当前解析出的位置 location。 | 无 |
| `sources.register` | 写入 | 登记一个来源文件并记录当前字节指纹：L0 原件的唯一授权入口。原件留在原处，不复制、不修改。 工作目录里的文件自动记为"工作根 + 相对路径"，迁移时随根重定向。 同一 ID 重复登记同一文件是幂等的；内容变了请用新 ID；文件搬家用 sources.relocate / sources.relocate_root。 | `source_id`*: string，`path`*: string，`title`: string，`sensitivity`: string |
| `sources.register_many` | 写入 | 批量登记来源（导入已有资料时使用）。 | `items`*: array |
| `sources.relocate` | 写入 | 原件搬家：新位置的字节必须与登记时一致，旧路径记入 relocated_from，已有固定引用继续有效。 | `source_id`*: string，`new_path`*: string |
| `sources.relocate_root` | 写入 | 工作目录整体搬家：逐个核对新位置下已登记文件的指纹，全部一致才改根的位置；已有固定引用不变。 | `root_id`*: string，`new_path`*: string，`allow_missing`: boolean |
| `sources.roots` | 只读 | 已登记的工作根（工作目录）与本次会话的工作目录。工作根下的来源记为"根 + 相对路径"，目录搬家只改根。 | 无 |
| `sources.update` | 仅用户（不暴露给 agent） | （仅用户）启用/停用来源或调整敏感级别。停用后引用它的记录会在读取时被隐去。 | `source_id`*: string，`enabled`: boolean|null，`sensitivity`: string|null |

## system

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `system.capabilities` | 只读 | 当前能力与降级情况（向量模型、重排模型、连接器）。 | 无 |
| `system.guide` | 只读 | 读取 burrowl 工作流说明全文（与 Skill 文件相同）。客户端没有加载 burrowl Skill 时，用它代替 Skill。 | `name`*: string |
| `system.ops` | 只读 | 全部操作的目录（名称、分组、读写类型、说明、参数 schema）。 | `groups`: array|null |
| `system.validate` | 只读 | 全工作区只读校验：提交链与指纹、写锁与残留、来源文件是否变化或缺失、索引是否落后。不修复。 | 无 |

## wiki

| 操作 | 类型 | 说明 | 参数 |
|---|---|---|---|
| `wiki.configure` | 管理 | 按配置 wiki.mode 开启/关闭外部自动 Wiki（auto/both 开启）。 | `connector_id`*: string |
| `wiki.list` | 只读 | 列出外部 Wiki 页面（authored_here 表示由本项目发布）。 | `connector_id`*: string，`query`: string，`limit`: integer |
| `wiki.read` | 只读 | 读取一页外部 Wiki（含外部自动生成的页面）。 | `connector_id`*: string，`slug`*: string |
