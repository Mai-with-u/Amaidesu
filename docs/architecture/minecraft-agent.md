# MinecraftAgent 设计

Minecraft 游戏 Agent（AI 玩家）的架构设计。定位：事件驱动的 ReAct Agent——用 MaiCraft v1 的 MCP 工具玩 Minecraft，主播 Agent 是它的用户。

## 驱动原则

- 只有主播 Agent 自我驱动；游戏 Agent 命令驱动（类 Code Agent）——收到命令启动任务循环，完成即停、空闲零消耗
- 因有自身状态与任务内自主决策，游戏 Agent 仍是 Agent 而非工具（三分判据见 [v2-architecture.md](v2-architecture.md)）

## 核心意象

**MinecraftAgent = 一个用 MCP 工具玩 Minecraft 的普通 ReAct Agent。**

- 系统提示词 + 工具列表 = 全部"编程"，不发明任何特殊协议
- 主播 Agent 是它的用户：发指令（跨 Agent 派活走框架委派 `framework_delegate`，中途插话走递话 `framework_prompt`）/读工作文档（`minecraft_get_work_log`）/看一眼游戏（`minecraft_glance`）/收上报（`game.report`）
- 需要角色动手的目标在 Mod 里后台推进；宿主用 `events` 长轮询盯着本 Agent 下达的目标，在它结束、提问或被暂停时带着完整结果唤醒任务，LLM 不用推理步数轮询
- LLM 可一次返回多个 tool_calls（批量请求 → 串行执行 → 批量作为观察返回，标准 function calling 循环）

## 任务生命周期（事件驱动 ReAct）

```
空闲（事件挂起，零消耗，无 LLM 调用；后台只有一条挂着的事件长轮询）
  │ 主播经 framework_delegate 委派("看一眼周围并记住一个地点")
  ▼
消息入队 → 唤醒
  ▼
任务批次（每步 = 一次 LLM 推理）：
  ├─ flush 新消息（主播提示词 / 宿主推送的目标通知）→ user 消息
  ├─ 新任务开局：宿主代读 observe(self)、observe(scene)、lookup()、goal(list)，以 [开局资料] 回执放进历史
  ├─ 历史超预算时集中整理一次
  ├─ LLM 推理（系统提示词 + 对话历史 + 工具列表）→ tool_calls（可多个）
  ├─ 串行执行（局部工具直接落状态；maicraft_* 经 ToolRegistry 透传 = MCP 调用）
  │    └─ execute 返回没结束的目标 → 开始跟踪（goal_id + state）
  ├─ 工具结果作为观察返回（OpenAI tool role + tool_call_id 关联）
  └─ 批次终止语义（全部系统可判定，见下节）
  → 回空闲（todo/notebook/reports 保留；目标跟踪跨批次持续）
```

暂停语义：平台 pause 在步骤间与工具调用间挂起（不打断当前执行中的工具调用），resume 后继续。

### 批次终止语义

| # | 情形 | 行为 |
|---|------|------|
| 1 | LLM 调 `minecraft_report(kind=delivery)` | 还有目标在跑或待办没完成时拒绝交付，返回错误观察；通过门禁后停止 |
| 2 | LLM 调 `minecraft_report(kind=escalation)` | 停止；委派账面写 `waiting_for_decision`，等递话唤醒 / 恢复续跑 / 硬取消清账 |
| 3 | 自然终止，无 report、无在跑的目标且待办完成 | 系统兜底交付，并结算原委派任务 |
| 4 | 自然终止或 `minecraft_wait`，仅剩在跑的目标 | 静默让出回合，等目标事件唤醒 |
| 5 | 有目标在等回答、被暂停或待办没推进，提醒后仍不行动 | 挂起并上报 escalation，保留原任务等继续指令 |

一整轮只在查询还在跑的目标（`maicraft_goal` get/list）时，先提醒用 `minecraft_wait`；连续第二轮直接并入等待，不再推理。

### 后台目标跟踪（events → 唤醒）

