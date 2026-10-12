# R20 AI 填写的字段与报错：服务能推导的不让 AI 写，要写的在填参数处讲清，错了一次说全

| 项 | 内容 |
|---|---|
| 状态 | 用户确认（2026-10-12：D-01–D-07 含 `sensitivity` 默认、嵌套参数拒收未知字段、错误日志三点；D-08–D-10 查阅外部做法后补充并确认），已实施；实施中的调整见 §7 |
| 适用范围 | 操作参数（`interfaces/ops/builtin/*.py`、`registry.py`）、MCP 适配（`interfaces/mcp.py`）、记录契约（`core/contract.py`、`core/records.py`、`core/schema/`）、提交与总结保存（`services/memory/service.py`、`services/workflow.py`）、错误结构（`core/errors.py`）、RULES / RECORDING / 三个工作流 Skill |
| 关系 | 不改记录模型与工作流顺序；只改"AI 怎么知道要填什么、填错了怎么知道错在哪" |

## 1. 背景（2026-10-12 核对代码与实测）

起因：另一个 agent 调 `memory_commit` 得到 `INVALID_SCHEMA "单批只允许一个归属对象"`，无从修正。

1. **复现**：draft 省略 `owner_id`、或写成别的对象，都得到同一条报错，`errors` 只有 `{path: "/owner_id", message: "归属不匹配"}`，path 指向请求顶层而不是 `/operations/N/draft/owner_id`（`service.py:147`）。`draft.owner_id` 是请求 `owner_id` 的重复，只在 `memory_contract` 的返回里说明；契约示例里写的是另一个对象 `RES-FLOATING-POINT-SUMMATION`，整段照抄同样触发。`memory_summary_save` 同理，且报错说成"只能保存为 experience 或 overview"（`workflow.py:90`）。
2. **服务端没有吞错**：业务错误以 `{code, message, details, errors, retryable}` 整体返回（`mcp.py:118`）。问题在内容本身。
3. **"先查契约"只是建议**：MCP 说明、RECORDING、burrowl-work、consolidate、`memory_commit` 参数说明共 5 处提到，但没有任何机制保证；而 RECORDING 第 7 节、consolidate 的 revise/resynthesize、RULES 的工作台方案 `steps`、`memory_summary_save` 都是不经过"先查契约"那一步就写草案的路径。工具说明里写的是 `memory.contract`，MCP 工具名是 `memory_contract`。
4. **工具说明就是 MCP 的工具描述**：`registry.op` 把函数 docstring 作为工具 description，`Annotated[..., Field(description)]` 进入参数 schema（`mcp.py:126,138`）。AI 组织参数时眼前就是这两样；Skill 在更远处。
5. **契约与服务行为不一致**：
   - `change_reason` 在契约里列为"系统自动维护、不要填写"（`contract.py:20`），但修订总结、重新激活、状态转换时服务要求它（`workflow.py:92`、`rules.py:156,172`），RECORDING §8 也要求写；服务端并不拒收它（不在 `SERVER_FIELDS`）。
   - `discovery` 标 `required: true`，说明却是"一般不写"，服务也会补。
   - `sensitivity` 说明写"internal（默认）"，实际缺了就报必填。
   - payload 子字段大多不标必填：实测 detail 缺 `run_ref`、`evidence_refs`、`figures` 都报必填，契约里看不出。
   - "新建只能用 client_key，修订必须 record_id + expected_revision ≥ 1"只在 `records.py:329`。
