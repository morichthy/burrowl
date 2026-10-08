---
name: burrowl-read
description: 需要在本地研发材料、经验或多个对象的文稿中检索、筛选并完整阅读时使用（含联想探索与片段快读）。直接读已知源码或配置不要使用。
requires_tools: [reading_start, reading_assess, reading_peek, reading_read, reading_note, reading_synthesize, reading_ask_user, reading_recall, reading_finish, reading_status, reading_delegate, reading_list, query_search, query_assemble, query_expand, query_documents, hub_search]
---
# 检索与阅读

先读 [RULES.md](../RULES.md)。阅读会话（RS）保存问题、召回、判断、阅读进度和笔记，可以续接；用户在工作台的"阅读会话"页实时查看并干预。

本 Skill 中反引号里的工具（如 `memory_owners`）都由 **burrowl MCP 服务**提供；不同客户端可能显示为 `mcp__burrowl__memory_owners`、`burrowl.memory_owners` 等形式，指的是同一个工具。看不到这些工具说明 burrowl MCP 没有接入，先告诉用户，不要改用读写文件代替。RULES.md 与 RECORDING.md 的内容也可以用 `system_guide(name="rules" / "recording")` 读取。

**每次工具响应里的 `inbox` 是用户的新操作**（批准、改判、补充方向、中止）。必须先处理，用户的改判优先于自己的判断。

## 选哪种方式

- **用户查询某主题的相关知识、找已有经验**（如“查询 存储与检索演进讨论 相关的知识”）：默认先 `reading_start` 建立可见的阅读会话；简查选 `strategy=quick`，需要完整理解或细节依据时选 `strategy=standard`，再按下面的步骤判断、阅读、写笔记和结束。
- **用户明确只要几个片段或候选列表**：可以直接 `query_search` → 选候选 → `query_assemble`（会补齐必要依据）。展开到经过或技术细节用 `query_expand`，读整篇文稿用 `query_documents`。
- **查已发布到知识中枢的内容**：`hub_search`。
- **续接已有会话**：`reading_list(owner_id=…)` 找到会话，再 `reading_status`。

`query_*`、`memory_owners`、`memory_resume` 是独立查询或对象续接工具，不会建立阅读会话，也不会出现在首页“当前阅读”或“阅读会话”中。不能用这些调用代替上述默认入口，也不能把检索命中写成“已完整阅读”。直接查看已知源码、配置或开发状态仍按公共规则直接读取。

## 阅读策略（在 reading_start 时选；之后只有用户能切换）

- **standard**（默认）：筛选对象，再逐个完整阅读。
- **associative**：原问题没有充分解决时，主 Agent 根据笔记中的缺口和线索制定联想方向，用 `reading_recall(mode=association)` 补查。受轮数上限约束。
- **quick**：只依据召回片段，不读全文，结论中要写明"非全文覆盖"。

## 步骤

1. **`reading_start`**：
   - `question` 保留型号、版本、数值和单位原文。技术问题加一条英文 `variants`；型号写进 `protected_terms`。
   - 有明确的字段条件（如"材料：X""温度 ≤ 300 K"）时，填进 `field_conditions`。
   - 返回第一批**筛选包**：每个对象 2–4 个命中窗口，以及 `digest`。
2. **`reading_assess`**：对每个对象给出判断，附 `reason`（依据窗口里的什么内容）和原样抄写的 `digest`。
   - **relevant**：窗口显示它能回答问题或提供必要依据，值得完整阅读。
   - **uncertain**：信息不足以判断。先用 `reading_peek` 看更多窗口；仍然拿不准就用 `reading_ask_user(kind=clarify)` 交给用户。uncertain 不会自动扩大阅读范围。
   - **irrelevant**：明确无关。
   - 首批不相关，不代表后面的批次也不相关，要继续判断返回的下一批。
   - quick 策略还要在 `accepted_windows` 列出可以作为笔记依据的窗口。
