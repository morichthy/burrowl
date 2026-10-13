# R23 结论的结构：多元参与者、自动标注与关系图分类

| 项 | 内容 |
|---|---|
| 状态 | 已实施（2026-10-13）；真实数据验收与带结构结论的评测待做，见 §8 |
| 决定记录 | [DEVELOPMENT_HISTORY 的 R23](../DEVELOPMENT_HISTORY.md#r23-结论的结构2026-10-13) 写演进思路和被放弃的方法；本文只写"做什么、怎么做、怎样算完成" |
| 关系 | 取代 [R12](R12_KNOWLEDGE_GRAPH_RETRIEVAL.md) D-11（二元 `relation` 与快捷边）、§4.4 中 `concept` / `claim_relation` 两个边类别、§4.5 的 `relation` 形态；对 `relation` 字段不适用 R12 D-27（契约只做增量），理由见 D-16。修订 [R19](R19_RELATION_KNOWLEDGE.md) §4.3 的统一边标准、§4.4 的中转节点、§4.5 的关系窗口文字 |
| 适用范围 | 记录契约与校验（`core/records.py`、`core/contract.py`、schema）、内容指纹（`core/records.py: content_hash`、`services/memory/claims.py: claim_hash` 及其调用方）、链接解析（`services/memory/links.py`）、图投影（`index/graph.py`）、检索投影与图扩展（`index/projections.py`、`services/indexing.py`、`services/reading/expand.py`）、中枢导出与交换格式（`services/hub.py`、`services/exchange.py`）、接口枚举（`interfaces/ops/builtin/knowledge.py`）、渲染（`services/render.py`）、关系图前端（`frontend/src/features/Relations.tsx`）、RECORDING 与 consolidate |
| 维护方式 | 每完成一个任务更新 §8；决定有变化时先改 §2 并在 DEVELOPMENT_HISTORY 补记，再改代码 |

## 0. 怎样使用本文件

- 决定写作 `D-xx`，任务写作 `T-xx`，验收写作 `AC-xx`。代码注释、测试名、提交信息引用编号（例如 `# R23 D-02：结构不计入内容指纹`）。
- 本文是计划，不代表功能已经实现。已实现的行为以代码、测试、CORE、ARCHITECTURE 为准。
- 遇到本文没写的情况，先回到 §2 找依据；依据不足时问用户，用户确认后先改本文。

## 1. 背景（2026-10-13 核对代码）

1. **关系图分类混乱**：边类别 `concept` 同时装着"材料提到了某个概念"（`mentions`）和"概念在结论中是主体或客体"（`subject_of`、`object_of`）；`claim_relation` 是从结论派生的 主体→客体 快捷边，与结论节点是同一份知识的两种画法，却作为并列类别出现（`index/graph.py:28`、`:195`）。
2. **只能表达二元关系**：`relation` 只有一个主体、一个谓词、一个客体。"A 与 B 共同导致 C"、"在指标 M 上 A 优于基准 B"只能写在陈述文字里，图上不完整。
3. **结构会牵连复核**：复核绑定记录的内容指纹（`claims.py:173` 比对 `target_content_hash` 与 `content_hash`），`relation` 在其中。修正结构就让复核失效，所以结构不能放心地自动生成。
4. **`contradicts` 有两种写法**：它既在谓词表（`records.py:42`），又是依据关系名，"A 与 B 矛盾"可以写成结论，也可以写成依据边。
5. **依据引用没有文字**：`evidence_refs` 与 `sources` 只有关系名，点击依据边看不到"这份材料的哪部分、怎样支持或反驳"。
6. **概念链接只在提交时解析**：`links.resolve` 只在提交时运行（`links.py:196`）。先写 `[[概念]]` 后建概念页，旧记录要等被修订才接上；`index_gaps` 一直报 `missing_concept`。
7. **兼容面**：全部兼容性标本与样例中没有任何 `relation`（`tests/fixtures` 中 `predicate` 出现 0 次），用户数据目录将清空重建（§6）。

## 2. 决定

| 编号 | 决定 | 理由 |
|---|---|---|
| D-01 | **结论分三层**：核心（谓词＋参与者）、限定（谓词框架中的可选角色与适用范围）、依据（`evidence_refs`）；另有状态（复核、成熟度，R12 D-15 不变）。**判断规则：去掉这一项后结论的意思变了，它属于核心或限定；只是更难让人相信，它属于依据** | 与 Wikidata 声明（主值、限定符、引用、等级）、Biolink 3.0 的"洋葱"模型、nanopublication（断言与来源分离）一致；给 AI 一条可操作的分界 |
| D-02 | **权威内容是陈述、适用范围、依据**；`relation` 是 AI 写结论时自动生成的结构标注，**不计入记录内容指纹与结论指纹**，不要求逐条核对。发现结构不对时对照陈述只改 `relation`，复核保持有效 | 数据多时逐条核对结构不可行；陈述文字完整保存并可检索，结构可以随时重新生成或重构 |
| D-03 | **`relation` 的形态**：`{predicate, negated?, participants: [{role, value}]}`。同一角色可出现多次（共同作用、多个结果）；`value` 是链接或纯文字（D-08）。没有关系的结论不写 `relation` | 存储保留完整的参与者与角色，二元读法由 D-04 推导，不另存 |
| D-04 | **谓词框架**：每个谓词预先定义必填角色、可选角色和方向读法（§4.1），程序常量一处定义，`memory.contract` 与 RECORDING 从它生成说明。框架外的角色、自定义谓词用 `x-` 前缀 | 角色由谓词定义（PropBank / FrameNet 的做法），不在每条结论里临时命名，查询和展示有确定的词 |
| D-05 | **谓词表删除 `contradicts`**：反例写在依据层（`evidence_refs` 的 `contradicts`），两条结论冲突走复核的 disputed | 消除同一含义的两种写法（§1-4） |
| D-06 | **条件来自适用范围**：`scope` 文字是条件的权威表达；其中的链接生成 Claim → X 的"条件"边。参与者中不写条件角色 | 同一条件不在两处各写一遍 |
| D-07 | **否定结论用 `negated: true`**，不另造"不改善"类谓词 | 与 Biolink 一致；谓词表保持小 |
| D-08 | **参与者的值**：已有概念页、记录、块、结论、Run、来源 → 链接；像概念但还没建页 → 仍写 `[[名字]]`（未解析不阻塞，列入缺口）；只是描述、不会被别处引用 → 纯文字。链接保留原文写法（`[[温漂\|温度漂移]]`） | "能当成一个东西就写链接"，以后建页即可接上；纯文字只留给描述 |
| D-09 | **概念链接在投影时解析**：图投影与检索投影按当前概念库重新解析记录中未解析的概念名，并跟随 `merged_into`；被拆分（`split_into`）的页面不自动改指，列为缺口。**投影水位包含概念库指纹**。记录、块、结论链接仍在提交时解析并固定版本 | 概念链接按名称匹配、不需要固定版本；记录本身不改。水位不含概念库会重犯 E7/E9（派生数据的新鲜度未覆盖全部输入） |
| D-10 | **身份规则**（沿用 R12 D-12、D-13，补拆分）：概念身份是概念页 ID，名称与别名只是入口（规范形精确匹配，库内不重复）；同名不同义用限定名；合并用 `merged_into` 重定向；拆分后的旧链接由收口逐条判断；纯文字值不自动升级，收口时把与概念名或别名一致的纯文字列为候选，由 AI 改写成链接（D-02 保证不影响复核） | 自动化只做名称匹配，语义上的"相同"由收口判断 |
| D-11 | **边类别**：structure 结构、evidence 依据、reference 引用、navigation 关联、**mention 提及**、**claim_part 结论要素**。`concept` 只作节点类型；删除 `claim_relation` 与快捷边，图中不再有派生的对象间直连边。结论要素边的类别由来源字段决定（`claims.relation.participants`、`claims.scope`），`type` 为角色名或 `condition` | 类别回答"这条边表达什么"，节点类型回答"这个点是什么"；一条结论只有一种表达 |
| D-12 | **只用节点模式展示**：结论节点默认显示；结论要素边的标签显示角色中文名（原因、结果、基准……）；提及默认关闭；来源节点默认关闭；L1 块在"记录内部细节"中展开。结论详情并排显示陈述、适用范围与结构，依据边显示 `note` | 结构是陈述的标注，用户在遇到时对照核验（D-02） |
| D-13 | **固定引用增加可选 `note`**（`evidence_refs`、`sources`）：说明材料的哪部分、怎样支持或反驳。`contradicts` 缺 `note` 时提交返回 warning；`supports` 鼓励填写 | 依据边需要可读的注释（§1-5）；用 warning 而不是拒绝，与 R19 D-09 一致 |
| D-14 | **图扩展经过结论**：种子结论沿要素边到达参与者；经低度数概念中转时，到达的是其他对象中以该概念为参与者的**结论本身**（作为扩展目标，不另设中转）。结论声明的边，关系窗口文字 = 陈述 + 适用范围 + 由框架推导的角色读法；失效结论的要素边不参与扩展。权重：`claim_part` × 成熟度系数，数值由评测确定。**2026-10-13 实施时修订**：原定"把结论加入中转节点、做两级中转"，核对 `expand.py` 后发现到达的结论本身就是合适的目标，两级中转只会多走一跳，故不做 | 条件与陈述随窗口带出，消除 R19 §4.5 要求"陈述 + 范围"而实现只给标题的差距 |
| D-15 | **中枢导出与交换格式带上结论节点**（陈述、适用范围、复核、成熟度）与 `claim_part` 边；不再导出快捷边；交换格式版本递增 | 删除快捷边后，外部系统仍能看到对象之间的关系，且带条件 |
| D-16 | **`relation` 不按 R12 D-27 增量修改**：直接替换为新形态，不保留旧写法的读取路径。内容指纹改为排除 `claims[].relation`；没有 `relation` 的结论与记录指纹不变 | 现有标本与样例中没有 `relation`（§1-7），用户数据目录清空重建；兼容性标本照常可读 |
| D-17 | **数据目录清空重建由用户执行**（工作台或命令行），或把配置指向新的空目录 | AGENTS 数据边界：agent 不直接改数据目录 |
| D-18 | **评估**：D-14 前后在浮点样例题集与检索评测集上对比（R12 D-24、R19 D-10）；不得明显变差，结果写入 §8 | ARCHITECTURE 评价标准 3 |

## 3. AI 行为指导（写进 RECORDING、consolidate 的内容）

- 写结论时先写完整的 `statement` 与 `scope`，它们是权威内容；能看出参与者与谓词时顺手写 `relation`，拿不准就不写。
- 角色按谓词框架选（`memory_contract` 给出）；共同作用就让同一角色出现多次，不要拆成多条二元结论。
- 条件写进 `scope`，需要被引用比较的工况写成链接；不要在参与者里重复写条件。
- 反例写进 `evidence_refs`（`contradicts`）并写 `note`；不要写"A 与 B 矛盾"这样的结论。
- 参与者：能当成"一个东西"的写链接（即使概念页还没建）；描述性短语写纯文字。
- 收口时处理：`missing_concept`（建页或合并）、被拆分概念的旧链接、可升级为链接的纯文字参与者。

## 4. 程序设计

### 4.1 谓词框架（程序常量，`core/records.py`）

| 谓词 | 必填角色 | 可选角色 | 方向读法 |
|---|---|---|---|
| improves 改善 / degrades 恶化 | agent 作用方、target 作用对象 | baseline 基准、metric 指标 | 作用方 → 作用对象 |
| causes 导致 | cause 原因、effect 结果 | mechanism 机制 | 原因 → 结果 |
| prevents 阻止 | agent 作用方、target 被阻止的现象 | mechanism 机制 | 作用方 → 被阻止的现象 |
| fails_on 失效于 | method 方法、case 失效情形 | metric 指标 | 方法 → 失效情形 |
| applies_to 适用于 | method 方法、domain 适用对象 | | 方法 → 适用对象 |
| requires 需要 | dependent 需求方、requirement 前提 | | 需求方 → 前提 |
| equivalent_to 等价 | item 比较项（≥ 2） | | 无方向 |
| part_of 组成 | part 部分、whole 整体 | | 部分 → 整体 |
| `x-…` 自定义 | 至少 2 个参与者，角色自定义（`x-` 前缀） | | 无 |

- 校验：必填角色齐全；框架外角色必须以 `x-` 开头；不接受 `condition` 角色（D-06）。
- 角色中文名与方向读法放在同一张表，图读法（R19 D-05 的 `PHRASES`）从这里取。

### 4.2 内容指纹（D-02、D-16）

- `content_hash(record)` 与 `claim_hash(claim)` 计算前去掉每条结论的 `relation`。`claim_hash` 目前在 6 处直接写 `canonical_hash(claim)`（`graph.py:178`、`projections.py:161/163`、`links.py:37`、`context.py:106`、`reading/text.py:21`），统一改为调用 `claims.claim_hash`。
- 只改 `relation` 的修订：新修订号，内容指纹不变，复核仍然绑定。
- 记录指纹 `record_hash` 仍覆盖全部字段（固定引用要能区分修订）。

### 4.3 图投影（D-11，`GRAPH_VERSION = graph-v3`）

| 类别 | 关系名 | 来源 | 参与扩展 |
|---|---|---|---|
| structure | belongs_to、contains、part_of、depends_on … | 不变 | 否 |
| evidence | supports、input、contradicts、supersedes | 不变 | 是 |
| reference | references、background … | 不变 | 是 |
| navigation | related_to、same_problem … | 不变 | 是 |
| mention | mentions | 正文概念链接（投影时解析，D-09） | 弱（经低度数概念中转） |
| claim_part | 角色名（cause、effect、agent …）、condition | `claims.relation.participants`、`claims.scope` 中的链接 | 是（经结论中转，§4.4） |

- 纯文字参与者不生成边；未解析的概念名不生成边。
- `CATEGORIES`、`EXPANDABLE`、`EXPAND_WEIGHTS`、`hub.GRAPH_EXPORT_CATEGORIES`、`knowledge.GraphCategory`、`render.py` 的引用类别集合同步修改；新增测试：每个类别都被导出与扩展处理，或被明确列为排除。

### 4.4 图扩展（D-14，修订 R19 §4.4）

- 中转节点仍是 `{concept, source}`。路径：种子结论 →（要素边）→ 参与者；种子 → 概念 c →（要素边的反向）→ 其他对象的结论 k，k 作为扩展目标，进入定点阅读。
- 关系窗口文字：声明这条边的结论的检索条目（陈述 + 适用范围 + 角色读法，例如"导致｜原因：A、B；结果：C"）。
- 发现条目文字（R19 D-04）：陈述 + 适用范围 + 角色读法，替代原来的"主体 谓词 对象"。

### 4.5 契约示例

```json
"relation": {
  "predicate": "causes",
  "participants": [
    {"role": "cause", "value": "[[温漂|温度漂移]]"},
    {"role": "cause", "value": "[[基准电压噪声]]"},
    {"role": "effect", "value": "高负载下的瞬态误差"}
  ]
},
"scope": "在 [[工况-满载]] 下，采样率 1 kHz",
"evidence_refs": [{"target_kind": "record", "...": "...", "relation": "supports", "note": "表 2 的满载组"}]
```

## 5. 不做的

- 不保留快捷边或"连线模式"展示。
- 不把多元结论自动拆成二元关系。
- 不为结构单独设复核状态。
- 不自动把纯文字参与者升级为链接，也不自动合并概念。

## 6. 用户的决定（2026-10-13）

- 存储用多元参与者（谓词框架加角色），二元读法由推导得出。
- 结构默认自动生成，不逐条核对；权威内容是文字声明，发现不对时对照陈述二次核验。
- 只用节点模式展示，结论节点默认开启。
- 不考虑旧数据兼容；数据目录清空重建。
- 中枢导出带上结论节点；角色词表允许 `x-` 自定义；带 `relation` 时必填角色齐全。

## 7. 验收

- AC-01：只改 `relation` 的修订后，复核状态不变；改陈述、适用范围或依据后，复核按原规则失效。
- AC-02：三个参与者的结论在图上显示为一个结论节点与三条带角色名的边；不出现对象间直连边。
- AC-03：先写 `[[新概念]]`，再建概念页，重建投影后边自动出现，`missing_concept` 消失，记录修订号不变；合并后的旧名跟随重定向；拆分的列为缺口。
- AC-04：写 `predicate: contradicts` 被拒并提示改写为依据；`contradicts` 依据缺 `note` 返回 warning。
- AC-05：图扩展经结论的要素边到达另一对象时，关系窗口包含声明结论的陈述与适用范围；经概念到达另一对象的结论时，该结论作为目标。
- AC-06：中枢导出包含结论节点与 `claim_part` 边。
- AC-07：全部兼容性标本照常读取；评测集结果不明显变差（D-18）。

## 8. 进度记录

| 任务 | 状态 | 说明 |
|---|---|---|
| T-01 契约与校验 | 已完成 | `core/predicates.py`（谓词框架唯一定义处：校验、读法、给 AI 的说明）；schema 中 `relation` 改为 `{predicate, negated?, participants}`，`Ref` 增加可选 `note`，删去 `subject_of` / `object_of`；`memory_contract` 对带结论的类型返回 `claim_relation`；反例缺 note 返回 `EVIDENCE_NOTE_MISSING`；L2 示例带 `relation` 与 `note` |
| T-02 内容指纹 | 已完成 | `records.content_hash` 与 `records.claim_hash` 去掉 `relation` 及只出现在其中的链接；6 处直接计算结论指纹的地方改为调用 `claim_hash`。**实施中发现**：提交服务按内容指纹判断"没有变化"，只改 `relation` 的修订会被静默丢弃；改为同时比较完整内容（`records.same_content`），由 `tests/service/test_claim_structure.py` 覆盖 |
| T-03 概念链接投影时解析 | 已完成 | `concepts.directory` 构造目录，`graph.Concepts` 查表（跟随合并，拆分列缺口 `split_concept_link`）；图水位 = 业务卡指纹 + 概念库 HEAD；概念库提交后补齐其他对象的图；`index_gaps` 按投影结果报告 `missing_concept` |
| T-04 图投影 | 已完成 | graph-v3：类别 mention / claim_part，结论要素边按来源字段判定（type 为角色名或 condition），删除快捷边与 `derived` 字段（索引库结构版本 6，升级时整库重建）；只出现在结论要素里的链接不再从记录连提及边 |
| T-05 同步调用方 | 已完成 | 中枢导出类别、渲染、图路线权重（配置键改为 `mention` / `claim_part`，旧键读入时改名）、接口枚举、关系服务；交换格式升为 `burrowl-exchange/2`；`gen_reference.py` 与 `npm run ops` 已运行 |
| T-06 图扩展与评测 | 已完成 | 结论声明的边，关系窗口取结论条目（陈述 + 适用范围 + 角色读法）；检索条目文字改为角色读法（fulltext-v4、discovery-v4）。D-14 按实施时的核对修订（不做两级中转）。评测：浮点样例 13 题，同一真实模型（fe-f3a92c14），改动前（HEAD）后两个投影的汇总指标逐项相同（发现投影 R@10 0.903 / MRR 0.556，全文投影 R@10 0.944 / MRR 0.885）。样例没有带 `relation` 的结论，只能说明没有变差；扩展权重仍无题集依据 |
| T-07 关系图前端 | 已完成 | 类别：结构、依据、引用、关联、结论要素、提及（默认关闭）；结论节点默认显示；连线详情并排显示陈述、适用范围、结构（标明自动生成）与依据 note；已保存视图中改名前的类别退回默认 |
| T-08 规则与 Skill | 已完成 | RECORDING 第 5、6 节（判断规则、relation 写法、note）；RULES 第 6 节表格一行；consolidate 第 66 行后补概念链接与纯文字参与者的处理 |
| T-09 文档 | 已完成 | 见下 |
| T-10 全量验证 | 已完成 | 见下 |

**文档处置**：README、ARCHITECTURE（检索投影状态、已知差距补两条：R23 的旧格式读取例外、要素边权重未评测）、docs/CORE（§2 外壳、§5 图投影与关系图、复核绑定）、docs/GLOSSARY（结论、边类别；"快捷边"改为"谓词框架"）、docs/TESTING、docs/README、docs/reference/exchange-format、DEVELOPMENT_HISTORY、R12 / R19 标注、AGENTS 已修改；DOCUMENTATION_MAINTENANCE 已核对无需修改。

**验证（2026-10-13）**：`uv run pytest` 全部通过（新增 `tests/unit/test_claim_structure.py` 5 项、`tests/service/test_claim_structure.py` 3 项、图扩展 1 项与若干断言）；ruff、分层依赖、Skill 检查通过；前端构建与类型检查、组件测试 49 项、浏览器端到端 11 项通过（进程正常退出）。版本升为 0.9.0 并生成兼容性标本 `tests/fixtures/compat/v0.9.0`（新增内容约 0.47 MB），旧标本照常读取。未打包安装，未在用户实际数据上验收；数据目录清空重建由用户执行（D-17）。
