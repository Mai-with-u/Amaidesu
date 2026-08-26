# ADR-004：OutputHandlerManager 直接调度 Handler

- 状态：已废弃（2026-08-25，v2.0.0 移除三阶段架构与 OutputHandlerManager，决策出口改为主播 Agent 调用 reply 工具；本文保留作历史记录，继任决策见 [ADR-005](005-v2-agent-tool-architecture.md)）
- 日期：2026-07-31
- 实现提交：`f9078e65dff65d61efe0daa6e83589ba95a8e409`（refactor(output): OutputHandlerManager 改为直接调度 Handler，移除事件样板代码）

## 背景（Context）

Amaidesu 采用 Input、Decision、Output 三阶段架构。Input 阶段由管理器直接迭代并调用 Collector，Decision 阶段由管理器直接调用 Decider 的决策能力。Output 阶段的调度机制却与前两者不一致：OutputHandlerManager 先发布 `DISPATCHED` 事件，Handler 通过订阅该事件获得意图，完成后再发布 `COMPLETED` 事件，由 Manager 聚合完成状态，最后发布 `FINISHED` 事件。

这套两段事件调度使 Handler 需要承担订阅、取消订阅和完成状态自报告等通用职责，也使新增 Handler 可能因遗漏 `COMPLETED` 报告而依赖超时兜底。三阶段采用不同的调用方式，增加了 Output 阶段的理解和维护成本。与此同时，`render_timeout_ms` 没有形成实际的渲染超时约束。

`DISPATCHED` 仍被 Broadcaster 和 EventRecorder 等监控组件使用。`FINISHED` 仍有 MainosabaCollector、LiveStreamSimulator（模拟直播间，原 SimulatedLiveStream）、EventHistoryService 和 Dashboard 四个下游消费者，因此两者的语义和可用性不能被一并移除。

## 决策（Decision）

OutputHandlerManager 直接调用每个已启用 Handler 的 `handle(intent)`，并由 Manager 统一等待所有 Handler 完成、处理完成状态和发布最终完成通知。

`DISPATCHED` 降级为监控信号，不再承担 Handler 调度职责。`COMPLETED` 的完成跟踪改由 Manager 内部管理，不再要求 Handler 通过事件向 Manager 自报告。`FINISHED` 保留不变，继续作为一个意图的所有输出处理完成后的阶段通知。

该决策使 Output 阶段与 Input、Decision 阶段保持一致，同时保留跨组件监控和下游收尾所需的事件边界。

## 替代方案（Alternatives）

### 维持现状并增加测试

拒绝。增加测试可以降低现有机制的回归风险，但不能消除三阶段调度方式不一致、Handler 样板职责过多以及 Handler 忘记报告完成状态的结构性问题。`render_timeout_ms` 也不会因此自然成为有效的超时约束。

### 装饰器

拒绝。通过装饰器包装 Handler 的订阅和完成报告，只能隐藏现有事件流程，无法改变 Handler 依赖事件调度的基本模型。装饰器还会增加隐式行为，使生命周期和完成责任更难从 Handler 接口中直接理解。

### Mixin

拒绝。Mixin 可以复用订阅、取消订阅和完成报告代码，但仍然保留两段事件调度及其状态耦合。它解决的是代码重复，不是 Output 阶段调度模型与其他阶段不一致的问题。

### 直接调度

采纳。由 OutputHandlerManager 直接调用 Handler，并集中管理并行处理、完成跟踪和最终通知，可以移除 Handler 的通用事件样板，统一三阶段的调度模型，同时保留 `DISPATCHED` 监控信号和 `FINISHED` 下游通知。

## 后果（Consequences）

### 正面影响

- Handler 代码简化，不再需要订阅、取消订阅或自报告完成状态。
- 新增 Handler 只需实现 `handle(intent)`，不需要了解 Output 调度事件的内部协作方式。
- Handler 忘记发布 `COMPLETED` 导致完成聚合失效的风险被消除。
- `render_timeout_ms` 成为实际生效的渲染超时约束。
- Output 阶段的调度方式与 Input、Decision 阶段一致，Manager 的职责边界更清晰。
- `FINISHED` 保留，现有依赖所有输出完成通知的组件不需要改变其语义。

### 需要接受的影响

- `DISPATCHED` 不再表示 Handler 已经通过事件收到意图，而只表示 Manager 已开始处理该意图，因此监控组件应按监控信号理解它。
- Handler 的完成聚合集中在 OutputHandlerManager，Manager 需要继续负责并行处理、异常隔离和最终完成通知。
- 直接调度减少了 Handler 与 EventBus 之间的调度耦合，但 Handler 间仍可使用既有的专用事件进行业务通信。

---

*2026-08-20 更新注记：本文正文为历史决策记录，不回改。`MainosabaCollector` 已更名为 `TextAdvGameCollector`（采集器服务文字冒险/视觉小说类游戏而非单一游戏），注册名/配置段同步改为 `text_adv_game`，语义不变。*
