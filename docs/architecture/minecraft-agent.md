# MinecraftAgent 设计

Minecraft 游戏 Agent（AI 玩家）的架构设计。定位：事件驱动的 ReAct Agent——用 MCP 工具玩 Minecraft，主播 Agent 是它的用户。

## 驱动原则

- 只有主播 Agent 自我驱动；游戏 Agent 命令驱动（类 Code Agent）——收到命令启动任务内有界循环，完成即停、空闲零消耗
- 因有自身状态与任务内自主决策，游戏 Agent 仍是 Agent 而非工具（三分判据见 [v2-architecture.md](v2-architecture.md)）

## 核心意象

**MinecraftAgent = 一个用 MCP 工具玩 Minecraft 的普通 ReAct Agent。**

- 系统提示词 + 工具列表 = 全部"编程"，不发明任何特殊协议
- 主播 Agent 是它的用户：发指令（已退役 `minecraft_send_prompt`；跨 Agent 派活走框架委派 `framework_delegate`）/读工作文档（`minecraft_get_work_log`）/收上报（`game.report`）
- execute 受理异步是唯一系统特判：`maicraft_execute` 返回受理回执（task_id），真实执行由 Mod 后台 tick 驱动（分钟级）——系统登记 handoff 跟踪，事件驱动唤醒（见下节），LLM 不用推理步数轮询
- LLM 可一次返回多个 tool_calls（批量请求 → 串行执行 → 批量作为观察返回，标准 function calling 循环）

## 任务生命周期（事件驱动 ReAct）

```
空闲（事件挂起，零消耗，无 LLM/无 MCP 调用）
  │ 主播调 minecraft_send_prompt("建一座房子")
  ▼
消息入队 → 唤醒
  ▼
任务批次（每步 = 一次 LLM 推理）：
  ├─ flush 新消息（主播提示词 / 系统注入的任务快照）→ user 消息
  ├─ 规整对话历史（旧观察 → 占位符，保留最近 N 条）
  ├─ LLM 推理（系统提示词 + 对话历史 + 工具列表）→ tool_calls（可多个）
  ├─ 串行执行（局部工具直接落状态；其余经 ToolRegistry 透传 = MCP 调用）
  │    └─ execute 受理回执（accepted=true + task_id）→ 登记 handoff 跟踪
  ├─ 工具结果作为观察作为观察返回（OpenAI tool role + tool_call_id 关联）
  └─ 批次终止语义（五条，全部系统可判定，见下节）
  → 回空闲（todo/notebook/reports 保留；handoff 跟踪跨批次持续）
```

暂停语义：平台 pause 在步骤间与工具调用间挂起（不打断当前执行中的工具调用），resume 后继续。

### 批次终止语义

| # | 情形 | 行为 |
|---|------|------|
| 1 | LLM 调 `minecraft_report(kind=delivery)` | 停止；工具内交付门禁：有未决 handoff → 拒绝并返回错误观察（LLM 自纠） |
| 2 | LLM 调 `minecraft_report(kind=escalation)` | 停止，静默等主播委派 |
| 3 | 自然终止，无 report、无未决 handoff | 系统兜底把终止文本包装为一次 delivery（主播必收到一次且仅一次交付） |
| 4 | 自然终止，有未决 handoff | 静默让出回合，等 handoff 唤醒 |
| 5 | 步数超 max_steps | `game.attention_required` 挂起 |

### handoff 跟踪（受理 → 唤醒）

execute 受理 ≠ 完成：等待期 LLM 自由行动（推进其他 todo / 记笔记 / 响应主播），系统负责把后台任务的真实进展送回来。

- **订阅通知**：handoff 登记时订阅 MaiCraft attention 资源（`maicraft://attention`，标准 MCP resources/subscribe，通道能力见 `McpClient.subscribe_resource`）；handoff 清空即退订
- **周期兜底**：订阅通知是提示（advisory，单槽合并、可丢）——`execute_poll_interval_ms` 到点也核实一次（防丢通知/断连）
- **事实核实**：通知/到点 → `maicraft_task(action="get")` 核实快照；**状态真迁移才注入**消息队列 + 唤醒 worker（虚假/无关通知 = 继续睡，LLM 零消耗）
- **wait_timeout**：`execute_wait_timeout_ms` 长期无进展 → 注入告警消息（不杀任务，deadline 顺延），LLM 自行决定后续
- 终态（success/failed/timeout/cancelled）注入后移除跟踪；决策点（waiting_for_decision）/暂停注入后保留跟踪（LLM 用 `maicraft_task(action="answer")` 应答后任务恢复后台跑）

## 工具契约

