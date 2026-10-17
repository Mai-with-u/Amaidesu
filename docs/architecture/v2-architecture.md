# Amaidesu 架构：自主主播与工具契约

> **本文定位**：这是 Agent + 工具架构的**完整叙事文档**——为什么选 Agent + 工具（而不是流水线）、这套分层如何从第一性原理推导出来、各层怎样设计与落地。它回答"为什么是这样"；速查类内容（组件清单、事件全表、API 细节）见文末相关文档。

---

## 三阶段架构为什么走不下去

三阶段流水线（采集器标准化消息 → Decider 决策出 Intent → Handler 并行渲染）暴露出四个结构性问题：

1. **Intent 是虚构的中间表示**。决策的本质是"调用渲染能力"，Intent 数据类和 `decision.intent.generated` 事件链是为管道而管道——真正需要 Intent 形状的消费者只有输出调度点自己。
2. **三套参与者生命周期并存**。Collector 用 `start()/collect()`、Decider 用 `setup()/decide()`、Handler 用 `init()/handle(intent)`，三种范式三套心智负担。
3. **加内容必须动框架**。新增直播玩法或游戏代理要改阶段管理器、注册表、事件链——框架没有做到"加内容零改动"。
4. **主播的主体性被肢解**。"聚合弹幕 → 判断要不要说话 → 生成表达 → 驱动渲染"这个完整的自主循环被拆散在三个阶段的 Manager 里，没有任何一处代码拥有闭环。主播退化成一台**被观众消息喂食的应答机**。

同期，以 MaiBot 为决策核心的桥接路线（maibot_decider 等）确认放弃：两个项目的架构已显著分化，桥接维护成本高于收益，Amaidesu 需要一条独立演进的主干。

## 第一性原理：从应答机到表达者

### 主体性判据：空房间冷启动

> **核心目标：Amaidesu 是从"应答机"走向"表达者"。**

判别标准是一个硬性思想实验——**空房间冷启动**：

> 给系统一个直播空房间，没有任何观众、任何外界消息。问：系统会自己动吗？
>
> - 三阶段老架构：**静止**。管道由数据驱动，无外界刺激即停摆——它是应答机。
> - 有主体性的架构：**自主推进**。按节目单推进内容、主动开口、有自己的节奏——观众是锦上添花，不是燃料。

由此派生三个层级（缺一即滑向换皮）：**意图来自自我**（节目单/人格/目标，而非观众消息）、**行为自主**（主动说话/推进/取舍，而非被迫反应）、**独立核心**（不是任何外部 Bot 的外壳）。

### 核心判别式：谁驱动谁

落到工程上，整个架构收敛为一个判别式。从四个维度展开才能区分干净：

| | 谁驱动 | 形态 | 暴露 | 例子 |
|---|---|---|---|---|
| **主播 Agent** | 自我驱动（唯一），直播期间持续运行 | 继承 `BaseAgent`，拥有决策循环 + 后台维护 | 整个生命周期 + `list_tools()` 聚合到 ToolRegistry | `StreamerAgent`（Planner 决策循环 + 后台 BackgroundMaintainer） |
| **游戏 Agent** | 命令驱动（类 Code Agent）：命令唤醒，任务内有界循环，完成即停、空闲零消耗 | 同上 | 同上 | `MinecraftAgent`（委派型：framework_delegate 委派唤醒）、`TextAdvGameAgent` / `Game2048Agent`（遥控型：主播直接用工具按键、选项） |
| **工具** | 被调才干活（纯被动，调用即返回 `ToolExecutionResult`） | 继承 `ToolProvider`，`list_tools()` + `invoke(ToolInvocation)` | 仅经 `ToolRegistry.invoke(name, args)` 暴露给 LLM | `vision_look_at_screen`、`vts_set_expression`（TTS 已迁至基础模块层，不是工具） |

