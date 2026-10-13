# 当前技术架构

本页只维护现行模块、依赖、状态归属和仍然有效的架构限制。

- 用户概念与用法：[README](README.md)
- 功能定义与流程：[CORE](docs/CORE.md)
- 细节入口：[文档索引](docs/README.md)
- 选择理由与历史：[DEVELOPMENT_HISTORY](docs/DEVELOPMENT_HISTORY.md)
- 术语：[GLOSSARY](docs/GLOSSARY.md)

## 评价标准

架构讨论和开发统一用以下三条评价（源项目 2026-09-09 由用户确定，本项目沿用）：

1. **控制依赖，让变化局部化。** 按变化原因划分模块；核心定义稳定接口，外围实现接口。记忆存储、检索和 AI 行为控制分别维护，换存储、模型或策略时不连带修改其他层。不为消除表面重复而提前绑定不同概念。
2. **控制状态，让行为可以推理。** 状态尽量少、集中、显式，每类状态只有一个权威来源和明确的转换。保存、索引、复核、关联采纳分别表达。请求的数据流、依据、联想路径和副作用都要能追踪。
3. **验证检索效率与性能。** 同时衡量准确度、召回、错误关联、延迟和上下文成本。新增抽象、缓存或图机制必须解决已存在或高度确定的问题，并用可重复的评估证明收益。

## 稳定设计约束

这些约束用于约束后续设计。原则写在这里，不表示代码已完全达到；实际差距见本页最后一节。

| 必须守住的边界 | 对开发方案的要求 |
|---|---|
| 按变化原因划分模块 | 规范存储、检索、连接器、AI 行为控制分别演进；依赖方向由 import-linter 强制。不凭接口名称宣称后端可替换，替换要有合约测试 |
| 状态少、集中、显式 | 新增状态前先说明维护者、生命周期、失败恢复和消费者。界面、缓存、索引、中枢映射都不能成为第二份业务真源 |
| 性能收益须有依据 | 输出 Top-K 或短正文，不能证明底层读取是有界的。优化要有测量 |
| 对象、内容、文稿分开 | 复用 Owner 的稳定身份，不复制等价对象。L0 是原件追溯，L1 的检索说明与完整块分开，层级不是可信等级，文稿和章节不另造记忆层 |
| 权威记录与派生数据分开 | 原件、规范修订、复核记录是权威。检索索引、中枢映射、外部中枢的内容都可以重建或重新发布 |
| 旧数据永远可读 | 真源的落盘格式（目录布局、HEAD 与提交清单、记录字段与指纹算法、业务卡、Run 卡、来源登记）只增不改。确需改变时，新代码必须同时保留对旧格式的读取。由历史版本的兼容性标本强制检验（见 docs/TESTING"兼容性标本"） |
| 发现与全文读取分开 | 默认发现使用独立的发现投影和独立水位，不能用全文索引加 level 过滤冒充。命中要回到规范固定版本核验。全文补偿必须由用户明确选择 |
| 写入和恢复保持单一边界 | 规范写入只走 MemoryService，保留版本冲突检测、幂等、不可变历史和单 Owner 事务边界。索引失败只补偿投影，不重复创建记录。跨对象部分完成不能写成全局成功 |
| 语义判断与程序执行分开 | AI 或人负责综合、反证、迁移判断和实际审查；程序负责校验、固定读取、组装和提交。监测和影响提示只产生候选，不自动改文稿、撤回结论或晋升复核。相似和导航采纳不等于科学支持 |
| 请求边界贯穿全链路 | 查询、续页、展开、组包、阅读，都保留用途、范围、排除项、权限、固定版本和预算。候选不是已读正文，组包无缺口不代表研究完整 |
| AI 可调用的能力与用户专属操作分开 | effect=user 的操作（确认复核、批准全文补偿、执行发布、停用来源、保存视图）只在工作台和 CLI 提供，不暴露给 MCP |

修改方案先在下方影响表定位责任模块，再追踪实际调用者。契约变化要同步检查：生成的前端类型、HTTP/MCP/CLI、Skill、相关测试、打包。只检查实际受影响的路径。

## 技术栈与运行入口