- **登记**：`maicraft_execute` 返回没结束的目标（`state` 为 running / awaiting_answer / paused）即开始跟踪；当场完成的目标（只改记忆、只读分析）结果已在回复里，不跟踪。模型用 `maicraft_goal(resume)` 解除暂停的旧目标也纳入跟踪
- **读事件**：`GoalWatch` 带着游标长轮询 `events`（`events_wait_ms`，默认 25 秒），事件流只给宿主读、不进模型工具列表。首次读取只建立游标，接手前的事件不补发
- **事实核实**：目标的 asked / paused / resumed / finished 事件只说明"变了"，宿主再用 `goal(get)` 取它此刻的完整样子（结果、变化、问题）交给任务；结束的目标不再跟踪
- **唤醒**：结束 → 注入"目标 N 结束了"与完整结果；提问 → 注入问题、选项与回答方式；被暂停 → 注入"身体没在做它"。只有这些需要模型处理的变化才唤醒，恢复推进静默记账
- **换世界**：事件流编号变了（换世界、重进世界、Mod 重启）时旧游标作废，逐个重新查在跟踪的目标；查不到或编号已是别的能力的目标，按"不在了"注入通知，不让任务挂着等不来的结果
- **身体事件**：与目标无关的事件（生存需求的临时任务开始与结束、处理不了的需求、角色死亡）进本批身体上下文；处理不了的需求与角色死亡同时注入任务并发 `game.attention_required`
- **Mod 挂出的决策**：死亡恢复这类决策不是谁下达的目标（`goal_id` 是负数）。它提问时读一次、带着问题与选项唤醒任务（空闲时也唤醒，没人回答角色就停在死亡屏幕），模型按问题里给的选项用 `maicraft_goal(answer)` 回答；不进跟踪名单、不记账本，执行结果事件 `death_recovery_applied` 不转给主播
- **账本镜像**：目标同时记进通用任务账本（`maicraft-goal-<id>`，发起方 minecraft），主播的任务查询看得到；账本的长时间无进展告警回到 Agent 注入提醒，成败本身以事件流为准

### 运营干预入口（递话 / 硬取消 / 断连恢复）

委派之外，minecraft 另有两个干预入口与一条自愈路径（原语定案见 ADR-034，账面语义见 ADR-035）：

- **递话 `receive_prompt`**：纯文本留言（主播经 `framework_prompt` 工具、运营经 REST），不派新任务、不进账本——文本入消息队列 + 唤醒；入队时按来源加 `[运营原话]` / `[主播补充]` 标签，冲突时原话与游戏结果优先于转述。
- **硬取消 `cancel_task`**：任务在委派追踪清单 → 清清单 + 账面写 cancelled + 注入"任务已被取消"通知，LLM 下一步自行停手（还在跑的游戏目标由它用 `maicraft_goal(cancel)` 取消）。软取消：不打断当前工具调用。
- **MCP 断连恢复续跑**：私有 MCP 恢复循环装配成功后注入"连接已恢复"通知 + 解锁挂起态 + 唤醒；能力清单与事件游标按新连接重新读取。

## 工具契约

**注册名 = `<Provider名字>_<工具名字>`**，分隔符 `_`（满足 LLM function calling 工具名字符集约束）。Provider 名全局唯一、用全名（`minecraft` 禁缩写）；工具名 Provider 内唯一、语义化。前缀由模块声明（provider 值）、ToolRegistry 一处拼接——工具名里不手写前缀。

**决策工具列表**（LLM 可见，注册名）：

| 工具 | 说明 |
|---|---|
| `minecraft_todo` | 待办文档（read/write 全量读写，无 id）。任务分解与推进由 LLM 自主决策 |
| `minecraft_notebook` | 工作笔记（read/write）。持久记忆：对话历史会整理、笔记不会——重要发现写这里 |
| `minecraft_wait` | 身体在执行后台目标、又没有可推进或可准备的事时单独调用，让出本轮等目标事件 |
| `minecraft_report` | 上报通道（玩家→主播唯一发声出口）：delivery 交付总结 / escalation 升级决策 |
| `minecraft_skill` | 按名读技能正文（装配了技能库时才有）。技能目录随系统提示词给出，按 MaiCraft 当前的能力清单筛选：技能的 `requires.abilities` 都在清单里才进目录；Mod 列能力时已按装了哪些模组筛过，模组相关的技能随能力一起出现。为旧版 Mod 写的技能标了 `maicraft: [v0]`，按 v1 重写前不进目录 |
| `maicraft_observe` / `maicraft_lookup` / `maicraft_execute` / `maicraft_goal` | MaiCraft v1 的工具，registry 动态发现（每任务重新拉取）；参数按 Mod 定义填写。`maicraft_events` 只给宿主读，不进 LLM 工具列表 |

