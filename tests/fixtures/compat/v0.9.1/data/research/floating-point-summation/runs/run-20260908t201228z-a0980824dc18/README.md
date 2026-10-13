# 探索02：补偿求和的抵消反例

Run：`RUN-20260908T201228Z-A0980824DC18`

## 问题

Kahan补偿能否修复三个数的所有排列？Neumaier与fsum如何？

## 实际结果

| 方法 | 精确命中 / 样本数 | 最大绝对误差 |
|---|---:|---:|
| sequential | 2/6 | 1 |
| kahan | 2/6 | 1 |
| neumaier | 6/6 | 0 |
| math.fsum | 6/6 | 0 |

三项抵消输入揭示Kahan实现仍存在排列反例；下一轮扩大排列数量，检查该限制是否继续出现。

## 如何复查

[完整输入](inputs.json)、[逐样本结果](results.json)、[实际脚本](reproduce.py)、[执行命令与退出码](execution.json)。输入和脚本哈希登记在run.json。

```powershell
.\automation\python.ps1 runs/run-20260908t201228z-a0980824dc18/reproduce.py --input runs/run-20260908t201228z-a0980824dc18/inputs.json --output .local/summation-round-2-recheck.json
```

原结果不会覆盖；复查需使用尚不存在的输出路径。仅用标准库，Windows x64接收端可用项目已有Python复算。误差以实际浮点输入的精确和为基准，无量纲；没有运行时间指标或真实业务验证。执行成功不意味着每个方法都准确；结论尚未人工复核。
