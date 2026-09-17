# 事件命名规范（v2 语义域）

> **本文档是 Amaidesu 事件命名的单一事实源**。所有新增事件必须先按本文档规则起名，再写代码；代码中的事件名以 [事件系统 - 事件事实表](event-system.md#事件事实表与拓扑) 为权威定义。
>
> 命名格式：**语义域 + 已发生事实**——无三阶段（input / decision / output）、无动词链（received → generated → dispatched）。

---

## TL;DR

> 事件名 = **域.子类（可选）.动作**，已发生事实语义（过去时），无阶段化、无动词链。
> 域（首段）= **拥有这个事实的来源子系统**——世界适配器（直播平台、游戏）与内部组件（Planner、TTS 等）都是合法来源，**不是阶段名**（input/decision/output 不存在）。
> payload 形态两规则：同族 + 同（超集）形状 → 一个类多注册 + 判别字段；形状不同 → 一事件一个类。
> 一个概念一个名字（查术语表，不混用同义词）；一个动词一个意思（细化，不要 `updated` / `data` 这种泛词）。
> 起名前先查这里；7 条自查清单不过 → 停下重审。

---

## 1. 语法

```
{域}.{子类(可选)}.{动作}
```

| 部分 | 必填 | 说明 | 示例 |
|---|---|---|---|
| `域` | ✅ | **拥有这个事实的来源子系统**：core/live/room/game/rundown/planner/streamer/tts/task | `room` |
| `子类` | 可选 | 该域内的**子层**：`message`（行为流）/ `state`（状态快照）/ `result`（工具结果）/ `command`（命令下发） | `message` |
| `动作` | ✅ | **已发生的事实**（过去时语义）：具体、单一、无歧义 | `danmaku` |

**允许最多 4 层**（如 `room.message.danmaku` 为 3 层，子类计入层数）；不强制 3 层（子类为可选，缺子类时 2 层，如 `live.started`）。

**分隔符**：统一点号 `.`；不用下划线作分隔符。

---

## 2. 首段 = 拥有这个事实的来源子系统

事件名第一段回答"**这件事是谁的事实**"——即拥有该事实的来源子系统。两类来源都合法：

- **世界适配器**：直播平台接入（弹幕/礼物/SC → `room`）、场次管理（`live`）、游戏 Agent（`game`）——它们感知外部世界发生的事实；
- **内部组件**：Planner（`planner`）、主播 Agent（`streamer`）、TTS（`tts`）、流程单（`rundown`）、任务记录表（`task`）、系统核心（`core`）——它们是内部业务事实的归属方。

**放置规则**：新事件归属于"事实发生时谁拥有它"，而不是"谁消费它"或"它发生在哪个处理阶段"。归属拿不准时，问"这件事的原始记录写给谁的名下"。

当前全部第一段（语义域）清单：

| 域 | 职责 | 典型事件 |
|---|---|---|
| **core** | 系统级核心状态（启动 / 关闭 / 错误）。**不属于任何业务域**，仅供系统组件订阅 | `core.startup` / `core.shutdown` / `core.error` |
| **live** | 直播场次生命周期（开播 / 下播）。**唯一含时间窗锚点**的域，所有 room/game 事件均需携带 `live_session_id` | `live.started` / `live.ended` |
| **room** | 直播间行为流 / 状态。**子层强制**：行为流走 `.message.*`（已发生事实），状态走 `.state.*`（当前属性快照，预留层） | `room.message.danmaku` / `room.message.gift` / `room.message.super_chat` / `room.message.guard` / `room.message.enter` / `room.message.partner_speech` |
| **game** | 游戏里程碑 / 异常 / 上报。**低频**，只发重大变化（挖到钻石 / 通关章节 / 安全阀偏差 / 交付总结）。`live_session_id` 为 int 场次主键：发布方不填，由场次盖章拦截器注入。另有 `game.body.*`（8 类）：MaiCraft 注意流经采集器**分类**后的 AI 玩家遭遇（被袭击/死亡/重生/紧急反应/切维度），高频、与 `game.*` 不混层 | `game.milestone` / `game.attention_required` / `game.error` / `game.report`；`game.body.attacked` 等 8 类 |
| **rundown** | 流程单（Rundown）状态变更（加载 / 跳转 / 推进 / 暂停 / 恢复）。**单事件 + payload 判别，仅变更即发**，不是周期性状态广播 | `rundown.changed` |
| **planner** | 主播决策轮记录：轮末一条 `planner.decision`（决策卡数据源）；裁决时刻即时一条 `planner.verdict`（reply 被调用时、表达生成之前） | `planner.decision` / `planner.verdict` |
| **streamer** | 主播 Agent 管线阶段与发言业务事实：`streamer.stage`（决策管线阶段变化）/ `streamer.speech`（一条发言已生成，与 TTS 启用与否正交） | `streamer.stage` / `streamer.speech` |
| **tts** | 一次发声实例的生命周期（开始 / 完成 / 失败），`utterance_id` 全链路串联；**终点广播**，消费者不得触发新决策 | `tts.utterance.started` / `tts.utterance.finished` / `tts.utterance.failed` |
| **task** | 异步任务生命周期（受理 → 进行中含决策点 → 终态）。发起方订阅按 `payload.initiator` 过滤唤醒；**通知是提示、查询是事实源**（记录表是事实源） | `task.changed` |
| **tool**（通配前缀） | 异步工具结果 / 工具健康切换。**通配 pattern**：`tool.result.#` / `tool.health.#` 一站式监听；emit 时用具体名 `tool.result.<tool_name>` | `tool.result.speak` / `tool.health.maicraft_speak` |

---

## 3. 「语义域不命名阶段」原则（首要）

> 2.0.0 无三阶段，**域 = 拥有事实的来源子系统**，不是 input/decision/output。

| ❌ 阶段命名 | ✅ 语义域命名 | 违反规则 |
|---|---|---|
| `input.message.received` | `room.message.danmaku`（弹幕按所属域归 room） | 阶段命名 → 改用语义域 |
| `decision.intent.generated` | （v2 无 Intent；决策出口 = 工具调用，无事件） | 无业务实体支撑 |
| `output.intent.dispatched` | （v2 无 OutputHandlerManager；统一为 ToolRegistry.invoke） | 无业务实体支撑 |
| `output.handler.completed` | （同上） | 同上 |
| `output.obs.command` | 工具调用直接调 OBS Provider（无事件中转） | 命令类不应走事件 |

**判定**：写事件名前，先问自己：这个事件是哪个**来源子系统**的事实？不要问"它在哪个阶段"。如果想了 5 秒想不到对应域，说明这件事可能根本不该发事件（直接函数调用即可）。

---

## 4. 行为 vs 状态必须分层

同一域下**行为流（发生的事）**与**状态（当前什么样）**性质不同，必须分不同子层，**禁止平铺同层**。

| ✅ 行为流 `.message.*` | ✅ 状态 `.state.*`（预留） | ❌ 平铺同层 |
|---|---|---|
| `room.message.danmaku`（弹幕已收到） | `room.state.heat`（当前热度快照） | `room.danmaku` 与 `room.heat_changed` 同层 |
| `room.message.gift` | `room.state.online_count` | `room.gift` + `room.online` 平铺 |

**判定**：加新事件时，先问"这是**发生的事**（`message`），还是**当前的状态**（`state`）？"再选子层。

**当前实现状态**：

- ✅ `room.message.*`（行为流，6 类已实现：danmaku / gift / super_chat / guard / enter / partner_speech）
- ⏳ `room.state.*`（预留层，当前不实现任何事件；将来若需主动广播订阅的状态变更才会启用，不与行为流平铺同层）

---

## 5. 行为 / 状态分层动词（子层约定）

| 子层 | 含义 | 例 |
|---|---|---|
| `.message.*` | 行为流（发生的事实 / 内容流入） | `room.message.danmaku` |
| `.state.*` | 状态（当前属性快照） | `room.state.heat` |
| `.result.#` | 工具 / 异步结果回传 | `tool.result.speak` |
| `.command` / `.control` | 命令下发（vs 已发生事件） | `room.control`（假设） |

**关键区别**：

- `.message.*` / `.state.*` = **已发生事实**（过去时）→ 订阅方拿来做事
- `.command` / `.control` = **命令下发**（将来时）→ 被调方执行
- `.result.*` = **结果回传**（过去时）→ 工具结果通道

> 事件名无动词链——事件已发生就是已发生，不存在"阶段流转"语义。

---

## 6. 命令 / 控制类：单事件 + payload 判别

同类控制操作合并为**单事件 + action 判别**。

| ✅ 单事件 + action | ❌ 拆多事件 |
|---|---|
| `room.control` + `action: Literal["set_title", "ban_user", "mute"]`（假设） | `room.set_title` / `room.ban_user` / `room.mute`（拆碎接口面膨胀） |

**判定**：

- 多个操作共享同一"实体 + 意图"，且是**命令下发**而非"已发生事实" → 用统一入口
- 命令类通常配 `X.command` / `X.control` 子层，与"已发生"事件（`X.message.*`）**分开**

---

## 7. payload 形态：多注册判别 vs 一事件一类

payload 类与事件名的绑定分两条规则，判定依据是**形状**与**订阅诉求**：

| 规则 | 适用条件 | 现例 |
|---|---|---|
| **一个类多注册 + 判别字段** | 同族事件、payload 是同一形状（或超集形状）、需要整族通配订阅 | `RoomMessagePayload` 六注册（`room.message.*`，判别字段 `message_type`）/ `GamePayload` 四注册（`game.*`，判别字段 `event_type`） |
| **一事件一个类** | 各事件字段本来就不同，硬塞一类只会互相留空字段 | `LiveStartedPayload`（started_at_ms/title/room_id）与 `LiveEndedPayload`（reason/duration_ms/empty_discarded）——字段不同，两个类是**正例** |

**判定**：

- 同族 + 同（超集）形状 + 需要整族订阅（`room.message.#`）→ 一个类多注册，类上声明判别字段（`_DISCRIMINANT_FIELD`），EventBus 在 emit 期校验"事件名末段 == 判别字段值"，挂错名直接报错
- 形状不同 → 一事件一个类，不要为"少写一个类"而把不相关字段塞进同一形状

**三档 topic 粒度**（决定事件名的组织方式）：

1. **单事件**：一个名字一个事实（`live.started`）——绝大多数事件
2. **有限多类**：名字可枚举、形状统一（`room.message.*` 六类）→ 一个类多注册 + 判别字段
3. **无界多实例**：名字含运行时变量、不可枚举（`tool.result.<tool_name>`）→ 一个类 + 通配订阅，不注册具体名

---

## 8. 一个概念一个名字

术语表定过的概念，**全库统一用，绝不换名**。

| ✅ 唯一命名 | ❌ 混用 | 违反规则 |
|---|---|---|
| Rundown（流程单 / RundownState / rundown.changed） | Rundown 和 Agenda 混用 | 同一概念两个名字 |
| `game.error`（游戏异常） | `game.exception`（同义换名） | 同一概念两个名字 |

**判定**：起名前查 `CoreEvents` 常量 + 术语表，已有概念绝不另起新名。

---

## 9. 异步工具结果统一 `tool.result.#`

工具结果类**全部**归 `tool.result.<tool_name>`，**不散落到任何域**。

| ✅ 统一通配 | ❌ 散落到域 |
|---|---|
| `tool.result.speak` / `tool.result.summarize_timeline` | `output.speak.done` / `planner.timeline_ready`（散到输出域 / planner 域） |

**特殊命名**：`tool.result.#` 是**通配订阅专用模式**（`CoreEvents.TOOL_RESULT_WILDCARD`），不是被 emit 的具体事件名。emit 时使用具体名 `tool.result.<tool_name>`，订阅者 `event_bus.on("tool.result.#", ...)` 一站式监听后按 `payload.tool_name` 字段分发。

---

## 10. 通配符在订阅中的使用规范

通配订阅采用 **AMQP topic 风格**（详见 [事件系统 - 通配订阅](event-system.md#通配订阅amqp-topic-风格)）：`*` 匹配**恰好一个**词段、`#` 只能放在末尾并匹配**零或多个**剩余词段——单层能力由 `*` 承担，两级通配都保留。

### 通配语义速查

| 通配符 | 行为 | 例 |
|---|---|---|
| `*` | 消耗**恰好 1 个** dot-token（单层） | `room.*` 匹配 `room.message`，**不**匹配 `room.message.danmaku` |
| `#` | **仅末尾**，消耗 **≥0 个**剩余 token（多层） | `tool.result.#` 匹配 `tool.result`、`tool.result.speak` |
| `#`（独立） | 无前缀，匹配一切 | `#`（事件记录器用它订阅全部事件） |

### 何时用通配，何时用精确订阅

| 场景 | 推荐方式 | 理由 |
|---|---|---|
| 订阅某类事件的全部子类 | **精确订阅各具体事件名**（如 `ROOM_MESSAGE_DANMAKU`） | 语义清晰，model_class 精确 |
| 一站式监听某域内全部事件 | 通配 `room.message.#`（行为流全部） | 适合监控/聚合组件 |
| 一站式监听所有工具结果 | 通配 `tool.result.#`，handler 按 `payload.tool_name` 分发 | 工具结果模式约定 |
| 记录一切事件 | 通配 `#` | 事件记录器（catch-all）唯一合法用途 |

**订阅者无顺序**：订阅广播是并发扇出，匹配到的所有订阅者并发执行、互不影响——不存在"精确先于通配"或"通配之间排序"的执行顺序语义。需要有序处理的是拦截器管道（显式 `priority`，见 [事件系统 - 事件拦截器](event-system.md#事件拦截器interceptor)）。

---

## 11. 常量命名（`CoreEvents` 类）

全大写下划线、前缀 = 域 / 语义：

```python
class CoreEvents:
    # Core 系统事件
    CORE_STARTUP = "core.startup"
    CORE_SHUTDOWN = "core.shutdown"
    CORE_ERROR = "core.error"

    # Live 场次生命周期
    LIVE_STARTED = "live.started"
    LIVE_ENDED = "live.ended"

    # Room 行为流（6 类）
    ROOM_MESSAGE_DANMAKU = "room.message.danmaku"
    ROOM_MESSAGE_GIFT = "room.message.gift"
    ROOM_MESSAGE_SUPER_CHAT = "room.message.super_chat"
    ROOM_MESSAGE_GUARD = "room.message.guard"
    ROOM_MESSAGE_ENTER = "room.message.enter"
    ROOM_MESSAGE_PARTNER_SPEECH = "room.message.partner_speech"

    # Game 游戏里程碑 / 上报（4 类）
    GAME_MILESTONE = "game.milestone"
    GAME_ATTENTION_REQUIRED = "game.attention_required"
    GAME_ERROR = "game.error"
    GAME_REPORT = "game.report"

    # Rundown 流程单变更（单事件，payload 判别）
    RUNDOWN_CHANGED = "rundown.changed"

    # Task 异步任务生命周期
    TASK_CHANGED = "task.changed"

    # Planner 决策轮记录（轮末）+ 裁决时刻（即时）
    PLANNER_DECISION = "planner.decision"
    PLANNER_VERDICT = "planner.verdict"

    # Streamer 管线阶段 + 发言业务事实
    STREAMER_STAGE = "streamer.stage"
    STREAMER_SPEECH = "streamer.speech"

    # TTS 一次发声实例生命周期（终点广播）
    TTS_UTTERANCE_STARTED = "tts.utterance.started"
    TTS_UTTERANCE_FINISHED = "tts.utterance.finished"
    TTS_UTTERANCE_FAILED = "tts.utterance.failed"

    # 工具结果 / 健康 / 行为流通配（仅订阅标识，emit 用具体名 tool.result.<tool_name> 等）
    TOOL_RESULT_WILDCARD = "tool.result.#"
    TOOL_HEALTH_WILDCARD = "tool.health.#"
    ROOM_MESSAGE_WILDCARD = "room.message.#"
    # AI 玩家遭遇（8 类具名 + 一条通配；判别字段 kind）
    GAME_BODY_ATTACKED = "game.body.attacked"
    GAME_BODY_ATTACK_ENDED = "game.body.attack_ended"
    GAME_BODY_DIED = "game.body.died"
    GAME_BODY_RESPAWNED = "game.body.respawned"
    GAME_BODY_REFLEX_STARTED = "game.body.reflex_started"
    GAME_BODY_REFLEX_FINISHED = "game.body.reflex_finished"
    GAME_BODY_DIMENSION_CHANGED = "game.body.dimension_changed"
    GAME_BODY_UNKNOWN = "game.body.unknown"
    # 通配订阅专用（emit 用上面的具体常量）
    GAME_BODY_WILDCARD = "game.body.#"
```

完整事件清单（24 个具名常量 + 3 个通配占位符）见 [事件系统 - 事件事实表](event-system.md#事件事实表与拓扑)。

---

## 12. 添加新事件的命名审批要点（7 条自查清单）

起新事件名前，逐条过；任一不过 → 停下重审：

1. **域对吗**？是来源子系统，不是阶段。（❌ 别写 input / decision / output）
2. **子层对吗**？行为流 `.message.*`？状态 `.state.*`？结果 `.result.#`？命令 `.command`？（❌ 别把行为 / 状态平铺同层）
3. **动作是过去时吗**？事件已发生。（❌ 别用 request / will / 将来式 / 未来时）
4. **概念名统一吗**？查术语表，是否已有概念？（❌ 别同一概念两个名字，如 Rundown / Agenda 混用）
5. **动词具体吗**？能否从事件名猜出发生了什么？（❌ 别用 updated / data / generic 这种泛词）
6. **要拆多事件吗**？同类命令能否合并成单事件 + action 判别？（❌ 别拆碎接口面）
7. **是工具结果吗**？是则归 `tool.result.#`，别散落到域

7 条全过 → 命名安全，**先更新 [事件事实表](event-system.md#事件事实表与拓扑) 再写代码**。

---

## 13. 正反例对照表

| 场景 | ✅ 正确 | ❌ 错误 | 违反规则 |
|---|---|---|---|
| 弹幕来了 | `room.message.danmaku` | `input.message.received` | ① 阶段命名 |
| 礼物来了 | `room.message.gift` | `input.gift`（无子层）/ `room.gift`（与 state 平铺） | ② 行为 / 状态未分层 |
| SC 来了 | `room.message.super_chat` | `room.message.donation`（泛化，混 SC 和礼物） | ⑤ 动词具体 |
| 上舰 | `room.message.guard`（付费消息独立事件，下游优先回应） | 并入 `room.message.enter`（事实不同） | ⑤ 动词具体 |
| 进房 | `room.message.enter` | `input.connected` | ① 阶段命名 |
| 热度变化（预留） | `room.state.heat` | `room.heat_changed`（与行为平铺） | ② 行为 / 状态未分层 |
| 开播 | `live.started` | `live.will_start` | ③ 将来式 |
| 下播 | `live.ended` | `live.stop` / `live.finish`（混用同义词） | ④ 概念名统一 |
| 游戏挖到钻石 | `game.milestone` | `game.progress`（泛化） | ⑤ 动词具体 |
| 游戏安全阀偏差 | `game.attention_required` | `game.warn`（泛化） | ⑤ 动词具体 |
| 游戏异常 | `game.error` | `game.exception`（混 error / exception） | ④ 概念名统一 |
| 游戏交付总结 | `game.report` | `game.summary`（泛化）/ `game.finish`（混同义词） | ⑤ 动词具体 |
| 流程单状态变更 | `rundown.changed` | `rundown.updated`（泛词 updated） | ⑤ 动词具体 |
| 决策轮完成记录 | `planner.decision` | `planner.round` / `planner.result`（泛化） | ⑤ 动词具体 |
| 裁决时刻（reply 被调用） | `planner.verdict` | `planner.decision`（与轮末记录混用） | ④ 概念名统一 |
| 工具 speak 完成 | `tool.result.speak` | `output.speak.done`（散到输出域） | ⑦ 工具结果散落 |
| 工具时间线总结 | `tool.result.summarize_timeline` | `planner.timeline_ready`（散到 planner 域） | ⑦ 工具结果散落 |
| 房间控制（未来） | `room.control` + `action: Literal[...]` | `room.set_title` / `room.ban` / `room.mute`（拆碎） | ⑥ 命令拆分 |
| 系统启动 | `core.startup` | `system.startup`（重复 system 前缀） / `core.started`（与事件名一致而非字段） | ④ 概念名统一 |
| 决策意图 | （无 Intent；v2 决策出口 = 工具调用，无事件） | `decision.intent.generated` | ① 阶段命名 + 无业务实体 |

---

## 相关文档

- [事件系统 - 事件事实表](event-system.md#事件事实表与拓扑)（权威事件清单）
- [事件系统 - 通配订阅](event-system.md#通配订阅amqp-topic-风格)（AMQP topic 风格详解）
- [数据流规则](data-flow.md)（域间边界）
- [架构决策记录](../decisions/README.md)
