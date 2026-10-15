# R24 图作为记录内的锚点与引用写法收口

| 项 | 内容 |
|---|---|
| 状态 | 已实施，随 0.10.0 发布（2026-10-15）；真实数据验收待做，见 §7 |
| 决定记录 | [DEVELOPMENT_HISTORY 的 R24](../DEVELOPMENT_HISTORY.md#r24-图作为记录内的锚点2026-10-15) 写讨论经过和被放弃的方法；本文只写"做什么、怎么做、怎样算完成" |
| 关系 | 扩展 [R12](R12_KNOWLEDGE_GRAPH_RETRIEVAL.md) D-09 的链接写法（`#` 后可以是块或图）；修订 [R21](R21_FAITHFUL_IMPORT.md) D-05 的图片写法（新写 `![[#图]]`，旧写法 `![图注](figure:N)` 继续有效）。契约只做增量（R12 D-27） |
| 适用范围 | 记录契约与校验（schema `DetailFigure`、`core/technical.py`）、链接语法与解析（`core/links.py`、`services/memory/links.py`、`services/memory/citations.py`）、取图（`services/memory/documents.py: figure`、`memory.figure`）、图投影（`index/graph.py`）、阅读材料（`services/query/assemble.py`、`services/reading/text.py`）、交换格式渲染（`services/render.py`）、覆盖报告（`services/snapshots.py`）、记录读取（`memory.inspect`）、工作台（`frontend/src/ui.tsx`、`citations.tsx`、`features/Memory.tsx`）、RECORDING 与 burrowl-work |
| 维护方式 | 每完成一个任务更新 §7；决定有变化时先改 §2 并在 DEVELOPMENT_HISTORY 补记，再改代码 |

## 0. 怎样使用本文件

- 决定写作 `D-xx`，任务写作 `T-xx`，验收写作 `AC-xx`。代码注释、测试名、提交信息引用编号（例如 `# R24 D-03：图锚点与块共用命名空间`）。
- 本文是计划，不代表功能已经实现。已实现的行为以代码、测试、CORE、ARCHITECTURE 为准。

## 1. 背景（2026-10-15 核对代码与 Kawaii 论文导入）

1. **图只能按序号嵌入，不能被引用**：L1 用 `![图注](figure:N)` 在原位置显示第 N 张图；上层记录要提到"Figure 1"只能引用整条记录或引用嵌图所在的块（`[[@实验一#p15]]`），块号不可读，块里还可能有其他文字。
2. **短名与图注混在一起**：方括号里写的是整段图注，`figures[].caption` 又存一份，L1 正文往往再照录一段图注，同一段文字存三处；工作台用数组序号显示"图 1："，与原文图号不一致（实验二记录里的第 0 张是论文 Figure 2）。
3. **引用写法多，AI 容易写错而不自知**：漏写 `@` 的 `[[实验一]]` 被当作概念提及，只得到"缺少出处"的提示，看不出原因；AI 读原始正文时只看到短名，不知道它指向哪条记录、哪个修订。
4. **工作台底部只列编号的依据**，正文提到的概念没有集中列表。
5. **取图检查过严**（已在本设计前修复，记入 T-00）：`memory.figure` 核对整条记录的全部依据，工作目录里的原件改一个字节，所有图都报 `STALE_BASIS`。

## 2. 决定

| 编号 | 决定 | 理由 |
|---|---|---|
| D-01 | **图是记录内的锚点，不是独立记录**：`figures[]` 增加可选 `label`（锚点名，如 `fig-1`），与 L1 块共用记录内的 `#` 命名空间；没有 `label` 的图按"序号 + 1"得到推导名 `fig-N`，只用于解析，不写回。提交时检查显式名与推导名、块号之间不重名 | 能精确引用到某张图，又不增加记录和关系图的主节点；与 handle 的"已存优先、推导兜底"一致 |
| D-02 | **图的锚点名用原文图号**：导入时按原文图号写（Figure 1 → `fig-1`，图 3-2 → `fig-3-2`）；原文没有图号时可以不写，用推导名 | 原文已有的名字不需要额外理解 |
| D-03 | **四条写法**（给 AI 的唯一说法）：① 引用记录、块或图：`[[@短名]]`、`[[@短名#块或图]]`，其他对象前加 `对象/`；② 引用结论：`[[!短名]]`；③ 在此处显示一张图：在引用前加 `!`，即 `![[@短名#图]]`，图的归属记录里显示自己的图写 `![[#图]]`；④ 提到概念：`[[名称]]`，不算出处。旧写法 `![图注](figure:N)` 继续有效、不再推荐 | 嵌图与引用统一成 `[[…]]` 一族；记录与结论保持两个符号（定位与失效规则不同，见 DEVELOPMENT_HISTORY R24） |
| D-04 | **图锚点的固定引用与块相同**：`{target_kind: record, revision, sha256: record_hash, locator: "figure:<锚点名>"}`，按引用方提交时的修订固定。**不**为图单独设指纹 | 正文链接不是依据，固定的旧修订始终可读，不会"失效"；只有写进依据字段时才按记录修订判断过期，这与块一致。块和图按锚点自身内容判断过期是另一项改动，列入 §5 |
| D-05 | **`![[…]]` 只能显示图**：`!` 前缀指向的不是图（记录、块、结论、概念）时提交失败 `UNRESOLVED_LINK` 并给出原因。`![[@…#图]]` 同时计入引用编号；`![[#图]]` 是归属记录显示自己的内容，不是引用，不进 `links`，只校验锚点存在 | 一种写法一种含义 |
| D-06 | **一张图只有一个归属记录**：图片附件只登记在原文转录它的 L1（或产出它的 Run、L2）的 `figures` 里；其他记录用 `[[@…#图]]` 或 `![[@…#图]]`，不重复登记 | 附件只存一份，出处只有一条路径。本轮只写进规则，不做程序检查 |
| D-07 | **图注只存在 `figures[].caption`**，照录原文；`label` 是短名。显示为"Figure 1：图注"（由 `label` 推出显示名；推导名显示"图 N"）。L1 正文不再另外照录图注；覆盖报告把嵌在块里的图的 `caption` 计入该块的正文 | 消除三处重复（§1-2），又不让覆盖报告把图注所在段落报成偏短 |
| D-08 | **给 AI 的正文附引用对照**：`memory.inspect` 读单条记录时返回 `link_targets`（每个正文链接 → 标题、目标 ID、固定修订、块或图）；阅读分页（`reading_read`）同样返回本页文字里出现的链接的 `link_targets`。用结构化字段而不是往正文里追加文字，避免改变正文、字数预算与工作台显示 | AI 不必记住语法也能知道引用指向哪里，避免把已读过的当成新材料重复分析 |
| D-09 | **漏写符号的提示**：`[[名称]]` 解析不到概念，但名称正好是本对象某条记录的短名或某条结论的短名时，提交返回 warning `LINK_SIGIL_MISSING`，提示改为 `[[@名称]]` 或 `[[!名称]]`。保存仍成功 | 把"为什么缺少出处"直接说出来 |
| D-10 | **报错与提示附四条写法**：`UNRESOLVED_LINK`、`INLINE_CITATION_MISSING`、`LINK_SIGIL_MISSING`、`UNREADABLE_REF` 的提示里给出 D-03 的示例（一处定义） | AI 忘了写法时当场看到正确示例 |
| D-11 | **工作台底部分两栏**：依据（〔引N〕编号：记录、块、图、结论）与提及的概念（不编号，可点击） | 概念有集中列表，又不和证据混在一起 |
| D-12 | **关系图**：图锚点是"记录#锚点"子节点（节点类型 `figure`），与块一样由记录 `contains`，默认折叠；指向图的链接与依据连到该子节点 | 不增加主节点 |
| D-13 | **取图**：`memory.figure` 增加可选 `label`，按锚点名取图（与 `index` 二选一）；附件本身严格核对，其余依据的变化随图返回 `basis_issues`（T-00） | 工作台显示别的记录的图时不需要先读对方的 `figures` |

## 3. AI 行为指导（写进 RECORDING、burrowl-work）

- 导入图片：`sources_attach` 得到附件后写进 `figures`：`{label: "fig-1", caption: 照录原文图注, ref}`；在原位置写 `![[#fig-1]]`；正文不再照录图注。
- 上层记录提到图：作为出处写 `[[@实验一#fig-1|Figure 1]]`；需要把图放在概览或报告里写 `![[@实验一#fig-1]]`。不要把同一张图再登记进上层记录的 `figures`。
- 引用写法只记四条（D-03）；提交返回的提示里有示例。

## 4. 程序设计

### 4.1 语法（`core/links.py`）

- 正文中的 `[[…]]` 前紧跟 `!` 时记为嵌入（`Link.embed = True`）；同一原文既嵌入又引用时合并为一个链接，`embed` 取"任一处嵌入"。
- `[[#锚点]]`（以 `#` 开头）是本记录锚点，`kind = "anchor"`；只在 `![[#…]]` 中有意义，单独写 `[[#…]]` 提交失败并提示改写。
- `[[@短名#锚点]]` 仍解析为 `kind = "block"`（名称沿用），锚点由解析层判断是块还是图。

### 4.2 解析（`services/memory/links.py`）

- 锚点先找块，再找图（显式 `label`，再推导名）；都找不到时候选包括块号与图锚点名。
- 图锚点 → `locator: "figure:<锚点名>"`（推导名也按推导名写入，旧记录补 `label` 后推导名不再有效，但已固定的旧修订仍可按序号读到）。
- `embed` 且目标不是图 → `UNRESOLVED_LINK`。
- 本记录锚点不进 `links`；锚点不存在 → 契约错误（`/payload/blocks/i/markdown`）。

### 4.3 锚点命名与校验（`core/technical.py`）

- `label` 与 `block_id` 同样不能为空、不能含 `[]|#/` 与空白；显式名、推导名、块号三者不得重复。
- 块正文中的 `![[#x]]` 必须指向本记录的图；`![…](figure:N)` 的旧校验保留。
- 一个函数 `figure_anchors(payload) -> {锚点名: 序号}` 供校验、解析、取图、覆盖、图投影共用。

### 4.4 消费 `figure:` 定位的地方

| 位置 | 处理 |
|---|---|
| `index/graph.py` | 图子节点与 `contains` 边；`target_node` 把 `figure:` 定位映射到子节点 |
| `services/render.py` | 交换格式：图锚点链接指向记录页（Markdown 库中没有图锚点，退回记录） |
| `services/query/assemble.py` | 定位到图时带出该图所在块与图；`_figures` 同时识别两种嵌入写法 |
| `services/reading/text.py` | 分页文字里两种嵌入写法都保留为图片标记 |
| `services/snapshots.py` | 覆盖报告识别两种嵌入写法；图注计入所在块正文（D-07） |
| `memory.figure` | 接受 `label`（D-13） |

### 4.5 工作台

- `Markdown`：`![[#x]]` 与 `figure:N` 在原位置显示本记录的图；`![[@…#图]]` 显示被引用记录的图，图注后带〔引N〕。
- 图注显示"显示名：图注"。
- `CitationList` 分两栏（D-11）；概念链接的显示名、跳转沿用 `ui.ref_targets`。

## 5. 不做的

- 块与图按锚点自身内容判断依据是否过期（目前按整条记录修订判断）。需要改 `claims._reference`、`Reader._check`、提交检查与维护计划，单独立项。
- 一张图被多条记录登记的程序检查（D-06 只写进规则）。
- 图的独立检索条目（图注已随记录与块进入全文投影）。
- 把已有数据批量补 `label` 的维护计划（旧数据用推导名即可引用）。

## 6. 验收

- AC-01：L1 写 `figures: [{label: "fig-1", …}]` 与 `![[#fig-1]]`，工作台在原位置显示图，图注为"Figure 1：…"。
- AC-02：另一条记录写 `[[@实验一#fig-1|Figure 1]]` 与 `![[@实验一#fig-1]]`：前者为编号引用，后者显示图且计入同一编号；关系图中两者连到"实验一#fig-1"。
- AC-03：`![[@实验一]]`、`![[!某结论]]` 提交失败并说明只有图可以显示；`[[#fig-1]]` 单独写提交失败。
- AC-04：`[[实验一]]`（漏写 `@`）提交成功并返回 `LINK_SIGIL_MISSING`。
- AC-05：`memory.inspect` 与阅读分页返回 `link_targets`。
- AC-06：旧写法 `![图注](figure:0)` 与没有 `label` 的旧记录照常显示、可用 `#fig-1` 引用；全部兼容性标本照常读取。
- AC-07：图注不在正文中照录时，覆盖报告不把该段报成 thin。

## 7. 进度记录

| 任务 | 状态 | 说明 |
|---|---|---|
| T-00 取图检查放宽 | 已完成 | 附件严格核对；其余依据变化返回 `basis_issues`，工作台提示复核；读取权限问题仍拒绝（`tests/service/test_documents.py::test_figure_survives_changed_basis`） |
| T-01 契约与锚点校验 | 已完成 | schema `DetailFigure.label`（可选）；`core/technical.py`：`figure_anchors`、`figure_display_name`、`embedded_figures`、`figure_errors`（写法、重复、与块号冲突），`![[#x]]` 指向不存在的图时报错 |
| T-02 语法与解析 | 已完成 | `core/links.py`：`Link.embed`、`kind="anchor"`、`WRITING_RULES`；`services/memory/links.py`：`#` 后先块后图、`figure:` 定位、`!` 只能显示图、本记录锚点只校验不进 links；修订沿用旧链接时，新增的 `!` 前缀会重新解析 |
| T-03 提示 | 已完成 | `LINK_SIGIL_MISSING`（预检与提交都返回，不进回执）；`UNRESOLVED_LINK` 的 `hint`、`INLINE_CITATION_MISSING` 的 `action`、`UNREADABLE_REF` 的兜底提示附四条写法 |
| T-04 取图与消费方 | 已完成 | `memory.figure(label)`，返回 `index`、`name`、`basis_issues`；graph-v4 图子节点（`block_id` 列存锚点名）；材料包定位到图时交付嵌图块；分页文字保留两种写法；交换格式原样保留 `![[#图]]`；覆盖报告识别两种嵌入写法，图注计入嵌图块；`sources_attach` 与导入提示改为新写法 |
| T-05 给 AI 的引用对照 | 已完成 | `targets.link_table`；`memory.inspect.link_targets`；阅读分页 `link_targets`（同一次读取共用一个 Reader） |
| T-06 工作台 | 已完成 | `frontend/src/figures.tsx`（共用的 `RecordFigure`，按序号或锚点名取图，图注显示"显示名：图注"）；`ui.tsx` 识别 `![[#图]]`；`citations.tsx` 嵌入别的记录的图并计入编号、定位显示为"块 x / 图 x"、底部"依据 / 提及的概念"两栏；关系图增加"图"节点类型，与块一起展开 |
| T-07 规则与文档 | 已完成 | 见下 |
| T-08 全量验证 | 已完成 | 见下 |

**文档处置**：README 已核对无需修改（用户可见概念未变）；ARCHITECTURE（已知差距补 D-04）、docs/CORE（导入图片、链接解析、图投影节点）、docs/GLOSSARY（链接、图锚点、附件、知识节点）、docs/TESTING（R24 测试）、docs/README（设计稿表）、DEVELOPMENT_HISTORY（R24）、AGENTS、skills/RECORDING（四条写法、figures、示例）、skills/burrowl-work（导入图片）已修改；DOCUMENTATION_MAINTENANCE、RULES、burrowl-read、burrowl-consolidate 已核对无需修改。`gen_reference.py` 与 `npm run ops` 已运行；`gen_draft_examples.py` 运行后示例无变化。

**验证（2026-10-15）**：`uv run pytest` 全部通过（6 项需要真实模型等的跳过；新增 `tests/service/test_figure_anchors.py` 9 项、`test_documents.py` 1 项）；ruff、分层依赖、Skill 检查通过；前端构建与类型检查、组件测试 63 项、浏览器端到端 13 项通过。版本升为 0.10.0 并生成兼容性标本 `tests/fixtures/compat/v0.10.0`（标本中 L1 同时带新旧两种嵌图写法与 `label`），旧标本照常读取。发布前复跑上述全部检查通过，打包为 0.10.0 并发布 GitHub Release（不含模型包）。未在用户实际数据上验收（用户将删除旧记录后按新写法重新导入）。