**对外工具**（经 ToolRegistry 注册、主播工具列表可见，不进玩家 LLM 工具列表）：
- `framework_delegate`：跨 Agent 委派通道——把工作交给另一 Agent（指令只当自然语言，不给步骤）；BaseAgent 默认拒收，minecraft 实现接收入口（指令入队带任务号 + 唤醒）
- `minecraft_get_work_log`：工作文档读服务——只读返回 `{todo, notebook, recent_reports}`；查任务进度用 `framework_task_status`
- `minecraft_glance`：主播看一眼游戏——读一次 `observe(self)` 与 `observe(scene)`，只返回直播叙事用得上的事实（身体状态、背包物品与数量、身边 24 格内的生物与设施、时段天气）与身体手头的工作（待办、在跑的目标）。原始 `maicraft_*` 工具只对玩家自己可见

## 事件契约（确定性系统事件，无 LLM 自觉汇报）

| 事件 | 触发 |
|---|---|
| `game.report` | LLM 调 `minecraft_report`（delivery/escalation）或批次终止系统兜底交付；kind 见 `GamePayload.report_kind` |
| `game.attention_required` | 身体处理不了的生存需求、角色死亡；任务期间身体先处理了一件急事（每批每件各一次）。只叙事、不打断任务 |
| `game.error` | 工具执行异常 / LLM 调用失败 / 无 LLM fail-fast |

事件 payload 复用 `GamePayload`（`game="minecraft"`），叙事面事件带上本批任务期间的身体事件；上报同时进内存 `recent_reports`（保留最近 10 条）。

## 对话管理与压缩

- 消息累积：任务内多轮，经 `LLMManager.generate(messages, profile=..., tools=...)`（OpenAI 格式消息）；工具结果以 `tool` role + `tool_call_id` 关联作为观察返回，内容就是 MaiCraft 原样的 JSON 回复
- 同一逻辑任务跨后台等待沿用历史；历史超过预算时只总结旧调用组，最新调用组保持可读。原始指令、待办、笔记、在跑的目标与最近结束的目标独立于摘要保留；让出等待时若历史已接近预算，借这段空档在后台提前整理
- 唤醒续做只补交相对上一份完整任务状态的变化
- 流式不做：决策 Agent 非聊天 Agent，文本断续；对外叙事经事件→主播侧已有流式表达

## 配置

```toml
[agents.minecraft]
events_wait_ms = 25000            # 跟踪后台目标时一次读事件流最多等多久（MaiCraft 上限 60 秒）

[agents.minecraft.context]        # 工作上下文预算
max_context_chars = 160000

[agents.minecraft.mcp]            # Agent 私有 MCP（位置即归属，owner_agent="minecraft"）
enabled = true
url = "http://127.0.0.1:8766/mcp"
```

配版本同步规则：配置结构变更升该文件 `[meta].version` 并保证漂移写回（见 ADR-014）。

## 解耦边界

- 通用 MCP 层只归一化协议数据；MaiCraft v1 的返回格式、目标运行状态与事件流语义只在 `src/agents/minecraft/maicraft.py` 解读一次
- 工具失败作为错误观察返回 LLM（`error.code` 与 `error.fields` 原样保留），由模型根据错误原因调整行动，确实无法推进时上报具体阻塞
- 内容特有逻辑内聚 `src/agents/minecraft/` 包（加内容=加包+配置，框架零改动）

## 相关文档

- 组件图与目录结构以代码为唯一事实源（`src/`、`ToolRegistry`）
- [架构叙事](v2-architecture.md) — Agent/Tool 判据推导
- [事件系统](event-system.md) — game.* 事件语义单一事实源
- [数据流规则](data-flow.md) — 事件流约束
- [组件开发指南](../guides/component.md) — 游戏 Agent 范式
- [ADR-034](../decisions/034-agent-intervention-primitives.md) — 递话/硬取消/运营直派原语定案
- [ADR-035](../decisions/035-escalation-ledger-semantics.md) — escalation 账面语义与断连恢复续跑