3. **`reading_read`**：standard/associative 逐个读 relevant 对象。第 1 页附目录，按 `unread_pages` 读完全部页；quick 跳过此步，只用已采纳的窗口写笔记。
   - 图片、L0、Run 先保留固定引用，需要解释图形或复算时再展开。没展开的不能写成"已核验"。
   - 返回 STALE_BASIS 说明对象刚有新提交，从第 1 页重读。
4. **`reading_note`**：完整阅读结束后（quick 为采纳窗口后），每个对象写一篇研究笔记（写法见下）；quick 明确写出“非全文覆盖”。跨对象的新认识用 `reading_synthesize`。
5. **依据不足时**：
   - 响应中 `fulltext_available=true`（说明发现召回不可用、不完整或不足）时，可以 `reading_ask_user(kind=fulltext)` 申请全文补偿。用户批准后，用得到的 `approval_id` 调 `reading_recall(mode=fulltext)`。
   - associative 策略：先制定具体的子问题和关键词，再 `reading_recall(mode=association, association_text=…)`。
   - 想换一种问法重新找：新开一个 `reading_start`。`reading_recall` 不做改写重查。
   - 达到轮数或范围上限、或者没有新的有效依据时，保留缺口并停止。
6. **`reading_finish(outcome=…)`**：结束会话，取回交接内容（笔记、固定出处、缺口）。
   - 陈旧的笔记（对象在写笔记后有新提交）不会交付。
   - 交接完整不代表研究完整。
   - 有复用价值的结果由 `burrowl-work` 另行保存为记忆。

## 研究笔记怎么写

目标是**一篇细节充分、可以继续研究的简化文稿**，不是几句摘要。

- 按材料的实际依据连接研究过程：起初遇到什么问题、已有方法为何不足、提出什么假设、为什么选这些方法和实验、观察到什么、失败或反例怎样促使认识修正、现在能得出什么。材料没写的历史不编造，自己的解释标为推断。
- **决定性细节不能丢**：定义、主公式和误差公式（变量、单位、初值、成立条件）、参数和输入输出、适用边界、反例和失败模式。
- 关键判断旁就近写出处 ID，`sources` 列全。程序会把出处补全为固定引用。
- 写材料本身的限制、缺失和待验证事项。材料中提出的建议放在"可选的下一步建议"标题下。
- **不写**：重复本次问题、自己的操作计划、派工理由、检索决定。
- 材料提到但没核实的方向写进 `clues`。它们只是导航，不会成为结论。
- **写完自查**：核心公式是否完整，变量、单位、条件是否保留，数字和图是否有出处，反例和限制是否遗漏。缺的先补，原文没有的写进限制，不编造。
- 笔记有 token 上限，超出会被整篇省略。先删重复和无关内容，不要把连贯论述压成提纲。

## 联想的边界

- 联想问题和关键词由主 Agent 制定，阅读者只按给定文本检索，并报告材料中的线索。
- 联想线索不能写进 supports 证据，采纳导航也不会提升复核状态。
- 跨领域类比要写明共享的问题结构和不能迁移的边界。

## 委派

适合拆分时用 `reading_delegate` 生成子 Agent 任务包：

- 只有返回 route=subagent 且宿主确实有子 Agent 工具时才派工。
- 同一会话一次只能有一位阅读者，不要递归委派。
- 子 Agent 写完笔记后只回报会话 ID、阶段和缺口。主 Agent 通过 `reading_finish` 或 `reading_status` 的交接内容接收结果，不重读全文。

## 程序已保证

以下由程序保证，违反时会报错：

- 判断必须带 digest 和理由；未判断不能读；quick 不能读全文。
- 要读完全部页才能写笔记；出处必须是已交付的材料。
- 对象更新后笔记和综合稿自动失效。
- 全文补偿需要用户批准且只能用一次。
- 重试同一个 request_id 不会重复执行；召回受会话预算约束；token 用量由程序计算。
