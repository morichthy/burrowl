---
name: burrowl-consolidate
description: 用户要求记录或整理当前成果、阶段总结、交接，或结果/反例/适用范围/方向发生实质变化时使用：完整阅读概览与文稿，核对上次文稿以来的变化，修订内容与文稿，处理来源变化后的语义维护、复核整理与关联，并可发布到知识中枢。
requires_tools: [memory_inspect, memory_document, memory_timeline, memory_contract, memory_commit, memory_summary_materials, memory_summary_save, memory_owner_create, review_queue, review_history, review_submit, provenance_impact, provenance_lineage, relations_associations, relations_similar, relations_candidates, relations_decide, index_gaps, config_get, config_update, maintenance_plan, maintenance_review, maintenance_apply, maintenance_status, monitor_candidates, hub_plan, hub_status]
---
# 整理成果

先读 [RULES.md](../RULES.md) 与 [RECORDING.md](../RECORDING.md)。

本 Skill 中反引号里的工具（如 `memory_owners`）都由 **burrowl MCP 服务**提供；不同客户端可能显示为 `mcp__burrowl__memory_owners`、`burrowl.memory_owners` 等形式，指的是同一个工具。看不到这些工具说明 burrowl MCP 没有接入，先告诉用户，不要改用读写文件代替。RULES.md 与 RECORDING.md 的内容也可以用 `system_guide(name="rules" / "recording")` 读取。

## 何时用 / 何时不用

- **用**：
  - 用户要求"整理/记录当前成果"、阶段总结或交接；
  - 自己发现结果、适用范围、反例或方向发生实质变化（主动使用，不等用户说）；
  - 来源变化或出现新反证，需要审查下游内容；
  - 要把知识发布到中心知识库。
- **不用**：普通的单条保存，直接用 `burrowl-work` 提交即可，每次提交不强制收口。用户明确只暂存或限定范围时，按用户的范围处理。

文稿的结构是 文稿根 → 章节 → 技术块。概览（L4）是独立的发现入口，不是文稿根，也不能代替全文。

## 步骤

1. **固定基线并完整阅读**：
   - 用 `memory_inspect` 记下对象的 HEAD 和目标记录的 revision。
   - 用 `memory_document(view="outline")` 确认文稿和章节，再用 `view="full"` 读完全文；太长就按章节用 `view="section"` 分批读完。
   - 同类文稿有多篇时，按目标、受众、范围选择，并记下实际 ID。
   - 只看标题或截断输出，不算读过全文。没有文稿时明确"基线为空"。
2. **列变化表**：
   - 用 `memory_document(view="impact")` 比较文稿固定的依据和当前修订；再用 `memory_timeline` 盘点上次文稿之后的新记录，特别是文稿没引用的新技术内容、经过、经验、反例和 Run。
   - 在工作清单里维护一张表，列：记录（旧 → 新版本）、受影响章节/概览/认识、实际阅读范围、处置（保留/修订/新增/撤回/暂缓）、理由。
   - 区分"引用旧版仍然适用"和"还没同步"。
3. **修订内容与文稿**：
   - 同一内容的演进沿用原记录 ID 修订，新的独立内容才新建；保留失败和适用边界。
   - 先保存被引用的技术内容、经过、经验，再更新概览和受影响章节，最后更新文稿根的 `section_refs`。未变化的内容沿用原修订，不为收口制造全量新版本。
   - 每条新建或修订的记录都按 RECORDING 第 2 节逐条回读。
   - 预期长期复用的对象要有一条当前、自足的 L4。
4. **来源变化与语义维护**（依据变化、出现反证、监测给出候选时）：
   - 用 `monitor_candidates` 或 `provenance_impact` 找到受影响的记录，用 `maintenance_plan(changed_ids)` 生成计划，并逐项阅读计划给出的正文与新旧依据。
   - 对每项给出处置和具体理由：
     - **retain**：依据变化不影响结论；
     - **revise**：修订，附新的完整草案；
     - **resynthesize**：基于新依据重新综合，附草案；
     - **retract**：撤回；
     - **defer**：暂缓，未知或超出范围时使用。
   - 用 `maintenance_review` 记录处置并预检，在授权范围内 `maintenance_apply` 提交，再用 `maintenance_status` 核对。它按对象逐个提交，可能部分完成，要如实报告。
