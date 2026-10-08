# 开发决策与演进记录

本页回答三个问题：为什么这样开发、从哪些错误中改过来、哪些讨论还没有定案。

- **全局架构或工作流方案讨论前完整阅读**；局部方案按相关决定查阅；开发后补记实际结果。
- 当前功能和模块以 [CORE](CORE.md)、[ARCHITECTURE](../ARCHITECTURE.md) 为准，本页只保存理由，不复制接口手册。
- 每条写清三点：决定、原因、影响或代价。被替代的决定不删除，标明被什么替代。

## 一、从源项目继承、仍然约束开发的选择

源项目 `A_work_auto_flow` 的这些决定在重构中保留，编号沿用源项目，便于追溯。

| 编号 | 决定 | 原因 | 在本项目中的体现 |
|---|---|---|---|
| D01 | 复用业务对象（Owner）的身份，用不可变修订和固定引用；保存、索引、复核分开表达 | 另建平行对象会让来源和修订对不上；保存成功不代表索引或结论有效 | `memory_store`、FixedRef、回执中 save_status 与 index_status 分开 |
| D02 | 技术单元的检索说明与完整技术块分开；章节和文稿独立于 L0–L4 | 单靠卡片无法保留完整技术分析，也难以形成连贯报告 | detail 的 `retrieval_description` 与 `blocks`；document / document_section |
| D05 | 少量现行文档维护用途、架构、功能、索引、文档规则和测试；详细内容按需展开 | 旧说明、历史计划、当前架构混在一起，入口越来越重 | 六份必读文档，见 [docs/README](README.md) |
| D06 | 按重要性、改动大小、失败后果选择验证强度，不固定执行全套 | 小改动承担无关测试，浪费且掩盖重点 | [TESTING](TESTING.md) |
| D25 | 检索时由 AI 给出少量等义改写（技术问题加英文），保护型号和数值，分路召回再融合 | 中英文材料互相召回不佳 | `query_plan`、`reading.start` 的 variants/protected_terms |
| D27 | 重要成果用专门流程收口：完整读概览和文稿、盘点上次以来的变化、维护全文和上下文；中间记录可以随时保存 | 只更新单条记录会让文稿和概览逐渐过时 | consolidate Skill |
| D28 | 阅读时可用离线 Cross-encoder 和显式字段条件重排，只调整次序、不删除对象 | 纯融合排序在需要满足具体条件的问题上表现差 | `screening.packets`、`field_conditions` |
| D29–D31 | 工作区设置集中在一个文件，由程序校验、CAS 写入；子 Agent 能力要求是自由文本，由宿主选模型 | 不同宿主的模型和能力不同，不应写死 | `config.json`、`collaboration` |
| D35–D37 | 阅读笔记是细节充分、可继续研究的简化文稿；阅读者写完自查公式、单位、出处；笔记只写材料知识，不写操作计划 | 实际笔记曾遗漏递推公式和图引用；也曾混入阅读者的派工状态 | read Skill 的"研究笔记怎么写"、`reading.note` 的出处校验 |
| D36 | 阅读分 standard / associative / quick 三种策略；联想方向由主 Agent 制定 | 不同问题需要不同的阅读深度；阅读者不应自己决定联想方向 | `strategy`、`reading.recall(mode=association)` |
| D40 | 每条 L0–L4 记录必须脱离上下文也能读懂；正文写在 body，payload 不会被拼回正文 | 浮点样例的 L2/L3 单独读时无法使用，而字段非空校验发现不了 | [skills/RECORDING.md](../skills/RECORDING.md) 第 2 节 |
| D41 | 简单检查（读源码、看配置、查状态）不启动工作流、检索或子 Agent | "当前默认参数是多少"这类问题曾被误路由到材料检索并触发子 Agent | RULES 第 1 节 |
| D42 | 默认阅读使用独立的 Owner 发现投影；全文补偿必须由用户明确选择 | 全文索引加层级过滤并不能真正限制读取范围；发现与全文读取是不同的成本 | `discovery` 投影、`fulltext_available`、`approval_id` |
| D43 | 架构文档只保留现行模块、状态权威和稳定限制；术语必须有权威定义 | 日期式的实施叙述让架构文档难以使用 | ARCHITECTURE、GLOSSARY、DOCUMENTATION_MAINTENANCE |