**判别口诀**：能自我维持状态/轮询/心跳的就是 Agent，只在被调用时执行的就是 Tool。落到工程上只需回答一句——"这个功能有没有自己的状态/轮询/心跳？"有就做成 Agent，没就做成 Tool。

这条规则同时约束**反对偷换概念**：基础能力（如屏幕感知）即便被多个 Agent 复用，也应做成 Tool（走 ToolRegistry + Protocol 注入），而不是塞进某个 Agent 内部。Agent 包的红线细则见「Agent 包边界硬规则」一节。

以及一句对内容生产者的解放：**直播内容是编排配置 + Planner 上下文/行为模式的变化，不是代码模块。** 加一档节目不需要写代码，加一类游戏才需要一个新 Agent 包。

## 防换皮的两道铁闸

### 为什么需要铁闸

三代换皮史证明：没有结构性约束，重构会在惯性下退回原形。git 历史调研同时挖出了当年插件系统被废的五大理由（过度插件化、服务注册复杂度、插件间依赖成石山、消息流不清晰、配置分散）——本架构对每一条都有明确的规避设计：

| 插件的错 | 规避设计 |
|---|---|
| 核心功能也做成插件，必需与可选混杂 | 工具/存储/事件/LLM 全部是框架基础设施（`src/modules/`）；只有"主体"（含主体自己的记忆）住在 Agent 包里 |
| 服务注册机制，依赖运行时才暴露问题 | 无服务注册；构造器注入 + 事件/工具契约 |
| 24 个插件互相依赖成石山 | 游戏 Agent 之间零依赖，经事件（`game.*`）/状态（工具，如 text_adv_get_state）/指令（framework_delegate 委派原语）三通道松耦合 |
| 消息流经中心中转，链路不清 | Agent → 工具/事件/存储直达，单向清晰 |
| 全局/插件级配置混乱 | 七文件按领域拆分 + Pydantic Schema 校验 |

### Agent 包边界硬规则

- ✅ Agent 包**只内聚主体的自我**：决策循环、目标、该内容的专属玩法逻辑
- ✅ 基础设施全部外借：感知用公用 `vision_look_at_screen`、记忆落 storage 仓储、表达走框架 reply 体系
- ❌ 红线（出现即等于插件换皮）：自备感知后端 / 自建缓存 / import 其他 Agent 的模块 / 包里塞配置读取逻辑 / 依赖服务注册

**判别口诀**：插件是功能封闭自包含包（什么都自带、互相依赖）；Agent 包是主体开放内聚包（只带自我，能力全借框架，Agent 间零依赖）。

### 实现层的两条硬考验

所有主体性设计最终落在两条可检验的约束上：

1. **Planner 循环必须由自我意图（Rundown 流程单）驱动**，而非由"消息到达"驱动；
2. **空房间里 Planner 必须能自主产生行动**（推进 Rundown、主动说话），而非空转等喂食。

> ⚠️ 若最终实现仍是"收到消息 → 调 LLM → 渲染"，只是包一层 `while` 循环改名 Agent——那就是真换皮。

## 全景

**Amaidesu = Agent（自主主体）+ 工具（能力契约）+ 存储（状态/记忆）+ 编排（Rundown 流程单）**

```mermaid
flowchart TB
    subgraph Agents["Agent 层（主播自我驱动 / 游戏命令驱动）"]
        SA["主播 StreamerAgent<br/>MessageBuffer → Planner 决策循环 → streamer_reply 工具 → Replyer 表达引擎<br/>+ Rundown 流程单 + 后台双任务"]
        GA["游戏代理（命令驱动）<br/>MinecraftAgent：framework_delegate 委派唤醒任务内有界循环（AI 玩家范式）"]
    end
    subgraph Tools["工具层（被动能力，ToolRegistry 注册）"]
        T1["output：字幕 / VTS / Warudo / OBS…<br/>（TTS 属基础模块，不经工具层）"]
        T2["vision：vision_look_at_screen"]
        T4["agent 控制 / streamer 自带 reply / minecraft 自有工具等"]
    end
    subgraph Infra["框架设施"]
        COL["Collectors<br/>bilibili / console / stt"]
        BUS["EventBus + 语义域事件 + 拦截器"]
        STO["SQLite 存储<br/>（表结构以迁移为唯一事实源）"]
    end
    EXT["外部输入"] --> COL -->|"room.message.*"| BUS
    BUS --> SA
    GA -.->|"game.* / framework_delegate"| SA
    SA -->|"invoke tools"| Tools
    SA & GA --> STO
```