**注册名 = `<Provider名字>_<工具名字>`**，分隔符 `_`（满足 LLM function calling 工具名字符集约束）。Provider 名全局唯一、用全名（`minecraft` 禁缩写）；工具名 Provider 内唯一、语义化。前缀由模块声明（provider 值）、ToolRegistry 一处拼接——工具名里不手写前缀。

**决策工具列表**（LLM 可见，注册名）：

| 工具 | 说明 |
|---|---|
| `minecraft_todo` | 待办文档（read/write 全量读写，无 id）。任务分解与推进由 LLM 自主决策 |
| `minecraft_notebook` | 工作笔记（read/write）。持久记忆：对话历史会压缩、笔记不会——重要发现写这里 |
| `minecraft_report` | 上报通道（玩家→主播唯一发声出口）：delivery 交付总结 / escalation 升级决策 |
| `maicraft_perceive` / `maicraft_execute` / `maicraft_task` 等 | registry 动态发现的 MCP 工具（每任务重新拉取）；参数按 Mod 定义填写 |

**对外工具**（经 ToolRegistry 注册、主播工具列表可见，不进玩家 LLM 工具列表）：
- `framework_delegate`：跨 Agent 委派通道——把工作交给另一 Agent（指令只当自然语言，不给步骤）；BaseAgent 默认拒收，minecraft 实现接收入口（指令入队带任务号 + 唤醒）。`minecraft_send_prompt` 已退役（职能并入接收委派入口），跨 Agent 派活的发送侧走框架委派而非 mcp 工具
- `minecraft_get_work_log`：工作文档读服务——只读返回 `{todo, notebook, recent_reports}` 三元组；本工具不查异步任务记录表，查任务进度用 `framework_task_status`（跨 Agent 委派 + 回执型工具的当前状态与快照）

## 事件契约（确定性系统事件，无 LLM 自觉汇报）

| 事件 | 触发 |
|---|---|
| `game.report` | LLM 调 `minecraft_report`（delivery/escalation）或批次终止系统兜底交付；kind 见 `GamePayload.report_kind` |
| `game.attention_required` | 步数超上限挂起 |
| `game.error` | 工具执行异常 / LLM 调用失败 / 无 LLM fail-fast |

事件 payload 复用 `GamePayload`（`game="minecraft"`）；上报同时进内存 `recent_reports`（状态查询数据源，保留最近 10 条）。`game.milestone` 不再由本 Agent 发射（todo-diff 自动里程碑已移除，防主播叙事刷屏）——剧情推进语义留给 text_adv。

## 对话管理与压缩

- 消息累积：任务内多轮，经已有 `chat_messages(messages, tools=...)`（OpenAI 格式）；工具结果以 `tool` role + `tool_call_id` 关联作为观察返回
- 压缩分级：① 旧观察规整（发送前把超出保留条数的 tool 消息替换为占位符）② notebook 承载关键信息（提示词引导"历史可能压缩笔记不会"）③ 摘要兜底（后置，真实长任务数据出现前不做）
- 流式不做：决策 Agent 非聊天 Agent，文本断续；对外叙事经事件→主播侧已有流式表达；WebUI 观察走事件快照

## 配置

```toml
[agents.minecraft]
max_steps = 50                    # 单任务 ReAct 循环最大步数（超出挂起上报，防失控）
execute_poll_interval_ms = 2000   # handoff 周期兜底核实间隔
execute_wait_timeout_ms = 1800000 # 后台任务单轮 wait_timeout 上限（告警不杀任务）

[agents.minecraft.mcp]            # Agent 私有 MCP（位置即归属，owner_agent="minecraft"）
enabled = true
url = "http://127.0.0.1:8766/mcp"
```

配版本同步规则：配置结构变更升该文件 `[meta].version` 并保证漂移写回（见 ADR-014）。

## 解耦边界

- agent 领域核心零 maicraft 接口知识：工具列表经 registry 动态发现（任务查询工具按原始名后缀匹配，注册名前缀形态不定）；MCP server 连接由通道层路由（`McpToolProvider` 绑定 server 的 client），agent 不感知
- 工具失败作为错误观察作为观察返回 LLM（ReAct 标准，LLM 自调整）；连续失败由 max_steps 兜底
- 内容特有逻辑内聚 `src/agents/minecraft/` 包（加内容=加包+配置，框架零改动）

## 相关文档

- [架构总览](overview.md) — 组件图与目录结构
- [v2.0.0 架构叙事](v2-architecture.md) — Agent/Tool 判据推导
- [事件系统](event-system.md) — game.* 事件语义单一事实源
- [数据流规则](data-flow.md) — 事件流约束
- [组件开发指南](../development/component-guide.md) — 游戏 Agent 范式