6. **草案报错可读性差**：缺字段时一次列全（好），但 path 相对草案（`/title` 而不是 `/operations/0/draft/title`），消息是英文 `Required field is missing`，不说明字段含义、也不指向 `memory_contract`。
7. **结构化参数大多是裸 `dict`**：Skill 提到的 72 个工具里，15 个参数由 AI 直接编写结构化内容，但 schema 只是 `object`/`array of object`，字段只写在一句说明里（见 §3 表）。缺字段时有的一次报一个（`relations_decide`），有的把提示放在 `details.hint`、有的放在 `errors`，格式不一。还有约 60 个标量参数没有 description。
8. **参数校验有两道，第一道绕过了错误格式**：MCP 框架先按工具签名校验参数，失败时直接返回 pydantic 原文（实测 `operations=[1]` 得到英文 `Input should be a valid dictionary … https://errors.pydantic.dev/…`，没有 code/path/hint），根本到不了 `registry.call` 的统一转换。D-03 引入嵌套类型后，走这条路的错误会更多。
9. **运行时异常**：非业务异常交给 MCP 框架，变成 `Error executing tool X: <异常文本>`，没有错误码、不说明是不是参数问题；服务没有任何日志文件，事后查不到堆栈。发布后的索引失败只留异常类型名（`service.py:385`）。成功结果里已有 `warnings: [{code, message, path?}]` 的惯例（提交、关系词表、新建对象），但并未统一。

## 2. 决定

