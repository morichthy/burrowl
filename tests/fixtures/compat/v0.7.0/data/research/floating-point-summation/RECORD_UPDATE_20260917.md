# 浮点记录独立性修订（2026-09-17）

审阅者：Codex AI。本轮仅维护 `RES-FLOATING-POINT-SUMMATION` 公开记忆，不重跑原实验，不提交科学确认。所有写入通过 `memory.api.dispatch` 的公开 validate-draft / commit / inspect / document 等动作；`.local` 下脚本只构造请求与留存回执。

## 已读基线与问题

- 起点 HEAD：`COM-1ded0a16-df08-4c51-b3ee-ee9988569d9c`，generation 14；公开 `baseline.json` 交付27条当前记录。
- 已读4个L1、5个L2、1个L3、1个L4、12个文稿章节与2个文稿；另有goal/checkpoint用于续接。
- 已用 outline / document / document-impact 读取两篇r1独立文稿，完整组装成功，基线变化和未覆盖技术单元均为空；分块正文实际读完，未把截断JSON当作全文。
- L2基线虽然已有结构字段，但正文只约数百字，依赖“第一轮”“三项”“四种方法”等外部背景；不就地解释输入、基准或为何选择该实验。L3只给结论与限制，缺实际输入、可操作核对步骤和决定性反例。
- 三个实验L1依赖“独立方法章”；完整方法L1已具备变量、递推、环境、基准及边界，保留r1。L4结果有计数却欠输入定义和判据。
- 原L0通过受控文件登记 `memory_level=L0` 和 `trace_only` 保存，没有MEM source卡。三份原结果登记说明仅为“用户授权新研究的实际计算结果”；本轮新增original_link说明卡，解释出处、输入、字段与范围，不复制原数组或改变原件。

## 处理与独立性自查

| 记录 | 自查要点 |
|---|---|
| L0 第一轮结果说明 | 唯一原件/指纹入口；1000单位增量的3组输入；6个输出；结果字段与精确误差解释；有限范围 |
| L0 第二轮结果说明 | 三项全部6排列、参考和1；4种方法24输出；字段和原件唯一性；无一般性推断 |
| L0 第三轮结果说明 | 30项/30排列、参考和10；120输出；种子不是固定数组；失败率不可外推 |
| L1 共同方法 | 已有完整变量、精确有理数基准、三种显式递推及fsum边界；沿用r1 |
| L1 第一轮实验 | 本条新增问题背景、binary64/环境、误差判据和方法释义；原输入表、轨迹、图与结果保留 |
| L1 第二轮实验 | 同上；保留Kahan补偿−1再次舍入与Neumaier末尾校正的逐步状态、6排列结果 |
| L1 第三轮实验 | 同上；保留30项构造、完整采样配方、误差分布与2图；不以种子替代固定数组 |
| L2 第一轮经过 | 具体问题→为何先隔离顺序→3组观察→小量丢失→转向三项补偿比较 |
| L2 第二轮经过 | 重述必要前轮背景→为何缩为三项→6排列与关键轨迹→补偿仍舍入→扩大同类结构 |
| L2 第三轮经过 | 重述三项反例→保持尺度增加项数/排列→成功与失败均保留→局部结论和停止边界 |
| L2 首次复核失败 | 明确要复核什么→数值输出与绘图导入分别判断→退出1不冒充成功→新Run重试条件 |
| L2 独立复试成功 | 重述首次失败→固定输入新Run→150输出与111轨迹各自组成→复算一致不等于人工确认 |
| L3 经验 | 定义适用问题/输入/误差；可执行验证步骤；决定性抵消反例；三轮计数依据；迁移/禁止外推条件 |
| L4 概览 | 背景与方法定义；三轮输入/结果/转折；失败与复试；当前阶段、确认边界、待测问题和固定入口 |
| 双文稿 | 完整过程保留细节与图；简版维持简要范围；两者同步固定技术单元及本轮独立性说明，全文回读 |

所有正文均标明2026-09-17为事后整理时间，历史运行日期不改；原始文件、实验结果和科学复核状态不改。150输出=6+24+120；111显式轨迹=3+18+90，不能把39组全部乘3，也不能声称fsum有内部轨迹。

## 保存/回读与边界

