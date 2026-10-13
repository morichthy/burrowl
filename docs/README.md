# 文档索引

必读文档只读本页，不自动展开全部链接；按问题查找对应的细节入口。

## 六份必读文档

| 文档 | 承载什么 | 何时读取或维护 |
|---|---|---|
| [README](../README.md) | 用途、概念、安装、日常用法、导入已有资料、目录维护 | 用户可见的概念、功能、入口或目录变化 |
| [ARCHITECTURE](../ARCHITECTURE.md) | 评价标准、稳定设计约束、模块影响表、状态归属、数据流与已知差距 | 职责、依赖、状态、数据流变化；全局唯一的架构入口 |
| [CORE](CORE.md) | 功能块定义、交接、核心流程伪代码 | 功能定义、关键行为或调用方式变化 |
| [docs/README](README.md)（本页） | 细节入口与分类 | 新增或移动文档 |
| [DOCUMENTATION_MAINTENANCE](DOCUMENTATION_MAINTENANCE.md) | 文档职责、术语规则、维护闭环 | 规则、Skill 或文档职责调整；全面一致性审查 |
| [TESTING](TESTING.md) | 按风险选择验证、测试分层、不能省略的检查 | 选择测试或维护测试 |

另外两份是按需阅读的支撑文档：

- [DEVELOPMENT_HISTORY](DEVELOPMENT_HISTORY.md)：选择理由、删除的功能、历史错误和未决讨论。讨论全局方案前完整阅读。
- [GLOSSARY](GLOSSARY.md)：术语的权威定义。

## 进行中的设计稿（design/）

设计稿是可直接指导代码变更的计划，带稳定的决定编号和任务编号，以及进度记录。实现完成后，稳定的说明迁移到 CORE / ARCHITECTURE，设计稿保留为历史。

| 设计稿 | 内容 | 状态 |
|---|---|---|
| [R12 知识图谱与检索升级](design/R12_KNOWLEDGE_GRAPH_RETRIEVAL.md) | 修复向量截断与建立评估集、长上下文嵌入与上下文信封、整理图（概念、结论关系、关联、成熟度）、图投影与图路线、交换格式、外部连接器 | P0–P4 已实施，部分验收待用户 |
| [R13 归属判定、投入分级与关键操作记录](design/R13_OWNER_ROUTING.md) | 简单任务不进流程、归属判定复用阅读召回、确认点与回退、提交回执中的归属说明、关键操作记录（工作台与对话） | P1–P2 实施中，P3 未开始 |
| [R14 Run 作为执行证据](design/R14_RUN_EVIDENCE.md) | Run 不参与对象扫描、不进默认检索；按编号定位；两种 Run 的知识去向；未被固定的执行证据报告；没有知识的 Run 丢弃到回收站 | P1–P3 已实施 |
| [R16 语义维护交给 AI](design/R16_MAINTENANCE_HANDOFF.md) | 候选一键成计划；AI 起草处置与修订稿；用户预览后执行或退回；执行仅限用户 | 已实施，细节待用户确认 |
| [R17 后切分与嵌入模型选择](design/R17_LATE_CHUNKING.md) | 后切分实现、默认嵌入模型 jina-v2-base-zh、模型 × 切分方式的评测方法与实测记录 |
| [R18 归属判定与阅读会话的衔接](design/R18_ROUTE_READING.md) | 判定后同一问题的阅读复用那一轮召回；先定归属再开阅读；关联在实际阅读后由 AI 写记录时建立 | 已实施 |
| [R19 关系知识的检索与联想](design/R19_RELATION_KNOWLEDGE.md) | 默认可见范围按记录类型；结论、关联、概念页进发现投影；统一边标准；联想改为按对象的一跳图扩展；扩大检索须用户批准；两层关系词表 | 已实施 |
| [R20 AI 填写的字段与报错](design/R20_AI_FIELDS_AND_ERRORS.md) | 草案契约与工具参数说明；统一报错、内部错误日志与统计；请求日志关闭行为、配置保存与影响预览反馈；可关闭的 MCP 错误兼容返回 | D-11–D-13 补修已实施并通过软件回归，其他客户端实测待做 |
| [R22 删除记录与对象](design/R22_DELETION.md) | 记录用墓碑提交删除（旧修订保留、可恢复），对象移入回收站；引用护栏；清空回收站仅限用户 | 已实施 |
| [R23 结论的结构](design/R23_CLAIM_STRUCTURE.md) | 结论分核心、限定、依据三层；多元参与者与谓词框架；结构自动生成且不计入内容指纹；概念链接投影时解析；关系图边类别重整、只用节点模式；图扩展的关系窗口带出结论陈述与范围 | 已实施，评测对比与真实数据验收待做 |
| [R15 工作台笔记交给 AI](design/R15_WORKBENCH_NOTES.md) | 用户把笔记交给 AI；AI 回写方案、提问或说明不处理；写入先预览，由用户在工作台执行；处理完折叠保留 | P1–P2 已实施，细节待用户确认 |
| [R21 外部资料导入不丢细节、引用可读可跳转、工作台后台运行](design/R21_FAITHFUL_IMPORT.md) | L0–L4 本质原则与兜底；完整转录、按原文逻辑切分与上下文说明；快照、原始图片附件、文字与图片覆盖提示、原文展开；来源变化对比、可读引用；工作台后台运行 | D-16–D-18 补修已实施，真实文档导入验收待做 |

## 面向 AI 的方法源（skills/）

| 文件 | 内容 |
|---|---|
| [skills/RULES.md](../skills/RULES.md) | 公共规则：何时进入流程、归属、三种状态、谁可以复核、工作清单、语言、禁止事项 |
| [skills/RECORDING.md](../skills/RECORDING.md) | 记录标准：L0–L4 写什么、独立可读、L1 写法、结论与范围、引用关系、保存顺序 |
| [burrowl-work](../skills/burrowl-work/SKILL.md) · [burrowl-read](../skills/burrowl-read/SKILL.md) · [burrowl-consolidate](../skills/burrowl-consolidate/SKILL.md) · [burrowl-dev-check](../skills/burrowl-dev-check/SKILL.md) | 四个工作流 |

## 按问题查

| 问题 | 去哪里 |
|---|---|
| 某个工具有哪些参数 | [reference/ops.md](reference/ops.md)（自动生成） |
| 某类记录的字段和示例 | 调用 `memory.contract(kind)`；示例文件在 `packages/burrowl/src/burrowl/core/schema/examples/` |
| 配置项与默认值 | [reference/config.md](reference/config.md)（自动生成） |
| 交换包（Markdown 库 + 图 JSONL）的格式与兼容规则 | [reference/exchange-format.md](reference/exchange-format.md) |
| 部署 WeKnora | [deploy/weknora/README.md](../deploy/weknora/README.md) |
| 打包与离线安装 | [packaging/build.py](../packaging/build.py) 的说明、README 的"安装"一节 |
| 源项目的功能在新项目中的对应情况 | [MIGRATION_AUDIT](MIGRATION_AUDIT.md) |

## 精确契约与运行代码

文档与代码不一致时，以代码和测试为准，并修正文档：

- 记录契约：`core/schema/memory.schema.json`（由 `tools/extract_schema.py` 生成）。
- 操作契约：各操作函数的类型签名（`interfaces/ops/builtin/`）。
- 配置契约：`core/config.py`。
- 真源兼容：`tests/fixtures/compat/` 中各版本冻结的标本（由 `tools/make_compat_fixture.py` 生成，规则见 [TESTING](TESTING.md)）。
