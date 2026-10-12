# R22 删除记录与对象：回收站与墓碑提交

| 项 | 内容 |
|---|---|
| 状态 | 已确认并实施（2026-10-13，§6 为用户的决定）；见 §8 |
| 适用范围 | 规范存储（STORE：`storage/memory_store.py`）、记忆服务（MEM：新增 `services/memory/removal.py`）、检索投影（IDX：`IndexSink.on_owner_removed`）、关键操作记录（ACT）、接口与工作台（APP）、规则（GUIDE） |
| 维护方式 | 每完成一个任务更新 §8；决定有变化时先改 §2，再改代码 |

## 1. 背景

2026-10-13 核对：记录只能撤回（retracted）或被替代（superseded），对象不能删除；只有 Run 能丢弃到回收站（R14 D-12）。误建的对象、测试记录、重复或放错对象的记录只能撤回，之后一直留在列表、检索和文稿里。

撤回和删除是两件事：

- **撤回**：结论曾被当真，后来发现错了。它是有价值的历史（失败教训、反证），要继续可见。
- **删除**：本来就不该记，例如测试数据、重复记录、写错对象。它不应再出现在任何地方。

## 2. 决定

| 编号 | 决定 | 理由 |
|---|---|---|
| D-01 | **删除记录 = 墓碑提交**：新提交从提交清单的 `record_heads` 移除该记录，并在新的可选字段 `removed_records` 中记下原指针、理由、时间、执行者。旧修订仍在旧提交里 | 提交链与指纹不变，"旧数据永远可读"；已有的固定引用（带修订号）照常回读；恢复只是再提交一次 |
| D-02 | **删除后的可见性**：不进当前快照，所以记录目录、检索、文稿、续接、关系图、发布都看不到；按修订号读取照常可读；不带修订号的读取报 `NOT_FOUND` 并说明"已删除"；时间线仍列出它的修订 | 只改一个权威来源（提交清单），各视图自然一致 |
| D-03 | **引用护栏**：其他记录（含同一对象）的当前修订通过 sources、payload 或 links 引用要删除的记录或其中的结论时拒绝，并列出引用者。一次删除的记录之间互相引用不算 | 删掉被引用的记录会让引用者的依据链断开；先改引用者或一起删除 |
| D-04 | **指针记录不能删**：policy、goal、checkpoint 是对象的指针记录，用修订或替代表达变化 | 删除后指针会悬空 |
| D-05 | **恢复记录**：把墓碑中的原指针放回 `record_heads`（同一修订、同一指纹），并从 `removed_records` 移除。恢复前检查它引用的记录、结论、对象仍存在；一次恢复的记录之间互相引用视为存在 | 恢复后的记录与删除前逐字节相同 |
| D-06 | **删除对象 = 移入回收站**：业务卡对象把整个对象目录（业务卡、记忆、其下的 Run 与其他文件）移到 `.burrowl/trash/owners/<编号小写>/item/`；被采用的文档只移走它的记忆目录（`<文件>.memory/`），**原件留在原处**。同目录写 `discard.json`（原位置、方式、理由、时间、执行者、记录数）| 与 Run 回收站一致，可恢复；原件只读 |
| D-07 | **对象删除的护栏**：① 其他对象的当前记录引用本对象、其中的记录或结论、其下的 Run，或位于对象目录内的已登记来源时拒绝；② 对象目录内还有其他知识对象（嵌套）时拒绝；③ 配置的概念库（`graph.concept_owner_id`）不能删；④ 带 `expected_head` 时 HEAD 必须一致 | 与 D-03 同理；嵌套对象会被连带移走；概念库被全库链接依赖 |
| D-08 | **派生数据**：删除对象后立即删除它的检索投影（`IndexSink.on_owner_removed`，失败时 `index.reconcile` 会补删）；已发布到知识中枢的内容在下次发布计划中列为删除；阅读会话、笔记、维护计划只是历史，保持原样 | 派生数据可重建，不另建状态 |
| D-09 | **恢复对象**：原位置未被占用时移回，然后重建投影。清空对象回收站是永久删除，仅限用户（effect=user） | 与 Run 一致 |
| D-10 | **关键操作记录**：记录删除与恢复来自提交清单的 `removed_ids` / `restored_ids`；对象的删除、恢复、清空来自回收站日志 `.burrowl/trash/owners.log.jsonl`（只追加） | 恢复后 `discard.json` 不再存在，日志保留这段历史 |
| D-11 | **AI 也能删除到回收站**（MCP 可用，必须写理由，有护栏）；恢复双方都能做；清空回收站只有用户 | 用户决定（§6） |
| D-12 | **单条记录不做物理抹除**：要彻底清除就删除并清空整个对象 | 抹除中间修订会破坏提交链指纹；用户决定（§6） |