- **运行环境**：Windows x64 本地应用，Python 3.12（uv 管理）。发行时内置独立 CPython 与全部依赖，见 [packaging](packaging/)。
- **接口**：
  - `burrowl serve`：FastAPI 工作台，同时在 `/<token>/mcp` 提供 Streamable HTTP 形式的 MCP；只监听 127.0.0.1，需要 token，校验 Host/Origin，并设置 CSP。端口与 token 第一次启动时选定并存入数据目录，以后网址不变。
  - `burrowl start / open / stop / restart / status`：工作台以无窗口的后台进程运行（`interfaces/server.py`，R21）。
  - `burrowl mcp`：stdio 形式的 MCP，供宿主直接拉起。
  - `burrowl call`：命令行调用任意操作。
- **存储**：
  - 规范记忆是 Owner 内的不可变文件提交（JSON）。
  - 检索用一个 SQLite 文件：FTS5 全文，加 sqlite-vec 向量（按模型指纹隔离），以及图投影（节点、边）和关联候选。
  - 阅读会话用"不可变修订 + CAS"文档存储。
- **模型**：
  - 嵌入：fastembed 离线加载 ONNX 模型。新建数据目录默认 jina-embeddings-v2-base-zh（中英双语，768 维，位置上限 8192，平均池化），使用后切分：整条记录一次编码（`late_context_tokens`，默认 2048），再按块或段落分别求平均（`span_tokens`，默认 512）。已有数据目录可能仍在用 MiniLM-384（输入上限 128 token，各段单独切窗）。也可选 bge-small-zh、bge-base-zh、Qwen3-Embedding-0.6B-Q。选型和评测方法见 R17 设计稿。
  - 重排：可选的 ONNX Cross-encoder。
  - 模型统一放在 `%LOCALAPPDATA%\burrowl\models`，所有数据目录共用。
- **前端**：React + Vite，构建产物随发行包分发，使用端不需要 Node。
- **扩展**：通过入口点注册：`burrowl.ops`（操作）、`burrowl.connectors`（连接器）。

```
interfaces   ops 注册表 → FastAPI / MCP / Typer CLI
app          组合根：按数据目录与配置装配服务
services     memory, review, query, reading, indexing, hub, runs, relations, monitor, maintenance, config, health, workflow,
             routing, activity, notes, snapshots
storage | index | connectors | portable   （四者互不导入）
core         记录模型与校验、契约说明、哈希、原子写与锁、路径安全、配置模型、错误
```

上层只能导入下层；函数内部不允许 import（可选依赖除外，需注明）。错误统一为 `BurrowlError(code, message, details)`，在接口层分别转换为 Problem JSON、MCP ToolError 和退出码。

## 模块与变化影响

路径相对于 `packages/burrowl/src/burrowl/`。下表是全局模块影响的唯一入口。