| 编号 | 决定 | 理由 |
|---|---|---|
| D-01 | **服务能推导的字段不让 AI 写**：`draft.owner_id` 可省略，由提交的 `owner_id` 补上（`memory_commit`、`memory_summary_save`，以及经由它们的工作台方案、维护处置）；写了且不一致才报错，报错写明第几条草案、写的是哪个对象、本次提交的是哪个对象，path 为 `/operations/N/draft/owner_id`。`discovery` 维持现状（可省略）。`sensitivity` 改为可省略、默认 `internal`（与说明一致）。示例草案去掉 `owner_id` | 重复信息只会制造错误；省略比"写对"更不容易错 |
| D-02 | **契约如实标注**：每个草案字段与 payload 子字段标为四类之一——`required`（必须写）、`optional`（可不写，写明默认值或不写的含义）、`conditional`（写明在什么情况下必须写，如 `change_reason`：修订、重新激活、状态转换时必填，新建时不写）、`server`（不要写）。分类由 schema 的 `required` 加服务默认值表**生成**，不手写；`write_with` 写全新建/修订两种操作的键与 `expected_revision` 规则 | 手写的必填说明已经和行为脱节；生成可以由测试锁住 |
| D-03 | **结构固定的参数用类型声明**：§3 表里除记录草案、配置补丁外的结构化参数改成 pydantic 模型（字段带 description、必填/可选、枚举），MCP 参数 schema 直接展示字段；`operations` 声明操作层（`op`、`client_key`/`record_id`、`expected_revision`、`repin`、`draft`），`draft` 仍是对象（结构随 kind 变，由契约描述）。嵌套模型拒绝未知字段，报"未知字段，可能拼错；允许的字段：…" | 字段说明出现在 AI 填参数的地方；缺字段、拼错由参数校验一次列全，不必逐个手写检查 |
| D-04 | **说明放在哪（回答 C）**：字段写法写在工具自己的 docstring 与参数 description 里（AI 填参数时就在眼前）；Skill 只写何时用、为什么、顺序与判断，不重复字段。`memory_commit` 的说明给最小清单：草案必写字段、可省略字段、修订必写 `change_reason`、payload 用 `memory_contract(kind)` 查。说明里提到其他工具一律用 MCP 名（下划线） | 两处各管一件事，不会互相过期 |
| D-05 | **字段错误一次说全、说清**：所有字段错误都放进 `errors`，每项 `{code, path, message, expected?, hint?}`：`path` 为从工具参数根开始的完整 JSON Pointer；`message` 中文，说明缺了/错了什么及该字段的含义（取自契约说明）；`expected` 给允许的值或结构。草案校验失败时 `details.hint` 指向 `memory_contract("<kind>")`。参数校验（pydantic）的英文消息统一翻译。原先只把问题写在 `details` 的字段错误（如 `relations_decide`）改为进 `errors`，仍保留 `details.hint` | AI 第一次失败就能改对，不再逐个试错 |
| D-06 | **运行时异常分两类返回**：<br>① **操作失败的意外异常**（缺陷、环境问题）：在 `registry.call` 边界（MCP/HTTP/CLI 共用）转成 `INTERNAL_ERROR`（500，不可重试），`message` 说明"服务内部错误，不是参数问题，不要改参数重试，请告诉用户"，`details` 给 `{op, exception: 类型, summary: 异常文本前 300 字, incident_id}`；完整堆栈写入数据目录 `.burrowl/logs/errors.jsonl`（按大小轮转），用户或开发者凭 `incident_id` 查。**堆栈不返回给 AI**。<br>② **操作成功、但有部分步骤失败或降级**（索引、向量、候选、抽取）：统一放进已有的 `warnings` 字段，每项 `{code, message, path?, action?}`，`action` 写 AI 能做什么（如"运行 index_reconcile""告诉用户检查嵌入模型配置""不要重复提交"）；异常只给类型和一句摘要；最多 10 条，多出的计入 `warnings_truncated` | AI 能据此决定下一步的信息才给；排查用的细节进日志，不占对话 |
| D-08 | **参数只在一处校验**：MCP 适配层不再按签名预校验（工具声明的 inputSchema 仍取自操作模型，AI 看到的结构不变），原始参数交给 `registry.call` 校验，所有输入错误都以 `isError` 工具结果和 D-05 的结构返回 | MCP 2025-11-25 规范把输入校验错误归为工具执行错误，"客户端应交给模型以便自我纠正"；两道校验格式不一，第一道还给不出 path |
| D-09 | **复杂参数附示例**：`memory_commit`、`relations_decide`、`reading_assess`、`reading_note`、`maintenance_review`、`relations_note_reply` 等复杂参数在 schema 的 `examples` 里给 1–3 个真实取值的示例（最小、典型、完整各一），测试校验示例本身合法；简单参数不加 | MCP 没有 `input_examples` 字段，JSON Schema 的 `examples` 是协议内的等价物；Anthropic 实测示例把复杂参数的正确率从 72% 提到 90%，schema 表达不了"何时填可选字段、ID 格式、字段之间的关系" |
| D-10 | **记录工具错误，用数据改说明**：D-06 的日志同时记下每次被拒绝的调用（操作名、错误码、出错的 path，不记参数内容），`system_validate` 或单独的统计入口按操作汇总近期的参数错误次数 | Anthropic 的做法：参数错误多说明说明或示例要改；没有数据就只能靠走查偶然发现，像这次一样 |
| D-07 | **一致性测试锁住**：契约分类与 schema、`SERVER_FIELDS`、服务默认值一致；契约标 `required` 的字段省略后提交确实失败，标 `optional`/`server` 的省略后成功；示例草案无 `owner_id`；agent 可用工具的每个参数都有 description；除白名单（记录草案、配置补丁）外不再有裸 `dict` 参数；工具说明不出现点号形式的工具名 | 以后改代码时说明不会悄悄脱节 |

## 3. AI 直接编写的结构化参数（Skill 提到的工具）