## 二、本次重构的决定（2026-10-08）

### R1 只支持 memory-v4 现行版本，不迁移旧数据

- **决定**：数据格式直接采用 memory-v4 契约中各类型的现行版本。不写迁移工具，不兼容 v1–v3、event/map 类型和 legacy 阅读会话。
- **原因**：用户确认旧的真实记录不保留；兼容层是源项目复杂度的主要来源。
- **影响**：旧数据目录不能直接打开。测试夹具使用浮点求和样例（本身就是 v4 格式）。

### R2 检索合并为一套：FTS5 + sqlite-vec；Qdrant 只作可选的服务器后端

- **决定**：源项目三份独立的检索代码（原件、记录全文、Owner 发现）合并为一个 `index` 模块，关键词和向量放在同一个 SQLite 文件里。
- **原因**：
  - 三份代码各自建表、同步水位、融合结果，重复且互相反向依赖。
  - Qdrant 本地模式会加排他进程锁，MCP 和工作台两个进程无法同时打开；它的检索实际上是 numpy 暴力计算。
- **影响**：`index.vector.kind` 预留了 `qdrant-server`，但适配器**尚未实现**（约 10 万向量以上再做）。

### R3 一个操作注册表生成 HTTP / MCP / CLI

- **决定**：所有能力定义为带类型的函数，注册后自动生成三种入口和前端类型。
- **原因**：避免三套接口参数不一致。
- **影响**：
  - effect=`user` 的操作（复核确认、批准全文补偿、执行发布、停用来源、保存视图）不暴露给 agent。
  - MCP 写操作失败时以 ToolError 返回 Problem JSON。

### R4 外部知识系统做成可插拔连接器，本地是唯一权威

- **决定**：WeKnora、Markdown 库等作为发布目标与检索来源。默认只发布已复核的 L4/L3 与依据完整的成稿文档；撤回或复核失效时删除或标记外部条目。
- **原因**：WeKnora 擅长收集整合、检索和 Wiki，且由专业团队维护；但版本变化快，不能作为权威数据。
- **影响**：
  - WeKnora 的接口细节集中在 `api_v08.py`，新版本另加一层。
  - 宿主 AI 编写的 Wiki 先作为本地 `wiki_page` 记录提交。
  - WeKnora 已部署在本机（v0.8.2），但连接器尚未连真实服务实测。

### R5 阅读交互：工作台实时页 + MCP 询问

- **决定**：
  - agent 通过 `reading.*` 推进会话；用户在工作台实时查看并操作，操作进入收件箱，随下一次响应交付。
  - 需要批准的动作走 `reading.ask_user`：支持 elicitation 的宿主在对话中弹窗，否则回退到工作台。
- **影响**：标准阅读从 14–18 次调用降到约 6–9 次，调用前不再需要先写 JSON 文件。

### R6 Skill 合并为 4 个，规则只写一次（2026-10-08 修订）

- **决定**：
  - 源项目 8 个 Skill 合并为 burrowl-work / burrowl-read / burrowl-consolidate / burrowl-dev-check。
  - 公共规则写在 skills/RULES.md，记录标准写在 skills/RECORDING.md。
  - 程序已强制的规则在 Skill 中只一句带过。
- **原因**：源项目开工前要加载 5–7 万 token，同一条规则重复写 3–7 次。
- **修订**：初版过度精简，丢失了必要的定义和判断规则（见第四部分 E1），已补回。现在 4 个 Skill 加 RULES、RECORDING 共约 580 行，仍远少于源项目。

### R7 工作台前端：薄客户端，只调用操作接口

- **决定**：前端按功能分目录；所有数据通过 `POST api/v1/ops/<name>` 获取；参数类型由操作注册表生成；阅读会话页用 SSE 刷新。
- **原因**：源项目前端约 2.2 万行，其中大量是各接口的手写类型和重复的状态管理。
- **影响**：
  - 没有引入 TanStack Query，用约 100 行的 `useOp` / `useAction` 代替。
  - 依赖版本与源项目一致，可以离线安装。
  - 浏览器端到端测试使用系统自带的 Edge。

