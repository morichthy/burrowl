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
| 发现与全文读取分开 | 默认发现使用独立的发现投影和独立水位，不能用全文索引加 level 过滤冒充。命中要回到规范固定版本核验。全文补偿必须由用户明确选择 |
| 写入和恢复保持单一边界 | 规范写入只走 MemoryService，保留版本冲突检测、幂等、不可变历史和单 Owner 事务边界。索引失败只补偿投影，不重复创建记录。跨对象部分完成不能写成全局成功 |
| 语义判断与程序执行分开 | AI 或人负责综合、反证、迁移判断和实际审查；程序负责校验、固定读取、组装和提交。监测和影响提示只产生候选，不自动改文稿、撤回结论或晋升复核。相似和导航采纳不等于科学支持 |
| 请求边界贯穿全链路 | 查询、续页、展开、组包、阅读，都保留用途、范围、排除项、权限、固定版本和预算。候选不是已读正文，组包无缺口不代表研究完整 |
| AI 可调用的能力与用户专属操作分开 | effect=user 的操作（确认复核、批准全文补偿、执行发布、停用来源、保存视图）只在工作台和 CLI 提供，不暴露给 MCP |

修改方案先在下方影响表定位责任模块，再追踪实际调用者。契约变化要同步检查：生成的前端类型、HTTP/MCP/CLI、Skill、相关测试、打包。只检查实际受影响的路径。

## 技术栈与运行入口

- **运行环境**：Windows x64 本地应用，Python 3.12（uv 管理）。发行时内置独立 CPython 与全部依赖，见 [packaging](packaging/)。
- **接口**：
  - `burrowl serve`：FastAPI 工作台，同时在 `/<token>/mcp` 提供 Streamable HTTP 形式的 MCP；只监听 127.0.0.1，需要随机 token，校验 Host/Origin，并设置 CSP。
  - `burrowl mcp`：stdio 形式的 MCP，供宿主直接拉起。
  - `burrowl call`：命令行调用任意操作。
- **存储**：
  - 规范记忆是 Owner 内的不可变文件提交（JSON）。
  - 检索用一个 SQLite 文件：FTS5 全文，加 sqlite-vec 向量（按模型指纹分表）。
  - 阅读会话用"不可变修订 + CAS"文档存储。
- **模型**：
  - 嵌入：fastembed 的多语言 MiniLM-384（ONNX，离线）。
  - 重排：可选的 ONNX Cross-encoder。
  - 模型统一放在 `%LOCALAPPDATA%\burrowl\models`，所有数据目录共用。
- **前端**：React + Vite，构建产物随发行包分发，使用端不需要 Node。
- **扩展**：通过入口点注册：`burrowl.ops`（操作）、`burrowl.connectors`（连接器）。

```
interfaces   ops 注册表 → FastAPI / MCP / Typer CLI
app          组合根：按数据目录与配置装配服务
services     memory, review, query, reading, indexing, hub, runs, relations, monitor, maintenance, config, health, workflow
storage | index | connectors | portable   （四者互不导入）
core         记录模型与校验、契约说明、哈希、原子写与锁、路径安全、配置模型、错误
```

上层只能导入下层；函数内部不允许 import（可选依赖除外，需注明）。错误统一为 `BurrowlError(code, message, details)`，在接口层分别转换为 Problem JSON、MCP ToolError 和退出码。

## 模块与变化影响

路径相对于 `packages/burrowl/src/burrowl/`。下表是全局模块影响的唯一入口。

