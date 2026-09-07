# MinecraftAgent 设计

Minecraft 游戏 Agent（AI 玩家）的架构设计。定位：普通 ReAct Agent——用 MCP 工具玩 Minecraft，主播 Agent 是它的用户。

## 驱动原则

- 只有主播 Agent 自我驱动；游戏 Agent 命令驱动（类 Code Agent）——收到命令启动任务内有界循环，完成即停、空闲零消耗
- 因有自身状态与任务内自主决策，游戏 Agent 仍是 Agent 而非工具（三分判据见 [v2-architecture.md](v2-architecture.md)）

## 核心意象

**MinecraftAgent = 一个用 MCP 工具玩 Minecraft 的普通 ReAct Agent。**

- 系统提示词 + 工具面 = 全部"编程"，不发明任何特殊协议
- 主播 Agent 是它的用户：派任务（`minecraft_assign`）、看进度（`minecraft_get_state`）、被事件触发（game.*）
- MCP 串行不特殊处理——LLM 自己明白 execute 返回 task_id 后自主用 `maicraft_task` 查询/应答，系统零干预
- LLM 可一次返回多个 tool_calls（批量请求 → 串行执行 → 批量喂回，标准 function calling 循环）

## 任务生命周期（ReAct 循环）

```
空闲（事件挂起，零消耗，无 LLM/无 MCP 调用）
  │ 主播调 minecraft_assign("建一座房子")
  ▼
消息入队 → 唤醒
  ▼
任务循环（每步 = 一次 LLM 推理）：
  ├─ flush 新消息 → 追加为 user 消息（怎么吸收是 LLM 的决策——系统不硬转向）
  ├─ 规整对话历史（旧观察 → 占位符，保留最近 N 条）
  ├─ LLM 推理（系统提示词 + 对话历史 + 工具面）→ tool_calls（可多个）
  ├─ 串行执行（局部工具直接落状态；其余经 ToolRegistry 透传 = MCP 调用）
  │    └─ todo write 后 diff：pending→done 转变 → 发 game.milestone（只发一次）
  ├─ 工具结果作为观察喂回（OpenAI tool role + tool_call_id 关联）
  └─ 终止：LLM 无 tool_calls（自然终止，最终文本 → 交付里程碑）
          或步数超上限（game.attention_required 挂起）
          或进程停止 / 平台暂停
  → 清空对话历史（todo/notebook/milestones 保留），回空闲
```

暂停语义：平台 pause 在步骤间与工具调用间挂起（不打断当前执行中的工具调用），resume 后继续。

## 工具契约

**注册名 = `<Provider名字>_<工具名字>`**，分隔符 `_`（满足 LLM function calling 工具名字符集约束）。Provider 名全局唯一、用全名（`minecraft` 禁缩写）；工具名 Provider 内唯一、语义化。前缀由模块声明（provider 值）、ToolRegistry 一处拼接——工具名里不手写前缀。

**决策工具面**（LLM 可见，注册名）：

| 工具 | 说明 |
|---|---|
| `minecraft_todo` | 待办文档（read/write 全量读写，无 id）。任务分解与推进由 LLM 自主决策；完成项由 LLM 标 done（系统自动 diff 发里程碑） |
| `minecraft_notebook` | 工作笔记（read/write）。持久记忆：对话历史会压缩、笔记不会——重要发现写这里，主播可经状态查询看到 |
| `maicraft_perceive` / `maicraft_execute` / `maicraft_task` 等 | registry 动态发现的 MCP 工具（每任务开始时重新拉取，非启动快照——Mod 重连后工具面变化可见）；参数按 Mod 定义填写 |

**对外工具**（主播侧调，不进 LLM 工具面）：
- `minecraft_assign`：命令通道——纯消息投递 + 唤醒；系统不代写 todo（目标分解是 LLM 用 `minecraft_todo` 自己做的事）
- `minecraft_get_state`：状态通道——只读返回 `{todo, notebook, recent_milestones}` 三元组

## 事件契约（确定性系统事件，无 LLM 自觉汇报）

| 事件 | 触发 |
|---|---|
| `game.milestone` | ① todo 项 pending→done（diff，重写不重复）② 交付总结（LLM 终止文本） |
| `game.attention_required` | 步数超上限挂起 |
| `game.error` | 工具执行异常 / LLM 调用失败 / 无 LLM fail-fast |

事件 payload 复用 `GamePayload`（`game="minecraft"`）；里程碑同时进内存 `recent_milestones`（状态查询数据源，保留最近 10 条）。

## 对话管理与压缩

- 消息累积：任务内多轮，经已有 `chat_messages(messages, tools=...)`（OpenAI 格式）；工具结果以 `tool` role + `tool_call_id` 关联喂回
- 压缩分级：① 旧观察规整（发送前把超出保留条数的 tool 消息替换为占位符）② notebook 承载关键信息（提示词引导"历史可能压缩笔记不会"）③ 摘要兜底（后置，真实长任务数据出现前不做）
- 流式不做：决策 Agent 非聊天 Agent，文本断续；对外叙事经事件→主播侧已有流式表达；WebUI 观察走事件快照

## 配置

```toml
[agents.game.minecraft]
max_steps = 50   # 单任务 ReAct 循环最大步数（超出挂起上报，防失控）
```

无时间循环字段（tick 已取消）。配 `CONFIG_VERSION` 同步规则：配置结构变更升版本号并保证漂移写回。

## 解耦边界

- agent 领域核心零 maicraft 知识：工具面经 registry 动态发现；MCP server 连接由通道层路由（`McpToolProvider` 绑定 server 的 client），agent 不感知
- 工具失败作为错误观察喂回 LLM（ReAct 标准，LLM 自调整）；连续失败由 max_steps 兜底
- 内容特有逻辑内聚 `src/agents/game/minecraft/` 包（加内容=加包+配置，框架零改动）

## 相关文档

- [架构总览](overview.md) — 组件图与目录结构
- [v2.0.0 架构叙事](v2-architecture.md) — Agent/Tool 判据推导
- [数据流规则](data-flow.md) — 事件流约束
- [组件开发指南](../development/component-guide.md) — 游戏 Agent 范式

---

*最后更新：2026-09-08（MinecraftAgent ReAct 设计定稿：命令驱动任务生命周期、工具契约与命名规则、确定性事件契约、对话压缩分级、解耦边界）*