| 模块 | 职责与代码入口 | 变化时检查的关联 |
|---|---|---|
| CORE 记录契约 | `core/records.py`（校验与领域规则）、`core/schema/`（memory.schema.json 与草案示例）、`core/contract.py`（给 AI 的契约说明；字段的 required / optional / conditional 由 schema 与默认值表生成，R20）、`core/links.py`（链接语法与 handle 生成） | 所有读写记录的服务、`memory.contract`、草案示例测试、前端类型、Skill 与 RECORDING；契约只做增量，已有指纹必须不变（`tests/service/test_links.py`）；契约标注与服务行为一致（`tests/service/test_ai_fields.py`） |
| STORE 规范存储 | `storage/memory_store.py`（提交、CAS、幂等、暂存回读、恢复；墓碑提交 `remove` 与 `tombstones`，R22；`read_head` 只读 HEAD、提交清单与指定指针记录；`VerifiedRecords` 已校验记录缓存）、`storage/owners.py`（对象发现、业务卡、核心算法准入；对象发现不进入 `runs/`，`RunLocator` 按编号与位置找 Run）、`storage/sources.py`（来源登记）、`core/atomic.py`（锁） | 所有服务；备份恢复；validate |
| MEM 记忆服务 | `services/memory/`：service（提交与读取过滤、handle；可见范围默认值见 policy）、removal（删除与恢复记录、对象回收站，R22）、vocabulary（两层关系词表与 warning，R19）、links（链接解析与重新固定）、concepts（概念库约束与术语扩展）、claims（结论、复核图、成熟度与派生统计）、documents（文稿）、history、raw_materials、provenance（影响与血缘）、associations、consolidation、export | REVIEW、QUERY、READ、HUB 的读取者；工作台记忆页；work 与 consolidate Skill |
| REVIEW 复核 | `services/review.py`、`services/memory/claims.py` | 检索的复核类别、正式用途、中枢发布策略、复核页 |
| IDX 检索 | `index/`（db、projections（含上下文信封）、graph（图投影）、fusion、query_plan、rerank、conditions、extract、embed、late（后切分分组）、model_store）、`services/indexing.py`（同步、图路线、关联候选、缺口） | QUERY、READ 的召回；关系图页；模型安装；索引重建；发现可见性语义；评估脚本 `tools/eval_retrieval.py`、`tools/eval_recall_models.py` |
| RUN 执行证据 | `services/runs.py`（建立、登记、执行、显式查找、丢弃与回收站）、`storage/owners.py` 的 `RunLocator`（R14：按编号与位置查找，上级按目录位置） | 引用解析、时间线与原始材料、关键操作记录、归属判定、收口的 `unfixed_evidence`、`system.validate`、关系图的 Run 节点；`tests/service/test_run_evidence.py` |
| ROUTE 归属判定 | `services/routing.py`（`memory.owner_route`：复用 `ReadingService.preview` → `screen` 的召回与筛选包，补充目标、检查点与来源重叠证据；结果可被随后同一问题的 `reading.start` 复用，R18） | 阅读第一轮召回的任何变化都会同步改变归属候选；RULES 第 1、2 节与 work Skill；`tests/service/test_routing.py` |
| ACT 关键操作记录 | `services/activity.py`（从业务卡、提交回执、复核记录、Run 业务卡、中枢执行记录派生；一行摘要生成函数）；写操作返回值的 `activity` 字段 | 提交回执与记录结构、Run 状态、中枢执行记录的变化；工作台首页；`tests/service/test_activity.py` |
| QUERY 材料查询 | `services/query/`（search、page、assemble、expand、deepen、documents，以及预算） | 检索页；read Skill 的片段模式；context_reach 语义 |
| READ 阅读会话 | `services/reading/`（service、screening、notes、text（含定点阅读）、session、reuse（筛选结果复用，R18）、expand（联想 = 图扩展，R19）） | 阅读会话页（SSE）；read Skill；配置中的阅读默认值与预算 |
| HUB 知识中枢 | `services/hub.py`、`storage/hub_state.py`、`connectors/`（spi、registry、fake、mdvault），以及独立包 `burrowl-connector-weknora`、`burrowl-connector-lightrag`（图模式，按需重建） | 发布策略与复核类别；中枢页；wiki_page 记录；合约测试；LightRAG 版本锁定在 1.5.7，升级前重跑 `tests/connectors/test_lightrag.py` |
| MAINT 维护与监测 | `services/maintenance.py`（计划状态 planned → queued → reviewed → applied / partial，执行仅限用户，R16）、`services/monitor.py`、`services/relations.py`（读取图投影、关联候选） | 维护页与导航计数、关系图页；consolidate Skill 与 MCP 说明；`maintenance.plan_from_candidates` 依赖候选记下的影响路径 |
| NOTE 工作台笔记 | `services/notes.py`（R15：用户交给 AI 的待办；状态转换、AI 方案的预演与预览、用户执行时核对依据 HEAD 后按方案写入） | 关系图页笔记面板、首页状态卡与导航计数；`memory.resume` 的 `workbench_notes`；RULES 第 8 节、work 与 consolidate Skill；方案步骤依赖 `memory.commit` 与 `relations.decide` 的参数和 dry_run |
| SNAP 导入与原文快照 | `services/snapshots.py`（快照生成与读取、图片附件、原件与快照的逐段对比、覆盖报告与原文展开，R21）、`core/segments.py`（分段、对比、覆盖的纯函数）、`index/extract.py`（各格式抽取，含 docx） | L1 契约的 `origin` 与 L0 的 `omitted`；维护计划的 `source_changes`；工作台"原文展开"页；work / consolidate Skill 的导入与收口步骤；备份包（快照与附件随数据目录打包）；`tests/service/test_faithful_import.py` |
| CFG 配置 | `core/config.py`（BurrowlConfig）、`services/config.py`（CAS 与影响提示）、`storage/config_store.py`、`storage/secrets.py` | 所有读取默认值的服务；设置页；MCP 工具组 |
| PORT 可移植包 | `portable/bundle.py`（burrowl-bundle 备份、校验、恢复） | 数据目录布局变化；排除规则 |
| APP 入口 | `interfaces/`（ops 注册表、api、mcp、cli、server（工作台后台运行与停止 / 重启，R21））、`app.py`、`frontend/`；`interfaces/ops/shapes.py`（结构化参数的形状：发布给 AI 与校验用同一份 schema）、`interfaces/ops/errorlog.py`（错误日志）。参数只在 `registry.call` 校验，MCP 适配层关闭 SDK 的预校验（R20） | 同一操作在 HTTP/MCP/CLI/前端的一致性；生成类型；effect 分类；参数说明与形状（`tests/interfaces/test_tool_errors.py`）；MCP SDK 升级后重跑该测试（关闭预校验依赖 SDK 内部的工具注册对象） |
| GUIDE 规则与说明 | `skills/`（RULES、RECORDING、4 个 Skill）、六份必读文档 | 所描述能力的代码与契约；`burrowl skills check` |
| QA 测试与交付 | `tests/`、`frontend/src/**/*.test.tsx`、`frontend/e2e/`、`packaging/` | 见 [TESTING](docs/TESTING.md) |

