# 核心功能与实现逻辑

本页面向使用者和开发 AI，用伪代码和必要说明解释核心功能，保留影响理解的选项和边界。

- 不展开字段清单、接口参数和算法常数：字段用 `memory.contract`，参数见 [ops 参考](reference/ops.md)，实现文件见各节末尾。
- 模块关系与变化影响只维护在 [ARCHITECTURE](../ARCHITECTURE.md)。
- 术语见 [GLOSSARY](GLOSSARY.md)。

核对日期：2026-10-08，依据当前工作树。相关实现变化时同步维护本页；当前没有自动同步文档的服务。

## 功能块、定义与边界

G01–G12 沿用源项目的功能分组身份，描述的是职责，不是十二个独立服务。

| 功能块 | 输入 → 输出 | 职责与边界 | 主要模块 |
|---|---|---|---|
| G01 固定读取与解析 | 固定引用 → 已核验内容或缺口 | 读取指定版本，核对字节指纹与授权；候选清单不算已读正文 | MEM、STORE |
| G02 内容与修订 | 草案、预期版本 → 新修订或冲突、回执 | 校验、幂等提交、历史保留；单 Owner 事务；保存不等于索引或复核成功 | MEM、STORE |
| G03 表示与组合 | 固定材料 → 可读内容 | 选择已有正文、技术块或章节；组合不产生新的语义判断 | MEM、QUERY |
| G04 索引维护 | 已保存内容 → 可检索投影与水位 | 全文、发现、原件三类投影；索引不是事实来源，不能补回失踪原件 | IDX |
| G05 候选召回 | 问题、范围、用途 → 候选与命中依据 | 精确身份、关键词、向量多路召回；模型或索引缺口单列 | IDX、QUERY |
| G06 去重与排序 | 各路候选 → 稳定顺序与命中解释 | 规范身份去重、RRF 融合；可选重排与显式条件；排名不是可信度 | IDX、READ |
| G07 回源与关系导航 | 固定种子 → 展开内容、关联候选 | 只沿作者保存的引用或已采纳关联展开；导航不构成科学支持 | QUERY、MAINT |
| G08 渐进查询 | 请求或游标 → 分页、进度、停止原因 | 续页、取消；保留原范围和累计预算 | QUERY |
| G09 上下文组包 | 已选候选 → 正文、必要依据、缺口 | 补齐声明的必要依据；不隐式二次检索；缺依据时父材料标为不完整 | QUERY |
| G10 阅读视图 | 对象或文稿 → 正文、目录、分页 | 按编排结构阅读；展示不改版本或复核状态 | MEM、READ、APP |
| G11 语义维护 | 新依据、受影响内容 → 计划、处置、提交 | AI 或人判断，程序执行；跨对象可能部分完成 | MAINT、MEM |
| G12 来源与证据 | 固定内容、用途、范围 → 授权、复核、影响结果 | 正式用途逐条核验；保存、相关性、导航都不能代替复核 | REVIEW、MEM |
| G13 知识中枢 | 发布策略 → 外部副本、检索命中、Wiki | 本地为权威，外部可重建；默认只发布已复核内容 | HUB |
| 共享执行控制 | 预算、实际消耗、取消 → 累计账本与终止原因 | 约束读取、查询、展开、组包、阅读召回 | QUERY、READ |

