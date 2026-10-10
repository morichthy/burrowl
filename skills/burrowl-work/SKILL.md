---
name: burrowl-work
description: 在研发工作区开展需要归属、留痕、复用或续接的工作时使用：选对象、续接上下文、导入已有资料、执行 Run、按 L0–L4 保存成果与交接。简单的源码/配置/状态检查不要使用。
requires_tools: [memory_owner_route, memory_owners, memory_owner_create, memory_owner_adopt, memory_resume, memory_inspect, memory_records, memory_contract, memory_commit, runs_create, runs_execute, runs_register, runs_update, sources_register, sources_register_many, sources_add_root, sources_list, index_status, index_reconcile, relations_decide]
---
# 工作与记录

先读 [RULES.md](../RULES.md)；写任何记录前读 [RECORDING.md](../RECORDING.md)。

本 Skill 中反引号里的工具（如 `memory_owners`）都由 **burrowl MCP 服务**提供；不同客户端可能显示为 `mcp__burrowl__memory_owners`、`burrowl.memory_owners` 等形式，指的是同一个工具。看不到这些工具说明 burrowl MCP 没有接入，先告诉用户，不要改用读写文件代替。RULES.md 与 RECORDING.md 的内容也可以用 `system_guide(name="rules" / "recording")` 读取。

## 何时用 / 何时不用

- **用**：需要找已有经验、续接之前的工作、执行并留痕、导入已有资料，或会产生值得保存的成果。
- **不用**：少量文件就能直接回答的问题（见 RULES 第 1 节）。
- 需要跨材料检索和完整阅读时，转用 `burrowl-read`。
- 阶段收口、交接整理、结论变化后的全面同步，转用 `burrowl-consolidate`。

## 步骤

0. **定档**：按 RULES 第 1 节判断是档 1 还是档 2。档 1 不做下面的第 1、2 步，结束时做沉淀检查，有内容才从第 1 步开始保存。
1. **定归属**：用 `memory_owner_route` 取得候选和证据，按 RULES 第 2 节选出暂定对象（或新建），理由写进工作清单。需要浏览全部对象时用 `memory_owners`。
2. **续接与确认**：持续任务先读工作清单，再用 `memory_resume` 完整阅读暂定对象；不相关就回到第 1 步换候选或新建（确认前不向暂定对象写入）。它按 目标 → 检查点 → 概览/经过/技术单元 → 路线/问题/经验 的顺序给出固定材料，只读，不执行任何下一步。工作清单与新回执冲突时，以新回执为准并更新清单。
3. **维护工作清单**：位置、内容和更新时机见 RULES 第 5 节。
4. **执行**：
   - 独立的执行建 Run：新 Python 脚本用 `runs_execute`，它会固定脚本和输入的指纹，捕获输出与日志，失败也完整登记；已有产物或其他工具的结果用 `runs_register`。
   - 工作目录里的脚本和输入先 `sources_register`，再把 SRC- ID 传给 Run（Run 只接受 SRC- ID 或数据目录内的相对路径）。Run 的输出与日志存在数据目录的 Run 目录下。
   - 结束时用 `runs_update` 写执行摘要（conclusion：成功或失败、产物、异常）和 limitations；还没跑完就把 status 写成未完成，不把日志当总结。
   - Run 不保存记录：执行得到的知识在第 5 步写进上级对象（独立 Run 按 RULES 第 2 节选对象）并引用该 Run。没有可写成记录的内容时，用 `runs_discard` 丢弃。
   - 没有执行的分析直接写记录，不虚构 Run。
5. **保存**：
   - 有用内容按 L0–L4 职责写草案，先调用 `memory_contract(kind)` 查字段和示例。
   - 用 `memory_commit` 提交，可以先 `dry_run`。本任务第一次往该对象保存时带 `attribution`（RULES 第 2 节）。
   - 新文件依据先 `sources_register`。
   - 保存顺序：先被引用的，再引用它的。
6. **回读**：
   - 用回执里的 `fixed_ref` 引用刚保存的记录。
   - 用 `memory_inspect` 回读，按 RECORDING 第 2 节逐条做脱离上下文的检查。
   - `index_status` 为 pending 时如实说明，必要时 `index_reconcile`，不重复提交。
7. **交接**：需要别人或下一次续接时：
   - 若对象还没有 goal 记录，先写一条（目标、约束、成功标准）；
   - 再写 checkpoint 记录（`goal_ref` 指向当前目标，写明已完成什么、下一步、前提和停止原因）。
   - 交接说明里给出工作清单路径、检查点 ID 和未解问题。
   - 没有可继续的工作就不建检查点。

## 导入已有资料

用户给出资料路径、用途和授权范围时：

1. 用 `memory_owner_route` 找已有对象（资料路径放进 `paths`）。资料缺稳定身份时：
   - 已有说明文档：用 `memory_owner_adopt` 采用（原文不改）；
   - 否则新建对象；
   - 公司核心算法要先核对文档依据。
2. 用 `sources_register_many` 批量登记原件（用绝对路径；原件留在原位，只记路径和指纹，见 RULES 第 0 节）。资料在工作目录之外的一整个文件夹时，先 `sources_add_root` 把它登记为工作根，以后迁移可整体重定向。已在 Run 中登记过的材料不重复登记。
3. **分批实际阅读**，按内容写记录：L4 说明是什么、能做什么；方法、定义、计算写 L1；能独立复用的判断写 L3。原文没写形成过程就不编 L2。
4. 在工作清单里记下读到哪里、失败和下一批起点。登记不等于已读，已读不等于已验证。
5. 导入后，用资料里独有的词或典型问题检索一次，确认能被找到；找不到就如实报告缺口。

## 需要 AI 判断的地方

- 除了主体目标，顺手检查过程中出现的可迁移方法、失败与反例、参数或工况边界、偶然发现。能独立复用的写 L3，技术细节写 L1。
- 中间成果有用就直接保存，不必等用户逐次要求；未复核的认识保持未复核。用户明确说只暂存或限定范围时照办。
- **随手连起来**：正文里提到概念库已有的概念就写成 `[[概念]]`，提到本对象或其他对象的记录、块、结论就写 `[[@handle]]`、`[[@handle#块]]`、`[[!结论短名]]`（写法见 RECORDING 第 6 节）。发现两条材料"值得放在一起看"，用 `relations_decide` 快速添加（只给 from、to、explanation，自动记为 related_to + 推测）。"A 会影响 B"写成结论，不写成关联。概念库里还没有的概念照样写链接，收口时再统一建页或合并。
- 已有依据足够就推进，不要为每条消息重新检索。
- 依据不足时判断：读深已有材料、换问法检索、扩大授权范围，还是先做实验。把理由记进清单。不按次数强制停下来问用户。

## 程序已保证

以下由程序保证，失败时工具会报错并说明原因：

- 提交冲突检测（expected_head）、幂等（request_id）、草案结构校验；
- 固定引用核对、受限来源不被引用；
- 索引在提交后自动更新；
- Run 输入指纹固定；
- 受限对象的材料不能登记进 Run；
- 正文链接在提交时解析并固定版本；记录、块、结论链接解析不了会给出候选，概念未建页不阻塞提交。
