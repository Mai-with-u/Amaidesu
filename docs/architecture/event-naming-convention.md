# 事件命名规范（v2 语义域）

> **本文档是 Amaidesu v2.0.0 事件命名的单一事实源**。所有新增事件必须先按本文档规则起名，再写代码；代码中的事件名以 [事件系统 - 事件事实表](event-system.md#事件事实表) 为权威定义。
>
> **取代**：v1 三阶段命名规范（`{domain}.{entity}.{verb}` + `input/decision/output` 阶段前缀 + `received → generated → dispatched` 动词链）。v2 已无三阶段，命名全面改为**语义域 + 已发生事实**。
>
> 设计依据：`.omo/drafts/amaidesu-v2-event-naming.md`（2.0.0 讨论教训提炼）。

---

## TL;DR

> 事件名 = **域.子类（可选）.动作**，已发生事实语义（过去时），无阶段化、无动词链。
> 域 = **领域**（live/room/game/agenda/planner/tool/core/output.sticker），**不是阶段**（input/decision/output 已删除）。
> 一个概念一个名字（查术语表，不混用同义词）；一个动词一个意思（细化，不要 `updated` / `data` 这种泛词）。
> 起名前先查这里；7 条自查清单不过 → 停下重审。

---

## 1. 语法

```
{域}.{子类(可选)}.{动作}
```

| 部分 | 必填 | 说明 | 示例 |
|---|---|---|---|
| `域` | ✅ | 事件所属**领域**（非阶段）：live/room/game/agenda/planner/tool/core；特例 `output.sticker` 保留作设备控制 | `room` |
| `子类` | 可选 | 该域内的**子层**：`message`（行为流）/ `state`（状态快照）/ `result`（工具结果）/ `command`（命令下发）/ `checkpoint`（检查点提醒） | `message` |
| `动作` | ✅ | **已发生的事实**（过去时语义）：具体、单一、无歧义 | `danmaku` |

**允许最多 4 层**（如 `room.message.danmaku`，子类存在时必然 4 层）；不强制 3 层（子类为可选，缺子类时 2 层，如 `live.started`）。

**分隔符**：统一点号 `.`；不用下划线作分隔符。

---

## 2. 语义域枚举及职责

Amaidesu 当前共 7 个语义域 + 1 个保留特例（`output.sticker`）。每个域有自己的职责边界，事件**只能在所属域内**。

| 域 | 职责 | 典型事件 |
|---|---|---|
| **core** | 系统级核心状态（启动 / 关闭 / 错误）。**不属于任何业务域**，仅供系统组件订阅 | `core.startup` / `core.shutdown` / `core.error` |
| **live** | 直播场次生命周期（开播 / 下播）。**唯一含时间窗锚点**的域，所有 room/game 事件均需携带 `live_session_id` | `live.started` / `live.ended` |
| **room** | 直播间行为流 / 状态。**子层强制**：行为流走 `.message.*`（已发生事实），状态走 `.state.*`（当前属性快照，预留层） | `room.message.danmaku` / `room.message.gift` / `room.message.super_chat` / `room.message.enter` |
| **game** | 游戏里程碑 / 异常。**低频**，只发重大变化（挖到钻石 / 通关章节 / 安全阀偏差） | `game.milestone` / `game.attention_required` / `game.error` |
| **agenda** | AgendaItem 运行进度变更（节目单打勾 / 改时间 / 插入）。**仅变更即发**，不是周期性状态广播 | `agenda.update` |
| **planner** | Planner 检查点提醒（纯提醒零决策）。**唯一合法的"低频周期性"事件**（Planner 空闲 + 无 pending 异步 + 队列空 + 有未完成 AgendaItem 四判据全过才发） | `planner.checkpoint` |
| **tool.result** | 异步工具结果回传（fire-and-forget 完成后）。**通配 pattern**：`tool.result.#` 一站式监听所有工具结果；emit 时用具体名 `tool.result.<tool_name>` | `tool.result.speak` / `tool.result.summarize_timeline` |
| **output.sticker**（特例） | Sticker → VTS 单向信号（§1.46.1 保留事件，设备控制特例，**不归到任何语义域**） | `output.sticker.command` |

---

## 3. 「语义域不命名阶段」原则（首要）

> 2.0.0 无三阶段，**域 = 领域**（live/room/game/agenda/planner/tool/core），不是 input/decision/output。

| ❌ 阶段命名 | ✅ 语义域命名 | 违反规则 |
|---|---|---|
| `input.message.received` | `room.message.danmaku`（弹幕按所属域归 room） | 阶段命名 → 改用语义域 |
| `decision.intent.generated` | （v2 无 Intent；决策出口 = 工具调用，无事件） | 无业务实体支撑 |
| `output.intent.dispatched` | （v2 无 OutputHandlerManager；统一为 ToolRegistry.invoke） | 无业务实体支撑 |
| `output.handler.completed` | （同上） | 同上 |
| `output.obs.command` | 工具调用直接调 OBS Provider（无事件中转） | 命令类不应走事件 |

**判定**：写事件名前，先问自己：这个事件是哪个**领域**发生的？不要问"它在哪个阶段"。如果想了 5 秒想不到对应域，说明这件事可能根本不该发事件（直接函数调用即可）。

---

## 4. 行为 vs 状态必须分层

同一域下**行为流（发生的事）**与**状态（当前什么样）**性质不同，必须分不同子层，**禁止平铺同层**。

| ✅ 行为流 `.message.*` | ✅ 状态 `.state.*`（预留） | ❌ 平铺同层 |
|---|---|---|
| `room.message.danmaku`（弹幕已收到） | `room.state.heat`（当前热度快照） | `room.danmaku` 与 `room.heat_changed` 同层 |
| `room.message.gift` | `room.state.online_count` | `room.gift` + `room.online` 平铺 |

**判定**：加新事件时，先问"这是**发生的事**（`message`），还是**当前的状态**（`state`）？"再选子层。

**当前实现状态**：

- ✅ `room.message.*`（行为流，4 类已实现）
- ⏳ `room.state.*`（预留层，当前不实现任何事件；将来若需主动广播订阅的状态变更才会启用，不与行为流平铺同层）

---

## 5. 行为 / 状态分层动词（子层约定）

| 子层 | 含义 | 例 |
|---|---|---|
| `.message.*` | 行为流（发生的事实 / 内容流入） | `room.message.danmaku` |
| `.state.*` | 状态（当前属性快照） | `room.state.heat` |
| `.result.#` | 工具 / 异步结果回传 | `tool.result.speak` |
| `.command` / `.control` | 命令下发（vs 已发生事件） | `room.control`（假设）/ `output.sticker.command` |
| `.checkpoint` | 检查点提醒（§1.7 纯提醒零决策） | `planner.checkpoint` |

**关键区别**：

- `.message.*` / `.state.*` = **已发生事实**（过去时）→ 订阅方拿来做事
- `.command` / `.control` = **命令下发**（将来时）→ 被调方执行
- `.result.*` = **结果回传**（过去时）→ 工具结果通道

> 旧 v1 用 `received` / `generated` / `dispatched` / `finished` 表达阶段流转；v2 **取消动词链**，事件已发生就是已发生，不存在"阶段流转"。

---

## 6. 命令 / 控制类：单事件 + payload 判别

同类控制操作合并为**单事件 + action 判别**（沿用旧 OBS_COMMAND 模式）。

| ✅ 单事件 + action | ❌ 拆多事件 |
|---|---|
| `room.control` + `action: Literal["set_title", "ban_user", "mute"]`（假设） | `room.set_title` / `room.ban_user` / `room.mute`（拆碎接口面膨胀） |
| `output.sticker.command`（§1.46.1） | 拆成 `output.sticker.show` / `output.sticker.hide` / `output.sticker.replace` |

**判定**：

- 多个操作共享同一"实体 + 意图"，且是**命令下发**而非"已发生事实" → 用统一入口
- 命令类通常配 `X.command` / `X.control` 子层，与"已发生"事件（`X.message.*`）**分开**

---

## 7. 一个概念一个名字

术语表定过的概念，**全库统一用，绝不换名**。

| ✅ 唯一命名 | ❌ 混用 | 违反规则 |
|---|---|---|
| Agenda（AgendaItem / agenda.*） | Agenda 和 Outline 混用 | 同一概念两个名字 |
| LivePayload（live.started / live.ended 共用） | 一个 `LiveStartedPayload` 一个 `LiveEndedPayload` | 同一形状拆两 Payload |

**判定**：起名前查 `CoreEvents` 常量 + 术语表，已有概念绝不另起新名。

---

## 8. 异步工具结果统一 `tool.result.#`

工具结果类**全部**归 `tool.result.<tool_name>`，**不散落到任何域**。

| ✅ 统一通配 | ❌ 散落到域 |
|---|---|
| `tool.result.speak` / `tool.result.summarize_timeline` | `output.speak.done` / `planner.timeline_ready`（散到输出域 / planner 域） |

**特殊命名**：`tool.result.#` 是**通配订阅专用模式**（`CoreEvents.TOOL_RESULT_WILDCARD`），不是被 emit 的具体事件名。emit 时使用具体名 `tool.result.<tool_name>`，订阅者 `event_bus.on("tool.result.#", ...)` 一站式监听后按 `payload.tool_name` 字段分发。

---

## 9. 通配符在订阅中的使用规范

通配订阅是 v2 增量能力，MQTT 风格（详见 [事件系统 - 通配订阅](event-system.md#通配订阅mqtt-风格)）。**生产代码当前均使用精确订阅**，通配仅在需要一站式捕获时使用。

### 通配语义速查

| 通配符 | 行为 | 例 |
|---|---|---|
| `*` | 消耗**恰好 1 个** dot-token（单层） | `room.*` 匹配 `room.message`，**不**匹配 `room.message.danmaku` |
| `#` | **仅末尾**，消耗 **≥0 个**剩余 token（多层） | `tool.result.#` 匹配 `tool.result`、`tool.result.speak` |
| `#`（独立） | 无前缀，匹配一切 | `#` |

### 何时用通配，何时用精确订阅

| 场景 | 推荐方式 | 理由 |
|---|---|---|
| 订阅某类事件的全部子类 | **精确订阅各具体事件名**（如 `ROOM_MESSAGE_DANMAKU`） | specificity=10000，永远先执行，语义清晰 |
| 一站式监听某域内全部事件（未来扩展） | 通配 `room.message.#`（行为流全部） | 适合监控/聚合组件 |
| 一站式监听所有工具结果 | 通配 `tool.result.#`，handler 按 `payload.tool_name` 分发 | 工具结果模式约定 |

### Specificity 排序规则

精确订阅永远先于通配订阅。详细数值计算见 [事件系统 - 通配订阅 - Specificity 排序](event-system.md#specificity-排序)：

| Pattern 类型 | Specificity |
|---|---|
| 精确订阅（`event_name` 与 emit 名完全一致） | **10000**（固定 `_EXACT_SPECIFICITY`） |
| 字面量 token | +4/段 |
| `*` token | +2/段 |
| `#` token | +1/段 |
| 独立 `#` | 1 |

排序键 = `(priority ASC, specificity DESC)`。

---

## 10. 常量命名（`CoreEvents` 类）

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

    # Room 行为流（4 类）
    ROOM_MESSAGE_DANMAKU = "room.message.danmaku"
    ROOM_MESSAGE_GIFT = "room.message.gift"
    ROOM_MESSAGE_SUPER_CHAT = "room.message.super_chat"
    ROOM_MESSAGE_ENTER = "room.message.enter"

    # Game 游戏里程碑（3 类）
    GAME_MILESTONE = "game.milestone"
    GAME_ATTENTION_REQUIRED = "game.attention_required"
    GAME_ERROR = "game.error"

    # Agenda / Planner
    AGENDA_UPDATE = "agenda.update"
    PLANNER_CHECKPOINT = "planner.checkpoint"

    # Sticker 特例（§1.46.1 保留）
    OUTPUT_STICKER_COMMAND = "output.sticker.command"

    # 工具结果通配（仅订阅标识，emit 用具体名 tool.result.<tool_name>）
    TOOL_RESULT_WILDCARD = "tool.result.#"
```

完整事件清单（15 常量 + 1 通配占位符）见 [事件系统 - 事件事实表](event-system.md#事件事实表)。

---

## 11. 添加新事件的命名审批要点（7 条自查清单）

起新事件名前，逐条过；任一不过 → 停下重审：

1. **域对吗**？是领域，不是阶段。（❌ 别写 input / decision / output）
2. **子层对吗**？行为流 `.message.*`？状态 `.state.*`？结果 `.result.#`？命令 `.command`？（❌ 别把行为 / 状态平铺同层）
3. **动作是过去时吗**？事件已发生。（❌ 别用 request / will / 将来式 / 未来时）
4. **概念名统一吗**？查术语表，是否已有概念？（❌ 别 Agenda / Outline 混用）
5. **动词具体吗**？能否从事件名猜出发生了什么？（❌ 别用 updated / data / generic 这种泛词）
6. **要拆多事件吗**？同类命令能否合并成单事件 + action 判别？（❌ 别拆碎接口面）
7. **是工具结果吗**？是则归 `tool.result.#`，别散落到域

7 条全过 → 命名安全，**先更新 [事件事实表](event-system.md#事件事实表) 再写代码**。

---

## 12. 正反例对照表

| 场景 | ✅ 正确 | ❌ 错误 | 违反规则 |
|---|---|---|---|
| 弹幕来了 | `room.message.danmaku` | `input.message.received` | ① 阶段命名 |
| 礼物来了 | `room.message.gift` | `input.gift`（无子层）/ `room.gift`（与 state 平铺） | ② 行为 / 状态未分层 |
| SC 来了 | `room.message.super_chat` | `room.message.donation`（泛化，混 SC 和礼物） | ⑤ 动词具体 |
| 进房 / 大航海 | `room.message.enter`（guard 并入 enter，无独立事件） | `room.message.guard`（拆独立事件）/ `input.connected` | ⑥ 拆碎 / 阶段命名 |
| 热度变化（预留） | `room.state.heat` | `room.heat_changed`（与行为平铺） | ② 行为 / 状态未分层 |
| 开播 | `live.started` | `live.will_start` | ③ 将来式 |
| 下播 | `live.ended` | `live.stop` / `live.finish`（混用同义词） | ⑦ 概念名统一 |
| 游戏挖到钻石 | `game.milestone` | `game.progress`（泛化） | ⑤ 动词具体 |
| 游戏安全阀偏差 | `game.attention_required` | `game.warn`（泛化） | ⑤ 动词具体 |
| 游戏异常 | `game.error` | `game.exception`（混 error / exception） | ⑦ 概念名统一 |
| AgendaItem 变更 | `agenda.update` | `outline.update`（混 Agenda / Outline） | ⑦ 概念名统一 |
| Planner 空闲 | `planner.checkpoint` | `planner.idle` / `planner.status` | ⑤ 动词具体 + 子层对齐（`checkpoint` 是子层约定） |
| 工具 speak 完成 | `tool.result.speak` | `output.speak.done`（散到输出域） | ⑧ 工具结果散落 |
| 工具时间线总结 | `tool.result.summarize_timeline` | `planner.timeline_ready`（散到 planner 域） | ⑧ 工具结果散落 |
| 房间控制（未来） | `room.control` + `action: Literal[...]` | `room.set_title` / `room.ban` / `room.mute`（拆碎） | ⑥ 命令拆分 |
| 贴纸（Sticker → VTS） | `output.sticker.command`（特例保留） | `vts.show_sticker`（落工具实现） / 归 `room.sticker.command`（语义错位） | 特例保留（§1.46.1） |
| 系统启动 | `core.startup` | `system.startup`（重复 system 前缀） / `core.started`（与事件名一致而非字段） | ⑦ 概念名统一 |
| 决策意图（v1 已删） | （无 Intent；v2 决策出口 = 工具调用，无事件） | `decision.intent.generated` | ① 阶段命名 + 无业务实体 |

---

## 13. 与 v1 旧规范对照

| 项 | v1 旧规范（阶段化） | v2 新规范（语义域） |
|---|---|---|
| 格式 | `{domain}.{entity}.{verb}` | `{域}.{子类(可选)}.{动作}` |
| 首段 | 阶段（`input` / `decision` / `output` / `core`） | 领域（`live` / `room` / `game` / `agenda` / `planner` / `tool` / `core`） |
| 动词链 | `received → generated → dispatched → finished` | **取消**（无阶段流转，用已发生事实） |
| 层数 | 最多 3 | 最多 4（子类存在时） |
| 动词 | 带方向性（进 / 决策 / 出） | 已发生事实（完成态、过去时） |
| 行为 / 状态 | 同层平铺（如 `room.danmaku` + `room.online`） | **强制分层**：行为流 `.message.*` vs 状态 `.state.*` |
| 命令类 | 拆多事件（早期 `OUTPUT_OBS_SEND_TEXT` 等） | 单事件 + payload 判别（如 `output.sticker.command`） |
| 工具结果 | 散落到 `output.*` | 统一 `tool.result.#` 通配 |

---

## 14. 迁移历史

### 2026-08-22：v2.0.0 语义域事件重构

- 删除三阶段事件：`input.message.received` / `decision.intent.generated` / `output.intent.*` / `output.handler.*` / `output.obs.command`
- 新增 15 个语义域事件 + 1 个通配占位符（`TOOL_RESULT_WILDCARD`）
- 引入 MQTT 风格通配订阅（`*` 单层 / `#` 多层）+ specificity 排序
- 类 → 多事件共享（`LivePayload` 双注册 / `RoomMessagePayload` 四注册 / `GamePayload` 三注册）
- 行为 / 状态分层（`room.message.*` vs `room.state.*` 预留层）
- Sticker → VTS 单向信号保留（`output.sticker.command`，§1.46.1）

### 2026-06-23：动词链重构 + OBS 合并（已废弃）

- 动词链：`ready → generated → ready` → `received → generated → dispatched`
- OBS 事件合并：4 个独立事件 → 1 个 `output.obs.command`（已删除）
- 阶段名前缀去重：`decision.decider.connected` → `decision.connected`

### 2026-02-16：事件命名规范化重构（已废弃）

- `data.message` → `input.message.ready` → 后续演进为 `input.message.received`
- 删除 22 个未使用的事件

---

## 相关文档

- [事件系统 - 事件事实表](event-system.md#事件事实表)（权威事件清单）
- [事件系统 - 通配订阅](event-system.md#通配订阅mqtt-风格)（MQTT 风格详解 + Specificity 排序）
- [数据流规则](data-flow.md)（域间边界）
- [架构决策记录](adr/README.md)

---

*最后更新：2026-08-25（v2.0.0 语义域事件对齐：取代 v1 三阶段命名规范；新增语义域枚举/职责、行为 vs 状态分层、命令类合并、工具结果统一、自查 7 条清单、正反例对照）*