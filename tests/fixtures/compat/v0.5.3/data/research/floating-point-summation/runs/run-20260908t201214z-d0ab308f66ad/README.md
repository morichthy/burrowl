# 探索01：大数与小增量的顺序影响

Run：`RUN-20260908T201214Z-D0AB308F66AD`

## 问题

同一组数改变排列后，逐项求和与fsum的误差是否变化？

## 实际结果

| 方法 | 精确命中 / 样本数 | 最大绝对误差 |
|---|---:|---:|
| sequential | 1/3 | 1000 |
| math.fsum | 3/3 | 0 |

逐项累加对本组输入的排列敏感；观察结果促使下一轮比较补偿算法，不能只依赖改顺序。

## 如何复查

[完整输入](inputs.json)、[逐样本结果](results.json)、[实际脚本](reproduce.py)、[执行命令与退出码](execution.json)。输入和脚本哈希登记在run.json。

```powershell
.\automation\python.ps1 runs/run-20260908t201214z-d0ab308f66ad/reproduce.py --input runs/run-20260908t201214z-d0ab308f66ad/inputs.json --output .local/summation-round-1-recheck.json
```

原结果不会覆盖；复查需使用尚不存在的输出路径。仅用标准库，Windows x64接收端可用项目已有Python复算。误差以实际浮点输入的精确和为基准，无量纲；没有运行时间指标或真实业务验证。执行成功不意味着每个方法都准确；结论尚未人工复核。