## 状态由谁负责

| 状态 | 权威来源 | 边界 |
|---|---|---|
| 对象身份 | 内容树中的业务卡（research.json 等），由 OwnerRegistry 扫描 | 文件存在不等于登记为对象；核心算法必须有文档依据 |
| 规范内容与历史 | Owner 的 `memory_home`：commits、HEAD.json、请求回执；由 MemoryStore 管理 | 单 Owner 提交可见；旧修订不改写；没有跨 Owner 事务 |
| 归属说明 | 归属确认点那次提交的回执中的 `attribution`（R13） | 随提交不可变；不进记录指纹；归属放错按纠错处理，不改写回执 |
| 关键操作记录 | 无独立状态：每次请求从上面各权威来源实时汇总（`memory.activity`） | 可随时重算；Run 结束时间来自业务卡 `ended_at`，发布来自中枢执行记录 |
| 来源授权 | `.burrowl/sources.json`（工作根 roots + 来源 sources；工作目录里的来源记为"根 + 相对路径"） | 搬家只改位置且必须指纹一致；登记不能被 agent 改写，只有用户能停用或降低敏感级别。文件内容变化后，引用它的结论显示依据失效 |
| 复核 | 各 Owner 中的 review 记录 | 绑定内容指纹；内容变化即失效 |
| 操作内对象列表 | 无独立权威：`OwnerRegistry.request_scope`（接口层每次 `call` 开启），按数据目录复用本次操作的 `owners.scan()` 结果 | 只在一次操作内有效，结束即丢弃，新线程不继承。写业务卡或 owner.json 后 `invalidate()`；发布前复核与读取回源用 `scan(fresh=True)` 重新扫描，所以操作开始后别处的修改仍会被拦下 |
| 已校验记录缓存 | 无独立权威：`MemoryStore.verified`，进程内，键为（记忆目录, 记录文件, record_hash） | 派生状态，进程退出即丢失，丢了只是变慢；有条目上限。HEAD 与提交清单每次重读并做路径检查，只有清单仍指向同一指纹才命中，所以其他进程的新提交自然可见。首次读取后磁盘上的记录被篡改，普通读取在重启前发现不了；recovery 与 `system.validate` 用 `fresh=True` 逐条重读校验 |
| 筛选结果复用 | 无独立权威：`ReadingService.reuse`，进程内，键为召回的全部输入（查询计划、语料、范围、过滤、条件、筛选与重排配置、整份配置、重排模型目录） | 派生状态，进程退出即丢失，丢了只是重新召回；10 分钟时限、8 条上限。命中还要求 `IndexService.search_state` 的指纹相同：就绪对象集合、这些对象的全部索引水位行、嵌入模型指纹与投影版本，检索只读就绪对象的索引，所以这些相同则召回结果相同。未就绪对象（如索引同步失败的对象）不进入结果，缺口每次现算。新建或采用的对象立即建投影，所以新建后召回会重新计算 |
| Run 登记 | 业务卡 `run.json`，在 `<上级对象目录>/runs/<编号小写>/` 或顶层 `runs/<编号小写>/`；上级按目录位置判断 | 不是对象：不进对象列表、不保存记录、不建检索投影，关系图的 Run 节点由引用它的对象投影。丢弃的 Run 在 `.burrowl/trash/runs/`（可恢复，不进备份包），清空回收站是用户专属操作 |
| 已删除的记录 | 提交清单的 `removed_records`（R22 墓碑：原指针、理由、时间、执行者；`removed_ids` / `restored_ids` 为本次变化） | 删除与恢复都是新提交，旧修订不动；只在用到时写入这些字段，旧清单格式不变 |
| 对象回收站 | `.burrowl/trash/owners/<编号>/`（`item/` 是移走的目录，`discard.json` 是说明）与只追加的 `.burrowl/trash/owners.log.jsonl`（R22） | 移走前核对写锁与 HEAD；被采用文档只移记忆目录；清空回收站是用户专属的永久删除；投影删除失败由 `index.reconcile` 补删 |
| 检索投影 | `.burrowl/index/index.sqlite3`：每个 (Owner, 投影, 通道) 的水位绑定 HEAD、投影版本和模型指纹；图投影（corpus=graph）与关联候选也在这里，图水位另绑定概念库 HEAD（概念链接在投影时解析，R23 D-09） | 可删除重建；结构版本不符时整库清空重建；pending/stale 的投影如实报告。边由声明它的对象负责，另一端缺失时查询排除并计入悬空数 |
| 概念库 | 配置 `graph.concept_owner_id` 指定的那一个 knowledge 对象（`knowledge/<slug>/knowledge.json`）；其他 knowledge 对象是通用知识对象 | 概念页是普通 wiki_page 记录；名称唯一性只在概念库内检查；未配置时概念链接都记为未解析 |
| 成熟度 | review 记录的 `maturity` 等字段 | 不写进结论或记录正文；随复核失效；派生统计不持久化 |
| 配置 | `.burrowl/config.json`（revision = 内容指纹，CAS 写入） | 密钥只存引用，明文在 Windows 凭据管理器 |
| 阅读会话 | `.burrowl/sessions/reading/RS-*/`（HEAD + 不可变修订） | 用户工作数据，不是缓存；笔记固定写作时的对象 HEAD |
| 维护计划 | `.burrowl/plans/MP-*/`（不可变计划修订） | 与规范提交分开；可能逐对象部分完成 |
| 监测 | `.burrowl/monitor/`：基线、观察记录、候选状态 | 只是观察，不改证据；扫描不完整时不更新基线 |
| 中枢映射 | `.burrowl/hub/state.sqlite3` | 派生状态，丢失后按外部元数据中的 local_key 认领 |
| 交换包 | 数据目录的 `exports/<名称>/`（manifest、vault、graph），由 `exchange.export` 生成 | 派生数据，可以删除重建；不进备份包；在其中的修改不会回到 burrowl |
| 外部中枢内容 | WeKnora 等；LightRAG 的暂存表、构建目录与嵌入缓存在连接器 `options.path` 下 | 派生副本，可以由本地重新发布；LightRAG 每次有修改或删除时整体重建，切换成功前旧构建照常可查 |
| 工作台偏好 | `.burrowl/workbench/view.json`（关系图视图） | 本机偏好，不是研究记录 |
| 原文快照 | `.burrowl/snapshots/<SRC>/<原件sha256>.json`（R21）：导入时对原件抽取的分段文本、指纹与图片标签，由 `SnapshotService.take` 写入，不再修改 | 不是缓存：原件改了就无法再生成旧版，所以随备份打包；只保存抽取的文本，不保存原件；原件变化后的逐段对比以它为基线，新内容用新来源 ID 登记并另存快照 |
| 图片附件 | 数据目录 `attachments/<sha256>.<ext>`（R21），登记为来源 `SRC-ATT-<sha 前 16 位>` | 按内容寻址，同一张图幂等；受来源登记的授权与敏感级别约束；随数据目录备份迁移；L1 的 figures 只接受数据目录内的附件 |
| 工作台进程 | `.burrowl/server.json`（R21）：端口与 token（第一次启动时选定，长期保存）；运行中的进程号、进程创建时间、启动时间、版本、网址（正常退出时清除） | 被强行结束时残留，`running()` 发现进程不在或 PID 已被复用就清除；token 是网址里的访问口令，与其他程序状态同等保护；`.burrowl/logs/serve.log` 是后台进程的输出（超过 2 MB 时清空重写） |
| 错误日志 | `.burrowl/logs/errors.jsonl`（R20）：意外异常的堆栈与 incident_id；被拒绝调用的工具名、渠道、错误码与出错路径 | 派生诊断数据，可以删除；不记参数内容；超过 2 MB 轮转一份；不进备份包；写失败不影响操作本身 |
| 请求日志 | `.burrowl/logs/requests.jsonl`：每次工具调用一行（请求编号、渠道、耗时、结果；full 级另记收到的原样参数、MCP 层从 JSON 字符串解析回来的参数与返回内容）。在 `registry.call` 写，MCP 层传入原样参数 | 默认关闭；配置 `diagnostics.request_log` 或 `burrowl mcp --request-log` / 环境变量 `BURROWL_REQUEST_LOG` 打开。派生诊断数据，可以删除，不进备份包；长字段截断，超过 5 MB 轮转一份；写失败不影响操作；读日志的调用本身不记 |
| 检索与图投影的访问一致性 | 投影时隐去读取会被拒的记录；来源停用或改敏感级别时由 `IndexService.on_access_change` 重新投影依赖者（投影新鲜度不含来源登记，E9） | 读取被拒的记录不能出现在检索结果和关系图中 |
| 工作台笔记 | `.burrowl/workbench/notes.json`（R15：笔记、往来、待确认方案与执行结果） | 用户交给 AI 的待办，不是研究记录；随备份带走；每个状态只由一方推进，读改写在同一把锁内；写入记忆只经用户执行方案 |
| 当前任务上下文 | 任务的 Markdown 工作清单，由 AI 编辑 | 不是后台同步的状态；检查点保存交接时点 |
| 模型 | `%LOCALAPPDATA%\burrowl\models`（或 `BURROWL_MODELS_DIR`） | 嵌入模型变化会改变指纹，向量投影要重建 |

