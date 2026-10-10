# R16 语义维护交给 AI：候选一键成计划、AI 起草、用户预览后执行

| 项 | 内容 |
|---|---|
| 状态 | 用户同意按建议实现（2026-10-11）；细节待用户确认，见 §6 |
| 适用范围 | 语义维护（`services/maintenance.py`）、监测候选（`services/monitor.py`）、操作（`maintenance.*`）、工作台维护页与导航、burrowl-consolidate Skill、MCP 说明 |
| 关系 | 沿用 [R15](R15_WORKBENCH_NOTES.md) 的模式：AI 只起草，写入由用户在工作台看预览后执行 |

## 1. 背景（2026-10-11 核对代码）

1. 维护计划的服务本身可用：`plan` 列出受影响记录与完整正文，`review` 记录处置与修订草案并预检，`apply` 按对象逐个提交（请求 ID 由计划确定，可重试）。
2. 工作台上手动用不通：修订要在文本框里手写记录的 JSON 草稿；监测候选与计划没有连起来，"已纳入计划"只改状态，计划要手动复制 ID 生成。
3. `maintenance.apply` 对 AI 开放（effect=write），AI 可以不经用户预览就批量修订，与 RULES 第 8 节"批量修订先给预览、由用户确认"不一致。

## 2. 已定决定

| 编号 | 决定 | 理由 |
|---|---|---|
| D-01 | **候选一键成计划**：`maintenance.plan_from_candidates(target_ids)`（用户）从所选候选记下的路径取出变化来源，生成只含所选记录的计划，并把这些候选标为 planned、记下计划编号。`maintenance.plan` 增加 `targets` 参数做同样的筛选 | 不再手动复制 ID；计划只含用户选中的记录 |
| D-02 | **计划状态**：planned（已生成）→ queued（交给 AI）→ reviewed（待你确认）→ applied / partial。用户可以把 planned、reviewed、partial 交给或退回 AI；AI 用 `maintenance.review` 写处置（planned、queued、reviewed 时都可以，重写会覆盖上一版） | 每个状态的推进方清楚；退回后 AI 在原处置基础上修改 |
| D-03 | **执行只由用户触发**：`maintenance.apply` 改为 effect=user，只能在 reviewed、partial（重试）时执行，applied 时幂等返回 | 与 R15、RULES 第 8 节一致：写入前必须经用户预览 |
| D-04 | **署名**：修订的提交署名为写处置的 AI（`reviewed_by`），修订原因追加"维护计划 MP-…，经用户在工作台批准后写入"；撤回结论的复核以执行的用户署名 | 内容由 AI 起草；撤回是用户的决定 |
| D-05 | **预览**：`maintenance.status` 对 reviewed / partial 的计划附带逐对象预检结果；工作台逐条显示处置、理由，修订显示修订后的标题与正文，可展开对照当前正文 | 用户确认的是服务端将要提交的内容 |
| D-06 | **列表与往来**：`maintenance.plans(status?)` 列出计划概况；计划带 `thread`（交给 AI、处置、退回意见、执行），AI 从这里读用户的意见 | 不必记住计划编号；退回意见有地方放 |
| D-07 | **入口**：导航"维护与监测"显示待你确认的计划数；consolidate Skill 与 MCP 说明提示 AI 查看 queued 计划 | AI 只在对话中运行，需要在自然节点看到待办 |

## 3. 不做的

- 不在工作台上手写或编辑修订草案（修订由 AI 起草，用户只确认或退回）。
- 不自动回滚部分完成的计划；失败的对象可重试或退回 AI。

## 4. 任务

| 任务 | 内容 |
|---|---|
| T-1 | 服务：`plans`、`handoff`、`send_back`、`plan_from_candidates`、`targets` 筛选、状态检查、署名与原因、`status` 附预检 |
| T-2 | 操作与 effect；生成操作参考与前端类型 |
| T-3 | 工作台：候选勾选与一键成计划、计划列表、预览与执行/退回、导航计数 |
| T-4 | consolidate Skill、MCP 说明、CORE、ARCHITECTURE、README、TESTING、DEVELOPMENT_HISTORY |

## 5. 验收

- 服务测试：候选成计划只含所选记录且候选标为 planned；planned 不能执行；交给 AI → review → 用户执行，提交署名为 AI、原因带计划编号；退回保留处置并记下意见；列表按状态筛选。
- 接口测试：`maintenance_apply` 不在 MCP 工具中，`maintenance_plans` 在。
- 组件测试：勾选候选生成计划；预览显示修订内容；执行与退回的参数。

## 6. 进度记录

| 任务 | 状态 | 说明 |
|---|---|---|
| 设计稿 | 已写（2026-10-11） | 用户同意先实现，细节之后确认 |
| T-1、T-2 | 已完成（2026-10-11） | `MaintenanceService.plans / handoff`、`plan(targets)`、状态检查、署名与原因、`status` 附预检；操作 `maintenance.plans`、`maintenance.handoff`、`maintenance.plan_from_candidates`（user），`maintenance.apply` 改为 user，`maintenance.review` 增加 `message`。测试 `tests/interfaces/test_maintenance_handoff.py`，MCP 工具清单断言 |
| T-3 | 已完成 | 维护页：候选勾选与一键成计划、计划列表、逐条预览（处置、理由、修订后正文、当前正文、预检）、执行与退回、每 5 秒刷新；导航"维护与监测"显示待你确认数。组件测试 1 项；端到端用真实后端走完扫描 → 勾选 → 交给 AI → AI 写处置 → 执行 |
| T-4 | 已完成 | consolidate Skill 第 4 步、MCP 说明、CORE §8、ARCHITECTURE、README、TESTING、DEVELOPMENT_HISTORY、docs/README、AGENTS |
| 待确认 | — | §2 的各项决定；尚未用真实 AI 对话走一遍（AI 处置只用接口模拟） |
