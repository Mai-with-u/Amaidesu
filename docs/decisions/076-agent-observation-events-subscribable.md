# ADR-076：观测面事件允许 Agent 域订阅

状态：已采纳
日期：2026-11-07
实现提交：实现与本 ADR 同笔交付（含 `test_event_flow_constraints.py` 的白名单条目），定位：`git log -- docs/decisions/076-agent-observation-events-subscribable.md`

## 背景

事件流向约束（`tests/architecture/test_event_flow_constraints.py`）规定 Agent 域只允许订阅输入面与基础设施事件（room / live / game / tool.result / core.startup），`agent.*` 整族作为决策输出事件被禁止 Agent 域订阅——存在的理由是防止 Agent 之间耦合彼此的内部决策过程。

`agent.replied` 在事件命名（`docs/architecture/event-naming.md`）中定义为**观测面**：游戏 Agent 每步响应正文（含中间工具调用步骤）的唯一观测通道，本身不承载唤醒语义。origin/dev 的【游戏那边此刻】功能（参考段末尾给主播看游戏 Agent 最新一步的话）让编排方主播 Agent 订阅了它，被 pre-push 门禁的架构契约测试拦下。

功能意图本身在案：游戏 Agent 的中间进展对主播不可见是场次 56-57 复盘立案的问题（"对账治说了假的，可见性治看不见真的"），本订阅正是该方向的实现。

## 决策

订阅约束从"按事件名前缀一刀切"细化为"按事件性质分"：

- **观测面事件**（当前即 `agent.replied`）允许 Agent 域订阅——编排方读执行方的公开观测，不构成对内部决策的耦合；
- **决策内容事件**（`planner.decision`、`rundown.changed`、`task.changed` 等流程/任务事件）继续禁止 Agent 域订阅。

白名单落在 `test_event_flow_constraints.py` 的 `AGENT_ALLOWED_EVENTS`，此后新增观测面事件时须显式决定是否进白名单。

## 替代方案

- **改渠道转发**（经 `game.*` / `tool.result.*`）：游戏 Agent 的中间推理话既不是游戏事件也不是工具结果，语义错位且要新设计事件管线，成本与收益不成比例。拒绝。
- **撤功能**：删除订阅即砍掉已在案的可见性方向的第一块实现，且远端该提交已并入。拒绝。

## 后果

- Agent 之间允许通过观测面单向获取对方的中间产出：耦合面是"读对方公开观测"，读取方不得据此唤醒自身决策循环之外的反向控制。
- 白名单是显式清单而非前缀通配——新增观测面事件默认不进，进则须连同理由改清单。
- 风险边界：观测面若开始泄漏决策细节（如完整推理链），本决策须重新评估收紧。