## 现行数据流与限制

### 保存与索引

`memory.commit` 的流程：

1. 校验、解析引用，核对固定字节与授权；
2. 锁内暂存并回读；
3. 原子切换 HEAD；
4. 同步更新该 Owner 的检索投影、图投影，并为变化的记录计算关联候选。

handle 分配和正文链接解析发生在第 1 步（`MemoryService.prepare`）。

索引失败时，内容仍然已保存（回执显示 pending），由 `index.reconcile` 补偿，不重复提交。

读取路径（inspect、resume、总结材料、导出）会隐去依据链上有受限或不可用来源的记录，并在 `omitted` 中说明原因。

### 投入分级与归属判定

- 简单问答不调用 burrowl；一次性小任务结束时做沉淀检查，有可复用内容才判定归属并保存；持续工作在开工时判定（RULES 第 1 节）。
- `memory.owner_route` 只读：与阅读会话第一轮同一实现的召回与筛选包（不建会话、不读完整材料），加上候选的目标、最新检查点（只读这两条指针记录）和来源重叠（图投影的边）。不给建议动作，由 AI 判断。
- 第一次往对象里保存才算确认归属，该次提交带 `attribution` 存进回执；确认前可换候选或新建。
- 关键操作（建立对象、归属确认、保存、复核、Run 结束、发布）在写操作返回值中给出一行摘要，并可用 `memory.activity` 分页查看；读取量随返回条数增长，每个对象至少读一份提交清单。

