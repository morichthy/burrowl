# R12 知识图谱与检索升级：设计与实施计划

| 项 | 内容 |
|---|---|
| 状态 | P0–P4 实现完成（T-P4.4 WeKnora 实测需要用户的 API Key）；待用户的验收与决定见 §10 末尾（2026-10-09） |
| 决定记录 | [DEVELOPMENT_HISTORY 的 R12](../DEVELOPMENT_HISTORY.md#r12-知识图谱与检索升级2026-10-09) 写演进思路和被放弃的方法；本文只写"做什么、怎么做、怎样算完成" |
| 适用范围 | 记录契约（CORE）、规范写入（MEM）、检索（IDX、QUERY、READ）、关系（MAINT）、复核（REVIEW）、中枢与连接器（HUB）、工作台、Skill 与文档 |
| 维护方式 | 每完成一个任务，更新 [§10 进度记录](#10-进度记录)；决定有变化时先改 §2 并在 DEVELOPMENT_HISTORY 补记，再改代码 |

## 0. 怎样使用本文件（给 AI 和子 agent）

- **编号是稳定的**：决定写作 `D-xx`，任务写作 `T-Pn.m`，验收写作 `AC-Pn.m`。代码注释、测试名、提交信息、交接文本都引用编号（例如 `# R12 D-15：成熟度不写进内容`）。
- **开工前**：读 §1、§2，以及要做的那一阶段（§6）。涉及数据形状时读 §4、§5。
- **遇到本文没写的情况**：先回到 §2 的决定找依据。依据不足时停下来问用户，不要自创方案；用户确认后先改本文。
- **子 agent 的任务说明**至少附上：任务编号、对应的 D-xx、要改的文件、验收条件，以及"不要做什么"。
- **本文是计划，不代表功能已经实现。** 已实现的行为以代码、测试、CORE 和 ARCHITECTURE 为准；某阶段完成后，把稳定的说明迁移到这些文档（见各阶段的"文档维护"），本文只保留计划和进度。
- 术语：现有术语见 [GLOSSARY](../GLOSSARY.md)。本文新引入的术语见 [§4.0](#40-本文新术语)，实现后再登记进术语表。

## 1. 目标与非目标

### 目标

1. **找得到**：修复向量通道的截断缺陷；用自然单元加必要上下文做嵌入，让检索单元与 L0–L4 结构一致。
2. **关联得对、找得全**：建立人和 AI 整理的知识图，包括概念、结论、关联、证据关系。图可以展示，可以用于检索中的联想补充，并且随新知识增量维护。
3. **复核分层**：在现有复核状态之外，增加证据成熟度，以及支持、反例、使用情况的派生统计。
4. **对外可转化**：定义带版本的交换格式（Markdown 库 + 图 JSONL）。外部知识库和检索工具通过薄适配器接入，本地检索始终可以独立工作。
5. **可验证**：建立小型检索评估集，每一步改动都在同一批问题上比较效果。

### 非目标（本轮明确不做）

- 不在 burrowl 内置 LLM 抽取管线（不在每次提交时调 LLM 抽实体或写摘要）。语义判断由宿主 AI 在工作和收口时完成（D-03）。
- 不实现 Graphiti 式的双时间模型、社区摘要（GraphRAG global search）或 RAPTOR 递归摘要树。
- 不把 Markdown 或外部系统当作真源（D-01）。
- 不更换 SQLite，也不实现 Qdrant 适配器（沿用 R2 的暂缓）。
- 不在本轮实现 late chunking（D-20），只保留为对照实验。

## 2. 已定决定

| 编号 | 决定 | 一句话理由 |
|---|---|---|
| D-01 | **一个真源，三种派生**：规范记录（JSON 不可变提交，正文为 Markdown）是唯一真源；本地索引（含图投影）、交换包（Markdown 库 + 图 JSONL）、外部系统的索引都是派生数据，都可以重建 | 避免多份真源逐渐不一致；Markdown 无法可靠承载不可变历史、指纹和校验 |
| D-02 | **不追求外部图与内部图一致**。一致性只在边界上保证：① 发布对账保证内容版本一致；② 外部命中必须能取回本地 ID 并回到固定版本核验；③ 整理过的边单向导出；④ 外部自动生成的结构只作为候选回流 | 外部系统的构图方式各不相同且不受控制，强求结构一致做不到，也没有必要 |
| D-03 | **核心资产** = 记录契约（L0–L4 与写作规则）+ 整理图（概念、关联、合并判断、结论间关系）+ 复核与成熟度 + 来源追踪。**可交给外部的** = 自动构图、图检索算法的高级变体、自动 Wiki、主题综合、浏览界面 | 前者是外部系统没有的、研发知识最值钱的部分；后者变化快且有成熟的外部实现 |
| D-04 | 本地保留完整的基线检索（关键词 + 向量 + 内置图路线）作为回退；外部检索只是可选的一路 | 外部不可用、或数据不宜外送时，系统仍然可用；阅读流程需要回到固定版本核对 |
| D-05 | **内置图检索在 SQLite 中自研，不使用 LightRAG 做回退** | LightRAG 查询时仍要调 LLM 抽关键词；它自带存储会多出一份要同步的副本；依赖重、接口变动频繁 |
| D-06 | L0–L4 的职责不变。新增**知识节点**，与层级正交：结论（已有）和概念（新增）。层级是容纳知识节点的容器 | 层级作为存储单位是合适的；图需要的是可以被单独引用和判断的节点 |
| D-07 | **L1 可以写结论**（kind 为 fact 或 calculation），并可通过 `block_id` 绑定到结果块 | 实验事实产生在 L1；否则 AI 为了得到一个结论节点不得不硬写 L2/L3，或者干脆不写 |
| D-08 | L1 拆分规则：一个技术单元围绕一个问题或方法；条件明显不同、且结论能各自独立复用时拆开 | 让每个检索入口和图节点的含义清楚 |
| D-09 | **统一的可读链接写法**（§4.3）。AI 只写可读名；提交时由程序解析并固定版本，写进 `links` 字段。正文中不出现 UUID 或哈希 | 降低 AI 负担，同时保持"引用不会悄悄指向新版"的规则 |
| D-10 | **统一边模型 + 通用抽边规则**（§4.4）：所有关系画在一张图上，用边属性区分；新记录类型只要使用标准引用或链接，就自动进入图。不新增基类，不合并现有记录类型 | 现有记录外壳和固定引用已经是事实上的基类；通用抽边可以保持记录类型的扩展自由 |
| D-11 | **结论是节点**，用可选的 `relation` 字段（主体、谓词、对象）表达带条件的二元关系；由此派生一条"快捷边"，条件始终留在结论上 | 三元组会丢失条件和限定语（上下文塌缩）；研发结论几乎都有条件 |
| D-12 | **全库只有一个概念库**，放在专门的 knowledge 对象里。概念带 `domains` 和 `category_path`，可以按领域筛选或屏蔽。同名不同义的概念用限定名区分，例如"收敛（数值）" | 跨领域的连线往往就是类比和迁移；没有连线的领域在图上会自然分开 |
| D-13 | 概念的合并和拆分用记录修订表达（`status`、`merged_into`、`split_into`），从不删除；判断原则是"相关不等于相同" | 保留历史；旧链接通过重定向继续有效 |
| D-14 | 关联新增 `certainty`（speculative / plausible / supported）、`origin`、`observed_in`，并新增关系类型 `related_to`。快速添加时默认为 `related_to` + speculative。**"A 会影响 B"这类可以验证的判断写成结论，而不是关联** | 能保存"有迹象但证据很少"的关联；导航和可验证的判断分开 |
| D-15 | **成熟度存在复核记录里，不写进记录内容**。现有的 `knowledge_facets.confidence` 是作者对内容的自评，保留但不作为成熟度 | 复核绑定内容指纹；如果把成熟度写进内容，每次评估都会让复核失效，形成死循环 |
| D-16 | 成熟度分四级：conjecture 设想、preliminary 初步、replicated 重复验证、established 已确立。established 按证据类型认定（真实工程案例、严格证明、公认结果、充分的重复实验），与 accepted 是同一道门。**程序强制：actor.kind=ai 不能设置 established，只能提出 `requested_maturity`**；可以由人（工作台/CLI）或经授权的验证流程（actor.kind=workflow）确认。确立之后仍然可以被推翻 | 最高等级必须非常谨慎；没有绝对确认，只有"目前证据充分" |
| D-17 | 支持数、反例数、未处理的反例、被引用次数、作为 Run 输入的次数、被采纳的反馈数，都是**派生统计**。统计支持数时按原始来源（L0 根）去重。"用得多"不等于"对" | 统计可以重算，不应成为第二份真源；同一份数据派生出的多条记录不能算成多份支持 |
| D-18 | 复核状态、成熟度和各项统计**从不写进记录正文**，只在渲染（导出、展示）时出现在 YAML 头和自动生成的区块里 | 同 D-15 |
| D-19 | 嵌入方面：先修截断（P0）；再换长上下文模型（首选 Qwen3-Embedding-0.6B，以实测为准）；用确定性的**上下文信封**（只加入向量文本，不加入 FTS 文本）；记录级和块级两种粒度都生成向量；L0 原件按结构切分并加标题链 | 当前问题主要是窗口太小；记录长度都在 2k token 以内，可以整体嵌入；按结构切分在全库检索中优于让 LLM 决定切分点 |
| D-20 | 先做"按结构补上下文"；late chunking 只作为 P1 之后的独立对照实验。上下文信封只放标题链和简短字段，**不把同一段概览复制到每个块** | 两种方法同时上，无法判断收益来自哪里；复制概览会让不同块的向量越来越相似 |
| D-21 | **交换格式 v1** 由 burrowl 定义并维护版本：Markdown 库 + 图 JSONL + manifest。每个外部工具一个薄适配器，只做"交换格式 → 该工具"的映射 | 内部字段变化时只改导出器；新工具接入的成本很小 |
| D-22 | 第一个本地外部连接器选 LightRAG（先做图模式，即导入我们的图，跳过它自己的抽取）；WeKnora 保留，作为团队或中心知识库。**2026-10-09 修订**：自定义图导入不能按文档删除（T-P4.0），改为"按需重建 + 嵌入缓存 + 后台替换"：只有新增时追加，有修改或删除时在新目录整体重建、成功后切换 | 不需要 Docker；支持导入自定义图；重建时未变内容的嵌入来自缓存，代价主要是文件读写 |
| D-23 | 外部检索作为 RRF 中的一路（"中枢"信号族）；命中必须取回本地 ID，回到固定版本核验后才能交付；外部不可用时自动退回本地，并在缺口里报告 | 落实 D-02、D-04 |
| D-24 | **评估集先行**（P0）。每一阶段都在同一批问题上比较，没有收益就不默认开启；图路线默认关闭 | ARCHITECTURE 评价标准 3；教训 E4；文献显示图在简单查找上可能变差 |
| D-25 | 送给外部工具处理（尤其是 LLM 抽取）的内容按敏感级别过滤，默认 confidential 及以上不发送 | 数据不随意离开本机 |
| D-26 | 概念的名称和别名参与查询的术语扩展；现有术语词库 `.burrowl/query-terms.json` 保留，本轮不合并 | 复用已有的扩展机制；两者是否合并留到后续 |
| D-27 | **契约只做增量**：新字段一律可选，**不设默认值、不自动补空值**，不提升 schema_version；只放宽约束，不收紧 | 已有结论的 claim_hash 和记录的 content_hash 保持不变，已有复核不会失效 |
| D-28 | 记录新增可读的 `handle`（同一对象内唯一，创建后不可变；未提供时由服务按标题生成）；对象业务卡可选 `short_name`；结论链接使用结论 ID 的短名部分 | 链接可读，标题可以随时修改 |

## 3. 现状核对（2026-10-09，依据当前工作树）

### 3.1 向量截断缺陷（实测）

- [`index/embed.py`](../../packages/burrowl/src/burrowl/index/embed.py) 中 `WINDOW_TOKENS, WINDOW_STRIDE, TITLE_TOKENS = 360, 300, 40`，代码注释写着"避免长中文片段被静默截断"。
- 但 fastembed 0.9 加载 `paraphrase-multilingual-MiniLM-L12-v2` 时，分词器自带截断：`{'max_length': 128, 'strategy': 'longest_first', 'direction': 'right'}`。访问路径是 `FastEmbedEmbedder.model.model.tokenizer.truncation`。128 也是该模型训练时的上限。
- 复现方法：两段文本前 159 token 相同、后续内容完全不同，`embed_passages` 得到的两个向量余弦相似度为 **1.000000**。
- 后果：每个窗口只有 128 token（包括特殊符号和最多 40 token 的标题前缀）进入向量，正文约 86–126 token；按步长 300 计算，**约 58%–71% 的正文对向量通道不可见**。关键词通道（FTS5）不受影响。`embed_query` 允许 512 token 的查询，同样会被截断。

### 3.2 记录长度（浮点样例，XLM-R 分词器）

| 内容 | 中位数 | 最大值 |
|---|---|---|
| L1 单个块 | 约 220–465（随块角色不同） | 477 |
| L1 整条记录（全部块） | 1319 | 1747 |
| L2 正文 | 1070 | 1092 |
| L3 / L4 正文 | 1543 / 1724 | — |
| 文稿章节 | 239 | 523 |

全部在 2048 以内。结论：换用 8k 或 32k 的模型后，记录级整体嵌入可以直接实现；真正需要切分的只剩 L0 原件。

### 3.3 已有能力与缺口

| 已有 | 位置 | 缺口 |
|---|---|---|
| 三类投影：fulltext（记录、L1 块、结论各一条）、discovery、source | `index/projections.py` | 嵌入时只带记录标题，不带层级路径、块角色、检索说明和依赖的定义块 |
| RRF 融合；按 Owner 汇总时每个信号族只投一票 | `index/fusion.py` | 没有图路线，也没有中枢路线 |
| 关联记录（candidate / accepted / rejected / withdrawn；只做导航） | `services/memory/associations.py`；schema `AssociationPayload` | 没有确定度、提出者、发现场合；没有 `related_to` |
| 关联候选（关键词 Jaccard 或向量近邻，需手动调用） | `services/relations.py: similar` | 保存时不会自动产生候选；不涵盖结论和概念 |
| 有界图遍历（沿作者引用和已采纳关联） | `services/query/relations.py: deepen` | 只在显式调用 deepen 时使用；每次实时读取记录 |
| 关系图（每次请求实时扫描所有记录） | `services/relations.py: build_graph`；`frontend/src/features/Relations.tsx` | 没有概念节点和结论节点；边没有分类 |
| `wiki_page`（slug 必须是 ASCII，`<类型>/<名称>`；有别名） | schema `WikiPagePayload`；`core/records.py: _WIKI_SLUG` | 不在发现投影中；正文没有链接，也没有反链 |
| 结论（`claim_id`、陈述、类型、适用范围、依据）只能写在 L2–L4 | schema `Claim`；`core/records.py` | L1 不能写；没有结构化关系 |
| 复核（not-reviewed / accepted / disputed / retracted / superseded；绑定内容指纹） | `services/review.py`、`services/memory/claims.py` | 没有成熟度 |
| `knowledge_facets.confidence`（high / medium / low，写在内容里） | `core/facets.py` | 不能用作成熟度（D-15） |
| `representation` 记录类型（同一内容的不同检索表达） | schema `RepresentationPayload` | 不参与检索（DEVELOPMENT_HISTORY 第三部分：暂缓）；本轮继续暂缓 |
| 术语词库（中英名称、别名、单跳关联词） | `storage/terms.py`、`index/query_plan.py` | 与概念页的别名是两套 |
| 中枢联邦检索（命中能对上本地时附固定引用） | `services/hub.py: search` | 没有进入召回融合 |
| mdvault 连接器（Obsidian 风格的 Markdown） | `connectors/mdvault.py` | 内容格式简单；没有链接、文稿嵌入、图文件 |
| 执行者类型：MCP 为 `ai`，工作台为 `human`，CLI 可选 | `interfaces/mcp.py`、`interfaces/api.py`、`interfaces/cli.py` | 可以据此由程序强制 D-16 |

## 4. 目标模型

### 4.0 本文新术语

实现后登记进 GLOSSARY。

| 术语 | 定义 | 不是什么 |
|---|---|---|
| 知识节点 | 可以被单独引用、判断和修订的图节点：结论、概念（以及作为容器的记录、块、对象、来源） | 不是层级 |
| 概念页 | 概念库对象中 `page_type` 为 concept 或 entity 的 `wiki_page`；名称加别名就是概念的身份 | 不是证据，也不是结论 |
| 链接 | 正文里的 `[[…]]` 写法；提交时解析并固定版本，存入 `links` | 不是证据关系，除非写在依据字段里 |
| 图投影 | 从记录派生的节点表和边表，存在 index.sqlite 中，带水位，可以重建 | 不是真源；要修改关系应修改记录 |
| 边类别 | structure / evidence / reference / navigation / concept / claim_relation | — |
| 快捷边 | 由结论的 `relation` 字段派生出的 主体 → 对象 的边，继承该结论的成熟度和复核状态 | 不独立存在；结论被撤回时随之失效 |
| 成熟度 | 一条结论在证据阶梯上的位置，存在复核记录中 | 不是复核状态，也不是主观概率 |
| 上下文信封 | 嵌入前由程序拼在文本前面的标题链和简短字段 | 不进入 FTS 文本，不写入记录 |
| 交换格式 | burrowl 定义的导出包：Markdown 库 + 图 JSONL + manifest | 不是真源 |
| 图路线 / 中枢路线 | RRF 融合中的两个新信号族：从种子沿图扩展得到的候选；外部检索得到的候选 | 都只是导航，不是证据 |

### 4.1 数据关系总图

```text
                      规范记录（唯一真源，MemoryStore 不可变提交）
                                  │ 程序确定性派生（可以重建）
        ┌─────────────────────────┼──────────────────────────────┐
  本地索引 index.sqlite      交换包 exports/<名称>/            渲染视图
  fulltext / discovery /     vault/*.md（YAML 头 + [[链接]]）   工作台记录页、概念页、
  source / 向量 / 图投影     graph/nodes.jsonl, edges.jsonl    关系图；Obsidian
  link_candidates            manifest.json
        │                         │ 薄适配器（文本模式 / 图模式）
        │                    外部工具（LightRAG、WeKnora、Obsidian、qmd…）各自建索引
        │                         │
  检索：关键词 + 向量 + 图路线 + 中枢路线 → RRF → 回到固定版本核验 → 交付
  外部生成的实体、关系、页面 → link_candidates → AI 在收口时判断 → 写成记录
```

#### 直观示例：一条记录会变成哪些点和线

**AI 只写记录；程序把记录"翻译"成点和线。** 要改图，只能改记录；图投影删掉后可以重新算出来。

以浮点样例中的一条 L3 经验为例（简化）。记录外壳和 payload 的含义见 [CORE §2](../CORE.md#2-结果怎样保存到本地)：

```text
外壳   record_id MEM-58fd…   owner_id RES-浮点求和   kind experience / L3
       body_markdown "在 [[大数抵消]] 输入下，[[补偿求和]] 仍可能丢失小量……（见 [[@第三轮实验#kahan]]）"
       sources [固定引用 → L1「第三轮实验」#kahan，relation=supports]
payload claims: [{ claim_id CLM-浮点-补偿抵消失效, statement "大数抵消时补偿项会被吞没",
                   scope "有限 binary64 输入、存在大数抵消",
                   evidence_refs [→ L1#kahan，supports],
                   relation {subject [[补偿求和]], predicate fails_on, object [[大数抵消]]} }]
```

程序从中得到下面这张图（实线 = 作者写下的关系，虚线 = 关联，`⇢` = 由结论派生的快捷边）：

```text
              RES-浮点求和（对象）
                 ▲ belongs_to（structure）
  L3 补偿经验 ──mentions──▶ [[补偿求和]] ⇢fails_on⇢ [[大数抵消]]
     │  ┆                       （快捷边由 CLM-浮点-补偿抵消失效 定义，继承它的成熟度和复核状态）
     │  ┆ same_failure_mode（navigation，certainty=speculative）
     │  ▼
     │  L2 第三轮经过
     │
     ├──supports（evidence）──▶ L1 第三轮实验 #kahan 块 ──input──▶ SRC 原件
     ▼ 结论属于这条记录
  CLM-浮点-补偿抵消失效（成熟度 preliminary，复核 not-reviewed）──supports──▶ L1 #kahan 块
```

对应关系：

| 作者写的东西 | 在图里是 | 精确规则 |
|---|---|---|
| 对象、记录、L1 块、结论、概念页、已登记来源 | **点**（结论虽然写在记录的 payload 里，但有独立 ID，可以单独引用和复核，所以单独成点） | §4.2 |
| `sources` 和 payload 中的固定引用 | **边**，type 就是引用里写的关系名 | §4.4 |
| 结论的 `evidence_refs` | 从结论点出发的 **evidence 边** | §4.4、§4.5 |
| 正文和结论陈述里的 `[[链接]]` | **mentions 边**（指向概念），或 reference 边（指向记录、块、结论） | §4.3 |
| association 记录 | 作为记录保存，画出来是两端之间的**一条边**；说明、certainty、状态都是这条边的属性 | §4.7 |
| 结论的 `relation` | 主体 → 对象的**快捷边** | §4.5 |
| 成熟度、复核状态、标题、领域 | 点或边上的**属性**，不是独立的点 | §4.8、§4.10 |

**category 和 type 的区别**：

- type 是具体关系，比如 supports、same_problem、mentions。它基本由"写在哪里"决定：
  - 依据字段只能填证据类关系；
  - `[[概念]]` 固定为 mentions；
  - 关联从导航类枚举中选，说不清时默认 related_to；
  - 只有结论的谓词使用开放词表。
- category 是关系所属的大类，**由程序按 type 查表得出，AI 从不填写**。它决定线怎么画、检索时的权重，以及是否计入支持数（§4.4）。

### 4.2 知识节点与层级

| 节点类型 | node_id 形式 | 来源 |
|---|---|---|
| owner | `RES-…` 等 | 业务卡 |
| record | `MEM-…` | 任何记录（review、policy、feedback 不进图） |
| block | `MEM-…#<block_id>` | L1 的 `blocks`（只建有入边或出边的块） |
| claim | `CLM-…` | L1–L4 的 `payload.claims` |
| concept | 概念页的 `MEM-…` | 概念库中 `page_type ∈ {concept, entity}` 的 `wiki_page` |
| source | `SRC-…` | 被引用的已登记来源 |

层级的写法规则增加以下内容（P2 写进 RECORDING）：

- L1 可以写 fact / calculation 类结论，可选 `block_id` 指向结果块（D-07）；L1 的拆分规则见 D-08。
- L4 用 `[[概念]]` 链接本对象涉及的主要概念；L3 链接问题结构和失败模式涉及的概念；L1 和 L2 按需链接。
- 结论的陈述里可以使用链接，例如"在 [[有限 binary64 输入]] 下，[[补偿求和]] 仍可能被 [[大数抵消]] 吞没"。

### 4.3 链接写法与解析（D-09、D-28）

| 指向 | 写法 | 解析规则 |
|---|---|---|
| 概念 | `[[补偿求和]]` | 在概念库中按标题、别名做精确匹配（忽略大小写，并规范全角和半角）；已合并的概念自动转向 `merged_into` |
| 记录（同一对象） | `[[@第三轮实验]]` | 本对象内按 `handle` 匹配（包括同一批新建的记录） |
| 记录（其他对象） | `[[@浮点求和/第三轮实验]]` | 斜杠前是对象的 `short_name`，没有时用 owner_id |
| 块 | `[[@第三轮实验#kahan]]` | 记录 + `block_id`（只对 L1 有效） |
| 结论 | `[[!补偿抵消失效]]` 或 `[[!浮点求和/补偿抵消失效]]` | 匹配 `claim_id` 以 `-补偿抵消失效` 结尾的结论（先找本对象，再找指定对象）；也可以写完整的 `[[!CLM-…]]` |
| 显示文字 | `[[@第三轮实验\|那次 Kahan 实验]]` | 竖线后面是显示文字 |

解析发生在 `MemoryService.prepare` 中，位于 `_replace_client_keys` 之后：

```text
扫描范围：body_markdown；detail 每个块的 markdown；document_section 每个 prose 块的 markdown；
         每个 claim 的 statement。payload 中的其他字符串不扫描。
对每个链接：
    概念：找到 → {target_kind: record, target_id: 概念页 ID, revision: 当前修订, sha256, relation: "mentions"}
          找不到 → 不阻塞提交；记为 {text, target: null, status: "unresolved_concept"}，
                   并出现在 index.gaps（missing_concept），等待收口时新建或合并概念
    记录 / 块 / 结论：找到 → 固定当前版本（relation: "references"；块的 locator 为 "block:<id>"）
          找不到或有歧义 → 提交失败 UNRESOLVED_LINK，details 中给出候选
          （dry_run 同样返回候选，AI 改写后重新提交）
结果写入记录外壳的 `links` 字段：[{text, ref | null, status}]
```

**固定版本规则（重新固定）**：修订已有记录时，链接文字没有变化的，沿用上一修订里固定的版本；只有新增的链接，或者操作里显式写了 `repin: true`，才固定到当前版本。这和"目标更新后不会悄悄指向新版"保持一致。

`links` 是服务端写入的字段，草案中不能提供（与 `SERVER_FIELDS` 同样处理），但它参与 `content_hash`（D-27：没有链接时**省略这个键**，不写空数组）。

`handle`：草案中可选；新建时如果没有提供，由服务根据标题生成（保留中文，空白改为 `-`，冲突时追加 `-2`、`-3`）；在同一对象内唯一；创建后不可修改（试图修改会得到 `INVALID_TRANSITION`）。`handle` 不参与 `content_hash`，但参与 `record_hash`。

### 4.4 统一边模型（D-10）

**边的属性**：`edge_id`（由 declared_by、field、src、dst、type 计算的哈希）、`src`、`dst`、`category`、`type`、`certainty`、`state`、`origin`、`declared_by`（记录 ID）、`declared_rev`、`field`（来源字段）、`derived`（是否由程序推出）。

| category | type（举例） | 从哪里抽取 | 检索默认权重 | 计入支持数 |
|---|---|---|---|---|
| structure | belongs_to、contains、part_of、depends_on、produces | 记录归属；文稿的 `section_refs` 和 unit 块；L1 块；`requires_block_ids`；Run 业务卡 | 0.3 | 否 |
| evidence | supports、input、contradicts、supersedes | `sources`、各类 `evidence_refs`、结论的依据；状态为 superseded 的复核 | 1.0 | 只有 supports 和 input |
| reference | references、background、derived_from、prerequisite、refines、used_in | `sources` 和 payload 中其他固定引用；记录/块/结论链接 | 0.6 | 否 |
| navigation | same_problem、same_failure_mode、analogous_to、related_to … | association 记录（不包括 rejected 和 withdrawn） | supported 0.7 / plausible 0.4 / speculative 0.2 | 否 |
| concept | mentions、subject_of、object_of | 概念链接；结论 `relation` 的主体和对象 | 0.5 | 否 |
| claim_relation | 结论 `relation` 的谓词（improves、fails_on、causes …） | 由结论派生的快捷边（`derived=true`） | 0.6 × 成熟度系数 | 否 |

**通用抽边规则**（在 `projections.graph` 中实现）：① `sources`；② payload 中任意位置的固定引用（复用 `services/memory/claims.py: iter_refs` 的遍历逻辑，具体实现放在 `index` 层的纯函数中，不跨层导入）；③ `links`；④ 结论的依据和 `relation`；⑤ association 的两个端点；⑥ 复核中的 superseded。新增记录类型无需修改抽边代码。

**画法**：颜色按 category，线型按 certainty（实线、虚线、点线），candidate 状态用浅色；可以按 category、domain、层级开关显示。

### 4.5 结论（D-07、D-11）

`Claim` 新增两个可选字段（D-27，都不设默认值）：

```json
"block_id": "kahan",
"relation": {"subject": "[[补偿求和]]", "predicate": "fails_on", "object": "[[大数抵消]]"}
```

- `block_id` 只能出现在 L1 的结论里，且必须是本记录中存在的块。
- `relation.subject` 和 `relation.object` 是一个概念链接或结论链接；`predicate` 来自一个开放词表：improves、degrades、causes、prevents、fails_on、applies_to、requires、equivalent_to、contradicts、part_of，以及 `x-` 开头的自定义谓词。
- 快捷边：主体 →（谓词）→ 对象，`declared_by` 为结论 ID，状态继承该结论的复核类别和成熟度。结论变为 unsafe（disputed、retracted、superseded、stale）时，快捷边在检索中被排除，在图中显示为失效。

### 4.6 概念库（D-12、D-13）

- 新增配置项 `graph.concept_owner_id`（默认 null）。为 null 时，概念链接全部记为 unresolved_concept，不阻塞任何操作。概念库对象由 AI 或用户用 `memory.owner_create`（type 为 knowledge）建立后，填入这个配置。
- `WikiPagePayload` 新增可选字段：`concept_kind`（method / phenomenon / failure_mode / tool / dataset / material / metric / other）、`domains`（字符串数组）、`category_path`（最多 3 段）、`status`（active / merged / split，缺省视为 active）、`merged_into`（固定引用）、`split_into`（固定引用数组）。
- `slug` 改为可选：没有提供时由服务生成 `concept/c-<记录 ID 的前 8 位十六进制>`，只用于外部中枢；放宽 `_WIKI_SLUG` 的检查，只在提供了 slug 时校验。
- 唯一性：在概念库中，所有状态为 active 的页面，其标题和别名（规范化之后）不能重复。重复时提交失败，并给出冲突的页面。
- 概念页写入原则（写进 consolidate Skill）：
  - 只为"被实质讨论过，或至少在两条记录中出现"的概念建页（参照 WeKnora 的 standard 粒度）；
  - 正文可以只是一句短定义加适用领域；
  - 新建前先查名称、别名和向量近邻，按"相关不等于相同"判断，相同就合并或加别名。
- 合并：保留页出一个新修订，加入被并页的名称作为别名；被并页出一个新修订，`status=merged`，填 `merged_into`。拆分：新建多个页面；原页面 `status=split`，填 `split_into`。

### 4.7 关联（D-14）

`AssociationPayload` 新增可选字段：`certainty`（speculative / plausible / supported）、`origin`（human / ai / tool:<连接器 id>）、`observed_in`（固定引用）。`Ref.relation` 和 `AssociationPayload.relation` 的枚举中加入 `related_to`。

- 缺省时：certainty 视为 plausible（为已有数据保持原有语义），origin 视为 unknown。**这是读取时的解释，不写回记录。**
- 快速添加（工作台的"添加关联"按钮，以及 AI 在工作中随手登记）：只需要两端和一句说明，自动设为 `relation=related_to`、`certainty=speculative`、`status=accepted`、`origin` 按执行者填写。
- `relations.decide` 的参数中增加这三个字段；`analogous_to` 仍然要求填写共享结构和迁移边界。
- **判断规则**（写进 RULES）："值得放在一起看"写成关联；"A 会影响 B"这类可以验证的判断写成结论（kind 为 inference），初始成熟度为 conjecture，并链接两端。

### 4.8 成熟度与"已确立"（D-15、D-16）

`ReviewPayload` 新增可选字段：

| 字段 | 取值 | 约束 |
|---|---|---|
| `maturity` | conjecture / preliminary / replicated / established | established 要求 `state=accepted`、提供 `evidence_basis`，且 `evidence_refs` 非空 |
| `evidence_basis` | engineering_case / proof / authoritative_source / replication | 只在 established 时必填 |
| `requested_maturity` | established | 只能与 `state=not-reviewed` 一起出现，表示"提议升级" |

判定标准（写进 RULES 第 4 节）：

| 级别 | 判定标准 | 谁可以登记 |
|---|---|---|
| conjecture 设想 | 有推理，没有数据 | AI 或人，写明理由 |
| preliminary 初步 | 至少一次 Run 或小样本支持；可能存在未处理的反例 | AI 或人，必须引用依据 |
| replicated 重复验证 | 多个工况或多次独立执行都成立，已知反例都已处理 | AI 或人，必须引用多份**独立**依据 |
| established 已确立 | 四类证据之一：真实工程案例（项目记录中有结果）、严格证明（逐步核对或形式化检查通过）、公认结果（引用教科书或标准）、充分的重复实验 | **只有人（工作台/CLI）或经授权的验证流程（actor.kind=workflow）**。AI 只能提交 `requested_maturity`，由用户在复核队列中一键确认 |

- 程序强制：在 `services/review.py` 中，`actor.kind == "ai"` 且 `maturity == "established"` 时拒绝，错误码 `PERMISSION_DENIED`，提示改为提交 `requested_maturity`。
- **有效成熟度** = 该结论最新一条未失效复核中的 `maturity`。结论或所在记录的内容变化后，复核失效，有效成熟度随之失效；显示为"失效（原为 X）"。
- 出现未处理的反例时只做标记，不自动降级。

### 4.9 派生统计（D-17）

由 `services/memory/claims.py` 的 `ClaimGraph` 扩展计算，不持久化（性能不够时再缓存进 index.sqlite，仍属于派生数据）：

| 统计 | 定义 |
|---|---|
| `supports` | 以 supports 或 input 指向本结论（或其所在记录）的依据数，**按 L0 根来源去重**：沿 derived_from、input 追溯到同一 `SRC-…` 或同一 Run 的，只算一份 |
| `contradicts` | contradicts 关系的数量 |
| `open_counterexamples` | contradicts 的来源方是结论、且该结论尚未被处理（不是 retracted 或 superseded）的数量 |
| `cited_by` | 在其他记录的依据字段中引用本结论或其所在记录的记录数 |
| `run_inputs` | 被 Run 登记为输入的次数 |
| `feedback_adopted` | `memory.feedback` 中 `adopted_in` 非空的条数 |

这些统计出现在 `review.queue` 的条目、`memory.inspect` 的结论状态和渲染视图中。

### 4.10 图投影

- 表结构，在 `index/db.py` 中新增：
  - `graph_nodes(node_id PK, node_type, owner_id, record_id, title, level, kind, domains, sensitivity, discovery, review, maturity)`
  - `graph_edges(edge_id PK, src, dst, category, type, certainty, state, origin, declared_by, declared_owner, declared_rev, field, derived)`
  - 索引：`(src)`、`(dst)`、`(declared_owner)`
- 生成：纯函数 `projections.graph(owner_info, records, badges, hidden) -> (nodes, edges)`，版本号 `GRAPH_VERSION = "graph-v1"`。`IndexService.sync_owner` 按对象写入；水位放在 `index_state` 中，corpus 为 `graph`，channel 为 `edges`。
- 跨对象的边由声明它的那个对象负责写入和删除（按 `declared_owner`）；另一端不存在或不可读时，边保留，查询时用内连接排除，并在结果中报告悬空数量。
- 敏感级别为 restricted 的记录和被隐藏的记录不进入图（与 fulltext 投影的规则一致）。
- 消费者：图路线（§4.11）、`relations.graph`（工作台）、交换包中的图文件、`link_candidates`。

### 4.11 检索

#### 上下文信封（P1，D-19、D-20）

纯函数 `projections.envelope(owner, record, block=None, section_path=None) -> str`，例如：

```text
浮点求和误差研究 › L1 技术单元：Kahan 补偿实验 › results
问题：比较补偿求和与朴素求和在大数抵消输入下的误差
```

- L1 的块：加上块角色、`retrieval_description.question`，以及 `requires_block_ids` 指向的定义块原文（超出信封预算时，只放定义块的标题）。
- 文稿章节：加上"文稿 › 章节"路径。
- 信封预算不超过模型窗口的 25%；超出时按顺序裁掉：定义块原文 → 问题 → 层级说明，标题链始终保留。
- 信封只加入**向量文本**（在 entries 表中新增 `embed_prefix` 列），FTS 文本不变。

#### 多粒度向量（P1）

- fulltext 投影中 detail 的记录级条目，文本改为"检索说明 + 全部块正文"；块级条目保持不变。两者共用同一个 `canonical_id`，`fusion.collapse` 会自动折叠。
- 文本不超过窗口时整条嵌入；超过时才分窗，窗口大小按模型的真实上限计算。

#### 图路线（P2，默认关闭）

```text
输入：同一查询计划中关键词和向量路线融合后的前 seeds 个命中（记录、结论、概念页）
扩展：对每个种子走 hops 跳（默认 1，最多 2），每个节点最多扩展 fanout 条边
      边权 = category 权重 × certainty 系数；
      节点惩罚 = 1 / log2(2 + 节点度数)，防止热门概念把无关内容带进来
      排除：rejected/withdrawn 关联、unsafe 结论的快捷边、被排除的对象、悬空边
得分：score(n) = Σ_种子 RRF(种子) × Π_路径上 边权 × 节点惩罚
映射：把节点映射回**当前所查投影**中、通过同样过滤（层级、类型、复核过滤、敏感级别、owner_only）的条目；
      映射不到的节点丢弃（例如发现投影中没有的 L1 块）→ 不破坏"发现与全文读取分开"
输出：一个路线批次（kind=graph，channel=graph），按 config 中的权重进入 fuse_routes；
      每个命中附带 via：[{from, edge type, declared_by}]
预算：复用 ledger 的 graph_nodes / graph_edges 计数
```

- 新增配置项 `index.graph_route`：`enabled`（false）、`hops`（1）、`seeds`（20）、`fanout`（20）、`weight`（0.5）、`category_weights`（取 §4.4 的表）、`algorithm`（`hops`，可选 `ppr`，ppr 是可选任务 T-P2.9b）。
- `query_plan.routes` 不变：图路线在 `IndexService.search_plan` 中，等其他路线结束后追加执行。

#### 中枢路线（P4）

- 新增配置项 `index.hub_route`：`connectors`（[]）、`weight`（0.5）、`timeout_s`。
- 用 `HubService.search` 的命中，按本地键找到本地记录，再映射到当前投影的条目（同样的过滤规则）。对不上本地的命中不进入融合，只在结果的 `external_only` 中作为提示返回。

### 4.12 交换格式 v1（D-21）

导出目录：数据目录下的 `exports/<名称>/`（用户工作数据，派生，可以删除重建）。

```text
exports/<名称>/
  manifest.json            {format: "burrowl-exchange/1", generated_at, data_dir_id, items: [{local_key, path, record_id, revision, sha256}]}
  vault/
    README.md              格式说明、链接规则、"本目录由 burrowl 生成，只读"
    concepts/<概念名>.md
    records/<对象短名>/<handle>.md
    documents/<对象短名>/<文稿 handle>.md
  graph/
    nodes.jsonl            {id, type, title, owner, level, kind, domains, review, maturity, local_key, path}
    edges.jsonl            {id, src, dst, category, type, certainty, state, declared_by, derived}
```

记录页的格式：

```markdown
---
burrowl_id: MEM-…            # 内部身份
local_key: MEM-…@3           # 记录 ID@修订，外部系统必须原样保存
handle: 第三轮实验
owner: RES-FLOATING-POINT-SUMMATION
kind: detail
level: L1
revision: 3
sha256: …
aliases: []
domains: []
claims: [{id: CLM-…, maturity: replicated, review: accepted}]   # 渲染时生成，D-18
---
# Kahan 补偿实验
## kahan
……正文，其中的 [[链接]] 改写为 vault 内的相对文件名……
## 结论
- 补偿求和在大数抵消下仍可能丢失小量 ^c-1a2b3c4d
<!-- burrowl:generated:start -->
## 关联与引用（自动生成）
……反链、关联（带确定度）、复核历史……
<!-- burrowl:generated:end -->
```

- 文稿页用 `![[records/<对象>/<handle>#<block_id>]]` 嵌入技术块，**不复制正文**。块以 `## <block_id>` 标题输出，使 Obsidian 的标题嵌入可以使用。
- 结论锚点用 `^c-<结论 ID 哈希的前 8 位>`，因为 Obsidian 的块 ID 只能使用 ASCII。
- 导出前按敏感级别过滤（D-25）。导出采用增量方式，按 manifest 比较：新增、更新、删除。

### 4.13 外部连接器的原则（D-02、D-21、D-22、D-23）

- 适配器只读取交换格式（或调用同一个渲染器），不直接读取内部记录。
- 每个适配器配合约测试：发布 → 检索 → 取回 `local_key` → 回到固定版本核验。
- 运行模式：`text`（交给工具自己抽取）、`graph`（导入我们的图）、`both`。
- 外部工具产生的实体、关系和页面通过 `candidates()` 接口回流到 `link_candidates`（origin 为 `tool:<id>`），从不直接写成记录。

## 5. 契约变更清单（P2 一次完成；D-27）

修改 `core/schema/memory.schema.json`（这是现行契约，直接编辑；`tools/extract_schema.py` 是一次性的历史工具，不再运行），同步修改 `core/records.py` 中的领域校验和 `core/contract.py` 中的说明，然后运行 `tools/gen_draft_examples.py`。

| 位置 | 变更 |
|---|---|
| 各记录类型 Draft / Record 的外壳 | 新增可选的 `handle`（string，1–64 字符，不能包含 `/ # \| [ ] @ !`）；新增服务端字段 `links`（array，草案中禁止提供） |
| `core/records.py` | `CONTENT_FIELDS` 加入 `links`，`content_hash` 改为只取存在的键（**已有记录的哈希必须保持不变，需要测试**）；`SERVER_FIELDS` 加入 `links`；放宽 `_WIKI_SLUG` 的检查 |
| `Claim` | 新增可选的 `block_id`、`relation{subject, predicate, object}` |
| `TechnicalUnitPayload` | 新增可选的 `claims`（Claim 数组），kind 只能是 fact 或 calculation |
| `AssociationPayload` | 新增可选的 `certainty`、`origin`、`observed_in`；relation 枚举加入 `related_to` |
| `Ref.relation` | 枚举加入 `related_to`、`subject_of`、`object_of` |
| `WikiPagePayload` | `slug` 改为可选；新增可选的 `concept_kind`、`domains`、`category_path`、`status`、`merged_into`、`split_into` |
| `ReviewPayload` | 新增可选的 `maturity`、`evidence_basis`、`requested_maturity` |
| 对象业务卡 | 可选的 `short_name`（由 `storage/owners.py` 读取） |
| 配置 `core/config.py` | `IndexConfig.embedding` 新增 `max_tokens`、`query_prefix`、`passage_prefix`；新增 `IndexConfig.graph_route`、`IndexConfig.hub_route`；新增顶层 `graph: {concept_owner_id}` |

改完以后：运行 `uv run python tools/gen_reference.py`，以及 `npm --prefix frontend run ops`。

## 6. 实施阶段

各阶段可以按顺序交付，每个阶段结束时都要满足验收条件，并在 §10 记录。P0 的两个任务互不依赖。

### P0 修复截断，建立评估集

**T-P0.1 按模型真实上限分窗**（`index/embed.py`）

- `FastEmbedEmbedder.__init__` 读取 `self.model.model.tokenizer.truncation["max_length"]`（读不到时，使用配置 `embedding.max_tokens`，再读不到用 512），保存为 `self.max_tokens`。
- 标题前缀上限 = `min(40, max_tokens // 4)`；正文窗口 = `max_tokens - 2 - 前缀实际 token 数`；步长约为窗口的 5/6。
- 删除模块常量 `WINDOW_TOKENS`、`WINDOW_STRIDE`；指纹中写入实际窗口参数，这样换参数会自动重建向量。
- `embed_query` 的上限改为 `self.max_tokens`。查询超过上限时，截断并在检索结果的 `gaps` 中加入 `{"channel": "dense", "reason": "query_truncated"}`，不再抛错（调用方 `IndexService.search`、`search_plan` 负责把这条缺口传上去）。
- 测试：
  - `tests/unit`：用桩分词器（上限 32）验证每个窗口的 token 数不超过上限，且窗口覆盖全部正文。
  - `real_model` 标记的测试：复现 §3.1 中的余弦实验，修复后，窗口内的不同内容应得到不同的向量。
- **AC-P0.1**：浮点样例中所有向量窗口的 token 数都不超过模型上限；`index.reconcile` 后向量全部重建；关键词检索的结果不变。

**T-P0.2 检索评估集与评估脚本**

- 题目文件格式（JSONL，每行一题）：`{id, question, variants[], type: fact|multihop|conditional|negative, expected_owners[], expected_records[], must_not[], notes, checked_by_user: bool}`
  - `must_not` 用于检查"条件错误"：列出在该条件下不应排在前面的记录。
- 两套题：
  - `tests/fixtures/eval/floating-point.jsonl`：基于浮点样例，约 12 题，进入回归测试，只看相对变化。
  - 用户数据目录下的 `eval/retrieval-v1.jsonl`：40–60 题。由 AI 从真实记录中出题，用户抽查；标注时保留题目的上下文。
- 脚本 `tools/eval_retrieval.py --data-dir <目录> --set <文件> [--corpus discovery|fulltext] [--label <名称>]`：
  - 对每题调用 `IndexService.search_plan`（group 为 owner 和 record 各一次）；
  - 输出 `eval/runs/<时间>-<label>.json`，包含配置指纹、模型指纹、投影版本、逐题命中；
  - 同时打印 Markdown 汇总表。
- 指标：Recall@5、Recall@10（对象级和记录级）、MRR@10、条件错误率（`must_not` 出现在前 10 的比例）、延迟 p50/p95、前 10 个筛选窗口的 token 总数。
- 先跑两条基线：B-1（修复截断之前）和 B0（修复之后）。
- **AC-P0.2**：两套题都能运行；两条基线的结果写入 §10。

文档维护：在 ARCHITECTURE 的已知差距中删除截断缺陷；在 TESTING 中补充评估脚本的用法；在 DEVELOPMENT_HISTORY 的 E6 中更新状态。

### P1 本地基线：长上下文模型与上下文信封

- **T-P1.1 模型选型实测**：写一个 `tools/bench_embed.py`，在本机测量候选模型（Qwen3-Embedding-0.6B、它的 -Q 量化版、EmbeddingGemma-300m，必要时加上 bge-m3）的三项指标：每秒处理的 token 数、峰值内存、浮点样例和用户数据的全量重建时间。
  - 同时确认 fastembed 对 Qwen3 的查询指令前缀是怎么处理的；如需手动添加，写进 `embedding.query_prefix`。
  - 结果写入 §10，由用户选定模型。
- **T-P1.2 模型安装与配置**：`index/model_store.py` 和 `burrowl models install` 支持选定的模型；在 `Embedding` 配置中加入 `max_tokens`、`query_prefix`、`passage_prefix`；修改 `dim`。
- **T-P1.3 上下文信封**：实现 `projections.envelope`（§4.11）；在 entries 表中新增 `embed_prefix` 列；`IndexDB.sync_vectors` 嵌入时使用"前缀 + 文本"。
- **T-P1.4 记录级整体向量**：按 §4.11 修改 detail 的记录级条目。
- **T-P1.5 索引结构自动重建**：`IndexDB.__init__` 发现 `schema_version` 或分词器版本不匹配时，删除并重建所有表，把所有对象标记为待更新，然后由 reconcile 补齐。不再抛出 INTEGRITY_ERROR；索引是派生数据，可以重建。
- **T-P1.6 L0 原件按结构切分并加标题链**：`index/extract.py` 中，Markdown/文本按标题切分，PDF 按页，PPT 按幻灯片；每段加上"文件名 › 标题路径"前缀（进入 `embed_prefix`）。
- 投影版本：`FULLTEXT_VERSION`、`DISCOVERY_VERSION`、`SOURCE_VERSION` 全部升为 v2。
- 评估：依次跑 B1（只换模型）、B2（加信封）、B3（加记录级向量），对比 B0。
- **AC-P1**：B3 在 Recall@10（记录级）和 MRR 上不低于 B0，并且在 fact 或 conditional 类题目中至少有一类明显提升。如果没有提升，在 §10 写明原因，再决定是否保留各项改动。

文档维护：CORE §3（召回中的信封和多粒度向量）；ARCHITECTURE 的技术栈（模型）；README 中的模型安装说明（如果有变化）。

### P2 整理图：概念、结论关系、成熟度、图投影、图路线

按依赖顺序排列：

- **T-P2.1 契约**：完成 §5 的全部变更，加上草案示例：新增 `concept_page.json`、`association_quick.json`、L1 带结论的示例，修改 `review.json`；运行 `tools/gen_draft_examples.py` 和 `test_draft_examples.py`。
  - 测试：**已有记录和结论的 `content_hash`、`claim_hash` 不变**（用浮点样例全量比对）。
- **T-P2.2 链接**：
  - 新增 `core/links.py`：纯函数，负责语法解析和规范化，不访问存储。
  - 新增 `services/memory/links.py`：解析链接目标（概念库、本对象及其他对象的 handle、结论）。
  - 在 `MemoryService.prepare` 中接入；实现"重新固定"规则；`UNRESOLVED_LINK` 时返回候选。
  - 测试：每种写法都能解析；歧义时报错；同一批新建的记录可以互相链接；未解析的概念不阻塞提交；没有变化的链接保持原来固定的版本。
- **T-P2.3 handle 与 short_name**：生成、唯一性、不可修改（`MemoryService.prepare`、`storage/owners.py`）。
- **T-P2.4 概念库**：`graph.concept_owner_id` 配置；active 页面的名称和别名唯一；合并和拆分的校验与重定向；概念别名进入查询术语扩展（修改 `index/query_plan.py` 中词库的读取来源，加入概念别名，并在词库指纹中体现）。
- **T-P2.5 成熟度**：修改 `services/review.py`：maturity 的约束、执行者强制规则（D-16）、`requested_maturity` 出现在 `review.queue` 中；工作台复核页增加"确认升级为已确立"的按钮（调用 `review.submit`，执行者为 human）。
- **T-P2.6 派生统计**：扩展 `ClaimGraph`，接入 `review.queue` 和 `memory.inspect`。
- **T-P2.7 图投影**：实现 `projections.graph`；修改 `IndexDB` 的表结构和 `sync_graph`；修改 `IndexService.sync_owner`、`reconcile`、`status`。
- **T-P2.8 关系页改为读取图投影**：`services/relations.py: build_graph` 改为从图投影读取（投影不新鲜时先补齐该对象）；`relations.graph` 增加 `categories` 和 `domains` 筛选；前端 `Relations.tsx` 按 category 和 certainty 区分样式，节点面板增加"快速添加关联"。
- **T-P2.9 图路线**：在 `IndexService.search_plan` 中实现 §4.11 的算法；增加 `index.graph_route` 配置；命中带 `via`；记入预算。
  - 测试：只返回当前投影和过滤条件允许的条目；热门节点惩罚生效；rejected 关联不参与扩展；关闭时结果与之前完全一致。
  - **T-P2.9b**（可选，评估显示有需要时再做）：个性化 PageRank。
- **T-P2.10 关联扩展与候选**：
  - `relations.decide` 支持新字段和快速添加的默认值。
  - 新增派生表 `link_candidates(src, dst, kind, score, origin, computed_at)`：保存后计算本次变化的记录——提到了概念别名却没有链接的情况，以及向量近邻（前 5 个，带阈值）。已经判断过的端点对会被过滤掉。
  - 新增只读操作 `relations.candidates`。
- **T-P2.11 index.gaps 扩展**：L1 块超过 1500 token；结果块没有声明依赖；有未解析的概念；概念页没有任何入边；结论有未处理的反例；有待确认的升级提议。
- **T-P2.12 规则与 Skill**：
  - RECORDING：知识节点、链接写法、L1 写结论、L1 拆分规则、概念链接。
  - RULES：成熟度判定和执行者限制、关联与结论的判断规则。
  - burrowl-work：工作中随手登记关联和概念链接。
  - burrowl-consolidate：处理候选、维护概念（新建、合并、拆分）、成熟度评估、两层体检。
  - 运行 `burrowl skills check`。
- 评估：B4 = B3 加图路线，按题型比较，**尤其关注 multihop 类和 fact 类的副作用**。
- **AC-P2**：
  - 浮点样例中可以建立概念库，链接、合并、快速添加关联、成熟度提议和确认都能端到端完成（服务测试，加上工作台的组件测试或端到端测试）；
  - 契约变更前后，已有记录的哈希不变；
  - 图路线关闭时，B4 的结果与 B3 完全一致；打开时的收益和副作用如实写入 §10。是否默认打开由用户决定。

文档维护：GLOSSARY（§4.0 中的术语）、CORE §1/§3/§5/§9、ARCHITECTURE（状态归属表加入图投影和 link_candidates；数据流）、TESTING（图路线的风险检查）、README（概念库、成熟度、快速添加关联）。

### P3 交换格式与渲染器

- **T-P3.1 渲染器**：新增 `services/render.py`，负责把记录、概念页、文稿渲染成 Markdown（链接改写、YAML 头、自动生成区块、文稿嵌入）。工作台的记录页和概念页也改用这个渲染器。
- **T-P3.2 导出**：新增 `services/exchange.py` 和操作 `exchange.export(name, filter)`（写入数据目录下的 `exports/`，effect 为 write），生成 manifest 和图 JSONL，增量更新。导出时按敏感级别过滤。
- **T-P3.3 mdvault 连接器**：改为写入交换格式的 vault（通过 `PublishItem` 携带渲染好的内容和链接），漂移规则不变。
- **T-P3.4 格式规范**：新增 `docs/reference/exchange-format.md`，写明格式版本、字段含义和兼容规则。
- **AC-P3**：
  - 浮点样例导出后用 Obsidian 打开，链接、反链、文稿嵌入都正常（人工验收，附截图或说明）；
  - 图 JSONL 中节点和边的数量与图投影一致（自动测试）；
  - 再次导出时，没有变化的文件不会被改写。

文档维护：README（导出与 Obsidian）、ARCHITECTURE（exports 的状态归属）、docs/README（新增 reference 入口）。

### P4 外部连接器与中枢路线

- **T-P4.0 LightRAG 探查**：在独立的虚拟环境中锁定一个版本，核实三件事：
  - 自定义图导入接口（`insert_custom_kg` 或等价接口）的签名；
  - 按文档删除的接口；
  - 查询时能否只返回上下文，并且返回的结果里能否拿到来源 ID。

  结论写入 §10；与本文假设不一致时，先修改本文。
- **T-P4.1 `packages/burrowl-connector-lightrag`**（与 WeKnora 连接器同样的结构，通过入口点注册）：
  - 图模式：
    - chunks ← 记录页正文（不含自动生成区块），`source_id` 为 `local_key`；
    - entities ← 概念，以及结论（实体类型为 claim，描述为"陈述 + 适用范围"，这样条件不会丢失）；
    - relationships ← reference、navigation、concept、claim_relation 类的边，描述中带上 certainty。
  - 检索：只取上下文，解析出来源 ID，映射回本地。
  - LLM 通过 oneapi 配置（只用于它查询时抽关键词；如果开启文本模式，也用于抽取）。
  - 合约测试使用一个假的 LightRAG 对象。
- **T-P4.2 中枢路线**：按 §4.11 实现；外部不可用时报告缺口。
- **T-P4.3 外部候选回流**：连接器 SPI 增加可选的 `candidates()`，结果写入 `link_candidates`。
- **T-P4.4 WeKnora 实测**：补上已知差距"未连真实服务实测"。
- 评估：B5 = B4 加中枢路线。
- **AC-P4**：合约测试通过；本机用 LightRAG 完成一次"发布 → 检索 → 核验"的人工验收；外部服务停止时，检索结果仍然正常，并且缺口如实报告。

文档维护：ARCHITECTURE（连接器、数据流）、README（接入 LightRAG）、deploy 说明（如果需要）。

## 7. 评估方案

- 同一批题目、同一份数据快照（记录数据目录的 HEAD 摘要）、只改一个变量，依次运行 B-1 → B0 → B1 → B2 → B3 → B4 → B5。
- 按题型分别报告结果，不只看总平均：图和外部路线可能让多跳类题目变好、事实类题目变差。
- 每次运行都记录配置指纹和模型指纹，结果写入 `eval/runs/`，汇总写入 §10。
- 评估集本身也会演进：新增题目时版本号加一，不改写旧版本。
- 测试通过不代表检索质量好；评估结果也不能证明任何科学结论（TESTING 中的原则）。

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 改契约导致已有复核失效 | D-27；T-P2.1 用全量哈希比对测试 |
| 链接解析给 AI 增加阻力（提交失败） | 未解析的概念不阻塞；dry_run 返回候选；显示名和别名都能匹配 |
| 概念数量膨胀，或出现"优化"这类超级节点 | 概念页的写入门槛（§4.6）；图路线中的节点度惩罚；工作台按领域筛选 |
| 0.6B 模型在 CPU 上重建索引太慢 | T-P1.1 先测；备选 EmbeddingGemma-300m（2k 窗口足够装下现有记录）；量化版 |
| 图路线降低事实类题目的效果 | 默认关闭；按题型评估；权重可以配置 |
| 外部工具的接口变化（LightRAG 等） | 锁定版本；适配器保持轻薄；合约测试；T-P4.0 先探查 |
| 评估标注的"测量陷阱"（去掉上下文后，标注者的一致性从 0.45 降到 0.04） | 标注时保留题目上下文；用户抽查 |
| 长程开发中上下文混乱 | 本文的编号体系；§10 进度记录；每个子任务都附 D-xx 和验收条件 |

## 9. 未决问题（实施到相应阶段时再定）

1. 嵌入模型的最终选择（等 T-P1.1 的实测结果）。
2. 图路线是否默认打开（等 B4 的结果）。
3. 术语词库与概念库是否合并（D-26，P2 之后再议）。
4. 是否支持从 Obsidian 编辑后写回（必须显式形成新修订；本轮不做）。
5. 概念库对象的命名和初始领域划分（P2 开始时与用户确认）。
6. 发现投影中 L2 摘要的长度与召回效果的关系（沿用 DEVELOPMENT_HISTORY 中尚未定案的讨论，可以用评估集回答）。

## 10. 进度记录

| 日期 | 任务 | 结果 | 证据（测试、评估运行、提交） |
|---|---|---|---|
| 2026-10-09 | 方案确定 | 本文完成；尚未改动代码 | DEVELOPMENT_HISTORY R12 |
| 2026-10-09 | T-P0.1 | 完成。窗口按模型真实上限切分（MiniLM 为 128）；标题前缀 ≤ max_tokens/4；超限时从尾部收缩并保证全文覆盖；指纹含切窗规则；查询超限报告 `query_truncated` 缺口。顺带修复：①向量水位不比对模型指纹，换模型后旧水位仍算就绪、向量通道静默为空；②装上模型后 disabled 水位永不补建；③旧指纹的向量行不清理。AC-P0.1：样例 49 个条目 290 个窗口，最大 128 token，0 个超限，全部正文被覆盖；关键词通道代码未改 | `tests/unit/test_embed_windows.py`（含 `real_model`）、`tests/service/test_indexing.py` 两个新测试 |
| 2026-10-09 | T-P0.2 | 部分完成。`tools/eval_retrieval.py` 与 `tests/fixtures/eval/floating-point.jsonl`（13 题：fact 6、conditional 4、multihop 2、negative 1）。真实数据题集按用户要求暂缓。顺带修复：`query_plan.numeric_tokens` 把 `1e16` 切成 `1e`，含科学计数法的问题会让 build 报错 | 见下方基线表 |
| 2026-10-09 | T-P1.3 | 完成。`projections.envelope`：标题链（对象 › 层级：标题 › 块标题）→ 检索问题 → 依赖的定义块原文；`entries.embed_prefix` 只进向量；前缀预算为窗口的 1/4，从尾部裁 | `tests/unit/test_projection_envelope.py` |
| 2026-10-09 | T-P1.4 | 完成。技术单元记录级条目 = 检索说明 + 全部块正文（FTS 与向量都用），块级条目不变 | 同上 |
| 2026-10-09 | T-P1.5 | 完成。`SCHEMA_VERSION=2`；版本或分词规则不匹配时整库清空重建，`index.reconcile` 返回 `index_rebuilt_from` | 同上 |
| 2026-10-09 | T-P1.6 | 完成。Markdown 按标题切节（代码围栏内的 # 不算），过长再按 80 行分；标签仍是行号区间；信封 = 文件名 › 标题路径。PDF 按页、PPTX 按幻灯片原本就是结构切分 | 同上 |
| 2026-10-09 | 投影版本 | fulltext / discovery / source 升为 v2 | — |
| 2026-10-09 | T-P1.1 | 完成。用户选择只测一个模型：Qwen3-Embedding-0.6B-Q（int8，1.1 GB）。fastembed 不会自动加 Qwen3 的查询指令，已写入 `query_prefix`。实测见下方"P1 模型实测"；**默认模型待用户决定** | `tools/bench_embed.py` |
| 2026-10-09 | T-P1.2 | 完成。`model_store.EMBEDDING_MODELS` 登记 minilm / qwen3-0.6b-q；`burrowl models install --embedding-model`；配置 `embedding.max_tokens / query_prefix / passage_prefix`；按模型名自动找本机目录；onnx 在子目录时也能加载；按 token 总量分批编码（长序列补齐会让注意力内存按平方增长） | `tests/unit/test_embed_windows.py` |
| 2026-10-09 | T-P2.1 | 完成。§5 全部契约变更；另加 `Operation.repin`、`knowledge.json` 业务卡类型（KNW-）、业务卡字段 `short_name`、错误码 `UNRESOLVED_LINK` / `PERMISSION_DENIED`。草案示例：detail 带 L1 结论与 handle、association 带 certainty/origin、wiki_page 改为概念页示例（未单独建 concept_page.json：`memory.contract` 按 kind 取示例）。浮点样例全部记录与结论的指纹不变 | `tests/service/test_links.py::test_existing_records_keep_their_hashes`、`test_draft_examples.py` |
| 2026-10-09 | T-P2.2 / T-P2.3 | 完成。`core/links.py`（语法、规范化、handle 生成）、`services/memory/links.py`（解析、候选、重新固定）；`MemoryService.prepare` 分配 handle 并解析链接；同批记录与同批结论可互相链接 | `tests/service/test_links.py` |
| 2026-10-09 | T-P2.4 | 完成。`services/memory/concepts.py`：概念库内有效名称与别名唯一、合并/拆分目标校验、合并转向；阅读会话的查询计划并入问题中出现的概念（最多 8 个） | 同上 |
| 2026-10-09 | T-P2.5 / T-P2.6 | 完成。成熟度登记规则与执行者强制；队列状态 `maturity-requested`；派生统计（独立依据按 L0 根去重）进入 `review.queue` 与 `memory.inspect`；工作台复核页可登记成熟度、确认"已确立"升级 | `tests/service/test_review_service.py`、前端 `features.test.tsx` |
| 2026-10-09 | T-P2.7 / T-P2.8 | 完成。`index/graph.py`（`GRAPH_VERSION=graph-v1`）、`graph_nodes / graph_edges`（SCHEMA_VERSION=5）；`relations.graph` 改读投影（`ensure_graph` 只补图，不调模型），增加 categories / node_types / domains / excluded_domains；关系图页按类别着色、按确定度区分线型、快速添加关联 | `tests/service/test_graph.py`、`test_relations_monitor.py`、e2e 4 项 |
| 2026-10-09 | T-P2.9 | 完成（hops 算法；T-P2.9b PPR 未做）。`index.graph_route` 配置默认关闭；材料查询与阅读会话召回都接入；按查询预算限制节点与边并报告 `graph_budget_truncated`；命中带 `via` | `tests/service/test_graph.py` |
| 2026-10-09 | T-P2.10 / T-P2.11 | 完成。快速添加关联；`link_candidates`（概念提到未链接、已存向量近邻，不调模型）与只读操作 `relations.candidates`；`index.gaps` 新增 6 类缺口 | 同上 |
| 2026-10-09 | T-P3.1 | 完成。`services/render.py`：YAML 头（JSON 写值）、链接改写为库内路径（范围外只留文字）、L1 块 `## <block_id>` 且块内标题降级、结论锚点 `^c-<8位>`、文稿嵌入技术块、自动生成区块（反链、关联、结论状态）；操作 `memory.render`；工作台记录页增加"导出视图"、handle 与正文链接列表 | `tests/service/test_exchange.py` |
| 2026-10-09 | T-P3.2 | 完成。`services/exchange.py` 与操作 `exchange.export`（工具组 exchange 加入默认组）；按敏感级别与依据链过滤；增量写入；图文件含导出记录的节点、被引用的对象与来源，两端都在范围内的边全部导出；`exports/*` 不进备份包 | 同上 |
| 2026-10-09 | T-P3.3 | 完成。`PublishItem` 增加 `path`、`front_matter`；mdvault 按交换格式写 `records/…`、`documents/…`，概念页走 Wiki 通道，链接指向库内 Wiki 文件；旧文件名的条目更新时搬到新路径；漂移规则不变。其他连接器的发布内容不变。顺带修复：wiki_page 的 slug 改为可选后，中枢发布按 `<类型>/c-<8位>` 生成 | 同上；`tests/service/test_hub.py` |
| 2026-10-09 | T-P3.4 | 完成。`docs/reference/exchange-format.md` | — |
| 2026-10-09 | AC-P3 | 自动部分通过（图文件与投影一致、再次导出不改写）。**Obsidian 人工验收待用户**：样例导出在 `build/exchange-sample/vault`（用 Obsidian 打开该目录，检查链接、反链、文稿嵌入） | — |
| 2026-10-09 | T-P2.12 | 完成。RECORDING（L1 结论与拆分、relation、链接写法与各层链接要求）、RULES（成熟度阶梯与执行者限制、关联还是结论）、burrowl-work（随手链接与快速关联）、burrowl-consolidate（候选处理、概念库维护、成熟度评估、两层体检）；`burrowl skills check` 通过 | — |

| 2026-10-09 | T-P4.0 | 完成（独立虚拟环境，lightrag-hku **1.5.7**，假向量 + 假 LLM，不联网）。① `insert_custom_kg(custom_kg, full_doc_id)`：chunks / entities / relationships，导入时不调用 LLM，实体名会被规范化，自环会被拒绝；② **按文档删除不可用**：自定义图导入不写文档状态，`adelete_by_doc_id` 返回 not_found，实体仍在；只能用 `adelete_by_entity` / `adelete_by_relation` 逐个删除（共享的概念实体不能随一条记录删除）；文档自己也注明该路径"不在文档级恢复保证之内"；③ `aquery_data(QueryParam(only_need_context=True, hl_keywords, ll_keywords))` 不调用 LLM（不给关键词时会调用一次 LLM 抽关键词）；返回的 chunk 带我们写入的 `file_path`，实体的 `source_id` 是 chunk 的哈希 ID。**与 D-22 的假设"可以按文档删除"不符，按 §6 的规定先停下来，由用户选择方案后再改本文** | 探查脚本 `scratchpad/lightrag_probe.py`（不入库） |
| 2026-10-09 | T-P4.1 | 完成（用户选定方案 A，D-22 已修订）。`packages/burrowl-connector-lightrag`（入口点 `lightrag`，可选依赖 `[runtime]` 锁定 lightrag-hku==1.5.7）：upsert/delete 只写暂存表；发布后 `finalize()` 比较上次构建——无变化跳过、只有新增且实体名都是新的就追加、否则在新目录整体重建后切换并删除旧构建；嵌入按文本指纹缓存（按模型指纹隔离）；LLM 函数在图模式下一被调用就报错；用逐字符分词器代替 tiktoken（tiktoken 首次使用要联网下载）；chunk 的 file_path 写 local_key 派生的标记（LightRAG 会规范化路径）。中枢侧：SPI 增加 `graph_import` 能力、`PublishItem.graph`、可选 `finalize()`；发布时只附上本条目声明的、端点满足发布策略的实体与关系（结构边和证据边不外发） | `tests/connectors/test_lightrag.py`（假 LightRAG 合约测试 + 真实 1.5.7 在断网条件下通过） |
| 2026-10-09 | T-P4.2 | 完成。配置 `index.hub_route`（connectors 为空即关闭、weight、timeout_s、limit）；材料查询调用 `HubService.search`，命中按本地固定引用映射回当前投影、同样过滤的记录级条目，作为 hub 路线参与融合；超时、出错报告缺口；对不上本地的命中以 `external_only` 缺口提示 | `tests/service/test_hub.py::test_hub_route_and_external_candidates` |
| 2026-10-09 | T-P4.3 | 完成。SPI 可选 `SupportsCandidates.candidates()` 与 `CandidateLink`；`HubService.external_candidates` 只保留两端都对上本地已发布记录的线索；操作 `hub.candidates_pull` 写入 `link_candidates`（origin=tool:<id>，kind=external:*），之后由 `relations.candidates` 判断 | 同上 |
| 2026-10-09 | T-P4.4 | 未做：需要用户在 WeKnora 中创建 API Key 并 `burrowl secret set` | — |

### 阅读入口局部补修（2026-10-09）

本轮局部补修（不计为原 P 阶段任务）：修正“查询主题相关知识”误走独立 `query.search` / `memory.resume`、导致工作台无阅读会话的入口说明。read Skill、MCP 服务说明与工具说明统一默认 `reading.start`（简查 quick、完整阅读 standard），保留低层查询的独立职责；接口回归验证 quick 会话从建立、采纳窗口、笔记到结束始终可经 `reading.list` / `reading.view` 查看。

首页和阅读列表补上每 5 秒轮询与窗口聚焦刷新，发现页面打开后经 MCP 新建的会话；详情继续用 SSE 接收该会话修订。当前安装版本经 MCP 实测得到 finished 会话（`RS-01efd53c-ce22-4d22-a9f2-af09a52779a3`，revision=4，相关对象 1，笔记 1）；quick 依据为采纳片段，非全文覆盖。

验证与交付：后端定向测试 11 项通过，前端组件 8 项通过，真实浏览器端到端 5 项通过（包含页面停留期间新建会话自动出现）；构建、类型检查、ruff、分层依赖与 Skill 检查通过。stdio 启动单项因本机虚拟环境的 Python 路径失效未完成；本轮真实 MCP 阅读链路已实测。修复已同步当前 0.2.0 安装包和 Codex read Skill，安装原件备份位于仓库 build/reading-context-hotfix-backup。README、ARCHITECTURE、CORE、本设计稿与自动生成的操作参考已更新；docs/README、DOCUMENTATION_MAINTENANCE、TESTING 已核对无需修改。

### 待用户决定（2026-10-09）

1. **默认嵌入模型**：建议继续用 MiniLM（数据见"P1 模型实测"）；Qwen3-Q 保留为可选，真实数据题集建立后再测。
2. **图路线是否默认打开**：建议保持关闭，等有概念链接和关联的真实数据评估。
3. **requested_maturity 的放宽**（见"实施中的澄清与偏离"）。
4. ~~LightRAG 方案~~：用户选定 A（按需重建 + 嵌入缓存 + 后台替换），已实现（T-P4.1）。剩下的是本机人工验收：配置 lightrag 连接器 → 发布 → 检索 → 核验（AC-P4）。
5. **真实数据评估集**与 **Obsidian 人工验收**（`build/exchange-sample/vault`）：等用户有空。
6. **概念库**：用户已定名称"概念库"、不预设领域。需要在用户安装新版本、重启 MCP 之后再建（旧版本不支持 knowledge 业务卡，也不能用新版本代码直接改用户数据目录的索引，否则旧版本会因索引结构版本不同而报错）。

### 工作台检索局部补修（2026-10-09）

沿 T-P1.5、T-P2.7 的索引恢复边界补修（不改召回模型或排名）：仅有图水位时总体索引状态必须为 pending，缺失的全文/发现通道明确为 not_indexed；补偿按必要通道集合检查，不能只数已有水位。工作台按实际 `owner_id/channel/reason` 显示缺口，并提供“补齐索引并重试”；搜索不隐式重建，旧请求不覆盖新查询或阅读包。

实际 MCP 复现：虚拟环境对象有 L4 记录，但关键词、向量均 not_indexed；两个关键词都只返回存储与检索对象。通过 `index.reconcile` 补齐后，搜索“虚拟环境”和“存储与检索”分别以对应主题记录排第一，缺口为空，`writes_to_memory=0`。第二项可能仍为语义相近候选，未添加未经评估的向量阈值。

### 关系图筛选局部补修（2026-10-09）

关系图用记录 kind 选项呈现概览（L4）、经验（L3）、经过（L2）、技术单元（L1）、原始依据（L0）及文稿章节，不再同时提供重复的层级筛选；节点可见性独立按对象、记录、结论（Claim）、概念、来源和内容块控制，默认勾选前五类并隐藏内容块。kind 只过滤记录，结论和内容块等导航上下文保留；全部取消节点类型时展示空图。保存与重置均作用于当前筛选。加载视图偏好只读取当前字段，不做旧版字段兼容。本次落实 R12 T-P2.8 已有的 `node_types` 筛选能力，不新增全局设计决定。

文档已同步 CORE 的关系图说明。验证通过：关系图组件新增用例 3 项、后端关系图与监测用例 7 项、真实服务加系统 Edge 的 E2E 新用例 1 项；前端构建与类型检查通过，`git diff --check` 通过。后端借用内置 Python 3.12.14 和项目已有依赖，以临时 `PYTHONPATH` 执行，未改动用户环境。E2E 使用默认 Playwright 配置；临时 wrapper 已删除并恢复状态。截图宽 1360 像素，AI 目视检查未见重叠；这不代替用户人工验收。

### 关系图展示改进（2026-10-09）

只改前端展示（`Relations.tsx`、`style.css`），不改图投影、`relations.graph` 参数或计数口径，不新增全局设计决定。样例工作区中 58 个节点有 361 条边，其中同一对节点的重复声明占 169 条，逐条画成平行线是画面杂乱的主因。改动：① 按无序节点对合并为连线（361→192），颜色取最有分量的类别，两个方向都有边时两端都画箭头，悬停列出每条关系的方向、类型、确定度与失效/候选状态；节点大小改按相连节点数计。② 布局：结构边和叶子边短而硬，语义边长而软，记录围绕所属对象成团。③ 标签分级：对象最早显示，连接数前 15% 的枢纽次之，其余放大后显示；选中节点的邻边不超过 12 条时在连线上写关系名。④ 形状区分节点类型（来源圆角方、结论菱形、概念六边形、对象带光环），社区着色时图例保留形状；复核有问题（disputed/retracted/superseded/stale/upstream-blocked）的节点加红色虚线边框。⑤ 交互：双击节点设为中心，点空白处取消选中，画布随容器尺寸同步，鼠标移出画布复位高亮。⑥ 筛选区改为"组名＋选项"两列；关系类别改为勾选即显示（默认全选，空数组仍表示全部，不能取消最后一类）；画布高度随窗口伸展。

验证：前端构建与类型检查通过；组件测试 22 项通过（新增合并计数、关系类别语义 2 项）；真实服务加系统 Edge 的 E2E 7 项通过。E2E 原断言的说明句改为组名悬停提示和"不选＝全部"，断言随之修改；E2E 还暴露了图标遮挡隐藏复选框的问题，已修复。在浮点样例副本上做了浅色/深色、悬停、选中、双击设中心的 AI 目视检查；这不代替用户人工验收。

### 关系图重点关系线型（2026-10-09）

只改前端展示。箭头放大（arrow-scale 0.55→0.95，结构边 0.7），连线基础宽度 1.2→1.5。重点关系按优先级取一种套用到连线上：矛盾（红色长虚线、挡板端点）、替代（紫色点划线）、支持（加粗实线）、输入依据（燕尾箭头）、依赖/前提（蓝灰点线、方块端点）；只看有效且非候选的边，箭头形状只画在该关系所指的一端。图例增加"重点关系"一行。验证：组件测试 23 项（新增图例 1 项）、E2E 7 项通过；样例中只有输入依据一类重点关系，其余样式仅经构建与图例检查，未在真实数据上目视确认。

### 工作台前端巡检修复（2026-10-10）

只改前端。关系图：① 选中记录后再选其他节点，上一记录的关联候选会残留在画布上，且在新候选返回前可被"采纳"到新节点名下——`useOp` 增加 `stale` 标记（数据不属于当前参数或已禁用），关系图丢弃 stale 候选并在加载时显示"加载中"；② 节点加入画布时即带颜色，消除每次加载约 60 条 cytoscape 映射告警和首帧无色闪烁；③ 标题筛选改为停顿 300 ms 后查询，不再每个按键重取整图并重新布局。其他页面：导航栏待处理请求数改为每 5 秒及窗口聚焦时刷新；知识复核的"已选 N 条"与批量提交只计当前列表可见项，批量确认的依据与单条一致（含上次复核补充的依据），一键确认的预检结果只回填到同一条草稿；复核与系统记忆的搜索框同样停顿后查询。验证：组件测试 27 项（新增 `useOp`/`useDebounced` 与复核筛选 3 项）、E2E 7 项通过；浏览器逐页巡检无控制台错误（仅保留有意设置滚轮灵敏度的提示）。

### 评估记录

新的进度行加在上面的表格末尾；评估结果按运行编号加在本节。

**P0 基线（浮点样例，fulltext，13 题；样例只有一个对象，对象级召回恒为 1，只看记录级的相对变化）**

| 运行 | 通道 | R@5 记录 | R@10 记录 | MRR@10 | must_not 率 | p50 ms |
|---|---|---|---|---|---|---|
| B-1（修复前分窗） | 全部 | 0.764 | 0.944 | 0.759 | 0.154 | 93 |
| B0（修复后） | 全部 | 0.806 | 0.944 | 0.863 | 0.077 | 93 |
| B-1 | 只向量 | 0.694 | 0.972 | 0.670 | 0.154 | 94 |
| B0 | 只向量 | 0.778 | 0.903 | 0.733 | 0.154 | 94 |
| — | 只关键词+身份 | 0.764 | 0.903 | 0.816 | 0.154 | 44 |

分题型：B0 的 fact 类 R@5 从 0.917 升到 1.0，conditional 类 MRR 从 0.75 升到 1.0；multihop 两题都没有改善（R@5 0.333），是后续图路线要回答的问题。题量很小，差异只作方向参考。

**P1 本地改动（模型仍为 MiniLM-128；B1 换模型待定）**

| 运行 | 改动 | 通道 | R@5 记录 | R@10 记录 | MRR@10 | must_not 率 | p50 ms |
|---|---|---|---|---|---|---|---|
| B0（消融复现） | — | 全部 | 0.806 | 0.944 | 0.863 | 0.077 | 99 |
| B2 | + 信封 | 全部 | 0.806 | 0.944 | 0.872 | 0.077 | 99 |
| B3 | + 信封 + 记录级全文 | 全部 | 0.806 | 0.944 | 0.885 | 0.077 | 110 |
| B0 | — | 只向量 | 0.778 | 0.903 | 0.733 | 0.154 | 98 |
| B2 | + 信封 | 只向量 | 0.792 | 0.903 | 0.799 | 0.154 | 99 |
| B3 | + 信封 + 记录级全文 | 只向量 | 0.750 | 0.903 | 0.799 | 0.154 | 115 |
| B3 | — | 只关键词+身份 | 0.806 | 0.903 | 0.819 | 0.154 | 47 |

- 消融用 `--ablate envelope,record_text` 复现了 B0 的数字（0.863），说明开关只改了目标变量。
- 信封对向量通道有效（MRR 0.733→0.799，multihop R@5 0.167→0.5）。记录级全文在 128 token 的模型上只是多出一批窗口：向量通道 fact 类 R@5 从 1.0 降到 0.917，延迟增加约 15%；融合后整体 MRR 仍升到 0.885。它的真正收益要等长上下文模型能整条嵌入时再看（B1 之后重跑 B3）。
- AC-P1 暂判：B3 的 R@10 与 B0 持平、MRR 更高；fact 类 MRR 0.875→0.917。题量只有 13 题，差异仅作方向参考；换模型之前不下结论。
- fp-08 的期望记录之一是文稿 `MEM-ab9db095`，而文稿不进 fulltext 投影，该题记录级召回上限为 2/3。按 §7 "不改写旧版本"，题集 v1 保持原样，下一版再修正。

**P1 模型实测（本机 CPU；浮点样例全量重建，fulltext + discovery）**

| 模型 | 输入上限 | 加载 s | 全量重建 s | 编码 token/s | 峰值内存 MB |
|---|---|---|---|---|---|
| MiniLM-L12（默认） | 128 | 2.3 | 18.8 | ≈2 840 | 1 358 |
| Qwen3-Embedding-0.6B-Q（上限调到 2048） | 2048 | 4.3 | 424.9 | 84 | 9 411 |

Qwen 的重建测量时，全量测试也在运行，耗时有偏高；数量级差异是确定的。

| 运行 | 模型 | 改动 | 通道 | R@5 记录 | R@10 记录 | MRR@10 | must_not 率 | p50 ms |
|---|---|---|---|---|---|---|---|---|
| B0 | MiniLM | — | 全部 | 0.806 | 0.944 | 0.863 | 0.077 | 99 |
| B1 | Qwen3-Q | 只换模型 | 全部 | 0.847 | 0.944 | 0.792 | 0.154 | 1 633 |
| B3 | MiniLM | 信封 + 记录级全文 | 全部 | 0.806 | 0.944 | 0.885 | 0.077 | 110 |
| B3-qwen | Qwen3-Q | 信封 + 记录级全文 | 全部 | 0.833 | 0.972 | 0.785 | 0.154 | 1 782 |
| B0 | MiniLM | — | 只向量 | 0.778 | 0.903 | 0.733 | 0.154 | 98 |
| B1 | Qwen3-Q | 只换模型 | 只向量 | 0.806 | 0.972 | 0.778 | 0.154 | 1 745 |
| B3 | MiniLM | 信封 + 记录级全文 | 只向量 | 0.750 | 0.903 | 0.799 | 0.154 | 115 |
| B3-qwen | Qwen3-Q | 信封 + 记录级全文 | 只向量 | 0.750 | 0.972 | 0.816 | 0.154 | 1 922 |

- Qwen 的向量通道单独看略好（MRR 0.733→0.778，信封后 0.799→0.816；R@10 0.903→0.972），但融合后 MRR 反而低于 MiniLM（0.885 vs 0.785），条件错误率也更高。题量只有 13 题，样例记录高度同质，差异不足以下结论。
- 代价是确定的：查询延迟约 17 倍（每次查询要在 CPU 上编码），重建约 20 倍，内存约 7 倍。
- **建议**：默认继续用 MiniLM（已修好截断，并有信封）；Qwen 作为可选项保留，等真实数据题集建立后在 B1/B3 上重测，再由用户决定。

**P2 图路线（B4，MiniLM，浮点样例）**

| 运行 | 改动 | R@5 记录 | R@10 记录 | MRR@10 | must_not 率 | p50 ms |
|---|---|---|---|---|---|---|
| B3（P2 契约变更后重跑） | — | 0.806 | 0.944 | 0.885 | 0.077 | 115 |
| B4 | + 图路线 | 0.806 | 0.944 | 0.885 | 0.077 | 165 |

- B3 重跑与 P1 的 B3 完全一致：契约变更没有改变检索结果；图路线关闭时结果与之前一致（AC-P2 的这一条成立，也有自动测试）。
- B4 与 B3 指标相同、延迟多约 50 ms。样例只有一个对象、30 条记录，没有链接、关联和结论，前 10 名已覆盖大部分记录，图路线在这里没有可测的增量。**图路线是否默认打开，要等真实数据（有概念链接和关联之后）的评估**，目前保持关闭。

### 实施中的澄清与偏离（2026-10-09，需用户知悉）

| 项 | 计划原文 | 实际做法与理由 |
|---|---|---|
| requested_maturity 的状态 | 只能与 `state=not-reviewed` 一起出现 | 允许 not-reviewed 或 accepted。理由：结论已经 accepted（例如 replicated）时，AI 若要提议升级，按原文必须先把复核改回 not-reviewed，相当于降级。**请用户确认** |
| 旧记录的 handle | 未提供时由服务按标题生成 | 已有记录在下一次修订时补上 handle；在此之前链接按"当前标题生成的 handle"匹配（只用于解析，不写回）。handle 不进内容指纹，所以补上不会让复核失效 |
| 对象名匹配 | 斜杠前是 short_name，没有时用 owner_id | 也接受业务卡的 slug（同样是人可读的稳定名） |
| 概念库对象 | `memory.owner_create`（type 为 knowledge） | 原来 knowledge 只能由"采用文档"产生，没有业务卡；新增 `knowledge.json` 业务卡（KNW-…） |
| 图投影的位置 | `projections.graph` | 放在独立模块 `index/graph.py`（纯函数），与检索条目投影分开维护 |
| L1 块节点 | 只建有入边或出边的块 | 每个块都有"记录 contains 块"的结构边，因此全部建点；关系图页默认隐藏块（"显示 L1 块"开关） |
| run_inputs 统计 | 被 Run 登记为输入的次数 | Run 的输入登记是文件，不是记忆记录；改为"以 input 关系引用它的 Run 对象数" |
| 草案示例 | 新增 concept_page.json、association_quick.json | `memory.contract(kind)` 按类型取示例，改为更新 wiki_page（概念页）、association（certainty/origin）、detail（L1 结论）示例 |
| 关联候选的近邻 | 向量近邻（前 5，带阈值） | 用已存的向量直接比较，不重新调用模型；阈值 0.75（对真实模型有意义，假向量下只是流程验证） |
