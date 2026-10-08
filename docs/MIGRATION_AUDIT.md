# 迁移核对：源项目 → burrowl

核对日期：2026-10-08。对象是源项目 `A_work_auto_flow` 的代码、工作台页面、Skill 和文档与本仓库的逐项对照，由四路并行审查完成。

- 本页只列有差异的项；完整迁移的模块只在开头概括。
- 状态：✅ 已修复（附验证）；**删除** 表示有意不再提供，理由见 [DEVELOPMENT_HISTORY](DEVELOPMENT_HISTORY.md) 第三部分。

## 已完整迁移（概括）

以下模块已完整迁移：

- 提交与存储：预检、CAS、幂等回执、client_key、暂存回读、写锁身份检查。
- 研究工作流记录：问题、路线、目标、检查点。
- 文稿：全文、目录、章节、影响、图片。
- 记录相关：时间线、原始材料、巩固、关联候选与决定、影响、血缘、问题有效性。
- 复核与技术单元。
- 检索：混合检索、RRF 融合、查询计划、发现投影与 owner_only 语义、OCR 抽取、累计预算与游标。
- 材料查询：assemble、expand、deepen、documents。
- 阅读：三种策略、筛选包、三态判断、联想补查、委派。
- 新建对象与 Run。

## A. 面向 AI 的契约与交付

| # | 问题 | 结果 |
|---|---|---|
| A1 | AI 无法得知各类记录草案的字段 | ✅ 新增 `memory.contract(kind)` / `memory.kinds`，返回字段说明、写作要点和示例；13 类示例由 `test_draft_examples.py` 用真实校验器验证。提交回执直接给出 `record_hash` 与 `fixed_ref` |
| A2 | MCP 默认工具组缺 sources、index、relations、maintenance | ✅ 默认工具组覆盖全部 agent 可用的组 |
| A3 | MCP 只下发 SKILL.md，RULES.md 不可达；安装后 `../RULES.md` 失效 | ✅ RULES、RECORDING 作为 MCP prompt 下发，并附在每个 Skill prompt 后；`skills install` 把它们装到目标根目录 |
| A4 | Skill 参数写错（`reading_finish` 应为 outcome；`reading_recall` 没有改写模式） | ✅ 已改正，并逐个核对 Skill 中提到的参数 |
| A5 | 缺少定义和规则 | ✅ 重写 RULES（谁可以复核、工作清单、语言、禁止事项、术语短表）；新增 RECORDING（逐层要求、L1 写法、claim/scope、三种关系、保存顺序）、GLOSSARY；四个 Skill 补全步骤与判断规则 |
| A6 | `skills check` 只查 requires_tools | ✅ 同时检查正文中反引号标出的工具名、用户专属操作、默认工具组 |

## B. 版本记忆与存储

| # | 问题 | 结果 |
|---|---|---|
| B1 | 写锁自动清理有竞态 | ✅ 改名后核对内容，不是原持有者就原样放回；清理留回执（`test_core_io.py`） |
| B2 | inspect、resume、summary_materials 不过滤依据链上受限的记录 | ✅ 隐去并在 `omitted` / gaps 中说明（`test_source_access.py`） |
| B3 | `sources.register` 可覆盖已有登记 | ✅ 拒绝改写，只能提高敏感级别；新增 `sources.relocate`（字节一致才可搬家）、`sources.register_many`、用户专属的 `sources.update`；受限对象内的文件不能登记 |
| B4 | feedback 无幂等、不校验 adopted_in | ✅（`test_hardening.py`） |
| B5 | Run 登记不检查受限对象 | ✅ |
| B6 | Run 回滚 TOCTOU | ✅ 锁内重新核对 |
| B7 | 核心算法准入丢失 | ✅ 必须给出 `source_document` |
| B8 | runs.update 无 expected_sha256；execute 可用 run.json；目录冲突未包装 | ✅ |
| B9 | 导出缺固定来源与经验边界；raw 与 source 重复 | ✅ |
| B10 | check-run / finalize-run 封存门 | **删除** |
| B11 | hash-file、benchmark | **删除** |