- 第一批8条修订公开预检通过，追加COM-9f9b3323-5ab8-4a32-9ffc-64266842ae22，generation15；3个实验L1 r3→r4，5个L2 r2→r3。公开inspect回读与全部草案字段完全一致（`content-readback.json`），FTS与向量索引均indexed。
- 首次沙箱commit因`memory/write.lock`权限失败，没有提交；用户已授权记录修订的同一命令经sandbox升级成功。没有绕过锁或更改历史。
- 本任务开始前已有3份历史commit文件工作树修改（COM-3a8…/MEM-58fd…、COM-9aac…/MEM-4acc…、COM-ec5…/MEM-58fd…），由主Agent基线确认，不属于本轮，未恢复或覆盖。
- 最终规范记录与双文稿状态见下表和回执；UI可读性由主任务另行现场验收，不把AI内容自查写成人工确认。

## 保留固定旧版的逐项审查

两份最终文稿均已通过公开 document 全文读取，组装完整、没有缺失、四个技术单元全部覆盖。递归 document-impact 合计返回132处 NEW_REVISION、18处 DOCUMENT_BASIS_MISMATCH；这是沿不同引用路径重复报告，并非150个独立错误。涉及17个不同的旧记录版本，均已核对，保留如下：

- 三个实验L1的r1/r3：实验输入、数字、轨迹及适用限制与当前r4一致；当前版本补就地背景。文稿直接组装的是r4，旧版来自研究经过保留的历史依据。
- 五条研究经过的r1：前三条记录历史观察与后续选择，后两条保存绘图失败和复试成功。旧失败仍为退出1，旧复试仍为150个输出/111组显式轨迹，与当前正文一致；旧版本保持其历史用途。
- 五条研究经过的r3和L3的r5：完整内容已经本轮实际阅读，后续版本只整理来源顺序、编号与末尾阅读指引，未改变研究结果或建议边界。

为核对上述历史关系，公开expand分两批完整展开8条r1（第一批受每阶段6条上限限制，第二批补齐失败/复试）。两批合计没有缺失或未完整读取项；回执见`.local/record-independence/retained-basis*.result.json`。18处技术依据差异提示来自这些递归历史关系，未通过重写历史消除；不宣称自动影响提示已经清零。当前内容同步结论仅针对实际审读范围，属于AI自查，不构成科学确认。

控制台独立验收由UI子任务完成：最终generation19共12页真实浏览器快照无错误、无横向溢出，4张图解码成功；L2/L3/L4正文不重复，L3作者编号与固定版本链接一致。正式截图和检查回执存于主任务Run的`ui-acceptance/final`。

## 最终固定版本与入口

最终HEAD：`COM-fcaee2c0-43b4-4cd3-972d-96f508dde638`，generation 19。索引：FTS `indexed`，向量 `indexed`。没有新增科学复核操作。

