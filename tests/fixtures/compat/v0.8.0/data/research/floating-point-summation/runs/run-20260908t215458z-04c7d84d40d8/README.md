# 逐步误差轨迹与图表复核

Run：`RUN-20260908T215458Z-04C7D84D40D8`，归属：`RES-FLOATING-POINT-SUMMATION`。

本次新执行逐项核对旧三轮共150个方法结果与精确绝对误差，全部一致；111组显式算法轨迹保存在原始附件，math.fsum内部状态未伪造。详细阅读入口：[第一轮](../../details/round-01.md)、[第二轮](../../details/round-02.md)、[第三轮](../../details/round-03.md)。

## 原始证据

[输入版本清单](source-manifest.json) · [固定脚本](reproduce.py) · [运行回执](execution.json) · [结果与输入](outputs/trace-results.json) · [逐步运算](outputs/operation-traces.json) · [检查与绘图环境](outputs/verification.json)

## 复算

在工作区根目录使用便携Python，以新输出目录复算全部数值，不需要第三方包：

```powershell
.\automation\python.ps1 research/floating-point-summation/runs/run-20260908t215458z-04c7d84d40d8/reproduce.py --root . --manifest research/floating-point-summation/runs/run-20260908t215458z-04c7d84d40d8/source-manifest.json --output .local/summation-trace-recheck --no-plots
```

图形已随Run保存为PNG，工作台阅读无需安装绘图库。重新绘图可使用 `--plot-dependencies` 指定独立Matplotlib环境，锁定的实际包版本见verification.json；该作者环境没有写入框架运行时。

原始三轮未移动、未改写；新增计算在本研究runs/内，避免破坏先前固定引用。前一次绘图失败保留在相邻Run中。本次状态succeeded仅表示复核执行完成，研究结论仍未人工复核。