| 模块 | 职责与代码入口 | 变化时检查的关联 |
|---|---|---|
| CORE 记录契约 | `core/records.py`（校验与领域规则）、`core/schema/`（memory.schema.json 与草案示例）、`core/contract.py`（给 AI 的契约说明） | 所有读写记录的服务、`memory.contract`、草案示例测试、前端类型、Skill 与 RECORDING |
| STORE 规范存储 | `storage/memory_store.py`（提交、CAS、幂等、暂存回读、恢复）、`storage/owners.py`（对象发现、业务卡、核心算法准入）、`storage/sources.py`（来源登记）、`core/atomic.py`（锁） | 所有服务；备份恢复；validate |
| MEM 记忆服务 | `services/memory/`：service（提交与读取过滤）、claims（结论与复核图）、documents（文稿）、history、raw_materials、provenance（影响与血缘）、associations、consolidation、export | REVIEW、QUERY、READ、HUB 的读取者；工作台记忆页；work 与 consolidate Skill |
| REVIEW 复核 | `services/review.py`、`services/memory/claims.py` | 检索的复核类别、正式用途、中枢发布策略、复核页 |
| IDX 检索 | `index/`（db、projections、fusion、query_plan、rerank、conditions、extract、embed、model_store）、`services/indexing.py` | QUERY、READ 的召回；模型安装；索引重建；发现可见性语义 |
| QUERY 材料查询 | `services/query/`（search、page、assemble、expand、deepen、documents，以及预算） | 检索页；read Skill 的片段模式；context_reach 语义 |
| READ 阅读会话 | `services/reading/`（service、screening、notes、text、session） | 阅读会话页（SSE）；read Skill；配置中的阅读默认值与预算 |
| HUB 知识中枢 | `services/hub.py`、`storage/hub_state.py`、`connectors/`（spi、registry、fake、mdvault），以及独立包 `burrowl-connector-weknora` | 发布策略与复核类别；中枢页；wiki_page 记录；合约测试 |
| MAINT 维护与监测 | `services/maintenance.py`、`services/monitor.py`、`services/relations.py` | 维护页、关系图页；consolidate Skill |
| CFG 配置 | `core/config.py`（BurrowlConfig）、`services/config.py`（CAS 与影响提示）、`storage/config_store.py`、`storage/secrets.py` | 所有读取默认值的服务；设置页；MCP 工具组 |
| PORT 可移植包 | `portable/bundle.py`（burrowl-bundle 备份、校验、恢复） | 数据目录布局变化；排除规则 |
| APP 入口 | `interfaces/`（ops 注册表、api、mcp、cli）、`app.py`、`frontend/` | 同一操作在 HTTP/MCP/CLI/前端的一致性；生成类型；effect 分类 |
| GUIDE 规则与说明 | `skills/`（RULES、RECORDING、4 个 Skill）、六份必读文档 | 所描述能力的代码与契约；`burrowl skills check` |
| QA 测试与交付 | `tests/`、`frontend/src/**/*.test.tsx`、`frontend/e2e/`、`packaging/` | 见 [TESTING](docs/TESTING.md) |

## 状态由谁负责

| 状态 | 权威来源 | 边界 |
|---|---|---|
| 对象身份 | 内容树中的业务卡（research.json 等），由 OwnerRegistry 扫描 | 文件存在不等于登记为对象；核心算法必须有文档依据 |
| 规范内容与历史 | Owner 的 `memory_home`：commits、HEAD.json、请求回执；由 MemoryStore 管理 | 单 Owner 提交可见；旧修订不改写；没有跨 Owner 事务 |
| 来源授权 | `.burrowl/sources.json`（工作根 roots + 来源 sources；工作目录里的来源记为"根 + 相对路径"） | 搬家只改位置且必须指纹一致；登记不能被 agent 改写，只有用户能停用或降低敏感级别。文件内容变化后，引用它的结论显示依据失效 |
| 复核 | 各 Owner 中的 review 记录 | 绑定内容指纹；内容变化即失效 |
| 检索投影 | `.burrowl/index/index.sqlite3`：每个 (Owner, 投影, 通道) 的水位绑定 HEAD、投影版本和模型指纹 | 可删除重建；pending/stale 的投影如实报告 |
| 配置 | `.burrowl/config.json`（revision = 内容指纹，CAS 写入） | 密钥只存引用，明文在 Windows 凭据管理器 |
| 阅读会话 | `.burrowl/sessions/reading/RS-*/`（HEAD + 不可变修订） | 用户工作数据，不是缓存；笔记固定写作时的对象 HEAD |
| 维护计划 | `.burrowl/plans/MP-*/`（不可变计划修订） | 与规范提交分开；可能逐对象部分完成 |
| 监测 | `.burrowl/monitor/`：基线、观察记录、候选状态 | 只是观察，不改证据；扫描不完整时不更新基线 |
| 中枢映射 | `.burrowl/hub/state.sqlite3` | 派生状态，丢失后按外部元数据中的 local_key 认领 |
| 外部中枢内容 | WeKnora 等 | 派生副本，可以由本地重新发布 |
| 工作台偏好 | `.burrowl/workbench/`（关系图视图、随手笔记） | 本机偏好，不是研究记录 |
| 当前任务上下文 | 任务的 Markdown 工作清单，由 AI 编辑 | 不是后台同步的状态；检查点保存交接时点 |
| 模型 | `%LOCALAPPDATA%\burrowl\models`（或 `BURROWL_MODELS_DIR`） | 嵌入模型变化会改变指纹，向量投影要重建 |