## C. 检索与阅读会话

| # | 问题 | 结果 |
|---|---|---|
| C1 | 阅读会话不固定来源、不检查陈旧 | ✅ 首次读取固定对象 HEAD；笔记出处补全为固定引用；交接时重新核对，陈旧笔记与综合稿不交付 |
| C2 | 读任意一页就能写 note | ✅ 必须读完全部页 |
| C3 | quick 无逐片段采纳 | ✅ assess 用 `accepted_windows` 采纳，note 只能引用采纳的窗口 |
| C4 | reranking=required 缺模型不报错 | ✅ |
| C5 | assess 的 digest 可省略 | ✅ digest 与理由必填 |
| C6 | 全文补偿缺前提 | ✅ 召回给出 `fulltext_available`，只有召回不可用、不完整或不足时才能申请 |
| C7 | 阅读写操作无幂等 | ✅ 可选 request_id，重试返回原结果 |
| C8 | 显式条件重排未接入；阅读预算未使用 | ✅ `field_conditions` 参与对象排序（冲突排后、不删除）；召回受会话预算约束 |
| C9 | 依据超出范围时父材料仍完整输出 | ✅ 父材料标为 incomplete 并报告缺口（`test_assemble_scope.py`） |
| C10 | recent/snapshot、Markdown 副本、decide/bind、freshness=fixed | **删除** |
| C11 | 探索线索 | ✅ note 的可选 `clues`，交接时单独列为"未核实，仅供导航" |

上述规则均在 `tests/service/test_reading.py` 中有对应测试。

## D. 工作台、设置与运维

| # | 问题 | 结果 |
|---|---|---|
| D1 | 关系图的保存视图、随手笔记没有入口 | ✅ 关系图页：保存视图、笔记侧栏 |
| D2 | 记录详情看不到溯源与影响 | ✅ 记录页"依据与影响"：原件血缘、下游影响 |
| D3 | 监测缺 dry_run，候选不能处置 | ✅ `monitor.run(dry_run)`、`monitor.candidates`、`monitor.resolve`；新增"维护与监测"页 |
| D4 | 语义维护没有页面 | ✅ 维护页：生成计划 → 逐项处置 → 预检 → 提交 |
| D5 | 文稿缺目录、影响、图表；原始材料无页面 | ✅ 文稿视图切换（全文/目录/依据变化影响）、图表、"原始材料"标签页 |
| D6 | 设置页缺字段 | ✅ 笔记 token、联想开关、筛选、重排、两类预算、"恢复默认（仅草稿）" |
| D7 | 关系图缺过滤、候选、聚类、导出 | ✅ 类型/层级过滤、候选虚线与采纳/拒绝、社区着色（Louvain，固定种子）、返回上个中心；导出摘要 **删除** |
| D8 | 缺 doctor 与全工作区 validate | ✅ `burrowl doctor [--require vector/rerank]`、`burrowl validate`、`system.validate`（`test_bundle_validate.py`） |
| D9 | 缺整个工作区的备份与恢复 | ✅ 见 D12 |
| D10 | 来源搬迁映射、批量登记 | ✅ 见 B3 |
| D11 | 图投影新鲜度与后台任务 | **删除** |
| D12 | 计划中的可移植包（burrowl-bundle）未实现 | ✅ `portable/bundle.py`；`burrowl backup / restore / verify-backup`；篡改检测、不覆盖、中文路径 |

页面改动由组件测试与浏览器端到端测试覆盖（`frontend/e2e/workbench.spec.ts` 第 4 个场景）。

## E. 文档体系

| # | 问题 | 结果 |
|---|---|---|
| E1 | 源项目的六份必读文档在新项目中被压缩成四份根文档，开发所需的上下文丢失 | ✅ 恢复六份必读文档（README、ARCHITECTURE、docs/CORE、docs/README、docs/DOCUMENTATION_MAINTENANCE、docs/TESTING），加 DEVELOPMENT_HISTORY 和 GLOSSARY；内容按新架构精简重写，保留源项目的评价标准、稳定设计约束、功能块定义和维护规则 |
