# ADR-017：主播 Agent 分包按接缝抽厚簇（执行抽、调度不抽）

- 状态：已采纳（2026-09-13）
- 日期：2026-09-13
- 实现提交：`4814cc94fe8aecbccc8d8937f5804d73bdc3d265`（docs(adr): 记录主播 Agent 分包边界——`task/streamer-agent-split` 分支并入主线时的 ADR-017 落地提交）

## 背景（Context）

`streamer_agent.py` 曾是 1700+ 行的 God file：发言管线、决策编排、命令接线、统计、流程单门面全部内联。需要定拆分边界：按"进/出方向"切，还是按"内聚接缝"切；规划器抽多少；散装小簇如何处置。

## 决策（Decision）

**按接缝抽厚簇，不按阶段方向切**：发言管线（`speech_dispatcher.py`）、决策执行半（`decision_executor.py`）、命令路由（`command/router.py`）、统计（`stats.py`）、流程单呈现（`rundown/presentation.py`）各自成件；外部契约零改动。

- 规划器只抽"执行半"（拿到批次执行一轮），调度半（flush 循环、缓冲、主动触发判定、`_flush_lock`）留 Agent——二者生命周期不同：调度是 Agent 主循环的心跳，执行是可独立测试的纯入出单元；按方向切会造出输入段↔输出段的双向依赖。
- 命令与统计是散装属性簇，收成组件使 Agent 退为装配根；流程单视图/控制翻译属流程单域知识，下沉 rundown 子包。协作件一律不反向 import `streamer_agent`。

## 后果（Consequences）

Agent 构造函数组合根变宽（六件套构造注入），但每簇可独立单测；决策失败早返回分支逐行平移保住全链路测试的字面依赖。替代方案（按输入/输出方向两大段切）因循环依赖被否决。