## 3. 存储格式（只增不改）

提交清单新增可选字段（旧清单没有，读取时视为空）：

```
removed_records: {MEM-…: {entry: <原 record_heads 指针>, removed_at, removed_in: COM-…, reason, actor}}   累积的墓碑
removed_ids:     [MEM-…]   本次提交删除的记录
restored_ids:    [MEM-…]   本次提交恢复的记录
```

删除或恢复的提交 `changed_ids` 为空；回执 `record_results` 的状态为 `removed` / `restored`。

回收站：

```
.burrowl/trash/owners/<编号小写>/item/          移走的目录（业务卡对象的整个目录，或被采用文档的记忆目录）
.burrowl/trash/owners/<编号小写>/discard.json   原位置、方式（folder / memory_home）、理由、时间、执行者、记录数
.burrowl/trash/owners.log.jsonl                discard / restore / purge 事件（只追加）
```

## 4. 操作

| 操作 | effect | 说明 |
|---|---|---|
| `memory.records_discard` | write | 删除记录（墓碑提交）；`dry_run=true` 只报告护栏与影响 |
| `memory.records_restore` | write | 恢复已删除的记录 |
| `memory.records_discarded` | 只读 | 对象内已删除的记录（标题、层级、理由、时间） |
| `memory.owner_discard` | write | 删除对象到回收站；`dry_run=true` 只报告护栏与影响 |
| `memory.owner_trash` | 只读 | 回收站中的对象 |
| `memory.owner_restore` | write | 从回收站恢复对象 |
| `memory.owner_empty_trash` | user | 永久删除回收站中的全部对象 |

## 5. 不做的

- 不做按记录的物理抹除（D-12）。
- 不做自动清空回收站或按时间过期。
- 删除不级联：被引用时拒绝，由 AI 或用户先处理引用者。

## 6. 用户的决定（2026-10-13）

- 谁可以删除：AI 和用户都能删到回收站；清空回收站只限用户。
- 单条记录：墓碑提交，可恢复；不做物理抹除。

## 7. 验收

- 删除记录后，记录目录、检索、文稿、续接中都看不到它；按修订号的固定引用仍可读；时间线有删除与恢复。
- 被引用的记录、指针记录、被引用的对象、嵌套对象、概念库的删除都被拒绝并说明原因。
- 恢复后的记录与删除前逐字节相同，检索可再次找到。
- 删除对象后它不在对象列表与检索中；被采用文档的原件未被移动；恢复后一切照旧。
- 用旧版本兼容性标本照常读取（不含新字段）。

## 8. 进度记录

| 任务 | 状态 | 说明 |
|---|---|---|
| T-1 存储：墓碑提交与读取 | 已完成 | `MemoryStore.remove`（与 commit 共用锁、幂等、暂存回读与 HEAD 发布）、`tombstones`；`read_record` 不带修订号读已删除记录报 `NOT_FOUND`（removed=true）；普通提交不能改写已删除的记录；回执带 `at` / `actor`（删除与恢复不写记录文件） |
| T-2 服务：记录删除与恢复、护栏 | 已完成 | `services/memory/removal.py`：引用护栏扫描 sources、payload、links 与复核记录的 `target_claim_id`；发布前用新扫描再核一遍；新引用已删除记录报 `UNRESOLVED_REFERENCE`（removed=true） |
| T-3 服务：对象回收站 | 已完成 | 护栏（外部引用含记录、结论、Run、目录内来源；嵌套对象；概念库；HEAD；写锁）；被采用文档只移记忆目录；`IndexSink.on_owner_removed` 删除投影，恢复后重建；回收站日志 |
| T-4 接口、关键操作记录 | 已完成 | 7 个操作（`memory.owner_empty_trash` 为 user）；`memory.activity` 增加 `remove` 类型；`gen_reference.py` 与 `npm run ops` 已运行 |
| T-5 工作台 | 已完成 | `features/Deletion.tsx`：对象页"删除对象"、记录勾选"删除所选"、记录页"删除记录"、"已删除的记录"恢复；维护页"已删除的对象"恢复与清空 |
| T-6 文档、规则与验证 | 已完成 | CORE §2、README、ARCHITECTURE（模块表与状态表）、RULES 第 4 节、GLOSSARY、TESTING、DEVELOPMENT_HISTORY、docs/README、AGENTS；`tests/service/test_deletion.py`（8 项）、`Deletion.test.tsx`（5 项）、e2e"删除与恢复" |
