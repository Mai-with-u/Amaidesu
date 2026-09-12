# ADR-011：Rundown 流程单取代 Agenda 编排子系统

- 状态：已实现（已并入 v2.0.0）
- 日期：2026-09-09
- 实现提交：
  - `f535fbf1`（docs(rundown): 流程单机制规范与 ADR-011）
  - `5eb0b9b5`（feat(dashboard): 编排页与控制台对齐流程单契约）
  - `227fc947`（docs: 架构文档对齐流程单重设计）
  - `20f3563a`（feat(rundown): 流程单子系统落地并取代 Agenda——主体 55 文件，快进并入 v2.0.0）

## 背景（Context）

v2 的 Agenda 子系统（`src/agents/streamer/agenda/`，五模块：数据契约 / 加载器 / 状态机 / 调度循环 / 存储适配）在实现审查与第一性原理分析中暴露三类问题：

1. **功能断线**：`AgendaIdle._tick` 的空转判定恒真导致时间驱动推进成为死代码（"零观众按计划直播"这一存在理由不成立）；`need_more_time` 延长写入的 `_extended_extra_ms` 无任何消费者（no-op）；持久化链路（`AgendaStore` 协议 / `SQLiteAgendaStore` / `restore_runtime` / 两张表）从未接线。
2. **设计耦合**：Planner（战术）与 Agenda（战略）双向纠缠——`DecisionPlan` 被塞入议程控制字段，战略层经 `note_plan_assessment` 回流通道操控状态机，"环节何时结束"的答案散布在 duration / min_duration / 评估延长 / 手动覆盖标志 / 调度 tick 五处；`AgendaIdle` 直接穿透 `AgendaState` 私有成员改状态，分层是纸面的。
3. **概念积压**：理解子系统需持有 15+ 概念（对比框架核心仅 3 个），并存在三套同物异形的数据模型（AgendaSegment / AgendaItem / ExpandedSegment）、无人置位的 `LOADING` 状态、无发布者的旧契约事件。

本质重问：这个组件的根本目的是什么？答案收敛为**备忘录 + 闹钟**——导演预写的环节清单（参考材料）叠加超时提醒（时间压力）。它不是执行器。而 AGENTS.md 主体性判据早已裁决：主播 Agent 是自驱动主体，"何时切换环节"应是它自己的决定。v2 用外部调度器推着 Agent 走，是对主体性判据的违反；评估回流与覆盖标志协议都是在缝合"两个大脑"的刻意发明。

术语考证（2026-09-09）：英文直播制作圈称此物 **Run of Show**（同义词 rundown），日语圈称 **進行台本・進行表（キューシート）**，中文圈称**分钟级脚本/排期表**；三个圈一致区分"逐字稿"与"环节时间轴"，本子系统属于后者——话术由 Agent 即兴，不预生成。

## 决策（Decision）

以 **Rundown（流程单）** 子系统整体取代 Agenda：

1. **推进权归 Agent**：新增 `RundownControlTool`（next / goto / pause / resume），约束（`min_duration_ms`、id 存在性）在状态变更边界以结构化拒绝执行，拒绝原因进入 Agent 上下文自纠。
2. **四件套结构**：数据契约 + 游标状态（四个可变字段，status 为派生值）+ 工具 + 超时闹钟（并入 ProactiveTrigger 的一个触发源，只提醒不执法）。无调度循环。
3. **存储**：SQLite `rundowns` 单表（segments_json 整体读写）；运行进度不持久化；TOML 格式移除，流程单经 WebUI 建立；库为空/未选单时使用内置 `DEFAULT_RUNDOWN`（初次直播·自我介绍）。
4. **唯一事件** `rundown.changed`；`agenda.update` / `planner.checkpoint` / `AgendaItem` 载荷删除。
5. **配置收敛**：`agenda_*` 六字段（及 v1 `outline_*`）剥离为单字段 `rundown_id`（空 = 默认流程单）。
6. **Planner 契约收缩**：`DecisionPlan` 移除 `may_advance` / `need_more_time` / `branch_id`，与提示词同批更新。

## 替代方案（Alternatives）

- **修复 v2 三处断线**：救活功能但救不掉双规划器耦合与概念税，病树喷药。拒绝。
- **保留调度器并补硬切换**：维持"外部推着走"的木偶模型，与主体性判据冲突；调度器与 Agent 的推进权竞争正是覆盖标志协议的根源。拒绝。
- **LLM 逐环节预扩展内容**（v2 ExpandedSegment）：其 fallback 恰好是 `task_description`，证明不扩展系统照样工作；投机性预生成换来一整条管道与缓存失效规则。拒绝，环节内容交由决策链现场生成。
- **命名**：Show（太宽泛）、Outline（缺闹钟维度）、Todo（被编程/Agent 生态占用，可检索性永久污染）、Script（行业语义偏逐字稿，粒度错位）、Agenda（语义可用但"会议"联想需每日压制，且背负 v2 失败史）。选定 **Rundown**：Run of Show 的行业同义词，词义原生携带"环节 + 时长 + 跟播执行"，生态零占用；中文权威名"流程单"。

## 后果（Consequences）

收益：

- 概念 15+ 收敛到 5（Rundown / Segment / State / Tool / 一个事件）；配置 6 字段收敛到 1；约 -1500 行。
- 三处断线随子系统重写自然消失；零观众场景由闹钟唤醒真正可用。
- 推进决策收敛到一处（工具调用 + 边界校验），可测、可解释、Dashboard 可展示。

代价与持续关注：

- 推进可靠性依赖 LLM 的工具调用纪律：以情境时间压力 + 超时闹钟兜底；若实测仍不足，闹钟处加 `hard_cutoff` 硬切换策略（预留未实现）。
- `DecisionPlan` 契约收缩与 Planner 提示词必须同批更新（`extra="forbid"` 下不同批即解析失败）。
- `agenda_plan` / `agenda_runtime` 两表 DROP 属破坏性 schema 迁移：因存储链路从未接线、表保证为空，零数据损失。
- 命名切换涉及事件表、组件清单、配置 Schema、Dashboard API 的批量一致性更新；`rundown` 与"流程单"的等价关系以本文档为准，禁止第三种叫法。
