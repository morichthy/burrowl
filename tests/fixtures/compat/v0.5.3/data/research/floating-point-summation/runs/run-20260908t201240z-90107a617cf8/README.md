# 探索03：固定种子多排列复验

Run：`RUN-20260908T201240Z-90107A617CF8`

## 问题

前轮观察在重复抵消结构的30个固定种子排列中是否保留？

## 实际结果

| 方法 | 精确命中 / 样本数 | 最大绝对误差 |
|---|---:|---:|
| sequential | 0/30 | 10 |
| kahan | 5/30 | 10 |
| neumaier | 30/30 | 0 |
| math.fsum | 30/30 | 0 |

本轮以固定随机种子扩展排列，结果限于这30个已保存输入；不作任意浮点数据的正确性保证。

## 如何复查

[完整输入](inputs.json)、[逐样本结果](results.json)、[实际脚本](reproduce.py)、[执行命令与退出码](execution.json)。输入和脚本哈希登记在run.json。

```powershell
.\automation\python.ps1 runs/run-20260908t201240z-90107a617cf8/reproduce.py --input runs/run-20260908t201240z-90107a617cf8/inputs.json --output .local/summation-round-3-recheck.json
```

原结果不会覆盖；复查需使用尚不存在的输出路径。仅用标准库，Windows x64接收端可用项目已有Python复算。误差以实际浮点输入的精确和为基准，无量纲；没有运行时间指标或真实业务验证。执行成功不意味着每个方法都准确；结论尚未人工复核。