5. **复核与溯源**：
   - 用 `review_queue(owner_id=…)` 列出未复核、已失效、有争议、依据受阻的结论，写进待复核清单并说明理由。
   - 复核本身由用户在工作台完成。只有符合 RULES 第 4 节的情况才由 AI 代为 `review_submit`。
   - **评估成熟度**：按 RULES 第 4 节的阶梯给结论登记 `maturity`（设想 / 初步 / 重复验证），写明理由和依据；认为证据已足以"已确立"时只提交 `requested_maturity=established`，由用户在复核队列确认。看 `review_queue` 里的派生统计（独立依据、未处理反例），有未处理反例的先处理反例。
   - `provenance_lineage` 查原件来源；来源数不等于独立支持数。
6. **关联与概念**：
   - 用 `relations_candidates(owner_id=…)` 看保存后自动算出的线索（正文提到概念却没链接、向量近邻），也可以用 `relations_similar` 补充。逐条判断：该链接的改写正文加 `[[…]]`，值得放在一起看的 `relations_decide` 保存（带 certainty），不相关的保存为 rejected（以后不再出现）。
   - 类比（analogous_to）必须写共享结构和迁移边界；"A 会影响 B"写成结论，不写成关联。关联只是导航，不进入证据。
   - **维护概念库**（全库唯一的 knowledge 对象；配置 `graph.concept_owner_id` 为空时，先问用户后用 `memory_owner_create(owner_type="knowledge")` 建立并用 `config_update` 填入）：
     - `index_gaps` 里的 missing_concept 是写了链接但还没有概念页的名字。只为被实质讨论过、或至少出现在两条记录中的概念建页，正文可以只是一句定义加适用领域（`domains`）。
     - 建页前先查名称、别名和相近概念，按"相关不等于相同"判断：相同就加别名或合并（被并页 `status=merged` + `merged_into`），同名不同义用限定名（如"收敛（数值）"），一个概念其实是两个就拆分（`status=split` + `split_into`）。从不删除概念页。
     - orphan_concept（没有任何记录链接的概念页）检查是否该合并或补链接。
7. **两层体检与全文一致性检查**：先用 `index_gaps` 看程序能确定的缺口（缺概览、检索说明为空、块过长、结果块没声明依赖、未解析概念、未处理反例、待确认升级），逐项处理或写明暂缓理由；再用新的 revision 读全文，检查：
   - 衔接、重复、术语和单位、公式和数字、结论和限制；
   - 关键判断是否就近引用固定依据；
   - 概览、全文、工作清单、检查点里的结果和下一步是否一致。
   - 结束前再查一次 HEAD；有并发修改先评估差异。
8. **收口声明**：在工作清单或结果摘要中写明：
   - 范围、检查时间、审阅者（AI 自查要写明）；
   - 起止 HEAD、文稿 ID 与 revision；
   - 变化处置、索引状态、未决事项、下一步。

   只有第 1–7 步都完成、必要依据都可读、保存回读成功，才写：

   > **本阶段完整成果已同步（范围：…；文稿版本：…；AI 一致性自查）**

   部分完成时写明实际保存了什么、"完整成果待同步"以及具体障碍。这个声明不代表科学复核，也不保证以后的更新会自动同步。
9. **发布（可选）**：需要进入中心知识库时：
   - 先 `hub_plan` 预览（默认只发布已复核的内容），把预览交给用户；
   - 由用户在工作台执行发布，之后用 `hub_status` 查看结果。

## Wiki

宿主 AI 编写 Wiki 页面：

- 用 `memory_commit` 提交 `kind=wiki_page` 记录，字段用 `memory_contract("wiki_page")` 查：
  - 概念页（page_type=concept/entity）写在概念库对象里，见第 6 步；
  - slug 可省略；要发布到外部中枢时写成 `<类型>/<名称>`（小写字母、数字、连字符），类型取 summary/entity/concept/synthesis/comparison/index；
  - aliases 写常见别名（也参与 `[[概念]]` 链接匹配和检索的术语扩展）；
  - source_refs 写固定引用。
- 随第 9 步发布。外部被人改过的页面不会被覆盖。

## 跨对象总结

需要把多个对象的成果汇总成一条 L3/L4 时：

1. 用 `memory_summary_materials(owner_ids)` 取固定材料；
2. 写好后用 `memory_summary_save` 保存，带上材料的 `basis_heads`。
3. 期间任何依据对象有了新提交，都会被拒绝，需要重新取材料。

## 程序已保证

- 内容变化后复核自动失效；formal 用途只输出已复核的结论。
- AI 不能登记"已确立"；重复验证要求至少两份独立依据。
- 概念库里有效概念的名称和别名不能重复。
- 维护计划提交时核对依据版本。
- 发布前 plan_hash 必须一致；外部 Wiki 漂移时不覆盖。