模块简称见 [ARCHITECTURE 的影响表](../ARCHITECTURE.md#模块与变化影响)。

## 功能块怎样衔接

箭头表示主要数据交接，不是固定执行顺序；权限、证据和预算检查贯穿各步。

```text
保存链：   AI/人组织内容 → G02 保存修订 → G04 更新索引 → G01 固定回读（保存与可检索分别核对）
查询链：   G08 管理查询 → G05 召回 → G06 去重排序 → G01 回源 + G12 资格 → 选择
           → 按需 G07 展开 → G03 选择形式 → G09 组包 → G10 阅读
阅读链：   G05 发现召回 → G06 筛选包 → 三态判断 → G10 完整阅读 → 笔记 → 交接（重新核对 G01）
维护链：   监测或 G12 发现影响 → G11 实际审查 → G02 保存新修订 → G04 更新投影 → G01/G10 回读
发布链：   G12 复核类别 + 发布策略 → G13 预览 → 用户确认 → 执行 → 轮询外部状态
```

## 1. AI 怎样整理和记录结果

Skill 指导 AI 读什么、怎么组织内容；程序负责校验和保存。**记录成功不代表解释正确。**

```text
AI 按任务选择 Skill：
    工作、导入资料、执行、续接 → work
    检索与完整阅读             → read
    阶段收口、来源变化、发布   → consolidate

先找对象（沿用 / 引用 / 新建），维护一份 Markdown 工作清单
读取已有目标、检查点、概览和固定依据
    → 核对输入、方法、实际结果、失败和缺口
    → 区分事实、计算、推断、建议

有用内容按职责保存（缺层正常）：
    L0 登记原件与 Run       L1 完整技术内容 + 检索说明
    L2 有上下文的经过        L3 可复用认识与边界        L4 自足的发现入口
    需要时编排文稿（章节 → 文稿根）

先保存被引用的内容，再保存引用它的；逐条脱离上下文回读
```

写作标准见 [skills/RECORDING.md](../skills/RECORDING.md)；字段与示例由 `memory.contract(kind)` 返回，示例经 `tests/service/test_draft_examples.py` 校验。

实现：`core/records.py`、`core/contract.py`、`core/schema/examples/`。

## 2. 结果怎样保存到本地

- **record ID** 保持内容身份；**revision** 是单条记录的修订号；**commit** 把一个 Owner 的若干变化一起发布。
- 每次提交只写变化后的完整记录；清单（manifest）保留全部当前版本的指针；旧修订留在父提交链中。
- 引用是"身份 + 版本 + 指纹"，目标更新后不会悄悄指向新版。

```text
AI 组成提交请求：{owner_id, expected_head, request_id, operations:[put_record…]}
    （可先 dry_run 预检）
保存服务：
    校验结构、领域规则、引用和授权（受限来源、敏感级别不得降低、支持关系不能成环）
    同一 request_id 且内容相同 → 返回原回执（幂等）
    HEAD 已变化 → VERSION_CONFLICT
    内容无变化 → 不产生新修订
    否则：锁内写暂存 → 回读校验 → 原子切换 HEAD → 写回执
    同步更新该对象的检索投影（失败只记 pending，由 reconcile 补偿）
回执：每条记录的 revision、record_hash、现成的 fixed_ref；save_status 与 index_status 分开
```

- 读取时（inspect、resume、导出、总结材料），依据链上有受限或不可用来源的记录会被隐去，并在 `omitted` 中说明原因。
- AI 不直接编辑存储文件。

实现：`services/memory/service.py`、`storage/memory_store.py`、`core/atomic.py`。

## 3. 检索与召回

```text
构造查询计划：原问题 + 最多 2 条等义改写（技术问题加英文）
              保护词（型号、数值原样）+ 术语库扩展
多路召回：精确身份 | 关键词（FTS5，中日韩按二元切分）| 向量（sqlite-vec，按模型指纹）
          在所选投影内：fulltext 全文 / discovery 发现 / source 原件
过滤：范围、排除、敏感级别、owner_only 可见性、复核过滤（review_filter）
融合：RRF，同一信号族只计一票；按规范身份去重
回源：命中回到固定版本核对；索引落后时给出缺口而不是"没有结果"
```

- **发现投影**：只含 L4/L3 正文、L2 摘要、L1 检索说明和对象元数据，是阅读会话的默认召回面。
- **全文投影**：包含技术块、结论等，用于材料查询和用户批准的全文补偿。
- **原件投影**：抽取 pdf/pptx/xlsx/csv/图片（OCR）等已登记文件的文本。

实现：`index/`、`services/indexing.py`。

## 4. 阅读会话

```text
start：建立会话，召回发现投影 → 每个对象 2–4 个互补窗口（可选 CE 只调窗口次序；
       显式条件只调对象次序，冲突的排后、不删除）
assess：三态判断（必须带 digest 和理由）；用户改判优先
        uncertain → peek 更多窗口或问用户；quick 时标出采纳的窗口
read：只读 relevant；首次读取固定对象 HEAD；按页读完（对象更新则从第 1 页重读）
note：standard/associative 须读完全部页；出处必须已交付，补全为固定引用；可附探索线索
synthesize：跨对象综合，固定所依据的笔记
全文补偿：仅当发现召回不可用、不完整或不足 → ask_user(fulltext) → 用户批准 → recall(fulltext)
联想补查：associative 策略按主 Agent 给定方向，受轮数上限
finish：交接文本 = 有效综合稿或逐对象笔记；陈旧笔记、超上限笔记不交付并列为缺口
用户侧：respond / override / direct / configure / abort / archive → 写入收件箱，随下次调用交付
```

所有 agent 写操作都可带 request_id，重试时不会重复执行；召回轮次受会话预算约束。

实现：`services/reading/`；工作台页面 `frontend/src/features/reading/`。

## 5. 关联怎样保存和查找

- `relations.similar` 按关键词重合或语义近邻给出候选，候选只是待判断的线索。
- 判断后用 `relations.decide` 保存为 association 记录（candidate / accepted / rejected / withdrawn）。类比必须写共享结构和迁移边界。
- 关联只做导航：不会进入 supports 证据，也不会改变复核状态。
- 关系图从当前记录实时构建，包含对象、记录、来源和作者保存的类型化关系；视图偏好和随手笔记保存在本机。

实现：`services/memory/associations.py`、`services/relations.py`。

## 6. 正文和必要上下文怎样组装

```text
assemble(已选候选)：
    每个候选取固定版本正文（技术单元按所选块，并补齐声明的必要定义块）
    沿 prerequisite 引用补齐必要依据（context_reach 决定可否跨出所选范围）
    依据不能加入 → 记缺口，并把父材料标为 incomplete
    整块加入，预算不够就整块省略并报告，不截断公式或定义
    formal 用途：只输出在指定范围内已确认、有效的结论原文
```

组包无缺口不代表研究完整，也不会隐式二次检索。

实现：`services/query/assemble.py`。

## 7. 组装、读取文稿和生成研究经过的区别

| 操作 | 做什么 | 不做什么 |
|---|---|---|
| 组包（query.assemble） | 把已选材料和必要依据拼成阅读包 | 不生成新内容，不读全文 |
| 读取文稿（memory.document） | 按文稿根 → 章节 → 技术块的编排展开全文、目录、单章或依据变化影响 | 不生成或修改文稿；依据有新修订时只提示，不自动替换 |
| 阅读会话（reading.read） | 优先读对象的研究过程文稿，没有文稿时按 L4→L2→L3→L1 读各记录正文 | 不判断相关性 |
| 写经过、概览、文稿 | 由 AI 或人实际阅读后撰写，经 memory.commit 保存 | 程序不会自动生成 |

## 8. 来源变化后怎样维护

```text
监测：比较对象 HEAD 与所引原件的指纹（可 dry_run）→ 观察记录 + 维护候选（open/planned/dismissed）
影响：provenance.impact 沿依赖找到下游（supports/input 是正式依赖，导航只提示）
计划：maintenance.plan 列出受影响记录及其完整正文
处置：AI/人逐项决定 retain / revise（附草案）/ resynthesize / retract / defer → maintenance.review 预检
提交：maintenance.apply 按对象逐个提交（依据版本不一致则拒绝），可能部分完成
回读：再按 consolidate 流程检查概览、文稿和上下文
```

- 观察和候选不改变任何证据或复核状态。
- 复核在内容变化后自动失效，需要重新复核。

实现：`services/monitor.py`、`services/maintenance.py`、`services/memory/provenance.py`。

## 9. 复核与正式使用

```text
review.queue：跨对象列出结论及状态（未复核、已失效、有争议、依据受阻……）与原因
review.submit：确认需要范围与证据；替代需要替代结论；dry_run 预检
    复核记录绑定"所在记录的内容指纹 + 结论指纹"，任一变化 → 已失效
检索：记录的复核类别（none/accepted/unreviewed/mixed/unsafe）进入投影，按 review_filter 过滤
正式用途：只返回在指定范围内 accepted、未失效、来源可见的结论
```

谁可以确认结论见 [skills/RULES.md 第 4 节](../skills/RULES.md)。

实现：`services/review.py`、`services/memory/claims.py`。

## 10. 知识中枢与 Wiki

```text
desired(策略)：从各对象当前 HEAD 选出应发布内容（默认：已复核的 L4/L3 + 依据完整的成稿文稿 + 宿主编写的 wiki_page）
plan：与映射对比 → 新增/更新/删除/标记撤回 + plan_hash
apply(plan_hash)：哈希一致才执行；映射丢失时按外部 local_key 认领；逐条记录结果
status：轮询外部异步解析
search：联邦检索，命中能对上本地时附固定引用和按本地当前状态计算的复核类别
wiki：外部被人修改（漂移）时不覆盖，除非显式 force
```

实现：`services/hub.py`、`connectors/`，以及独立包 `packages/burrowl-connector-weknora/`。

## 11. 数据目录、工作目录与迁移

- **数据目录**存放 burrowl 的全部状态（记忆、会话、Run 登记、配置）；**工作目录**是用户和 AI 放原始数据与代码的地方，原件不复制进数据目录。
- 记录引用原件只经来源登记（SRC- ID + 登记时指纹），从不在记录里写路径。所以原件搬家只需改登记，固定引用不变。
- 工作目录登记为**工作根**（ROOT-…）；其中的来源记为"根 + 相对路径"。MCP 以 `--work-dir` 启动时，登记该目录下的文件会自动建根。

```text
登记 sources.register(绝对路径)：
    在数据目录内 → 改写为相对数据目录的路径（按对象检查访问权限）
    落在已有工作根下 → {root, 相对路径}；否则落在本次工作目录下 → 自动建根
    其他 → 绝对路径
    记录登记时 sha256；同 ID 只能幂等重复，内容变化用新 ID
目录搬家 sources.relocate_root(根, 新目录)：新目录下每个已登记文件指纹一致才改根；否则拒绝
打包 burrowl backup：数据目录逐文件 + 外部已登记原件（只打包与登记指纹一致的；变化或缺失的列入清单）
恢复 burrowl restore 包 新目录 [--map 根ID=新目录 | 旧前缀=新前缀]：
    校验全部指纹 → 为每个根/绝对路径来源决定去向：
        有映射 → 新位置；原位置仍是同一批文件 → 不动；否则 → <新目录名>-work/<根>/
    目标处已有不同内容的文件 → 整体中止，什么都不写
    写数据文件和原件 → 改写新数据目录的来源登记（relocated_from 留痕）
```

实现：`storage/sources.py`、`portable/bundle.py`、`interfaces/cli.py`（`connect`、`mcp`、`backup`、`restore`）。
