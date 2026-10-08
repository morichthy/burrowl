# 记录标准：怎样写 L0–L4、文稿和结论

写任何记忆记录前读本页；具体字段用 `memory_contract(kind)` 查（会返回经过校验的示例草案）。术语定义见 [GLOSSARY](../docs/GLOSSARY.md)。

## 1. 层级是什么

L0–L4 是**内容压缩与展开的层级**，同时决定检索时的"发现面"：

- L4 和 L3 的正文、L2 的摘要、L1 的检索说明，会进入全库发现索引。
- L1 的完整正文、文稿和 L0 原件，要在完整阅读时才读到。

所以 **L4 写不好，别人就找不到这个对象**。

层级只表示内容职责，不表示可信程度，也不是必须逐级完成的流水线：

- 缺层是正常的。小操作可以不写记录；没有独立经验就不写 L3。
- **预期会被其他任务检索、续接或引用的对象，至少维护一条当前的 L4。**
- 内容很短时可以只有一条 L4，它的正文就是完整内容。

| 层 | 类型（kind） | 正文写在哪 | 关键结构字段 |
|---|---|---|---|
| L0 | source | `body_markdown`：登记说明 | `source_ref`（先 `sources_register`）、`acquisition`、`completeness` |
| L1 | detail | **`payload.blocks`**；`body_markdown` 必须是空字符串 | `unit_type`、`run_ref`、`retrieval_description`、`blocks` |
| L2 | narrative | `body_markdown` | `question`、`stages[]`、`limitations`、`claims` |
| L3 | experience | `body_markdown` | `problem_structure`、`recommendation`、`applicable`、`prohibited`、`failure_modes`、`knowledge_type`、`claims` |
| L4 | overview | `body_markdown` | `question`、`methods`、`results`、`current_stage`、`limitations`、`open_questions` |
| — | document / document_section | 章节的 `blocks`（prose 文字或 unit 技术块） | 文稿根：`document_type`、`section_refs` |

**L2–L4 的正文必须写在 `body_markdown`**：读者和检索都读正文，不会把 payload 拼回正文。不能把背景、结果、限制只放在 payload 里。

## 2. 每条记录必须独立可读

读者只拿到**这一条的正文**（L1 包括它声明依赖的定义块），不读聊天、相邻记录或整篇文稿，也要能回答这几个问题：

- 对象是什么，为什么记这一条；
- 在什么条件下做了什么，得到了什么；
- 什么时候不能用。

规则：

- 关键术语第一次出现时解释；公式给出变量、单位、初值和成立条件；比较要写明对象、输入和评价基准。
- 不写"同上""见上一轮"，也不用记录 ID 或附件链接代替解释。
- 未知的背景或依据明确写成缺口。未完成的内容可以保存，但不能称为可直接复用的完整经验。

| 层 | 本条至少要让读者理解 |
|---|---|
| L0 | 材料是什么、谁或哪次执行产生、针对什么问题、版本和输入条件、怎么解释（列、坐标、单位、限制）。原件只读；没展开读过的原件不能声称核验了全文 |
| L1 | 问题与范围、输入与方法、必要定义和公式、主要结果、限制、复算依据。结果块必须用 `requires_block_ids` 带上它依赖的定义块 |
| L2 | 背景和起点 → 本条记录的实际尝试、选择理由、观察或失败、认识变化 → 简单结论和未解决的问题。只发生了一步就如实写一步，不编造多轮演进；写明是原文记载、本次工作还是事后补写 |
| L3 | 在什么问题和条件下，有什么观察、结论、假设或建议；怎样使用、为什么有依据、关键反例、不能推广到哪里。假设要保留验证条件，建议不冒充实测 |
| L4 | 对象的用途和问题边界、主要方法或内容、当前结果或状态、限制和未决事项。不展开下层也能知道"它是什么、现在能用来做什么" |

**写完后在同一次任务中逐条做脱离上下文的回读**：只看这一条，检查上面的问题能否回答，缺了先补再提交。这个自查不产生任何"已确认"状态，结构校验通过也不等于内容充分。

## 3. 不同对象怎样用同一套层级

| 对象 | 可以按 L0 → L1 → L2 → L3 → L4 组织的内容（按需选取） |
|---|---|
| research / project | 研究或交付依据 → 方法、分析、实验 → 实际探索与取舍 → 有条件的认识 → 问题和成果概览 |
| core-algorithm | 公司定义文档与实现版本 → 方程、接口、推导、验证 → 文档记载或本次的演进与排错 → 适用域和工程经验 → 算法用途与边界 |
| knowledge | 原文及出处 → 原文的方法或推导 → 原文明确写出的形成过程（**原文没写就不编 L2**） → 区分原文主张与本次认识 → 主题、用途和边界 |
| tool / data | 版本化的工具或数据登记 → 使用方法、处理与分析 → 排错、选择或治理经过 → 复用条件和失败经验 → 用途、接口或数据范围 |
| report / run | 报告依据或实际输入输出 → 分析方法 → 有意义的取舍 → 限定范围的认识 → 目标、结果和限制。Run 日志不能代替 L2，执行成功不代表结论成立 |