| 层级 | 固定记录入口 | 记录ID | 修订 |
|---|---|---|---|
| 文稿 | [浮点求和的数值稳定性：简版研究报告](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-71eea162-eaa4-442e-9867-c676781d3109/records/MEM-21132a5c-2477-50ea-ad28-2244de823f4f.json) | MEM-21132a5c-2477-50ea-ad28-2244de823f4f | r2 |
| L1 | [浮点求和方法：精确基准、递推与适用条件](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-9aac6b89-542c-4e2f-9a8d-01a6a0cfee63/records/MEM-4acc73bb-59c7-560d-9415-fd42e9dcaa2d.json) | MEM-4acc73bb-59c7-560d-9415-fd42e9dcaa2d | r1 |
| L3 | [抵消求和如何验证：精确基准、失败轨迹与算法选择边界](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-3967e6bd-7ea4-4642-945c-ab122a0086d8/records/MEM-58fd845a-05a5-516c-8bbe-c88d887cbb91.json) | MEM-58fd845a-05a5-516c-8bbe-c88d887cbb91 | r6 |
| L2 | [三项反例扩展到30项：固定排列复验的研究经过](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-3967e6bd-7ea4-4642-945c-ab122a0086d8/records/MEM-77b35d77-f6c1-5d05-80a1-7c3b9d5e9d09.json) | MEM-77b35d77-f6c1-5d05-80a1-7c3b9d5e9d09 | r4 |
| L0 | [第一轮原始结果：单位增量顺序与抵消](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-f3d4b6f5-6420-419c-91ae-c93ab7a1b7e4/records/MEM-7b98f79a-da6f-5e55-b1dd-85f36ccfd11a.json) | MEM-7b98f79a-da6f-5e55-b1dd-85f36ccfd11a | r1 |
| L0 | [第二轮原始结果：三项抵消的全部排列](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-f3d4b6f5-6420-419c-91ae-c93ab7a1b7e4/records/MEM-865addc3-4285-5b10-a9b1-0e41d7af5c59.json) | MEM-865addc3-4285-5b10-a9b1-0e41d7af5c59 | r1 |
| L1 | [探索03：反例扩大到30项和30种排列后是否仍成立？](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-9f9b3323-5ab8-4a32-9ffc-64266842ae22/records/MEM-96f79923-f023-509e-a002-40f2d5a4fca1.json) | MEM-96f79923-f023-509e-a002-40f2d5a4fca1 | r4 |
| L4 | [浮点求和数值稳定性：整体概览](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-fcaee2c0-43b4-4cd3-972d-96f508dde638/records/MEM-a8226b54-a4cd-545a-8f22-422c2f513bfc.json) | MEM-a8226b54-a4cd-545a-8f22-422c2f513bfc | r6 |
| 文稿 | [浮点求和的数值稳定性：完整研究过程](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-71eea162-eaa4-442e-9867-c676781d3109/records/MEM-ab9db095-2545-594c-9e94-f72bab43c464.json) | MEM-ab9db095-2545-594c-9e94-f72bab43c464 | r2 |
| L1 | [探索02：Kahan补偿为何仍会在三项抵消中失败？](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-9f9b3323-5ab8-4a32-9ffc-64266842ae22/records/MEM-c4a61c00-1bf1-59b3-a65e-0fa4847db2e1.json) | MEM-c4a61c00-1bf1-59b3-a65e-0fa4847db2e1 | r4 |
| L1 | [探索01：相同数量的小量，为什么会因顺序不同而丢失？](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-9f9b3323-5ab8-4a32-9ffc-64266842ae22/records/MEM-c52df8fb-ca9e-53a7-a49b-342592884a50.json) | MEM-c52df8fb-ca9e-53a7-a49b-342592884a50 | r4 |
| L2 | [数值复核输出已产生但绘图失败：首次复核经过](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-fcaee2c0-43b4-4cd3-972d-96f508dde638/records/MEM-d117fbde-ec6b-5168-9a40-f4a25459aa2a.json) | MEM-d117fbde-ec6b-5168-9a40-f4a25459aa2a | r5 |
| L2 | [从单位增量丢失到补偿比较：第一轮研究经过](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-3967e6bd-7ea4-4642-945c-ab122a0086d8/records/MEM-eae11b05-09a7-5f4e-a2fd-65fc1ec6000e.json) | MEM-eae11b05-09a7-5f4e-a2fd-65fc1ec6000e | r4 |
| L2 | [补偿为何仍会丢失：三项抵消的研究经过](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-3967e6bd-7ea4-4642-945c-ab122a0086d8/records/MEM-f1b731ce-9e87-502b-a9cf-a93c3f47c5f6.json) | MEM-f1b731ce-9e87-502b-a9cf-a93c3f47c5f6 | r4 |
| L0 | [第三轮原始结果：30项固定排列](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-f3d4b6f5-6420-419c-91ae-c93ab7a1b7e4/records/MEM-fdd0a731-86bd-5eb0-8c19-92318afb3d25.json) | MEM-fdd0a731-86bd-5eb0-8c19-92318afb3d25 | r1 |
| L2 | [固定输入复算一致：失败后独立复试的研究经过](D:/共享桌面/A_work/A_work_auto_flow/research/floating-point-summation/memory/commits/COM-fcaee2c0-43b4-4cd3-972d-96f508dde638/records/MEM-ff3cf263-e13e-51b3-8a4f-6c7574040f10.json) | MEM-ff3cf263-e13e-51b3-8a4f-6c7574040f10 | r5 |

保留旧固定依据的理由：L2核心内容依据已完整阅读的实验L1 r3；r4仅补独立前提，原实验数字、算法轨迹和限制不变。部分章节/经验指向L2 r3/r4或L3 r5，后续修订仅修正来源编号/排列与末尾指引，科学内容不变，因而无须循环追新。两份文稿技术单元使用本轮L1 r4，概览固定到本轮内容与新文稿。

本阶段完整成果已同步（范围：浮点研究当前L0–L4与完整过程/简版两份文稿；AI一致性自查）。原始实验没有重新执行；未覆盖的新输入域、性能、跨平台和人工科学审阅继续保留。
