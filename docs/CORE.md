# 核心功能与实现逻辑

本页面向使用者和开发 AI，用伪代码和必要说明解释核心功能，保留影响理解的选项和边界。

- 不展开字段清单、接口参数和算法常数：字段用 `memory.contract`，参数见 [ops 参考](reference/ops.md)，实现文件见各节末尾。
- 模块关系与变化影响只维护在 [ARCHITECTURE](../ARCHITECTURE.md)。
- 术语见 [GLOSSARY](GLOSSARY.md)。

核对日期：2026-10-10，依据当前工作树（含 R12 的 P0–P4 与 R13 的 P1–P2）。相关实现变化时同步维护本页；当前没有自动同步文档的服务。

## 功能块、定义与边界

G01–G12 沿用源项目的功能分组身份，描述的是职责，不是十二个独立服务。

| 功能块 | 输入 → 输出 | 职责与边界 | 主要模块 |
|---|---|---|---|
| G01 固定读取与解析 | 固定引用 → 已核验内容或缺口 | 读取指定版本，核对字节指纹与授权；候选清单不算已读正文 | MEM、STORE |
| G02 内容与修订 | 草案、预期版本 → 新修订或冲突、回执 | 校验、幂等提交、历史保留；单 Owner 事务；保存不等于索引或复核成功 | MEM、STORE |
| G03 表示与组合 | 固定材料 → 可读内容 | 选择已有正文、技术块或章节；组合不产生新的语义判断 | MEM、QUERY |
| G04 索引维护 | 已保存内容 → 可检索投影与水位 | 全文、发现、原件三类投影；索引不是事实来源，不能补回失踪原件 | IDX |
| G05 候选召回 | 问题、范围、用途 → 候选与命中依据 | 精确身份、关键词、向量多路召回；模型或索引缺口单列 | IDX、QUERY |
| G06 去重与排序 | 各路候选 → 稳定顺序与命中解释 | 规范身份去重、RRF 融合；可选重排与显式条件；排名不是可信度 | IDX、READ |
| G07 回源与关系导航 | 固定种子 → 展开内容、关联候选 | 只沿作者保存的引用或已采纳关联展开；导航不构成科学支持 | QUERY、MAINT |
| G08 渐进查询 | 请求或游标 → 分页、进度、停止原因 | 续页、取消；保留原范围和累计预算 | QUERY |
| G09 上下文组包 | 已选候选 → 正文、必要依据、缺口 | 补齐声明的必要依据；不隐式二次检索；缺依据时父材料标为不完整 | QUERY |
| G10 阅读视图 | 对象或文稿 → 正文、目录、分页 | 按编排结构阅读；展示不改版本或复核状态 | MEM、READ、APP |
| G11 语义维护 | 新依据、受影响内容 → 计划、处置、提交 | AI 或人判断，程序执行；跨对象可能部分完成 | MAINT、MEM |
| G12 来源与证据 | 固定内容、用途、范围 → 授权、复核、影响结果 | 正式用途逐条核验；保存、相关性、导航都不能代替复核 | REVIEW、MEM |
| G13 知识中枢 | 发布策略 → 外部副本、检索命中、Wiki | 本地为权威，外部可重建；默认只发布已复核内容 | HUB |
| 共享执行控制 | 预算、实际消耗、取消 → 累计账本与终止原因 | 约束读取、查询、展开、组包、阅读召回 | QUERY、READ |