- 一个事实只有一个权威归属，其他对象用固定引用，不复制。
- 形成记录时，除了主体目标，还要检查过程中出现的可迁移方法、失败与反例、版本/参数/工况限制、工具或数据特征、偶然发现：能独立复用的写 L3，方法和计算细节写 L1。
- 资料原文的主张、本次观察、AI 推断分开写明。

## 4. L1 技术单元怎么写

- `unit_type`：experiment / method / derivation / analysis。**experiment 必须用 `run_ref` 绑定真实 Run**；其他类型 `run_ref` 为 null，但仍要给出固定依据或写明缺口。不要为了凑字段虚构 Run。
- `retrieval_description`：question、method、key_findings[]、applicable[]、not_applicable[]、limitations[]。它只用于发现候选，**不是正文**。
- `blocks`：每块有稳定的 `block_id`、`role`（definitions / methods / inputs / derivation / results / discussion / limitations / appendix / introduction）、`markdown` 和 `requires_block_ids`。

正文按读者能独立复核的顺序组织，短小节可以合并，但不能只留题目、总评和附件链接：

1. **问题与范围**：检验什么，为什么要做，比较对象和可证伪条件。
2. **输入与方法**：关键参数、单位、取值理由、样本量、随机种子、假设、程序与环境。完整数组放 L0，影响结论的参数写在正文。
3. **公式与计算**：公式、变量定义、可核验的推导步骤；写可公开复核的解释，不写模型的私有思考过程。
4. **结果**：结果表、图与图注（坐标、单位、误差定义）。图引用固定附件。
5. **讨论与限制**：观察、反例、失败类别、适用范围、不能推出的结论、下一步问题。
6. **证据与复算**：Run ID、来源 ID、版本和指纹、实际脚本和命令。没执行的计算标为推导或建议。

示例：浮点误差要区分"精确实数输入"和"已表示为 binary64 的输入"。
$S_{\mathrm{ref}}=\sum_i \operatorname{Fraction}(x_i)$，$e_{\mathrm{abs}}=|\operatorname{Fraction}(\widehat S)-S_{\mathrm{ref}}|$。
其中 $x_i$ 是程序实际收到的浮点数，无量纲。其他研究要写自己的量纲和基准，不能照搬。

## 5. 结论（claim）怎么写

需要被复核、被正式引用的判断写进 L2–L4 的 `payload.claims`，每条包含：

- `claim_id`：全库唯一，建议 `CLM-<对象>-<短名>`。
- `statement`：一句可判断真假的话。
- `kind`：fact / calculation / inference / recommendation。
- `scope`：适用范围。
- `evidence_refs`：支持它的固定引用（relation 用 supports 或 input）。

- **scope 要具体**。"有限 binary64 输入、存在大数抵消"是好的；"一般情况下""大多数场景"不是范围。复核时确认的范围必须与 scope 一致。
- 改了结论或所在记录的正文，原复核会自动失效，需要重新复核。

## 6. 引用与三种关系

所有正文在事实、公式、结果和关键判断旁**就近引用**固定依据，并与 `sources` 对应。历史记录没有具体位置时，如实写缺口，不按来源顺序猜。

三种关系分开维护，不能互相冒充：

| 关系 | 用什么表达 | 含义 |
|---|---|---|
| 展开 / 组成 | `process_refs`（指向 L2）、`technical_refs`（L1）、`experience_refs`（L3）；章节的 `unit` 块；文稿的 `section_refs` | 怎样从概括展开到细节 |
| 证据 | 引用的 `relation` 为 supports、input、contradicts；claim 的 `evidence_refs` | 判断依据什么成立或被反驳 |
| 导航 | association 记录（same_problem、same_failure_mode、analogous_to…），用 `relations_decide` 保存 | 相似、对照、可能迁移；**不是证据** |

相似度、同一对象、阅读线索都不能自动变成 supports 或复核。

**固定引用怎样拿**：

- 刚保存的记录：直接用提交回执里的 `fixed_ref`。
- 已有记录：用 `memory_inspect` 取 revision 和 record_hash。
- 文件：用 `sources_list` 里登记的 sha256。

不要手抄或猜指纹。

## 7. 保存顺序与回读

1. **先保存被引用的内容**（L0 → L1 → L2 → L3），再保存引用它们的概览、章节，最后保存文稿根。同一批提交里，可以用 `{client_key: "x"}` 引用同批新建的记录。
2. 用 `memory_commit`（可先 `dry_run`）提交，带上 `expected_head` 和 `request_id`：
   - 新建：`{op: "put_record", client_key, draft}`；
   - 修订：`{op: "put_record", record_id, expected_revision, draft}`。
3. 看回执：`save_status` 和 `index_status` 是两件事。索引 pending 说明内容已保存，**不要换新 request_id 重复提交**，等它补齐或调用 `index_reconcile`。
4. 用 `memory_inspect` 回读本次修订，按第 2 节逐条检查。

## 8. 文稿

- 文稿根（document）按 `section_refs` 的顺序编排章节（document_section）。章节由作者写的衔接文字（prose，带依据）和整块收录的技术单元（unit）组成。
- research_process 是完整过程稿，research_report 是精简报告。按实际交付需要编写，可以只有一种；交付两种时依据必须一致。
- 文稿不会因为依据有新修订而自动改变。依据变化后用 `memory_document(view="impact")` 查看影响，再按 consolidate 流程处理。