| 工具.参数 | 现状 | 处置 |
|---|---|---|
| `memory_commit.operations` | 裸 `list[dict]`；草案字段只能查契约；创建/修订规则只在代码里 | D-03 操作层模型；D-01 草案 `owner_id` 可省；D-04 最小清单；D-05 草案报错带完整 path 与契约说明 |
| `memory_commit.attribution` | 裸 `dict`，结构写在说明里 | D-03 模型 `{action, decided_by, reason, rejected?[{owner_id, reason}]}` |
| `memory_summary_save.draft` | 裸 `dict`，**没有任何说明** | 补说明（experience/overview 草案，字段查 `memory_contract`）；D-01；修订时 `change_reason` 的要求写进说明 |
| `memory_summary_save.basis_heads` | `dict[str, str\|null]`，**没有说明** | 补说明：`memory_summary_materials` 返回的各依据对象 HEAD，原样传回 |
| `maintenance_review.decisions` | 裸 `list[dict]` | D-03 模型 `{target_id, action, reason, draft?}`；`revise`/`resynthesize` 时 `draft` 必填由校验报出 |
| `relations_decide.payload` | 裸 `dict`；一次只报一个问题 | D-03 模型（`from`、`to`、`basis_refs` 为 Ref 模型，快速添加的三字段写明）；D-05 |
| `relations_note_reply.steps` | 裸 `list[dict]` | D-03 按 `op` 区分的两种步骤模型，内层复用 `memory_commit` 与 `relations_decide` 的模型 |
| `reading_assess.judgments` | 裸 `list[dict]` | D-03 模型 `{owner_id, verdict, reason, digest, accepted_windows?}` |
| `reading_note.expand_from` | 裸 `list[dict]` | D-03 模型 `{ref, why}` |
| `reading_start/recall.variants`、`memory_owner_route.variants` | 裸 `list[dict]` | D-03 共用模型 `{language, question, lexical_terms?}` |
| `reading_start.field_conditions` | 裸 `list[dict]` | D-03 模型 `{id, kind, field, operator, expected, unit?}` |
| `review_submit.evidence_refs` | 裸 `list[dict]`，**没有说明** | D-03 共用 Ref 模型；补说明（从哪里取 revision/sha256） |
| `runs_update.fields` | 裸 `dict` | D-03 模型（列出可改字段，全部可选） |
| `sources_register_many.items` | 裸 `list[dict]` | D-03 模型 `{source_id, path, title?, sensitivity?}` |
| `reading_synthesize.note/sources` | 没有说明 | 补说明（与 `reading_note` 一致） |
| `config_update.patch` | JSON Merge Patch | 保持对象（白名单）；已有说明，报错走配置校验 |

另：约 60 个标量参数没有 description（如 `index_status.owner_id`、`review_submit.state`、`runs_execute.script`），逐个补一句；名称已自明的（`dry_run`、`limit`、`offset`）可不补，由 D-07 的白名单列出。

Ref 模型在 `memory_commit` 之外复用于 `relations_decide`、`review_submit`；草案内的 Ref 仍由记录 schema 校验，报错同样走 D-05。

## 3.1 外部参考（2026-10-12 查阅）