模块简称见 [ARCHITECTURE 的影响表](../ARCHITECTURE.md#模块与变化影响)。

## 功能块怎样衔接

箭头表示主要数据交接，不是固定执行顺序；权限、证据和预算检查贯穿各步。

```text
保存链：   AI/人组织内容 → G02 保存修订 → G04 更新索引 → G01 固定回读（保存与可检索分别核对）
查询链：   G08 管理查询 → G05 召回 → G06 去重排序 → G01 回源 + G12 资格 → 选择
           → 按需 G07 展开 → G03 选择形式 → G09 组包 → G10 阅读
阅读链：   G05 发现召回 → G06 筛选包 → 三态判断 → G10 完整阅读 → 笔记 → 交接（重新核对 G01）
维护链：   监测或 G12 发现影响 → G11 实际审查 → G02 保存新修订 → G04 更新投影 → G01/G10 回读
发布链：   G12 复核类别 + 发布策略 → G13 预览 → 用户确认 → 执行 → 轮询外部状态
```

## 1. AI 怎样整理和记录结果

Skill 指导 AI 读什么、怎么组织内容；程序负责校验和保存。**记录成功不代表解释正确。**

```text
AI 按任务选择 Skill：
    工作、导入资料、执行、续接 → work
    检索与完整阅读             → read
    阶段收口、来源变化、发布   → consolidate

先定投入档位（直接回答 / 一次性小任务 / 持续工作，见第 12 节）
需要时判定归属（沿用 / 引用 / 新建），维护一份 Markdown 工作清单
读取已有目标、检查点、概览和固定依据
    → 核对输入、方法、实际结果、失败和缺口
    → 区分事实、计算、推断、建议

有用内容按职责保存（缺层正常）：
    L0 登记原件与 Run       L1 完整技术内容 + 检索说明 + 事实/计算类结论
    L2 有上下文的经过        L3 可复用认识与边界        L4 自足的发现入口
    需要时编排文稿（章节 → 文稿根）
正文里用可读链接连起知识节点：[[概念]]、[[@handle]]、[[@handle#块]]、[[!结论短名]]
"值得放在一起看"→ 快速添加关联；"A 会影响 B"→ 写成结论（成熟度从设想起）

先保存被引用的内容，再保存引用它的；逐条脱离上下文回读
```

**L0–L4 是信息分级（R21）**：按内容片段回答的问题落层，不按文档类型套模板。L1 是无损层，保留一切会消失或检索读不到的有用细节（导入时接近原文，交互工作写到可复算）；L2–L4 是压缩层，各自提炼，并在关键处就近链接 L1。拿不准时偏向保全（保留、放 L1、类型用 `general`）。定义失手的兜底：

| 兜底 | 机制 | 适用 |
|---|---|---|
| 规则内 | 拿不准就保留、放 L1 | 全部 |
| 覆盖报告 | 原文快照逐段对比 L1 的 `origin` 与正文，列出未覆盖、明显偏短且未登记省略的段落（只提示） | 导入外部原件 |
| 检索 | 原件分段本来就进 source 投影，L1 漏掉的细节仍能检到原文 | 导入外部原件 |
| 收口清单 | `unfixed_evidence`（没被引用的 Run、L0）与 `unlinked_details`（没被上层指向的 L1） | 全部 |
| 人工 | 工作台"原文展开"页按原文顺序核对，简略处写笔记交给 AI，AI 按 `origin` 只重做对应段落 | 全部 |

```text
导入外部原件（burrowl-work "导入已有资料"）：
    sources.register_many(snapshot=true) → 原文快照（.burrowl/snapshots/<SRC>/<sha>.json：分段 key、文本、指纹、图片标签）
    sources.snapshot(keys) 分批读原文 → 按原文结构写 L1（origin.locators = 分段 key；块上可细到 locators）
        图片：sources.attach → attachments/<sha256>.<ext>，登记为 SRC-ATT-…，L1 figures 引用、正文 ![图注](figure:N)
        有意省略：L0 payload.omitted = [{locators, reason}]
    再写 L2–L4（就近链接 L1）→ memory.document(view=coverage) 核对 → 检索验证
工作台"原文展开"：memory.document(view=expansion) 按快照顺序拼出认领各段的 L1 块（视图，不是记录）
```

写作标准见 [skills/RECORDING.md](../skills/RECORDING.md)；字段与示例由 `memory.contract(kind)` 返回，示例经 `tests/service/test_draft_examples.py` 校验。

实现：`core/records.py`、`core/contract.py`、`core/schema/examples/`；导入相关：`index/extract.py`（含 docx）、`core/segments.py`（分段、对比、覆盖，纯函数）、`services/snapshots.py`（快照、附件、覆盖与展开）。

## 2. 结果怎样保存到本地

- **record ID** 保持内容身份；**revision** 是单条记录的修订号；**commit** 把一个 Owner 的若干变化一起发布。
- 每次提交只写变化后的完整记录；清单（manifest）保留全部当前版本的指针；旧修订留在父提交链中。
- 引用是"身份 + 版本 + 指纹"，目标更新后不会悄悄指向新版。
- 每条记录都由**外壳**和 **payload** 两部分组成：
  - **外壳**：所有类型共用，包括身份、所属对象、类型和层级、标题、正文 `body_markdown`、来源 `sources`、修订信息。程序据此统一处理保存、版本、权限和检索。
  - **payload**：随类型变化，存放程序需要读取或校验的结构化字段，例如 L1 的技术块和检索说明、L3 的适用与禁用条件、association 的两个端点，以及各层的结论 `claims`。
  - 给人读的完整内容写在正文里（L1 写在 blocks 里），不要只放在 payload 中。
- 固定引用在任何位置都是同一种格式，可以出现在 `sources`、结论的依据和关联的端点等处；引用中的 `relation` 说明这条引用的用途。各类型的具体字段由 `memory_contract(kind)` 给出。
- 外壳还有两个服务端维护的字段：`handle`（本对象内的可读短名，创建后不可改，不进内容指纹）和 `links`（正文链接的解析结果，进内容指纹；没有链接时省略）。契约只做增量，已有记录的指纹不变。
- 记录中的引用、链接、结论、关联怎样变成关系图的点和线，见 [R12 设计稿 §4.1 的直观示例](design/R12_KNOWLEDGE_GRAPH_RETRIEVAL.md#直观示例一条记录会变成哪些点和线)和第 5 节。

```text
AI 组成提交请求：{owner_id, expected_head, request_id, operations:[put_record…]}
    （可先 dry_run 预检）
保存服务：
    handle：修订沿用原值（改动 → INVALID_TRANSITION）；新建未提供时按标题生成，本对象内冲突追加 -2、-3
    正文（L1–L4、文稿章节、结论陈述）中新写入的长编号（XXX-<uuid>）或 64 位指纹 → UNREADABLE_REF，
        hint 给出可直接替换的 [[@短名]]（上一修订已有的不拦，由维护页"引用改写"处理）
    解析正文链接 → links：概念找不到记为 unresolved_concept（不阻塞）；记录/块/结论找不到或有歧义
        → UNRESOLVED_LINK + 候选；修订时原文未变的链接沿用旧固定版本，repin=true 才改指当前版本
    校验结构、领域规则、引用和授权（受限来源、敏感级别不得降低、支持关系不能成环）
    提交到概念库对象时：有效概念的名称与别名唯一，合并/拆分目标必须是有效概念
    同一 request_id 且内容相同 → 返回原回执（幂等）
    HEAD 已变化 → VERSION_CONFLICT
    内容无变化 → 不产生新修订
    否则：锁内写暂存 → 回读校验 → 原子切换 HEAD → 写回执
    同步更新该对象的检索投影（失败只记 pending，由 reconcile 补偿）
回执：每条记录的 revision、record_hash、现成的 fixed_ref；save_status 与 index_status 分开
```

- 读取时（inspect、resume、导出、总结材料），依据链上有受限或不可用来源的记录会被隐去，并在 `omitted` 中说明原因。
- AI 不直接编辑存储文件。

**AI 填写的字段与报错（R20）**

- 服务能推导的草案字段可以省略：`owner_id` 取提交的对象（写了别的对象才报错）；`sensitivity` 新记录为 internal、修订沿用原值；`discovery` 按类型取默认、修订沿用；`keywords`、`provenance_gap`、`handle` 也有默认。时间字段（名称以 `_at` 结尾，以及 `valid_from` / `valid_until`）写带时区的 RFC3339 时间或 null，保存前统一换算为以 Z 结尾的 UTC（字符串顺序即时间顺序，同一时刻只有一种写法、不影响指纹）；只写日期或不带时区的拒绝。契约的字段说明里直接写出这种格式。`memory_contract(kind)` 的 `draft_fields` 把每个字段标为 required / optional / conditional（`change_reason` 修订时写），由 schema 的 required 与默认值表生成，并逐类记录用测试核对"标 required 的省略后确实失败、标 optional 的省略后确实通过"。
- 工具参数只在操作注册表（`registry.call`）校验一次，HTTP、MCP、CLI 共用。AI 直接编写的结构化参数（提交操作、归属说明、关联 payload、阅读判断、扩展锚点、维护处置、工作台方案等）在工具的参数 schema 中声明字段；记录相关的结构直接取自记录契约。复杂参数附经校验的示例。
- 报错结构 `{code, message, details, errors[], retryable}`：顶层 `message` 直接写出第一处错误的路径与原因（有的客户端只显示它）；`errors` 一次列出全部问题，每项的 `path` 从工具参数的根算起（如 `/operations/0/draft/title`），`expected` 给允许的值或字段，`details.hint` 给下一步。
- 意外异常返回 `INTERNAL_ERROR`（异常类型、一句摘要、`incident_id`），完整堆栈只写入数据目录的 `.burrowl/logs/errors.jsonl`。被拒绝的调用只记工具名、错误码和出错路径（不记参数内容），`system.tool_errors` 按工具汇总，用来发现哪些工具的说明需要改。
- 请求日志（默认关闭）：`.burrowl/logs/requests.jsonl` 每次调用一行；summary 只记结构，full 另记收到的原样参数、MCP 层从 JSON 字符串解析回来的参数与返回。开启时报错的 `details.request_id` 指向这一行，`system.request_log` 可按它查。开关在工作台设置（`diagnostics.request_log`），`burrowl mcp --request-log` 或环境变量 `BURROWL_REQUEST_LOG` 优先。

**删除与恢复（R22）**

删除只用于本来就不该记的内容（测试数据、重复、写错对象）；结论错了用复核撤回，保留教训。

```text
删除记录 records_discard(owner_id, record_ids, reason, [expected_head], [dry_run])
    护栏：指针记录（policy / goal / checkpoint）不能删；其他记录的当前修订经 sources、payload、links
          引用这些记录或其中的结论时拒绝（INVALID_TRANSITION，列出引用者）；一起删除的记录互相引用不算
    墓碑提交：record_heads 移除该记录，清单的 removed_records 保存原指针、理由、时间、执行者
    之后：当前快照、检索、文稿、续接、关系图、发布都没有它；按修订号的固定引用照常可读；
          不带修订号读取 → NOT_FOUND（removed=true）；新引用它 → UNRESOLVED_REFERENCE；普通提交不能改写它
恢复记录 records_restore：原指针放回，记录逐字节不变；它引用的记录、结论、对象必须仍存在（一起恢复的视为存在）

删除对象 owner_discard(owner_id, reason, [expected_head], [dry_run])
    护栏：其他对象的当前记录引用本对象、其中的记录或结论、其下的 Run、目录内已登记的来源；
          目录里还有其他对象；配置的概念库 → 拒绝
    业务卡对象：整个对象目录移到 .burrowl/trash/owners/<编号>/item/；被采用文档：只移走记忆目录，原件留在原处
    之后：检索投影立即删除；知识中枢在下次发布计划中按 on_retract 删除或标记
恢复对象 owner_restore：原位置未被占用时移回并重建投影
清空回收站 owner_empty_trash：永久删除，仅限用户（工作台"维护与监测"，或 burrowl call）
```

删除与恢复进入关键操作记录：记录来自墓碑提交，对象来自回收站日志 `.burrowl/trash/owners.log.jsonl`。

实现：`services/memory/service.py`、`services/memory/removal.py`、`storage/memory_store.py`、`core/atomic.py`。

## 3. 检索与召回

```text
构造查询计划：原问题 + 最多 2 条等义改写（技术问题加英文）
              保护词（型号、数值原样）+ 术语库扩展
多路召回：精确身份 | 关键词（FTS5，中日韩按二元切分）| 向量（sqlite-vec，按模型指纹；
          切分方式 index.embedding.chunking：
            late（后切分，平均池化的长上下文模型）：信封 + 整条正文一次编码（≤ late_context_tokens，
                超过时按段落分批、每批带信封），再按段落区间（≤ span_tokens）分别求平均；
                L1 记录只编码一次，块级条目取各自区间，记录级条目取块以外的检索说明等
            windows（其他模型）：每段单独编码，窗口按模型真实输入上限切分；
          查询超限时报告 query_truncated 缺口；信封只进向量不进关键词）
          在所选投影内：fulltext 全文 / discovery 发现 / source 原件
图路线（index.graph_route，默认关闭）：从前几名出发沿图投影扩展 1–2 跳，按边类别和确定度加权、
          热门节点按度数降权，映射回同一投影、同样过滤的条目，命中附 via（经过的边）
过滤：范围、排除、敏感级别、owner_only 可见性、复核过滤（review_filter）
融合：RRF，同一信号族只计一票；按规范身份去重
回源：命中回到固定版本核对；索引落后时给出缺口而不是"没有结果"
```

- **上下文信封**：对象标题 › 层级与记录标题 › 块标题，加 L1 的检索问题和依赖的定义块原文；最多占模型窗口的 1/4，超出时从尾部裁（先裁定义块，标题链最后裁）。
- **技术单元的两种粒度**：记录级条目包含检索说明和全部块正文（关键词索引全文；后切分时向量只取块以外的部分），块级条目各自一条，融合时按记录折叠。
- **后切分**（R17）：每个向量只覆盖一段，但编码时看过整条记录；`late_context_tokens` 决定上下文长度，`span_tokens` 决定向量粒度，两者分开设置。新建数据目录默认 jina-v2-base-zh 并启用后切分；已有数据目录保持原模型。评测方法见 [R17 §5](design/R17_LATE_CHUNKING.md#5-评测方法供后续比较)。
- **原件分段**：Markdown 按标题切节（过长再按 80 行），PDF 按页，PPTX 按幻灯片；信封为"文件名 › 标题路径"。
- **术语扩展**：`.burrowl/query-terms.json` 词库之外，问题中出现的概念名或别名也会带上该概念的全部别名（阅读会话）。
- 索引结构或分词规则升级后，索引文件整库清空重建（`index.reconcile` 报告 `index_rebuilt_from`）；换模型或切分规则（chunking 及两个长度）后，向量按指纹自动作废重建。
- 索引总体状态须检查全文、发现和图投影的必要通道；仅有关系图不能显示为“已索引”。工作台检索页把结构化缺口转换为可读提示，索引缺失或落后时提供显式补齐并重试；搜索本身不隐式重建索引。

- **发现投影**：只含 L4/L3 正文、L2 摘要、L1 检索说明、对象元数据，以及结论、已采纳的关联、概念页（R19），是阅读会话与归属判定的默认召回面。结论与关联是"关系陈述"：自带文字，能被直接搜到；supports 等引用是"关系指向"，只在图扩展时显示为关系线索。
- **可见范围**：知识记录（L1–L4、关联、概念页）默认全库可见，目标、检查点、路线、问题等工作状态默认只在本对象可见；对象策略或草案显式设置优先，修订不写时沿用原值（R19 D-01、D-02）。
- **全文投影**：包含技术块、结论等，用于材料查询和用户批准的全文补偿。
- **原件投影**：抽取 pdf/pptx/xlsx/csv/图片（OCR）等已登记文件的文本。

实现：`index/`、`services/indexing.py`。

## 4. 阅读会话

用户查询主题相关知识或找已有经验时，默认先 `reading.start`。简查选 quick，只采纳召回窗口并写笔记；完整阅读选 standard。两种策略均保留工作台可见的会话。独立 `query.*` 查询和 `memory.resume` 对象续接不会创建会话，明确只要片段/候选列表或直接查看开发状态时可使用。

```text
start：建立会话，召回发现投影 → 每个对象 2–4 个互补窗口（可选 CE 只调窗口次序；
       显式条件只调对象次序，冲突的排后、不删除）
assess：三态判断（必须带 digest 和理由）；用户改判优先
        uncertain → peek 更多窗口或问用户；quick 时标出采纳的窗口
read：只读 relevant；首次读取固定对象 HEAD；按页读完（对象更新则从第 1 页重读）
note：standard/associative 须读完全部页（定点阅读读完定点页）；出处必须已交付，可细到 L1 块与结论，补全为固定引用；
      可附探索线索与扩展锚点 expand_from（最多 3 个，必须在出处中）
synthesize：跨对象综合，固定所依据的笔记
全文补偿：仅当发现召回不可用、不完整或不足 → ask_user(fulltext) → 用户批准 → recall(fulltext)
联想 = 图扩展（R19）：associative 策略，读完并写好笔记后 → recall(association, focus=当前缺口)
      种子 = 笔记出处（锚点 1.0、其余 0.3）；只走跨对象的边一跳，概念与来源只作中转（热门的不中转）；
      按焦点相关度过滤、按对象汇总 → 最多 expand_owners 个新候选，窗口为关系线索（不可作出处）；
      扩展来的对象默认定点阅读（只读被指到的节点），scope=full 升级为整读并计入整读上限；受轮数与 graph_edges 预算限制
扩大检索：放宽条件或换更宽的问题 → ask_user(broaden) → 用户批准 → recall(broaden, question)
finish：交接文本 = 有效综合稿或逐对象笔记；陈旧笔记、超上限笔记不交付并列为缺口
所属对象：start 的 owner_id 还没有任何记录时，本会话的召回都不收它（只有业务卡，无可读材料；回执 skipped_owner_ids）
复用：start 的输入与刚做过的归属判定相同、参与检索的对象及其索引都没变时，直接用那一轮召回与筛选包
      （回执 reused_from；缺口现算）；判定时的判断不带进会话，照常 assess
用户侧：respond / override / direct（作为下一轮图扩展的焦点）/ configure / abort / archive → 写入收件箱，随下次调用交付
阅读与归属判定都不走 RRF 图路线（R19 D-08）
```

所有 agent 写操作都可带 request_id，重试时不会重复执行；召回轮次受会话预算约束。

实现：`services/reading/`；工作台页面 `frontend/src/features/reading/`。

## 5. 关联、概念与关系图

```text
图投影（每次同步对象时与检索条目一起更新，可删除重建）：
    节点：对象、记录、L1 块、结论、概念页、被引用的来源（restricted 与被隐藏的记录不进图）
    边（通用抽边，新记录类型不改代码）：记录归属与块依赖 | sources 与 payload 中任意固定引用 |
        正文 links | 结论的依据、relation 两端与派生的快捷边 | association 两端（rejected/withdrawn 除外）|
        复核中的 superseded
    category 按关系名查表：structure / evidence / reference / navigation / concept / claim_relation
关联候选（保存后只对变化的记录计算）：正文提到概念名或别名却没链接；已存向量的近邻（前 5，相似度 ≥ 0.75）
    → relations.candidates（已判断过的端点对不再出现）→ 改写链接或 relations.decide
关联：relations.decide 保存（带 certainty、origin、observed_in）；只给两端和说明时
      按快速添加处理：related_to + speculative + accepted；关联不能是 supports/input
概念库：配置 graph.concept_owner_id 指定的 knowledge 对象，概念页可按 domains 筛选或屏蔽（其他 knowledge 对象是通用知识对象）
```

- 关联只做导航：不会进入 supports 证据，也不会改变复核状态。
- 关系图页（`relations.graph`）读取图投影，不新鲜的对象先补齐图（不调用嵌入模型）；可按边类别、节点类型、领域筛选；颜色区分类别，线型区分确定度，形状区分节点类型。同一对节点之间的多条边在画布上合并为一条连线（颜色取证据 > 结论关系 > 关联 > 概念 > 引用 > 结构中最靠前的类别，悬停列出全部关系与方向），节点大小按相连节点数计。矛盾、替代、支持、输入依据、依赖/前提几种重点关系另用线型、粗细和箭头形状区分（红色长虚线＋挡板、紫色点划线、加粗实线、燕尾箭头、蓝灰点线＋方块），并有单独图例；这只影响展示，不改变图投影和计数口径。节点面板可以快速添加关联。
- 记录筛选按 kind 提供概览（L4）、经验（L3）、经过（L2）、技术单元（L1）、原始依据（L0），并可单独按文稿和文稿章节筛选；不另设重复的层级筛选。节点类型可独立勾选对象、记录、结论（Claim）、概念、来源和内容块，默认显示前五类并隐藏内容块；全部取消时显示空图。kind 只过滤记录，结论、内容块等作为导航上下文仍可见。筛选偏好保存后可重置，视图偏好保存在本机。

**工作台笔记（R15）**：关系图旁的笔记（分组名、桥接问题、关系设想）不是研究记录，用户可以把它交给 AI：

```
草稿 ──用户：交给 AI──▶ queued ──AI：relations.note_reply──▶ proposal（方案，只预演）→ 待你确认
                                                          question → 需要你回答 → 用户回答 → queued
                                                          declined → 已处理（不写入）
待你确认 ──用户：执行（relations.note_apply）──▶ 核对各目标对象 HEAD 未变 → 按方案原样写入 → 已处理（附写入的记录）
                                             └ HEAD 已变或某步失败 → 退回 queued（已写入的步骤如实列出，不回滚）
         ──用户：退回（附意见）──▶ queued
```

- 方案的步骤只能是 `memory.commit` 或 `relations.decide`，最多 10 步，同一对象只提交一次；服务端逐步 dry_run，并从操作本身生成预览（新建或修订的记录、结论原文、关联两端）。
- 写入者是起草方案的 AI，记录的保存原因带"依据工作台笔记 WN-n 起草，经用户在工作台批准后写入"。
- AI 在续接（`memory.resume` 的 `workbench_notes`）和收口时查看 queued 笔记；新建、交给 AI、执行、退回、清除都只能由用户在工作台完成。
- 草稿和未结束的笔记可以编辑；待你确认时编辑会作废方案、退回 AI。已处理的笔记默认折叠保留，可重新打开后编辑，"清除已处理"由用户手动执行；编号不复用。

实现：`index/graph.py`、`services/relations.py`、`services/notes.py`、`services/memory/associations.py`、`services/memory/concepts.py`、`services/memory/links.py`、`core/links.py`。

## 6. 正文和必要上下文怎样组装

```text
assemble(已选候选)：
    每个候选取固定版本正文（技术单元按所选块，并补齐声明的必要定义块）
    沿 prerequisite 引用补齐必要依据（context_reach 决定可否跨出所选范围）
    依据不能加入 → 记缺口，并把父材料标为 incomplete
    整块加入，预算不够就整块省略并报告，不截断公式或定义
    formal 用途：只输出在指定范围内已确认、有效的结论原文
```

组包无缺口不代表研究完整，也不会隐式二次检索。

实现：`services/query/assemble.py`。

## 7. 组装、读取文稿和生成研究经过的区别

| 操作 | 做什么 | 不做什么 |
|---|---|---|
| 组包（query.assemble） | 把已选材料和必要依据拼成阅读包 | 不生成新内容，不读全文 |
| 读取文稿（memory.document） | 按文稿根 → 章节 → 技术块的编排展开全文、目录、单章或依据变化影响 | 不生成或修改文稿；依据有新修订时只提示，不自动替换 |
| 阅读会话（reading.read） | 优先读对象的研究过程文稿，没有文稿时按 L4→L2→L3→L1 读各记录正文 | 不判断相关性 |
| 写经过、概览、文稿 | 由 AI 或人实际阅读后撰写，经 memory.commit 保存 | 程序不会自动生成 |

## 8. 来源变化后怎样维护

```text
监测：比较对象 HEAD 与所引原件的指纹（可 dry_run）→ 观察记录；原件指纹变化时登记维护候选（open/planned/dismissed）
影响：provenance.impact 沿依赖找到下游（supports/input 是正式依赖，导航只提示）
计划：工作台勾选候选 → maintenance.plan_from_candidates（只含所选记录，候选标为 planned）；
      或 maintenance.plan(changed_ids, targets?) 列出受影响记录及其完整正文
处置：用户把计划交给 AI（queued）→ AI 逐项决定 retain / revise（附草案）/ resynthesize / retract / defer
      → maintenance.review 预检 → 待你确认（reviewed）；用户可附意见退回（回到 queued，处置保留）
原件有快照时（R21 D-09）：计划附 source_changes（快照与当前原件逐段对比：unchanged / changed / removed / added），
      转写自它的 L1 附 origin_changes（认领的分段哪些改了、删了；没变的只需改指新登记的原件）
可见范围补齐（R19 §8）：maintenance.visibility_candidates 列出旧默认存成 owner_only 的知识记录 → 用户勾选 →
      maintenance.plan_visibility 生成处置已写好的计划（改为全库可见、正文不变，直接 reviewed）→ 预览后执行
引用改写（R21 D-10）：maintenance.ref_rewrite_candidates 列出正文写了长编号的记录 → 用户勾选 →
      maintenance.plan_ref_rewrite 把能确定指向的编号机械替换为短名链接（直接 reviewed）→ 预览后执行
提交：只由用户在工作台执行 maintenance.apply，按对象逐个提交（依据版本不一致则拒绝），可能部分完成；
      修订署名为写处置的 AI，修订原因带计划编号；撤回结论的复核署名为执行的用户
回读：再按 consolidate 流程检查概览、文稿和上下文
```

- 观察和候选不改变任何证据或复核状态。
- 工作台上不手写修订稿：处置与修订稿由 AI 起草，用户看逐条预览（修订后的正文、当前正文、预检结果）后执行或退回（R16）。
- 复核在内容变化后自动失效，需要重新复核。

实现：`services/monitor.py`、`services/maintenance.py`、`services/memory/provenance.py`。

## 9. 复核与正式使用

```text
review.queue：跨对象列出结论及状态（未复核、已失效、有争议、依据受阻、待确认升级……）、原因、
    有效成熟度与派生统计（独立依据按原件去重、反例与未处理反例、被引用、被采纳反馈）
review.submit：确认需要范围与证据；替代需要替代结论；dry_run 预检
    可同时登记成熟度：preliminary 需依据，replicated 需至少两份独立依据，
    established 需 accepted + 证据类型 + 依据，且执行者不能是 ai（ai 只能提交 requested_maturity）
    复核记录绑定"所在记录的内容指纹 + 结论指纹"，任一变化 → 已失效（成熟度随之失效）
检索：记录的复核类别（none/accepted/unreviewed/mixed/unsafe）进入投影，按 review_filter 过滤
正式用途：只返回在指定范围内 accepted、未失效、来源可见的结论
```

谁可以确认结论见 [skills/RULES.md 第 4 节](../skills/RULES.md)。

实现：`services/review.py`、`services/memory/claims.py`。

## 10. 知识中枢与 Wiki

```text
desired(策略)：从各对象当前 HEAD 选出应发布内容（默认：已复核的 L4/L3 + 依据完整的成稿文稿 + 宿主编写的 wiki_page）
plan：与映射对比 → 新增/更新/删除/标记撤回 + plan_hash
apply(plan_hash)：哈希一致才执行；映射丢失时按外部 local_key 认领；逐条记录结果
status：轮询外部异步解析
search：联邦检索，命中能对上本地时附固定引用和按本地当前状态计算的复核类别
中枢路线（index.hub_route）：材料查询时外部命中映射回本地记录，作为一路参与融合；外部不可用只报告缺口
外部候选（hub.candidates_pull）：外部工具发现的关系 → 关联候选（origin=tool:<id>），从不直接写成记录
LightRAG（图模式）：条目附带本条目声明的整理图；upsert/delete 只写暂存，apply 结束后 finalize：
    无变化跳过 | 只有新增 → 追加 | 有修改或删除 → 新目录整体重建、成功后切换；嵌入按文本指纹缓存
交换包（exchange.export）：Markdown 库 + 图 JSONL + manifest，按敏感级别过滤、增量写入；mdvault 用同一渲染器发布
wiki：外部被人修改（漂移）时不覆盖，除非显式 force
```

实现：`services/hub.py`、`connectors/`，以及独立包 `packages/burrowl-connector-weknora/`。

## 11. 数据目录、工作目录与迁移

安装更新读取生成目录中的文件，不读取源码工作树。默认构建生成离线 zip；`packaging/build.py --install` 完整构建后直接安装生成目录。同版本也会同步更新文件，`BUILD_INFO.json` 和安装输出标识实际构建。安装不会替换已有进程加载的 Python 模块，更新后须重启工作台（首页"重启服务"或 `burrowl restart`）和 MCP，再刷新浏览器。

**工作台的运行（R21 D-12–D-15）**

```text
burrowl start：已在运行 → 给出网址；否则以无窗口后台进程启动（安装包里是 python\burrowl-workbench.exe，
    开发环境是 pythonw），等它就绪后给出网址。输出写 .burrowl/logs/serve.log
网址固定：端口与 token 第一次启动时选定，存 .burrowl/server.json；端口被占用时换一个并保存
burrowl open：start + 打开浏览器（开始菜单"burrowl 工作台"快捷方式就是它）
burrowl stop：经工作台接口请求正常退出，超时再按进程号结束（先核对进程创建时间，避免误杀复用了 PID 的程序）
首页"工作台服务"：运行版本 / 已安装版本；重启 = 新进程经 current 联接加载新版本，等旧进程退出后接管同一端口
    （server.info / server.stop / server.restart，用户操作，不暴露给 MCP）；AI 客户端里 stdio 的 MCP 需在客户端重启
被强行结束（任务管理器）后 server.json 留下的进程信息，下次 status / start 发现进程不在就清除
```

- **数据目录**存放 burrowl 的全部状态（记忆、会话、Run 登记、配置）；**工作目录**是用户和 AI 放原始数据与代码的地方，原件不复制进数据目录。导入时进入数据目录的只有原文快照（抽取的文本）和从原件中复制出的图片附件，它们随备份迁移；原件本身随 `burrowl backup` 打包、`restore --map` 重定向。
- 记录引用原件只经来源登记（SRC- ID + 登记时指纹），从不在记录里写路径。所以原件搬家只需改登记，固定引用不变。
- 工作目录登记为**工作根**（ROOT-…）；其中的来源记为"根 + 相对路径"。MCP 以 `--work-dir` 启动时，登记该目录下的文件会自动建根。

```text
登记 sources.register(绝对路径)：
    在数据目录内 → 改写为相对数据目录的路径（按对象检查访问权限）
    落在已有工作根下 → {root, 相对路径}；否则落在本次工作目录下 → 自动建根
    其他 → 绝对路径
    记录登记时 sha256；同 ID 只能幂等重复，内容变化用新 ID
目录搬家 sources.relocate_root(根, 新目录)：新目录下每个已登记文件指纹一致才改根；否则拒绝
打包 burrowl backup：数据目录逐文件 + 外部已登记原件（只打包与登记指纹一致的；变化或缺失的列入清单）
恢复 burrowl restore 包 新目录 [--map 根ID=新目录 | 旧前缀=新前缀]：
    校验全部指纹 → 为每个根/绝对路径来源决定去向：
        有映射 → 新位置；原位置仍是同一批文件 → 不动；否则 → <新目录名>-work/<根>/
    目标处已有不同内容的文件 → 整体中止，什么都不写
    写数据文件和原件 → 改写新数据目录的来源登记（relocated_from 留痕）
```

实现：`storage/sources.py`、`portable/bundle.py`、`interfaces/cli.py`（`mcp-config`、`mcp`、`backup`、`restore`）与 `interfaces/agents.py`（Skill 安装与升级）。

## 12. 投入分级、归属判定与关键操作记录

```text
定档：档 0 直接回答（不调用）| 档 1 一次性小任务（结束时沉淀检查，有内容才往下走）| 档 2 持续工作
memory.owner_route(task, variants?, protected_terms?, paths?, owner_hint?)：只读
    召回 + 筛选包 = ReadingService.preview（与 reading.start 第一轮同一实现，不建会话）
    补充：前 limit 个候选的目标 objective、最新检查点 next_step；paths → 来源重叠 / 同目录
          用户指定与有路径证据的对象即使不在召回前列也补入
    → 候选与证据（不给建议动作）
AI：选暂定对象 → memory.resume 完整阅读 → 同一目标：第一次 memory.commit 带 attribution（确认点）
                                         → 不相关：换候选或新建（确认前不写入暂定对象）
确认后放错：在正确对象重新保存并引用原记录，原结论 superseded 或建关联（consolidate Skill）
判定之后的阅读（R18）：确认或新建对象 → reading.start(owner_id, question = 判定时的 task)
    沿用已有对象：复用判定的召回；新建对象：它已进入索引（owner_create/adopt 立即建投影），重新召回
    判定与续接时觉得"可能相关"的对象不直接写成关联；实际读完、验证后由 AI 写记录时引用或 relations.decide
关键操作记录 memory.activity：业务卡创建 | 回执 attribution | 提交（按层级计数一行）| 复核记录 |
    Run 结束（ended_at）| 中枢执行记录 → 时间倒序归并、分页；写操作返回值附同样的一行摘要
```

- `attribution` 只存在提交回执里，进入请求指纹（同一 request_id 改了理由视为不同请求），不进记录内容指纹。
- 关键操作记录不另存日志；受限记录不计入摘要；检索、阅读、归属判定调用、来源登记不在其中。

实现：`services/routing.py`、`services/activity.py`、`services/reading/service.py`（`preview`、`screen`）、`services/reading/reuse.py`、`storage/memory_store.py`（`read_head`、`commit_records`、`read_receipt`）。