各层要点：

- **主播 Agent**：`src/agents/streamer/`——弹幕窗 MessageBuffer 聚合，Planner 以 ReAct 循环决策（工具列表 = 全局 ToolRegistry + reply 局部工具，Planner LLM profile 代码硬编码 `planner`（高质量模型档），`planner_max_steps=8` 防失控）：查信息（游戏状态 / 往场回顾 / 观众印象）→ 调 `streamer_reply` 工具（spec 声明名 `reply`）提供情绪与强度 → Replyer 表达引擎生成纯文本 speech（含敏感词净化），由该工具组装情绪与元数据。Replyer 使用一条 system 人设与一条 user（聊天记录、本轮消息、回复指引、风格），末尾为“现在，你说：”，不向模型发送 tools。**Planner 与 Replyer 都是内部件，两者都不注册为工具**（reply_tool 是 LLM 调用入口）。Rundown 流程单子系统以"备忘录 + 闹钟"给环节方向，推进权归 Agent 自身。
- **游戏代理**（`src/agents/<name>/`，如 minecraft / text_adv）：AI 玩家范式——感知（公用 `vision_look_at_screen` 快照）、推进（专属工具如 text_adv_advance）、循环内聚于一个自包含包。加游戏 = 加包 + 配置，框架零改动。
- **工具层**：全部工具统一 ToolSpec 契约，两个来源——内置（进程内渲染/感知）、MCP（外部扩展）。同步调用结果直返，异步工具经 `tool.result.<name>` 事件回传。
- **存储层**：SQLite 存储（具体表与字段以 schema_migrations 为唯一事实源）+ schema_migrations 版本化迁移；模拟数据带 `simulated` 列，统计查询一律排除——模拟观众不是观众。

## 一场直播怎么运转

静态形状之上，看动态：观众说话的时候，和没人说话的时候。

```mermaid
sequenceDiagram
    participant 输入 as 控制台 / B 站弹幕采集器
    participant 总线 as 事件总线 + 拦截器
    participant 主播 as StreamerAgent
    participant 工具 as ToolRegistry
    participant 出口 as TTS / 虚拟形象 / 演播

    rect rgb(248, 248, 248)
    Note over 输入,出口: 时刻一——观众说话了
    输入->>总线: room.message.danmaku「今天玩什么？」
    总线->>总线: 拦截器：限流 + 相似合并（拦刷屏与复读）
    总线->>主播: 分发
    主播->>主播: MessageBuffer 攒批（默认窗口 3 秒 / 满 20 条）
    主播->>工具: memory_recall_session(n_back=1)
    工具-->>主播: 上场回顾「答应大家这周开 Minecraft 新坑」
    Note over 主播,工具: Planner ReAct 决策（有步数上限，防失控）
    主播->>工具: obs_switch_scene（切到游戏场景）
    主播->>工具: streamer_reply（生成台词与情绪）
    工具-->>主播: 台词 + 情绪
    主播->>出口: 台词进发言队列播出；情绪驱动虚拟形象表情
    end
    rect rgb(248, 248, 248)
    Note over 输入,出口: 时刻二——没人说话，主播自己开口
    主播->>主播: rundown_overdue（ProactiveTrigger 内部触发：流程单环节超时）
    主播->>主播: ProactiveTrigger：空转检测
    主播->>出口: 主动开口「该开游戏了」
    end
```