| 来源 | 要点 | 本稿对应 |
|---|---|---|
| [MCP 规范 2025-11-25 · Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) | 输入校验错误是工具执行错误（`isError: true`），应带可执行的反馈让模型自我纠正；协议错误只留给请求结构本身的问题。服务端必须校验所有输入。无参工具推荐 `additionalProperties: false` | D-05、D-08 |
| [Anthropic · Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | 参数名无歧义（`user_id` 而不是 `user`）；用严格的数据模型约束输入；错误信息给"具体、可执行的改法"并附正确格式示例，不给不透明的错误码或堆栈；截断时告诉 agent 怎样拿到更精确的结果；用真实任务评估工具，统计调用次数、token、工具错误，参数错误多就改说明或示例 | D-03、D-05、D-06、D-10 |
| [Claude 文档 · Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools) | 描述是工具表现"最重要的因素"：做什么、何时用与不用、每个参数的含义、限制，每个工具至少 3–4 句；嵌套对象、格式敏感的参数用经 schema 校验的示例 | D-04、D-09 |
| [Anthropic · Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) | JSON Schema 表达不了用法约定；示例让复杂参数正确率 72% → 90%；示例用真实值，最小/部分/完整混合，每个工具 1–5 个 | D-09 |
| [OpenAI · Function calling](https://developers.openai.com/api/docs/guides/function-calling) | 用枚举和对象结构让无效状态无法表达；"实习生测试"：只看说明能否正确使用；应用已知的值不要让模型填（如上文已有的 `order_id`）；推荐 strict 模式（所有对象 `additionalProperties: false`） | D-01、D-03 |

说明：strict 模式（约束解码，保证参数符合 schema）由调用模型的客户端开启，MCP 服务端控制不了；服务端能做的是让 schema 本身严格（D-03 的嵌套模型拒绝未知字段、用枚举），使开启 strict 的客户端直接受益，并且始终自己校验（D-08）。

## 4. 不做的

- 不把整份记录 schema 塞进 `memory_commit` 的参数 schema：随 kind 变化，体积大，仍由 `memory_contract(kind)` 按需查询。
- 不强制"先调 `memory_contract` 才能提交"：靠 D-04 的就近说明与 D-05 的报错提示纠正，不在服务里追踪调用顺序。
- 不把堆栈、内部路径返回给 AI；不新增日志查询工具（先由用户/开发者读日志文件，需要时再议）。
- 不改记录模型、不改提交的原子性与归属规则（单批仍只写一个对象）。

## 5. 任务

| 任务 | 内容 |
|---|---|
| T-1 | D-01：`service.prepare` 与 `workflow.save_summary` 补 `owner_id`、精确报错；`sensitivity` 默认值；示例草案去 `owner_id` 并重新生成 |
| T-2 | D-02：`contract.py` 按 schema + 服务默认值表生成四类标注；`change_reason` 条件说明；`write_with` 写全规则 |
| T-3 | D-03 / D-04：§3 各参数的模型（放在 `interfaces/ops/` 下共用模块）；`memory_commit` 等说明改写；补标量参数说明；说明中的工具名统一为下划线形式 |
| T-4 | D-05：`registry.call` 的参数错误翻译与完整 path；`validate_commit_request` 与记录校验的 path 前缀、中文消息、契约说明、`memory_contract` 提示；`relations_decide` 等改为 `errors` 列表 |
| T-4b | D-08：MCP 适配层改为接收原始参数、只在 `registry.call` 校验；确认框架不再产生 pydantic 原文错误。D-09：复杂参数的 schema `examples` 与校验测试 |
| T-5 | D-06 / D-10：`INTERNAL_ERROR` 错误码与 `registry.call` 边界；`.burrowl/logs/errors.jsonl`（轮转）；`warnings` 统一格式、`action`、条数上限；索引/向量等降级边界补摘要 |
| T-6 | D-07 测试；前端调用适配（`npm --prefix frontend run ops` 后类型检查）；`gen_reference.py`、`gen_draft_examples.py` |
| T-7 | 规则与文档：RECORDING §7（草案最小清单与修订的 `change_reason`）、burrowl-work / consolidate 中草案相关句、RULES 工作台方案、MCP 服务说明；CORE、ARCHITECTURE（错误结构、日志文件的状态归属）、TESTING、GLOSSARY（如新增术语）、DEVELOPMENT_HISTORY、docs/README |

## 6. 验收

- 用 dry_run 复现：草案省略 `owner_id` 成功；写错对象时报错指向 `/operations/0/draft/owner_id` 并说明两个对象。
- 缺多个字段的草案：一次列全，中文说明，每项完整 path，带 `memory_contract` 提示。
- §3 每个参数：缺字段、拼错字段、枚举值错误各一例，报错一次列全且 path 正确。
- 制造一个意外异常：返回 `INTERNAL_ERROR` 与 `incident_id`，日志中可查到同一 id 的堆栈；成功但索引失败的提交返回带 `action` 的 `warnings`。
- 契约一致性测试、全量 pytest、ruff、lint-imports、`burrowl skills check`、前端 build/test/e2e 通过。
- 实际 AI 场景：在测试数据目录副本上，由一个未加载 Skill 的 agent 只凭工具说明完成一次 L1 提交与一次修订，记录试错次数。

## 7. 进度记录

| 任务 | 状态 | 说明 |
|---|---|---|
| 设计稿 | 已确认（2026-10-12） | |
| T-1 | 已完成 | `MemoryService._check_request` 补 `owner_id`、不一致时逐条报出；`prepare` 中 `sensitivity` 新记录取 internal、修订沿用原值（与 discovery 同理，避免把 confidential 悄悄降级）；`workflow.save_summary` 同样处理并一次列出 kind、owner_id、change_reason、record_id/expected_revision 的问题；示例草案去掉 `owner_id` 与 `sensitivity` 后重新生成 |
| T-2 | 已完成 | `core/contract.py`：`DEFAULTED`（服务补的字段）、`CONDITIONAL`（change_reason）、`SYSTEM_FIELDS = SERVER_FIELDS ∪ {level, schema_version}`；输出 `fill` / `default` / `do_not_write` / `payload_legend`；`payload.missing_refs` 标可选；`write_with` 写全新建与修订的键 |
| T-3 | 已完成 | `interfaces/ops/shapes.py`：15 个结构化参数的形状（Operation、Attribution、Ref、AssociationPayload 取自记录契约；草案只发布为"对象 + 说明"）；Skill 提到的工具除 limit/offset 外每个参数都有说明；`memory_commit`、`memory_summary_save` 的工具说明给最小清单；说明里的工具名统一为下划线形式 |
| T-4 | 已完成 | 校验器（`core/jsonschema.py`）消息改中文并带 `expected`（枚举、未知字段的允许字段）；records / technical / facets 的领域校验消息改中文；草案错误、规则层错误的路径前缀为 `/operations/N/draft`，总结保存换成 `/draft`、关联换成 `/payload`；缺字段时附契约说明，`details.hint` 指向 `memory_contract(kind)` |
| T-4b | 已完成 | `registry.check_arguments`：pydantic 错误译成中文，结构化参数按形状校验，所有错误一次抛出；MCP 适配层把 SDK 的 `fn_metadata` 换成不校验的版本，自己解析 JSON 字符串参数（只对接受对象或列表的参数）。示例：`memory_commit`（新建与修订各一）、`reading_start.field_conditions`、`reading_assess`、`relations_decide`、`memory_owner_route/reading_*.variants` |
| T-5 | 已完成 | `interfaces/ops/errorlog.py`：`INTERNAL_ERROR`（500、不可重试）带异常类型、摘要与 `incident_id`，堆栈写 `.burrowl/logs/errors.jsonl`（2 MB 轮转一份，备份包排除）；被拒绝的调用记工具名、渠道、错误码、路径；`system.tool_errors` 汇总。写操作结果的 `warnings` 最多 10 条（多出的记 `warnings_truncated`，诊断类读操作如 `system_validate` 完整列出）；INDEX_PENDING 与关系名提示带 `action`；提交后索引失败附一句异常摘要 |
| T-6 | 已完成 | `tests/service/test_ai_fields.py`（13 类记录逐字段核对契约标注等，共 21 项）、`tests/interfaces/test_tool_errors.py`（10 项）；`test_links.py` 一处改为按路径断言；`test_draft_examples.py` 改用固定的样例对象。`gen_reference.py`、`npm run ops` 已重新生成 |
| T-7 | 已完成 | RULES（工具报错怎么处理）、RECORDING §7（草案必写与可省略字段、修订写 change_reason）、burrowl-work 第 5 步；CORE §2、ARCHITECTURE（模块表 CORE/APP、状态表"错误日志"、已知差距）、TESTING、GLOSSARY（工具名）、DEVELOPMENT_HISTORY（R20、E11）、docs/README、AGENTS、README（`.burrowl/` 内容）。`burrowl skills check` 不再把记录字段名（如 `provenance_gap`，恰好以工具组名开头）当成工具 |
| 未做 | 记录 | 实际 AI 场景验收（未加载 Skill 的 agent 只凭工具说明完成一次新建与修订、统计试错次数）按用户决定（2026-10-12）暂不进行，之后需用新代码重启 MCP 服务再做；索引同步内部的其他降级边界（向量、关联候选）仍只在水位里记失败，未进 warnings |