### 发现、筛选与完整阅读

- 默认在**发现投影**中召回 Owner。发现投影由 L4/L3 正文、L2 摘要、L1 检索说明和对象元数据组成。
- 召回后，给每个对象 2–4 个互补窗口组成筛选包，可选用 Cross-encoder 只调整窗口次序；显式字段条件只调整对象次序。
- 判为相关的对象，按页读取其完整研究过程文稿或各层正文。
- 全文投影只在用户批准全文补偿时使用。
- owner_only 的记录只在明确指定对象时可见。

### 阅读会话与用户交互

- agent 通过 `reading.*` 推进；首页与阅读列表每 5 秒轮询并在窗口聚焦时刷新，发现 MCP 在页面打开后新建的会话。会话详情通过 SSE 接收该会话的修订推送。
- 主题知识查询、找已有经验默认由 read Skill 引导 `reading.start`：简查 quick、完整阅读 standard。独立的 `query.*` 和 `memory.resume` 保持查询/续接职责，不隐式创建会话；阅读页只消费真实阅读会话状态。
- 用户操作写入会话的收件箱，在 agent 下一次调用时交付一次。
- 支持 MCP elicitation 的宿主会在对话中直接弹出询问。
- 笔记固定写作时的对象 HEAD，交接时重新核对，陈旧的不交付。