时刻一是观众消息的来路：先过拦截器（拦掉刷屏与复读），再攒成一小批统一处理；开口之前主播可以先查记忆、看看游戏里的情况，然后决定这批说不说、怎么说。时刻二是设计目标所在：没人说话时，主播也要能靠节目单与空转检测继续推进——还是同一个决策循环，输入换成了"此刻节目单走到哪、我是什么人设"。这一目标仍在打磨，实际效果依赖节目单与提示词的质量。

回复产出的是台词与情绪：台词送去合成播出，情绪驱动虚拟形象表情；而要做的事（切场景、换装扮）不打包进回复，在决策循环里直接执行。按设计，加一档直播节目只需改节目单与提示词，不新增代码模块。

### 游戏 Agent：同一类，派活通道不一样

游戏 Agent（Minecraft、文字冒险、2048）与主播的分工不同：主播自己找活干，游戏 Agent 等命令——拿到命令才启动自己的循环、做完即停、空闲时不运行。进展上报到事件总线，主播订阅后当作"游戏里发生了什么"纳入决策；状态从各自暴露的查询工具拿；彼此之间互不依赖。差别在"命令从哪来"：Minecraft 走委派，一整轮是这样跑的：

```mermaid
sequenceDiagram
    participant 主播 as StreamerAgent
    participant 注册表 as ToolRegistry
    participant MC as MinecraftAgent
    participant 总线 as 事件总线

    主播->>注册表: framework_delegate("minecraft", "建一座房子")
    注册表-->>主播: 受理回执（任务号）
    Note over MC: 指令入队，唤醒任务循环
    MC->>MC: 有界循环：调 MCP 工具一步步干
    MC->>总线: game.report（交付总结）
    总线->>主播: 订阅 game.*：先记下，空闲时再处理
    主播->>注册表: minecraft_get_work_log()
    注册表-->>主播: 工作文档（想看细节时再查）
```

文字冒险和 2048 走另一条通道：不接委派，主播直接调它们的专属工具驱动——启停观察循环、推进剧情、在选项间做选择、对局落子；文字冒险的观察循环遇到选项屏会上报，等主播来选。两种交互形态的定案见 [ADR-043](../decisions/043-game-agent-interaction-shapes.md)。

按设计，加一类游戏只需新增一个独立的 Agent 包和一份配置，不需要改框架。

### 工具：一张清单，各拿一份

主播和游戏 Agent 要用的能力——看屏、查记忆、控制虚拟形象与 OBS、接外部 MCP——都做成被动工具：被调才干活，调用即返回结果，失败也是结果而不是异常，一次工具故障不会打断决策循环。所有工具注册进同一张清单，每个 Agent 按可见名单各拿一份，自带的工具只登记给自己；带外部连接的工具自己维护连接、接受健康探活，连续失败自动熔断、从所有工具列表里暂时消失，恢复后自动回来。想接入自己的工具，见[组件开发指南](../guides/component.md)。

## 支撑系统的同步升级

### 事件系统：语义域 + 通配订阅 + 拦截器

事件名从流水线位置词（`input.message.received`）改为语义域（`room.message.danmaku`）——事件描述"世界上发生了什么"，而不是"它处在管道第几站"。EventBus 支持 AMQP topic 风格通配订阅（`*` 单层 / `#` 多层尾缀）；精确与通配命中的订阅者并发分发，无先后语义。

输入净化职责由 EventBus 分发层的**事件拦截器**承担（限流、相似过滤）。

### 配置：七文件 + 每文件版本 + 包内权威 + 单一管线

`agents / collectors / tools / avatar / model / storage / infra` 七文件按领域拆分（`config/` 目录）；每文件自带 `[meta].version` 结构版本，经升级钩子注册表按区间独立推进（缺失硬错）。avatar 配置从 tools 分类迁出为独立域文件、域文件数边界由 [ADR-028](../decisions/028-avatar-domain-config-graduation.md) 修订（迁出判据：域内多 provider 实例 + 自有协议/模块目录 + 配置已跨文件分散）。组件配置权威在各组件包内的 `ConfigSchema`（中央树只留槽位与聚合段），加载走单一管线（read → 版本推进 → Pydantic 校验硬错 → 漂移写回（备份 + 自写压标）→ 合并视图）。启用开关收敛为两处：`[agents].enabled` 与 `collectors.toml` 顶层 `enabled`；全局工具停用名单为 `[tools].disabled_tools`（重启生效）。设计决策见 [ADR-014](../decisions/014-config-six-file-refactor.md)。

