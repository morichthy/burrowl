---
name: burrowl-dev-check
description: 修改本框架（burrowl 代码、Skill、文档、前端、打包）时使用：按影响定位模块与文档，选择验证强度，完成文档与 Skill 的维护闭环。研究或业务工作不要使用。
requires_tools: [system_ops, system_capabilities, system_validate]
---
# 框架开发检查

本 Skill 只补开发需要的验证和维护要求，不规定实现方法。

## 开发前

1. 用 [ARCHITECTURE](../../ARCHITECTURE.md) 的"模块与变化影响"表定位受影响模块，追踪实际调用者和数据消费者。
2. 查 [DEVELOPMENT_HISTORY](../../docs/DEVELOPMENT_HISTORY.md) 中的相关决定。讨论全局方案前完整阅读。
3. 新增职责、状态或依赖前，先说清：归属哪个模块、由谁维护、生命周期、失败如何恢复、谁消费它。遵守 ARCHITECTURE 的"稳定设计约束"。

## 验证（按影响选择，见 [TESTING](../../docs/TESTING.md)）

在仓库根目录执行：

```
uv run pytest                        # 全量；只测受影响部分：tests/unit、tests/service、tests/interfaces、tests/connectors
uv run ruff check packages tests tools
uv run lint-imports                  # 分层依赖
uv run burrowl skills check              # Skill 引用的工具都存在、agent 可调用、在 MCP 默认工具组内
npm --prefix frontend run build      # 改了前端：生成类型 + 类型检查 + 构建
npm --prefix frontend test           # 组件测试
npm --prefix frontend run e2e        # 浏览器端到端（真实后端）
uv run python packaging/build.py     # 改了依赖、打包或启动器：构建并做一次离线安装
```

- 改了操作（op）或配置：运行 `uv run python tools/gen_reference.py` 和 `npm --prefix frontend run ops`，类型检查会暴露前端不匹配的调用。
- 改了记录契约：运行 `uv run python tools/gen_draft_examples.py`，并跑 `tests/service/test_draft_examples.py`。
- 需要真实模型（`real_model`）或真实 WeKnora（`live_hub`）的测试默认跳过；改到相关代码时要说明是否实测。

## 开发后：文档与 Skill 闭环

按 [DOCUMENTATION_MAINTENANCE](../../docs/DOCUMENTATION_MAINTENANCE.md) 的分工，只更新受影响的部分：

| 改了什么 | 维护哪里 |
|---|---|
| 用户可见的概念、功能、目录、安装方式 | README |
| 模块职责、依赖、状态归属、数据流、已知限制 | ARCHITECTURE |
| 功能定义、交接、关键行为与选项 | docs/CORE |
| 新增或移动文档 | docs/README |
| 测试分层或风险检查 | docs/TESTING |
| 全局取舍、被替代的方案、失败教训 | docs/DEVELOPMENT_HISTORY |
| 术语 | docs/GLOSSARY |
| AI 需要遵守的规则或写法 | skills/RULES、skills/RECORDING 或对应 Skill，并查看直接调用者 |

全局整合、发布或用户要求全面审查时，前后完整阅读六份必读文档（README、ARCHITECTURE、docs/CORE、docs/README、docs/DOCUMENTATION_MAINTENANCE、docs/TESTING）和全部 Skill，逐项核对。

## 报告

在本次计划或结果摘要中写清：

- 改了什么，跑了哪些检查、结果如何；哪些没跑，原因是什么。
- 每份受影响文档的处置：**已修改 / 已核对无需修改 / 延期（原因）**。
- 软件测试、实际 AI 场景、人工验收、科学结论分开报告。

不为了让测试通过而放宽断言；预期行为变了，要在测试里写明理由。
