# R14 Run 作为执行证据：存储、查找、知识去向与检索可见性

| 项 | 内容 |
|---|---|
| 状态 | 已确认（2026-10-10，§10 为用户的决定）；P1–P3 与遗留项已实现，见 §11 |
| 决定记录 | [DEVELOPMENT_HISTORY 的 R14](../DEVELOPMENT_HISTORY.md#r14-run-作为执行证据2026-10-10) 写理由和被放弃的方法；本文只写"做什么、怎么做、怎样算完成" |
| 适用范围 | 对象发现（STORE：`storage/owners.py`）、Run 服务（`services/runs.py`）、引用解析与复核图（MEM：context、claims、reader、history、raw_materials、rules、consolidation）、检索投影与图投影（IDX）、归属判定（ROUTE）、关键操作记录（ACT）、维护（MAINT）、规则与 Skill（GUIDE） |
| 维护方式 | 每完成一个任务更新 [§11 进度记录](#11-进度记录)；决定有变化时先改 §2，再改代码 |

## 0. 怎样使用本文件

- 决定写作 `D-xx`，任务写作 `T-Pn.m`，验收写作 `AC-Pn.m`。代码注释、测试名、提交信息引用编号（例如 `# R14 D-02：对象扫描不进入 runs/ 目录`）。
- 本文是计划，不代表功能已实现。已实现的行为以代码、测试、CORE、ARCHITECTURE 为准。
- 遇到本文没写的情况，先回到 §2 找依据；依据不足时问用户，用户确认后先改本文。

## 1. 背景、目标与非目标

### 背景

Run 记录一次实际执行：输入与脚本的指纹、输出、日志、成功或失败。它有两种：

- **对象内 Run**：研究、项目等对象里的具体执行，放在 `<对象目录>/runs/<run-id>/`。
- **独立 Run**：不属于任何对象，放在数据目录顶层的 `runs/<run-id>/`，通常来自临时的测试或执行。

现状（2026-10-10 核对代码与实测）：

1. **Run 是对象（Owner）**：身份来自 `run.json`，与研究、项目同等参与对象扫描。扫描成本按对象数线性增长，每个对象约 0.7–1.3 ms，而 Run 是增长最快的对象：1200 个 Run 时单次扫描约 1.5 s，一次保存要扫描 5 次。按每年 2000 个 Run 估算，第 3 年保存一次约 40 s。
2. **Run 进入默认检索**：每个 Run 都会在发现投影中生成一条对象条目（标题、编号、类型、描述），实测一个没有任何记录的 Run 会出现在默认阅读会话的候选里。归属判定已经把召回到的 Run 映射回上级对象（`routing.py`），但阅读和查询没有。
3. **Run 可以持有记忆记录**：`memory.commit` 不拒绝 RUN- 对象，RECORDING 的对象表也列了 run。实际数据中没有这样的 Run（样例与用户测试数据目录都为 0）。
4. **Run 业务卡里有自由文本结论**：`run.json` 的 `conclusion` 写着实质结论（样例 5 个 Run 全都有），这些内容不经复核、不进检索，`rules.py` 却允许用它作为"问题已解决"的依据，与 DEVELOPMENT_HISTORY 第三节"Run 的结论写成记忆记录，统一走复核"相矛盾。
5. **没有检查执行证据是否被固定**：规则要求 L1 实验用 `run_ref` 绑定 Run、consolidate 人工盘点未引用的 Run，但程序不报告哪些 Run 或 L0 记录没有被任何正式记录引用。
6. **上级对象的判定不一致**：时间线只认业务卡的 `owner_id`（"目录位置不算"），权限检查（`owner_of`）却按目录位置。样例中 3 个 Run 放在研究目录下，业务卡的 `owner_id` 却为空。

### 目标

1. **Run 是执行证据，不是知识入口**：和 L0 原件一样，它的价值通过引用它的正式记录（L1–L4）体现，也通过这些记录被找到。
2. **两种 Run 都不进入默认检索**；只有显式查找或从引用它的记录展开时才读到。
3. **执行中产生的有用知识都落到知识对象的正式记录里**，并且程序能报告遗漏。
4. **成本与 Run 的数量无关**：对象扫描、保存、查看、检索的固定成本只随知识对象数增长。

### 非目标

- 不迁移或改写已有 Run 的目录与业务卡；不改变 Run 的引用格式与指纹算法。
- 程序不替 AI 判断执行结果是否有价值，不自动生成记录，不删除 Run。
- 不新增对象类型。
- 不改变 L0 原件的检索规则（默认不检索，`content_source=all` 时才检索原件）。

## 2. 已定决定

| 编号 | 决定 | 理由 |
|---|---|---|
| D-01 | **Run 是执行证据**：不持有记忆记录（向 RUN- 提交返回 `INVALID_ARGUMENT`，提示写到上级对象）；不进入发现、全文、图路线的检索投影；不作为归属候选 | 知识要经过复核与检索的统一入口；执行日志不能代替 L2（RECORDING）；实际数据中没有 Run 持有记录 |
| D-02 | **对象扫描不进入任何 `runs/` 目录**（顶层 `runs/` 与 `<对象目录>/runs/`）。对象列表只含知识对象 | 扫描成本只随知识对象增长；Run 的所有用途都只需要"按编号或按上级找到它"，不需要每次请求枚举全部 Run |
| D-03 | **按编号定位 Run，不新增持久状态**：Run 目录名就是小写的 Run 编号。查找顺序：顶层 `runs/<id>` → 各知识对象的 `<目录>/runs/<id>`（每个对象一次 stat）→ 都没有时慢路径遍历全部 `runs/` 目录（兼容目录名不规范的旧数据）。结果在一次操作内复用 | 不引入需要维护一致性的索引文件；查找成本与知识对象数成正比，与 Run 数无关；慢路径只在异常时出现 |
| D-04 | **上级对象按目录位置判断**：Run 所在的对象目录就是上级；顶层 `runs/` 下的是独立 Run。业务卡的 `owner_id` 只作说明，与位置不一致时 `system.validate` 报告 | 权限检查本来就按位置（受限对象目录下的材料一律受限）；列出某对象的 Run 只需列它自己的 `runs/` 目录；`runs.create(owner_id=…)` 建的 Run 位置与字段一致 |
| D-05 | **引用格式与指纹不变**：`{target_kind: "owner", target_id: "RUN-…", sha256: 业务卡指纹}` 继续有效，解析改为经 D-03 查找 | 契约只做增量；已有记录的指纹与复核不受影响 |
| D-06 | **知识去向**：对象内 Run 的知识写进上级对象的 L1–L4（实验类 L1 用 `run_ref` 绑定，L2 的 stage 用 `evidence_refs` 引用）。独立 Run 的知识按主题写进合适的知识对象：工具、库、环境的行为写进 tool；数据集的特性写进 data；方法、现象、比较写进 research；属于某个交付的写进 project。由沉淀检查 → `memory_owner_route` 选定；没有非常合适的对象时，放进**通用知识对象**（knowledge 类型，见 D-13）。Run 留在原处，被正式记录引用即视为已固定 | 知识按主题积累才能被后续任务找到；搬目录会改变路径和指纹，引用本身已经记录了"这次执行支持了哪条知识" |
| D-07 | **Run 的显式入口**：`runs.search`（按标题、问题、关键词查找）、对象的时间线与原始材料（`memory.timeline`、`memory.raw_materials` 列出该对象位置下的 Run 和被其记录引用的 Run）、从引用它的记录展开。查询与阅读的 `owner_ids` 不接受 RUN-，报错并指向 `runs.search` | "除额外指定外不进检索"：显式入口足够，并且不会混进主题召回 |
| D-08 | **Run 业务卡的 `conclusion` 只写执行摘要**：发生了什么（成功或失败、产物、异常），不写知识；不能作为问题的解决依据（删除 `rules.py` 中 kind=run 的分支）。字段保留，已有内容不改 | 结论只有一个入口：记忆记录与复核；保留字段避免契约变更 |
| D-09 | **报告未被固定的执行证据**：`memory.consolidate_prepare` 列出本对象位置下的 Run 与本对象的 L0 记录中，没有被全库任何 L1–L4 引用的；`runs.search(unfixed_only=true)` 全局列出这样的 Run，`system.validate` 给出数量提示。只报告，不写记录、不删除 | 语义判断由 AI 做，删除由用户做；程序负责把遗漏暴露出来 |
| D-10 | **一次性小任务（档 1）默认不建 Run**：直接执行；沉淀检查认定执行结果要被引用时，在选定的对象下 `runs_create` 再 `runs_register` 登记已有产物（或 `runs_execute` 重跑）。需要在执行时就由 burrowl 捕获、但归属未定时，才建独立 Run | 避免产生大量没人引用的独立 Run；登记已有产物同样固定指纹 |
| D-11 | **关系图中的 Run 节点只来自引用**：记录 → Run 的边照常画出；删除"上级 → Run"的 produces 边 | produces 边需要枚举全部 Run；位置关系已经由记录的引用和时间线表达 |
| D-12 | **没有知识的 Run 不保留**：执行结束、沉淀检查认定没有可写成记录的内容时，用 `runs.discard` 丢弃。只有当前没有任何记录引用的 Run 才能丢弃；丢弃把 Run 目录移到 `.burrowl/trash/runs/`（可恢复），清空回收站是用户专属操作。失败本身有教训时写成记录并引用，不丢弃 | 用户确认：没有知识总结、不能写成记录的 Run 不应存下来；移入回收站而不是直接删除，误判时可以恢复 |
| D-13 | **knowledge 类型分两种用途**：配置 `graph.concept_owner_id` 指定的那一个是概念库（只放概念页）；其他 knowledge 对象是**通用知识对象**，承接不属于任何具体研究、项目、工具、数据的知识（例如跨项目的工程经验）。按主题复用已有的通用知识对象，主题不同时新建；不往概念库里写经验 | 用户确认兜底放 knowledge；代码本来不限制 knowledge 对象的数量，"全库唯一"只是文档定义，概念库由配置项指定 |

## 3. 两种 Run 的工作流

```text
对象内 Run（持续工作，档 2）
  runs_create(owner_id=X) → runs_execute / runs_register → 状态结束
  → 在 X 中保存：L1（实验，run_ref = 该 Run）/ L2（stage.evidence_refs 引用 Run 或其产物）
  → consolidate：prepare 报告 X 下未被引用的 Run 与 L0 → AI 补记录；确实没有知识的 Run 用 runs_discard 丢弃

独立 Run（临时执行）
  默认：档 1 不建 Run，直接执行
  需要捕获执行：runs_create()（无 owner）→ 执行
  沉淀检查：
    无可复用内容 → 不记录；runs_discard 丢弃该 Run（移入回收站）
    有可复用内容 → memory_owner_route → 选定知识对象 Y（按主题：tool / data / research / project）
                 → 没有非常合适的 → 按主题复用或新建通用知识对象（knowledge，不是概念库）
                 → 在 Y 中保存 L1–L3（引用该 Run），需要被找到时维护 Y 的 L4
```

检索时：知识对象 Y 的 L4/L3 正文、L2 摘要、L1 检索说明进入发现投影，命中后完整阅读，从记录里的引用回到 Run 及其产物。

## 4. 存储与查找（D-02 至 D-05）

### 4.1 对象扫描

- `OwnerRegistry._scan` 遍历内容树时，跳过名为 `runs` 的目录（顶层与对象目录下的都跳过）；`.run-captures` 原本就跳过。
- `OwnerIndex` 不再包含 run 类型；`readable("run")` 的调用方全部改为经 Run 查找（4.2）。

### 4.2 Run 查找（`storage/runs.py`，新模块）

```text
RunLocator(workspace)
  find(run_id, owners: OwnerIndex) -> Owner | None     # D-03 查找顺序；返回 owner_type="run" 的 Owner（不进入 OwnerIndex）
  list_under(owner: Owner | None) -> list[Owner]        # 某对象位置下的 Run；None 表示顶层独立 Run
  of_path(relative_path) -> Owner | None                # 路径位于某个 Run 目录内时返回该 Run（权限检查用）
  iter_all(owners) -> Iterator[Owner]                   # 显式全量（runs.search、maintenance、activity），成本 O(Run 数)
```

- 读取业务卡复用现有的 `_card_owner`（同一个指纹算法），路径检查用 `safe_path`。
- 一次操作内复用：与对象扫描共用 `request_scope`；写 run.json 的地方调用 `invalidate()`（现有 RunService 三处）。
- 权限：对象内 Run 的材料由上级对象的位置覆盖（`owner_of` 不变）；独立 Run 的材料按 `of_path` 找到 Run 卡判断敏感级别。

### 4.3 调用方改动

| 调用方 | 现在 | 改为 |
|---|---|---|
| `RefResolver` / `ClaimGraph` / `Reader` 解析 owner 引用 | `index.get(id)` | RUN- 开头走 `RunLocator.find` |
| `core/technical.py` 的 `run_ref` 校验 | 只校验格式 | 不变（存在性由 RefResolver 解析时检查） |
| `runs.search` / `_run` | 遍历 `readable("run")` | `iter_all` / `find` |
| `history._run_ids`、`raw_materials` | 遍历全部 Run 比对业务卡 `owner_id` | `list_under(owner)` + 记录中的 RUN 引用 |
| `activity` 的 Run 结束事件 | 遍历对象列表中的 Run | 指定对象时 `list_under`；全局时 `iter_all`（见 §9 风险） |
| `ClaimGraph.stats` 的 `run_inputs` | `index.find(...).owner_type == "run"` | 按引用目标的编号前缀与查找结果判断 |
| `routing.py` | 召回后把 Run 映射回上级 | Run 不再被召回，删除映射 |
| `indexing` 图投影 | Run 作为对象投影，画 produces 边 | 不投影 Run；Run 节点来自记录的引用（D-11） |
| `memory.commit` | 接受 RUN- | 拒绝（D-01） |
| `rules.py` 问题解决依据 | 接受 kind=run 的 `conclusion` | 删除该分支（D-08） |

## 5. 检索可见性（D-01、D-07）

- 由于 Run 不在对象列表中，`index.reconcile` 不再为 Run 建投影，并按现有逻辑删除已有的 Run 投影（对象不存在即清理派生数据）。
- 查询（`query.*`）与阅读（`reading.*`）的 `owner_ids` 含 RUN- 时返回 `INVALID_ARGUMENT`，details 指向 `runs.search`。
- 默认检索中 L0 的行为不变：L0 记录不进入全文与发现投影；原件只在 `content_source=all` 时检索。

## 6. 未固定证据报告（D-09）

- `memory.consolidate_prepare` 增加 `unfixed_evidence`（被**全库**任何 L1–L4 引用即算固定，知识可能按主题写在别的对象里；收口准备本来就建全库复核图，不增加读取）：
  - `runs`：本对象位置下的 Run 中，没有被任何 L1–L4 引用（`run_ref`、`sources`、`evidence_refs` 等任何字段）的；
  - `raw_records`：本对象的 L0 记录中，没有被任何 L1–L4 引用的。
  每项给出编号、标题、状态、结束时间。consolidate Skill 要求逐项处理：补写记录，或在收口说明里写明不需要的理由。
- `runs.search(unfixed_only=true)` 列出没有被任何 L1–L4 引用的 Run；`system.validate` 有这样的 Run 时给出 `RUN_UNFIXED` 提示。（原计划放在 `maintenance.status`，但它只是单个维护计划的状态，不是全局报告，改到 Run 的显式入口。）
- 都只报告，不改记录。丢弃由 AI 按 D-12 显式调用 `runs.discard`。

### 6.1 丢弃 Run（D-12）

- `runs.discard(run_id, reason)`（effect=write，agent 可调用）：先核对当前没有任何记录引用该 Run（遍历知识对象的当前记录，读取走已校验缓存），有引用时返回 `VERSION_CONFLICT` 并列出引用它的记录；否则把 Run 目录整体移到 `.burrowl/trash/runs/<run-id>/`，并写入 `discard.json`（原位置、理由、时间、执行者）。
- 只核对当前修订：旧修订引用过的 Run 被丢弃后，那条历史修订的回源会失败；回收站保留原目录，可以恢复。
- `runs.restore(run_id)`（effect=write）：原位置空闲时移回。
- 清空回收站（effect=user）：只在工作台与 CLI 提供，不暴露给 MCP。
- 回收站不进备份包（与索引同为可排除数据），不参与对象扫描与 Run 查找。

## 7. 契约与兼容

- 记录契约不变；引用格式、业务卡指纹算法不变；浮点样例全部记录的 content_hash、record_hash 不变（`tests/service/test_links.py`）。
- 已有 Run 目录与业务卡不改。业务卡 `owner_id` 与位置不一致时，`system.validate` 给出 warning（不是 problem）。
- 操作接口：新增返回字段（`unfixed_evidence`、`unreferenced_runs`）属于增量；`memory.commit` 对 RUN- 的拒绝、查询与阅读对 RUN- 范围的拒绝是行为收紧，在 ops 说明中写明。

## 8. 实施阶段

### P1 存储与查找

- **T-P1.1** 对象扫描跳过 `runs/`（D-02）。
- **T-P1.2** `storage/runs.py`：`RunLocator`（find / list_under / of_path / iter_all），复用操作内作用域（D-03、D-04）。
- **T-P1.3** 按 §4.3 改调用方；`memory.commit` 拒绝 RUN-（D-01）；图投影去掉 Run 与 produces 边（D-11）。
- **T-P1.4** `system.validate` 报告业务卡 `owner_id` 与位置不一致（D-04）。
- **T-P1.5** 测试与实测：扫描、保存、查看在 0 / 1200 个 Run 下的耗时对比；样例记录指纹不变。

### P2 检索与知识去向

- **T-P2.1** 查询与阅读拒绝 RUN- 范围（D-07）；确认 reconcile 清理已有 Run 投影。
- **T-P2.2** `rules.py` 删除 Run 结论作为解决依据（D-08）。
- **T-P2.4** knowledge 类型的两种用途（D-13）：GLOSSARY、CORE、ARCHITECTURE 状态表、`memory.owner_create` 说明、RULES 第 2 节。
- **T-P2.3** 规则与 Skill：RULES 第 1、2 节（档 1 默认不建 Run、独立 Run 的知识去向）；burrowl-work（Run 结束后在上级对象保存）；RECORDING 对象表删除 run 行，写明 `conclusion` 只写执行摘要；MCP 服务说明。

### P3 未固定证据报告

- **T-P3.1** `consolidate_prepare` 的 `unfixed_evidence`；consolidate Skill 增加逐项处理要求（D-09）。
- **T-P3.2** `runs.search(unfixed_only)` 与 `system.validate` 的 `RUN_UNFIXED`（工作台维护页的只读列表另行实施）。
- **T-P3.3** `runs.discard` / `runs.restore` / 清空回收站（D-12）；RULES 与 burrowl-work 写明何时丢弃。

### 文档维护

ARCHITECTURE（模块表、状态表、已知差距）、CORE（Run 的定义与工作流）、GLOSSARY（Run、独立 Run、执行证据）、RULES、RECORDING、burrowl-work、burrowl-consolidate、docs/README、DEVELOPMENT_HISTORY（R14）。

### 验收

- **AC-P1.1** 对象扫描耗时与 Run 数无关：1200 个 Run 与 0 个 Run 相比，扫描、保存、inspect 耗时差异在 10% 以内。
- **AC-P1.2** 样例中全部引用 Run 的记录照常解析；`test_links.py` 指纹不变；引用不存在的 Run 时与改前一样报 `NOT_FOUND`。
- **AC-P1.3** 受限对象目录下的 Run 材料、受限的独立 Run 材料，仍不能登记或引用。
- **AC-P2.1** 默认阅读会话、材料查询、归属判定都不返回 RUN-；`runs.search` 能找到。
- **AC-P3.1** 浮点样例的 5 个 Run 都已被 L1 或 L2 引用，`unfixed_evidence.runs` 为空；在样例对象下新建一个 Run 且不写记录后，它出现在列表中；写一条引用它的 L2 后消失。

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 全局关键操作记录要读全部 Run 卡，成本 O(Run 数) | 已处理：按目录修改时间（结束时间的上界）惰性读取，页满即停；同一秒内大量结束的 Run 仍会多读 |
| 旧数据的 Run 目录名不是小写编号，查找走慢路径 | 慢路径遍历一次后在本次操作内复用；`system.validate` 报告目录名不规范的 Run |
| AI 不按规则把执行知识写进记录，知识留在 Run 卡或对话里 | D-09 的报告在每次收口时暴露遗漏；D-08 让 Run 卡里的结论不能冒充依据 |
| 删除 produces 边后，关系图上看不到"对象有哪些 Run" | 时间线和原始材料仍按位置列出；被引用的 Run 在图上可见 |
| 独立 Run 长期堆积 | D-12：没有知识的 Run 及时丢弃；maintenance 报告遗漏 |
| 误丢弃仍有价值的 Run | 只能丢弃当前无引用的 Run；移入回收站可恢复；清空回收站只能由用户执行 |

## 10. 用户的决定（2026-10-10）

| 编号 | 问题 | 决定 |
|---|---|---|
| Q1 | 独立 Run 的知识没有合适对象时放哪里 | AI 判断放入合适的对象；没有非常合适的，放入 knowledge 类型的通用知识对象（D-06、D-13） |
| Q2 | 是否把独立 Run 搬到知识对象目录下 | 不搬，被引用即固定 |
| Q3 | 未被引用的 Run 如何处理 | 没有知识总结、不能写成记录的 Run 不应保留：用 `runs.discard` 移入回收站（D-12）；maintenance 报告遗漏 |
| Q4 | Run 卡的 `conclusion` 字段 | 保留字段，只写执行摘要（D-08） |
| Q5 | Run 结束时是否提醒未被引用 | 不提醒，只在收口时报告（D-09） |

## 11. 进度记录

| 任务 | 状态 | 说明 |
|---|---|---|
| 设计稿 | 已确认（2026-10-10） | 用户决定见 §10 |
| T-P1.1–T-P1.4 | 已完成（2026-10-10） | 对象扫描跳过 `runs/` 与 run.json；`RunLocator` 并入 `storage/owners.py`（与 `OwnerRegistry` 同文件，避免循环导入），`OwnerIndex.get/find` 遇到 RUN- 时按位置查找，`owners`/`readable()` 不含 Run，`owner_of` 补独立 Run 的路径；调用方按 §4.3 修改；`memory.commit` 拒绝 RUN-；关系图 Run 节点由引用它的对象投影（`GRAPH_VERSION` → graph-v2），删除 produces 边；`system.validate` 报告编号重复、目录名不规范、位置不一致 |
| T-P1.5 | 已完成 | `tests/service/test_run_evidence.py`。实测 20 个知识对象：扫描 0 / 1200 个 Run 时 14.0 / 14.4 ms（改前 1200 个 Run 时 1520 ms）；保存 291 / 294 ms，inspect 70 / 71 ms（AC-P1.1） |
| T-P2.1–T-P2.2 | 已完成 | 查询与阅读拒绝 RUN- 范围；`rules.py` 删除 Run 结论作为解决依据（测试确认改动前该用例不报错） |
| T-P2.3–T-P2.4 | 已完成 | RULES 第 1、2 节与名词表、burrowl-work 第 4 步、burrowl-consolidate 第 2 步、RECORDING 第 3 节；GLOSSARY、CORE、README、ARCHITECTURE；ops 说明（`runs.*`、`memory.owner_create`） |
| T-P3.1–T-P3.3 | 已完成 | `unfixed_evidence`；`runs.search(unfixed_only)` 与 `RUN_UNFIXED`；`runs.discard` / `runs.trash` / `runs.restore` / `runs.empty_trash`（effect=user）；回收站不进备份包 |
| 遗留项 | 已完成（2026-10-10） | 工作台维护页新增"未写成记录的 Run"面板（丢弃需写理由、回收站恢复与清空）；首页"Run 结束"链到上级对象，独立 Run 链到维护页；删除 `ClaimGraph.stats` 的 `run_inputs`；全局关键操作记录按目录修改时间惰性读 Run 卡、`runs.search` 按目录名倒序惰性读，批量读卡共用目录枚举并在返回前复核。实测 1200 个 Run：`runs.search(limit=50)` 约 0.1 s，读全部卡约 1.8 s（改前每张卡都把 runs/ 目录列一遍，首次读约 25 s） |