### 错误隔离：让边界守边界，不让一处失败扩散成全局停摆

错误隔离不是"防御性编程的清单"，而是一条贯穿边界的契约——**任一边界都对自己的失败负责，上层永远拿到结构化结果而非异常。**

这条契约有四个具名落点：

- **`ToolRegistry.invoke` 永远不抛异常**。未知工具返回失败 `ToolExecutionResult`；调用方抛异常也返回失败结果 + `error_message`。Agent/LLM 在工具失败时仍能拿到结构化结果继续推进，而不是被异常打断决策循环。
- **事件拦截器**返回 `None` 即丢事件；拦截器内部异常被吞并放行——上游 bug 不能阻塞全链路分发。
- **Collector 后台消费任务**异常被 catch（`CancelledError` 重抛），单次循环出错不影响采集器后续轮次。
- **`AgentManager.stop_all` / `cleanup_all`** 按注册顺序逐一 try/except，单个 Agent 失败不影响其余 Agent；`Dashboard` 启动失败（ImportError 等）仅 warning，整体仍可继续运行。

工程含义是双重的：一方面单点故障被局部化——一个工具挂掉不会拖垮 Agent，一个 Agent 停不下来不会卡住其他 Agent 的清理；另一方面上层不需要到处包 try/except，假设下层永远返回结构化结果。这反过来要求每条边界都对自己的失败"兜底"——边界守边界。

### 依赖注入：服务走构造器，数据走参数

依赖传递有且只有两条路径：

- **服务对象**（`EventBus` / `LLMManager` / `PromptManager` / `ConfigService` 等）一律构造器注入
- **数据对象**（Payload、配置 dict）走参数或 `**kwargs`

**禁止**把服务塞进 Context 容器传递。这条禁令的工程含义是：依赖图在 `__init__` 签名里就是完整的、可静态扫描的——重构时改一个构造器签名，所有调用点会立刻被类型检查或 IDE 跳出来；用 Context 容器则把耦合推迟到运行时，调用点靠"上下文里有这个键"才能工作。详见 [依赖注入指南](../guides/dependency-injection.md)。

## 被否决的设计

| 设计 | 否决原因 |
|---|---|
| Agent/Episode/Sensor/Performer 等比喻命名 | 中二且不知所云；是什么就叫什么（Planner/Replyer/工具/存储） |
| 换皮三阶段（感知/推理/执行改名版） | 正确模型 = Agent+工具+存储+编排，不是改名 |
| 内容层/内容包（@content 装饰器 + 接线盒） | 插件换皮的变体；世界无"内容"实体，内容是节目单配置 |
| StateStore 大杂烩 | 职责不清 → 拆成职责分离的独立存储模块 |
| 屏幕感知附属化（内容自带屏幕采集） | 屏幕感知是基础能力，任何内容复用 |
| Intent/IntentPayload 作为中间表示 | 应答机式抽象，不应再引入 |
| 插件系统（@plugin） | 历史否决项；不应回归 |

---

## 相关文档

- 组件清单、目录结构、启动时序以代码为唯一事实源（`src/`、`ToolRegistry`）
- [数据流与边界规则](data-flow.md) - 三条约束层面的精确表述
- [事件系统](event-system.md) - 事件全表、通配语义、拦截器开发
- [组件开发指南](../guides/component.md) - 采集器/工具/Agent 三范式实操
- [ADR-005](../decisions/005-v2-agent-tool-architecture.md) - 本决策的正式决策记录