### 排序、预算与降级

- 融合用 RRF，排序不代表科学可信度。
- 查询有累计预算（时间、读取、输出、候选）；阅读会话的召回轮次也受预算约束。
- 嵌入模型或重排模型缺失时：关键词检索照常，向量通道报告未就绪，重排报告不可用。重排设为 `required` 时，模型缺失直接报错。
- 索引状态分别核对全文、发现和图投影；只补齐关系图不能冒充检索就绪。检索页解释缺口并提供用户点击的索引补偿入口，调用 `index.reconcile` 后重试原查询，不改变查询的隐式预算或规范记录。
- 图路线默认关闭（`index.graph_route.enabled`）。打开后在材料查询和阅读会话的召回中作为一路参与 RRF，按查询预算的 graph_nodes / graph_edges 限制扩展，超出时报告缺口。
- 中枢路线默认关闭（`index.hub_route.connectors` 为空）。打开后材料查询并行调用外部检索，命中映射回本地并经同样过滤后参与 RRF；外部超时或出错时只报告缺口，结果仍来自本地。

### 知识中枢

发布是"期望状态对账"：

1. 按策略算出应发布的集合；
2. 与映射比较，得出新增 / 更新 / 删除 / 标记；
3. `plan` 给出预览和 plan_hash，`apply` 时必须一致；
4. 外部异步解析，用 `status` 轮询。

宿主 AI 编写的 Wiki 以本地 `wiki_page` 为真源，外部被人改过时报告漂移，不覆盖。

### 安装与迁移

- 离线安装包按版本安装，用 `current` 联接切换和回滚版本。同版本重装同步包内文件；源码开发可用 `packaging/build.py --install` 完整构建后直接调用同一安装器，不生成 zip。`BUILD_INFO.json` 标识构建的提交、修改状态和时间，不代替知识记录。文件安装与运行进程分开：已有工作台及 MCP 须重启后加载新代码。
- 整个数据目录的备份、迁移用 burrowl-bundle（逐文件 sha256；不含可重建的索引）。默认连同数据目录外的已登记原件一起打包；恢复时按 `--map` 或默认规则放置原件，并自动改写来源登记。
- 接入方式（数据目录、工作目录、运行模式 auto/manual）是宿主 MCP 配置里的启动参数，不写进数据目录；`burrowl mcp-config` 输出标准 MCP 配置、`burrowl skills install / pack` 提供 Skill，登记到哪里由各客户端里的 agent 按其规范决定（`interfaces/agents.py`）。Skill 安装目录记入安装根目录的 `burrowl-install.json`，升级时 `burrowl upgrade` 只替换未被用户修改的文件。项目开关 `.burrowl.json` 优先于模式；工作流说明另由 `system.guide` 提供给不支持 Skill 的客户端。
- 不提供源项目旧数据（v1–v3）的迁移。memory-v4 以来的数据靠"旧数据永远可读"约束兼容：`tests/fixtures/compat/` 冻结各版本写出的标本，新代码必须能读取、校验并在其上继续写入。目前还没有"读入时把旧版本结构转换成当前结构"的机制，第一次改格式时需要补上。

### 已知差距