### R8 离线安装包与模型

- **决定**：
  - 安装包内置独立 CPython（取自 uv 管理的 python-build-standalone 解释器），依赖在构建时装好。
  - 每个版本独立目录，用目录联接 `current` 切换或回滚。
  - 模型放在本机共享目录，用 `burrowl models install` 安装。
  - MCP 宿主配置直接调用 `current\python\python.exe`。
- **原因**：
  - 宿主通过 cmd.exe 启动带空格路径的 `.cmd` 会出错。
  - cmd 按控制台代码页（GBK）读取 `.cmd` 文件，`.cmd` 里只能用纯 ASCII。
- **影响**：数据目录和安装目录都可以含中文与空格，已实测。

### R9 可移植包（burrowl-bundle）同时承担备份、恢复和迁移

- **决定**：整个数据目录按字节打包，附逐文件 sha256 清单；排除可重建的索引和中枢映射；恢复先完整校验，只写入空目录。
- **原因**：源项目的 portable 快照和迁移工具都依赖旧格式；新格式下按字节原样搬运即可保持所有固定引用有效。
- **影响**：外部原件（数据目录外的已登记文件）不打包，只在清单中列出；恢复后用 `sources.relocate` 指向新位置。
- **修订（2026-10-08）**：用户要求迁移时外部原件一同打包并自动重定向。改为：
  - 来源登记增加**工作根**，工作目录里的来源记为"根 + 相对路径"，整个目录搬家只改根；
  - 默认打包与登记指纹一致的外部原件（变化或缺失的只列入清单，因为固定引用指向登记时的内容）；
  - 恢复时先规划全部去向（映射 → 原位置不动 → 并列的 `<目录名>-work/`），有冲突就整体中止，再写入并改写来源登记。
  - 工作目录中未登记的文件（仓库其余部分、虚拟环境）不打包：只有被引用的原件才影响记忆的完整性。

### R11 项目改名为 burrowl（2026-10-08）

- **决定**：项目由 rdwork 改名为 burrowl（burrow 深洞贮藏 + owl 研究与智慧），全部统一：Python 包 `burrowl`、命令 `burrowl`、MCP 服务名、环境变量 `BURROWL_*`、数据目录状态 `.burrowl/`、项目标记 `.burrowl.json`、迁移包格式 `burrowl-bundle/1`、凭据前缀 `keyring:burrowl/`、安装目录 `%LOCALAPPDATA%\burrowl`。Skill 改为 burrowl-work / burrowl-read / burrowl-consolidate / burrowl-dev-check。
- **原因**：Skill 会装到客户端的全局目录，work、read 这类不带限定词的名字会和其他 Skill 冲突，也看不出归属。尚未正式发布，是改名成本最低的时候。
- **影响**：旧名称不保留兼容；之前用 rdwork 安装或接入的环境需要用新安装包重装并重新运行 `burrowl connect`。

### R10 接入方式是宿主配置，不是数据目录配置

- **决定**：数据目录、工作目录和运行模式（auto / manual）写在宿主 MCP 配置的启动参数里；`mcp.mode` 只作为未给参数时的默认值。auto 模式通过 MCP 服务说明（instructions）让宿主在每次对话中默认采用 burrowl-work / burrowl-read / burrowl-consolidate。
- **原因**：同一数据目录可以被多个项目、多个宿主使用，每个项目的工作目录不同；用户希望接入后"默认就按这套方式工作"，又能按项目关闭。
- **影响**：`burrowl connect` 一步完成数据目录、`.mcp.json` 与 Skill；burrowl-dev-check 默认不安装。
- **修订（2026-10-08）**：用户要求接入面向通用 agent，而不是优先 Claude；manual 要能按项目开启。改为：
  - `burrowl connect -c <客户端>` 按客户端写配置（generic、claude-code、codex、cursor、vscode、gemini、claude-desktop），默认用户级；
  - Skill 只装进原生支持 SKILL.md 的客户端；其余客户端由 MCP 工具 `system.guide` 提供同样的全文。MCP prompts 通常只能由用户触发，agent 读不到，所以不能作为唯一通道；
  - 项目开关 `burrowl enable / disable`：写 `.burrowl.json`（MCP 服务启动时按工作目录读取，manual 下启用、auto 下关闭），并在 AGENTS.md（及已存在的 CLAUDE.md / GEMINI.md）中维护标记块，让不读 MCP 服务说明的客户端也知道。

