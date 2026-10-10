# R15 工作台笔记交给 AI：方案预览、用户执行、结果回写

| 项 | 内容 |
|---|---|
| 状态 | 用户已确认方向（2026-10-10，§9）；P1–P2 已实现，细节待用户确认，见 §10 |
| 适用范围 | 工作台笔记（`services/notes.py`，原 `WorkbenchPrefs` 中的随手笔记）、操作（`relations.note_*`、`memory.resume`）、工作台关系图页与首页、规则与 Skill（RULES、burrowl-work、burrowl-consolidate）、MCP 说明 |
| 维护方式 | 每完成一个任务更新 [§10 进度记录](#10-进度记录)；决定有变化时先改 §2，再改代码 |

## 0. 怎样使用本文件

- 决定写作 `D-xx`，任务写作 `T-Pn.m`。代码注释、测试名、提交信息引用编号（例如 `# R15 D-05：执行前核对依据 HEAD`）。
- 本文是计划；已实现的行为以代码、测试、CORE、ARCHITECTURE 为准。

## 1. 背景、目标与非目标

### 背景（2026-10-10 核对代码）

1. 关系图页的"随手笔记"存在数据目录 `.burrowl/workbench/notes.json`（备份包含该目录），定位是"本机偏好"。
2. AI 能通过 `relations_notes` 读到全部笔记，但没有任何 Skill 或说明提到它，AI 不会主动去看。
3. 笔记只有 open / done / dismissed 三种状态："完成"表示用户自己处理完，没有"交给 AI"的状态；AI 也没有回写处理结果的操作，用户看不到 AI 做了什么。
4. 笔记编号是 `WN-{现有条数+1}`，没有删除时不会重复；加上"清除已处理"后会重号。

### 目标

1. 用户可以把一条笔记**交给 AI**；AI 在下次对话中看到它并处理。
2. AI 对记忆的任何写入都**先给用户预览**；用户在工作台点"执行"后，服务端**按预览原样写入**。
3. AI 可以向用户提问，用户在工作台回答；处理结果（写入了哪些记录）回写到笔记上，可以追溯。
4. 处理完的笔记默认收起而不删除；用户可以手动清除。

### 非目标

- 笔记本身不是研究记录：不进检索、复核、知识中枢，也不写进 `memory/`。
- AI 不在后台自动处理笔记（AI 只在用户与它对话时运行）。
- 不做多轮聊天线程；一问一答，可以反复往返。
- 不改变记忆记录契约（Actor 仍只有 kind 与 id）。

## 2. 已定决定

| 编号 | 决定 | 理由 |
|---|---|---|
| D-01 | **笔记是用户交给 AI 的待办**，单独的服务 `WorkbenchNotes`（`services/notes.py`）持有 `.burrowl/workbench/notes.json`；关系图视图偏好仍在 `WorkbenchPrefs` | 状态归属清楚：视图是本机偏好，笔记是用户与 AI 之间的工作状态，两者生命周期不同 |
| D-02 | **状态**：`open` 草稿 → `queued` 待 AI 处理 → `proposed` 待你确认 / `needs_user` 需要你回答 → `handled` 已处理；用户也可以直接 `done`（完成，无需 AI）或 `dismissed`（放弃）。合法转换见 §3 | 每个状态只由一方推进：queued、needs_user、proposed 的出口分别属于 AI、用户、用户，避免互相覆盖 |
| D-03 | **用户专属**：新建、交给 AI、收回、完成、放弃、回答、执行方案、退回方案、重新打开、清除（`effect=user`，不暴露给 agent）。**AI 只能**读笔记和回写（`relations.note_reply`，只能作用于 `queued` 的笔记） | 与"用户专属操作在工作台完成"的规则一致；AI 不能替用户确认或执行 |
| D-04 | **AI 的回写有三种**：`proposal`（写入方案，状态 → proposed）、`question`（提问，→ needs_user）、`declined`（说明不需要或不能写入，→ handled，无写入） | 覆盖"能做、要问、不做"三种结果；都附一句给用户看的说明 |
| D-05 | **方案即预演过的操作清单**：步骤只能是 `memory.commit` 或 `relations.decide`，最多 10 步；同一对象的 `memory.commit` 至多一步（多条记录放进同一次提交），提交步骤排在关联步骤之前。回写时服务端逐步 `dry_run` 预检，记下每个目标对象的当前 HEAD；`memory.commit` 的 `expected_head` 必须等于当前 HEAD。每步分配固定 `request_id` | 预览与执行同源；同一对象多次提交会让第二次的 `expected_head` 失效；关联写入会推进 HEAD，所以放在后面 |
| D-06 | **执行由用户触发、服务端原样写入**：`relations.note_apply` 先核对所有目标对象的 HEAD 与方案记录一致，任何一个变化就不写，笔记退回 `queued` 并注明"依据已变化"；一致则按步骤执行，写入者是起草方案的 AI（Actor 不变），记录的保存原因在回写时追加"依据工作台笔记 WN-n 起草，经用户在工作台批准后写入" | 用户看到的就是写入的；过时的方案不会写进去；不改记录契约也能追溯批准来源 |
| D-07 | **部分失败如实报告**：某一步失败时停止，笔记退回 `queued`，在笔记上列出已写入的步骤和错误；AI 需要对剩余部分重新出方案。不自动回滚已写入的记录 | 跨对象没有原子提交；已写入的记录有正常的复核与撤回流程，不能静默删除 |
| D-08 | **预览由服务端从操作生成**：新建/修订记录列出类型、标题、正文开头、结论原文，修订标明记录 ID 与修订号；关联列出两端（含标题）、关系类型、状态与说明 | 用户确认的是服务端将要写的内容，不依赖 AI 自述 |
| D-09 | **AI 的发现入口**：`relations.notes(status=…)` 按状态筛选；`memory.resume` 返回与该对象相关（笔记引用了该对象或其记录）的未结笔记；MCP 说明与 burrowl-work、burrowl-consolidate 各加一步检查 `queued` 笔记 | AI 只在对话时运行，需要在续接和收口这两个自然节点看到待办 |
| D-10 | **默认不删除**：已处理（handled / done / dismissed）的笔记移到折叠的"已处理"区，可重新打开；"清除已处理"由用户手动执行并二次确认 | 写入的记录在保存原因里引用笔记编号，保留笔记才能追溯；规则要求不静默删除 |
| D-11 | **编号不重复**：文件改为 `{next_seq, notes}`，编号取 `WN-{next_seq}` 后自增；读取旧的列表格式时按已有最大编号续上 | 清除后编号仍唯一，记录里的引用不会指错 |
| D-12 | **读改写在同一把锁内完成** | AI 回写与用户点击可能同时发生，避免互相覆盖 |
| D-13 | **用户可以编辑未结束的笔记**（`relations.note_edit`，类型、标题、内容）：草稿、AI 处理中、需要你回答的直接改；待你确认时改动会作废方案、笔记退回 queued；已处理的先重新打开 | 用户反馈添加后无法修改；方案按旧内容写成，内容变了不能再执行旧方案 |

## 3. 状态与操作

```
            ┌─────────────── 用户：重新打开 ───────────────┐
            ▼                                              │
  open ──用户：交给 AI──▶ queued ──AI：proposal──▶ proposed ──用户：执行──▶ handled
   ▲         │   ▲        │                          │   └─依据变化/某步失败─▶ queued
   │         │   │        ├──AI：question──▶ needs_user ──用户：回答──▶ queued
   │         │   │        └──AI：declined──────────────────────────────────▶ handled
   │         │   └──────────用户：退回方案（附意见）◀── proposed
   └─用户：收回─┘
  open / queued / needs_user / proposed ──用户：完成或放弃──▶ done / dismissed
  handled / done / dismissed ──用户：清除已处理──▶（删除）
```

| 操作 | 谁 | 说明 |
|---|---|---|
| `relations.notes(status?, owner_id?)` | AI、用户 | 读取；`owner_id` 只返回引用了该对象或其记录的笔记 |
| `relations.note_add` | 用户 | 新建草稿（open） |
| `relations.note_resolve(status)` | 用户 | open↔queued（交给 AI / 收回）、→ done / dismissed、已处理 → open |
| `relations.note_answer(text)` | 用户 | needs_user → queued |
| `relations.note_edit(kind?, title?, text?)` | 用户 | 改写未结束笔记的内容；proposed 时方案作废 → queued（D-13） |
| `relations.note_reply(outcome, message, steps?)` | AI | 只作用于 queued；见 D-04、D-05 |
| `relations.note_apply(proposal_id)` | 用户 | 执行方案，见 D-06、D-07 |
| `relations.note_reject(proposal_id, comment)` | 用户 | proposed → queued，意见进入笔记往来 |
| `relations.note_clear()` | 用户 | 删除 handled / done / dismissed |

每条笔记带 `thread`：按时间记录交给 AI、方案、提问、回答、退回、执行、依据变化、失败等事件（谁、何时、说明）。方案保存在 `proposal`，执行结果保存在 `result`（写入的对象、记录 ID、提交 ID）。

## 4. 工作台

- **关系图页的笔记面板**按状态分组：待你确认（显示 AI 说明与逐条预览，"执行"/"退回"）、需要你回答（问题与回答框）、草稿（"交给 AI"/"完成（无需 AI）"/"放弃"）、AI 处理中（"收回"）、已处理（折叠，显示结果和写入的记录链接，"重新打开"；"清除已处理"）。
- **首页侧栏**新增"工作台笔记"状态卡：待你确认、需要你回答、AI 待处理的数量，链到关系图页。
- **导航**："关系图"旁显示待你处理（待你确认 + 需要你回答）的数量。

## 5. 规则与 Skill

- RULES 新增"工作台笔记"一节：什么时候查看、三种回写各自的条件、方案只能包含的写入、不能替用户确认或执行、修订已有记录必须在方案里标明。
- burrowl-work 续接步骤、burrowl-consolidate 收口步骤各加一句：检查 `queued` 笔记并回写。
- MCP 说明加一行入口。

## 6. 实施阶段

| 任务 | 内容 |
|---|---|
| T-P1.1 | `services/notes.py`：存储格式与编号（D-11、D-12）、状态转换（§3）、旧格式兼容 |
| T-P1.2 | 方案：回写校验与预演、预览生成（D-05、D-08） |
| T-P1.3 | 执行：HEAD 核对、按步写入、部分失败、保存原因追加（D-06、D-07） |
| T-P1.4 | 操作与 `memory.resume` 的未结笔记（D-09）；从 `WorkbenchPrefs` 移除笔记；生成操作参考与前端类型 |
| T-P2.1 | 工作台：笔记面板、首页状态卡、导航计数 |
| T-P2.2 | RULES、burrowl-work、burrowl-consolidate、MCP 说明 |
| T-P2.3 | 文档：CORE、ARCHITECTURE、README、GLOSSARY、DEVELOPMENT_HISTORY、docs/README、AGENTS |

### 验收

- 服务测试覆盖：每个合法与非法转换；AI 不能回写非 queued 笔记；方案步骤类型、数量、同对象多次提交、`expected_head` 不符都被拒绝；执行成功写入的内容与预览一致且保存原因带笔记编号；HEAD 变化时不写入并退回；中途失败时报告已写入步骤；清除后编号不复用；旧列表格式可读。
- 接口测试：用户专属操作不出现在 MCP 工具列表。
- 前端组件测试：交给 AI、执行方案、退回、回答、清除。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| 方案预演通过、执行时因其他原因失败 | 如实报告已写入步骤，笔记退回 AI（D-07） |
| 用户在预览后很久才执行，期间对象被修改 | 执行前核对 HEAD，不一致就不写（D-06） |
| AI 把未确认的想法写成既成事实 | 方案只能写导航关联或"未复核"的结论；复核仍由用户在知识复核页完成 |

## 9. 用户的决定（2026-10-10）

1. 草稿上两个按钮："交给 AI"与"完成（无需 AI）"。
2. AI 写入前必须预览，由用户在工作台执行。
3. 处理完的笔记默认收起不删除，另设手动"清除已处理"（本文建议，用户同意先实现）。

## 10. 进度记录

| 任务 | 状态 | 说明 |
|---|---|---|
| 设计稿 | 已写（2026-10-10） | 用户要求先实现，细节之后确认 |
| T-P1.1–T-P1.4 | 已完成（2026-10-10） | `services/notes.py`（`WorkbenchNotes`）；操作 `relations.notes(status, owner_id)`、`note_add/resolve/answer/apply/reject/clear`（effect=user）与 `note_reply`（effect=write）；`memory.resume` 返回 `workbench_notes`；`WorkbenchPrefs` 只留视图。测试 `tests/service/test_workbench_notes.py`（7 项），MCP 工具清单断言 |
| T-P2.1 | 已完成 | `frontend/src/features/Notes.tsx`：按"等你处理 / 草稿 / AI 处理中 / 已处理"分组，方案卡逐条显示服务端预览；首页"工作台笔记"状态卡；导航"关系图"显示待你处理数。组件测试 `Notes.test.tsx`（4 项）；端到端用真实后端走完交给 AI → 方案（确认未写入）→ 执行 → 已写入 |
| T-P2.2 | 已完成 | RULES 第 8 节"工作台笔记"；burrowl-work 第 2 步、burrowl-consolidate 第 5 步；MCP 说明 |
| T-P2.3 | 已完成 | CORE §5、ARCHITECTURE（模块表 NOTE、分层、状态表）、README、GLOSSARY、TESTING、DEVELOPMENT_HISTORY、docs/README、AGENTS |
| D-13 编辑（2026-10-11） | 已完成 | 用户反馈添加后无法编辑。`relations.note_edit`；面板上未结束的笔记有"编辑"，待你确认时保存前提示方案作废。服务测试 1 项、组件测试 1 项 |
| 待确认 | — | 本文 §2 的各项决定与界面细节；尚未用真实 AI 对话走一遍（只用接口模拟 AI 回写） |