- Qdrant 服务器适配器未实现：sqlite-vec 是暴力检索，约 10 万向量以上需要它。
- R19 之前按旧默认存成 owner_only 的知识记录与关联不会自动改为全库可见，修订时也沿用原值；由用户在维护页"可见范围补齐"勾选后经维护计划修订（R19 §8）。
- 图扩展的默认上限与阈值（每轮 5 个对象、阈值 0.2、中转对象数 20）没有真实题集依据；结论要素边的扩展权重（0.6 × 成熟度系数，R23）同样未经评测。
- R23 把结论 `relation` 改为多元参与者时没有保留旧写法（主体、谓词、对象）的读取：全部兼容性标本与样例中没有 `relation`，用户数据目录在升级时清空重建（用户决定，R23 D-16）。这是"旧数据永远可读"的一次有据例外；若发现仍有旧写法的数据，需要补读入时转换。
- 检索评估只有浮点样例的小题集（13 题，`tools/eval_retrieval.py`），只能看相对变化；基于真实数据目录的 40–60 题评估集待用户抽查后建立（R12 的 T-P0.2）。样例只有一个对象、没有链接和结论，图路线在上面没有可测的收益。
- 嵌入模型和切分方式的证据还不完整（R17 §7）：jina 在合成中文题集上明显好于 MiniLM，但在浮点样例的 13 题上名次指标更低；英文科研题集（SciFact）上 jina 还没测；后切分与 windows、whole 的对比，需要以中文长 L1 记录为主的题集。fp32 版 jina 在 2 线程 CPU 上编码约每秒 450 token，首次建索引较慢。
- 交换包尚未在 Obsidian 中人工验收（R12 AC-P3）。
- LightRAG 连接器只有自动测试（假实现 + 真实 1.5.7 断网运行），尚未在本机做"发布 → 检索 → 核验"的人工验收（R12 AC-P4）。中枢路线（`index.hub_route`）默认关闭。
- WeKnora 连接器只用模拟接口测试，尚未连真实服务实测。
- 宿主 token 用量不由本项目保证。
- 读取与提交的固定成本仍随对象总数线性增长：
  - 复核图（`ClaimGraph`）需要全库的结论、复核与引用，每建一张都要对每个对象读 HEAD 与提交清单；记录正文走已校验缓存，不再重读。提交后的索引同步、inspect、总结材料都要建一张。
  - 对象扫描（`owners.scan`）遍历全部业务卡，每个对象约 0.7–1.3 ms（几乎都是路径检查的文件系统调用）。一次操作内已复用扫描结果，但发布前复核（`RefResolver.recheck`，每次提交 4 次）和读取返回前回源（`Reader.recheck`）必须重新扫描，所以一次提交仍扫描 5 次。
  - Run 不参与对象扫描（R14），扫描成本只随知识对象增长：1200 个 Run 时单次扫描约 14 ms，与 0 个 Run 相同。全局关键操作记录按 Run 目录修改时间（结束时间的上界）惰性读卡、页满即停；不限对象的 `runs.search` 按目录名（含创建时间）倒序读、找够即停，只有无命中的查询和 `unfixed_only` 的引用统计仍随 Run 数增长。批量读卡共用一次目录枚举、返回前复核（1200 张卡约 1.8 s，首次打开新文件时受杀毒扫描影响可达十余秒）。
  - 实测（每对象 6 条记录，假向量，本机）：提交 N=20 约 0.31 s、N=50 约 0.61 s；inspect N=20 约 0.08 s、N=50 约 0.21 s。就绪判断 50 个对象约 0.25 s（R13 §8）。
  - 关键操作记录每次请求都要为每个对象读最新提交，对象很多时需要可重建的派生缓存。
- 第二台物理机安装与真实业务验收尚未进行。
- 覆盖报告（R21 D-06）的阈值（字数比、字符 4-gram 重合度都低于 0.3 算"偏短"）没有真实导入数据的依据，只作提示。docx 中的公式（OMML）按线性文字抽取，扫描件依赖 OCR，转写时都要对照原文。PDF 按页分段，原件改版后页码移动会让较多分段显示为"改了"。
- 正文长编号检查（R21 D-10）只拦新写入的；上一修订已有的由维护页"引用改写"处理，替换不了的（Run、指纹、找不到 L0 的原件）要 AI 或人工改写。
- MCP 适配层通过 SDK 内部的工具注册对象关闭参数预校验（R20 D-08），SDK 升级后可能失效，由 `tests/interfaces/test_tool_errors.py` 检测。宿主是否把 `errors` 列表完整交给模型由宿主决定。