## 三、删除或暂缓的功能

| 功能 | 处理 | 说明 |
|---|---|---|
| 旧 Q/CTX/CF 检索反馈流程 | 删除 | 由 `memory.feedback` 记录取代 |
| run.json 内的原生复核、check-run / finalize-run 封存门 | 删除 | Run 的结论写成记忆记录，统一走复核；Run 的输入指纹在登记时固定 |
| 材料查询的高级分面过滤（roles / outcomes / confidence / applicability） | 暂缓 | 层级、对象、复核过滤、显式字段条件覆盖了常用场景 |
| representation 定义的检索使用 | 暂缓 | 记录类型保留，可以写入和读取，但不参与检索 |
| freshness=fixed（只用固定修订检索） | 删除 | current / allow_stale 覆盖实际用法 |
| 最近阅读 recent/snapshot、阅读笔记 Markdown 副本、reading-decide / bind | 删除 | 工作台会话页与 `reading.view` / `status` / `finish` 取代 |
| 关系图的导出摘要、图投影新鲜度与后台任务 | 删除 | 关系图每次请求实时构建 |
| hash-file、规模基准 benchmark、演示工作区 | 删除 | 开发辅助，风险低；测试使用浮点样例 |
| 旧工作区原地升级（setup --target） | 删除 | 见 R1；新版本用独立目录安装 |
| Qdrant 服务器适配器 | 暂缓 | 见 R2 |
| 检索质量评估集 | 暂缓 | 用户 2026-10-08 确认暂缓 |
| evidence.html、workbench.html 旧页面 | 删除 | 由 React 工作台取代 |

## 四、历史错误与教训

| 编号 | 观察 | 对后续方案的提醒 | 状态 |
|---|---|---|---|
| E1（2026-10-08） | 初版重构把 Skill 和文档压到约 200 行，用户指出缺少必要的上下文和定义，担心 AI 误解。核对发现：逐层写作要求、草案字段、谁可以复核、交接需要哪些记录都缺失，Skill 中还有两处工具参数写错 | 精简只删重复和程序已强制的内容，定义和判断规则必须保留；每类记录的字段由 `memory.contract` 给出并有测试保证；`burrowl skills check` 检查正文中的工具 | 已修复；见 [MIGRATION_AUDIT](MIGRATION_AUDIT.md) A 节 |
| E2（2026-10-08） | 迁移核对发现阅读会话丢失了若干源项目的程序规则：来源固定与陈旧检查、读完才能写笔记、quick 逐片段采纳、全文补偿前提 | 重构时，AI 依赖的程序规则要逐条列出并配测试，不能只迁移"主流程" | 已修复并加测试；MIGRATION_AUDIT C 节 |
| E3（2026-10-08） | 来源登记可以被覆盖，可借此重新启用来源或降低敏感级别；读取路径不过滤依据链上的受限来源；写锁清理存在竞态 | 授权边界要在存储层强制，不能依赖调用者自觉；并发清理要核对"移走的是不是自己看到的那把锁" | 已修复并加测试；MIGRATION_AUDIT B 节 |
| E4（源项目） | 软件回归通过，但检索留出题集质量不达标 | 能运行不等于找得准；检索方案需要独立的质量、延迟和上下文成本证据 | 评估集暂缓，仍需补 |
| E5（源项目） | 材料已保存，但来源回读或索引覆盖缺失 | 分别核对登记、固定原件、索引和复核；重建索引不能补回原件 | `burrowl validate` 分项报告 |

## 五、尚未定案的讨论

- 检索质量评估集的题目来源和指标。
- 发现投影的 L2 摘要长度与召回效果的关系。
- WeKnora 的模型配置（本机无 Ollama，可用 oneapi 或云 API），以及自动 Wiki 与 AI 编写 Wiki 的分工。