## 现行数据流与限制

### 保存与索引

`memory.commit` 的流程：

1. 校验、解析引用，核对固定字节与授权；
2. 锁内暂存并回读；
3. 原子切换 HEAD；
4. 同步更新该 Owner 的检索投影。

索引失败时，内容仍然已保存（回执显示 pending），由 `index.reconcile` 补偿，不重复提交。

读取路径（inspect、resume、总结材料、导出）会隐去依据链上有受限或不可用来源的记录，并在 `omitted` 中说明原因。

### 发现、筛选与完整阅读

- 默认在**发现投影**中召回 Owner。发现投影由 L4/L3 正文、L2 摘要、L1 检索说明和对象元数据组成。
- 召回后，给每个对象 2–4 个互补窗口组成筛选包，可选用 Cross-encoder 只调整窗口次序；显式字段条件只调整对象次序。
- 判为相关的对象，按页读取其完整研究过程文稿或各层正文。
- 全文投影只在用户批准全文补偿时使用。
- owner_only 的记录只在明确指定对象时可见。

### 阅读会话与用户交互

- agent 通过 `reading.*` 推进；用户在工作台实时查看，通过 SSE 接收会话的修订推送。
- 用户操作写入会话的收件箱，在 agent 下一次调用时交付一次。
- 支持 MCP elicitation 的宿主会在对话中直接弹出询问。
- 笔记固定写作时的对象 HEAD，交接时重新核对，陈旧的不交付。

### 排序、预算与降级

- 融合用 RRF，排序不代表科学可信度。
- 查询有累计预算（时间、读取、输出、候选）；阅读会话的召回轮次也受预算约束。
- 嵌入模型或重排模型缺失时：关键词检索照常，向量通道报告未就绪，重排报告不可用。重排设为 `required` 时，模型缺失直接报错。

### 知识中枢

发布是"期望状态对账"：

1. 按策略算出应发布的集合；
2. 与映射比较，得出新增 / 更新 / 删除 / 标记；
3. `plan` 给出预览和 plan_hash，`apply` 时必须一致；
4. 外部异步解析，用 `status` 轮询。

宿主 AI 编写的 Wiki 以本地 `wiki_page` 为真源，外部被人改过时报告漂移，不覆盖。

### 安装与迁移

- 离线安装包按版本安装，用 `current` 联接切换和回滚版本。
- 整个数据目录的备份、迁移用 burrowl-bundle（逐文件 sha256；不含可重建的索引）。默认连同数据目录外的已登记原件一起打包；恢复时按 `--map` 或默认规则放置原件，并自动改写来源登记。
- 接入方式（数据目录、工作目录、运行模式 auto/manual）是宿主 MCP 配置里的启动参数，不写进数据目录；`burrowl connect -c <客户端>` 按客户端写好 MCP 配置并在原生支持时安装 Skill（`interfaces/agents.py`）。项目开关 `.burrowl.json` 优先于模式；工作流说明另由 `system.guide` 提供给不支持 Skill 的客户端。
- 不提供源项目旧数据（v1–v3）的迁移。

### 已知差距

- Qdrant 服务器适配器未实现：sqlite-vec 是暴力检索，约 10 万向量以上需要它。
- 检索质量评估集未建立。
- WeKnora 连接器只用模拟接口测试，尚未连真实服务实测。
- 宿主 token 用量不由本项目保证。
- 第二台物理机安装与真实业务验收尚未进行。
