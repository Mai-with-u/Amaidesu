# MinecraftAgent 设计

Minecraft 游戏 Agent（AI 玩家）的架构设计。定位：用 MaiCraft v1 的 MCP 工具玩 Minecraft 的 ReAct Agent——既是**委派型**（主播交代目标，游戏侧自带 LLM 拆解），也支持**自主领航型**（游玩期接管游戏内主导权，自驱规划、执行与游戏公屏社交）。

两种形态与遥控型的定类见 [ADR-043：游戏 Agent 交互形态](../decisions/043-game-agent-interaction-shapes.md) 与 [ADR-081：游玩期动态主从移交](../decisions/081-dynamic-lead-handover.md)。

## 驱动原则

- 基础形态仍是命令驱动（类 Code Agent）：委派或递话唤醒任务循环，完成即停、空闲零消耗——空闲时只有附属采集器与事件长轮询，没有 LLM 调用
- 配置 `lead_mode = "dynamic"` 时，游玩环节主播可把游戏内主导权移交给本 Agent，进入**自主领航态**：不再被动等委派，持续自驱（见下文[主控权交出与收回](#主控权交出与收回)）
- 配置 `lead_mode = "streamer_only"` 时 100% 退回主播常驻主控的委派模式
- 因有自身状态与任务内自主决策，游戏 Agent 是 Agent 而非工具（三分判据见 [v2-architecture.md](v2-architecture.md)）
- 主播 Agent 是它的用户（主播主控态）：
  - 发指令：跨 Agent 派活走框架委派 `framework_delegate`，中途插话走递话 `framework_prompt`，非强杀建议走 `framework_suggest`
  - 读工作文档：`minecraft_get_work_log`
  - 看一眼游戏：`minecraft_glance`
  - 收上报：`game.report`
- 游戏 Agent 与主播 Agent 各读各的输入：直播间行为流只进主播，游戏内采集流（游戏公屏、身体事件）只进游戏侧——谁主控谁消费（[ADR-081](../decisions/081-dynamic-lead-handover.md) 五束连线契约）
- 框架层不出现游戏名（[ADR-018：游戏无关边界](../decisions/018-game-agnostic-boundary.md)）
- LLM 可一次返回多个 tool_calls（批量请求 → 串行执行 → 批量作为观察返回，标准 function calling 循环）

## 模块地图

各模块按域归组（目录 `src/agents/minecraft/`，职责一句话从模块 docstring 提炼）：

**Agent 本体**

| 模块 | 职责 |
|---|---|
| `agent.py` | MinecraftAgent（BaseAgent）：主控/委派两态的任务批次 ReAct 循环，消息路由与唤醒 |
| `state.py` | Agent 内存状态：待办、工作笔记、近期上报，按存档分开 |
| `config.py` | 本游戏特有配置（上下文预算、事件长轮询、私有 MCP、看画面额度、图纸生成） |
| `maicraft.py` | MaiCraft v1 返回格式、目标运行状态与事件流，只在这一处解读 |
| `tools.py` | 局部工具（todo/notebook/rules/report/get_work_log/glance/chat_send/context_fold/context_recall/skill/drawing）Spec + Provider |
| `wake_queue.py` | 消息队列协议：指令、闲着也开批的通知、互动通知、天色通知、普通通知的开批判定 |
| `prompts/`、`skills/` | 系统提示词与本地直播、上报策略（玩法技能随 MaiCraft 发布） |

**主从交接（自主领航态）**

| 模块 | 职责 |
|---|---|
| `lead_mode.py` | 交权简报格式化、观众建议消息、只读台词回执缓冲、主控态系统提示词附加段 |
| `known_places.py` | 世界设施短目录：名称与分类的精简目录树，尾部轻量注入 |
| `companions.py` | 两条附属采集器的声明（随本 Agent 一同开关） |

**采集与分类（常驻外部世界接入）**

| 模块 | 职责 |
|---|---|
| `events_collector.py` | 常驻长轮询事件流的采集器基类（连接、游标、重连） |
| `attention_collector.py` | 身体事件采集器：`topic=self` 流里的身体遭遇转成 `game.body.*`，步进完成转 `game.milestone` |
| `attention_matrix.py` | 分类表：事件流里哪些值得向观众叙述（注意力分类），哪些留在任务通道 |
| `attention_material.py` | 主播素材发布器：身体事件原文发 `game.attention_required`，长文先概述 |
| `chat_collector.py` | 游戏内聊天采集器：别人说的话转 `game.chat.received`，私聊标 `private` |
| `chat_attribution.py` | 聊天归属：按 Mod 给的凭据附一句"谁说的、是不是借名发的"说明 |

**社交（游戏公屏与玩家互动）**

| 模块 | 职责 |
|---|---|
| `chat_gate.py` | 游戏公屏门控：分级唤醒判定、短合并窗、防刷屏冷却、远处旁听滑窗 |
| `interaction_throttle.py` | 玩家肢体动作的按人叫醒节流 |
| `player_body_language.py` | 肢体动作事件读成给主播的一句话与给游戏 Agent 的通知 |
| `player_interactions.py` | 肢体动作与切磋结果读成结构化台账条目（只落库，`game.player.interacted`） |
| `sparring.py` | 切磋收手结果汇总成给主播的一句话 |
| `creature_book.py` | 生物名册：自家的、起过名的生物是谁、在不在身边、死没死（死讯与墓碑认领） |
| `social_rules.py` | 按存档筛选与缓存多人世界公共社交规矩（只放行有游戏公屏引文依据或人工锁定的行） |
| `others_food.py` | 从别人家箱子拿了吃的：两头记账（主播口径 + 游戏侧按世界记最近几笔） |
| `others_things.py` | 动了别人放的东西：透明记账（不拦动作，但两头记清） |

**目标与账**

| 模块 | 职责 |
|---|---|
| `goals.py` | 后台目标跟踪：事件长轮询，目标需要处理时唤醒任务 |
| `goal_ledger.py` | 目标三簿记账（此刻样子/被暂停/最近结束）与目标↔委派账联动文案 |
| `delegation_book.py` | 委派账：本 Agent 名下委派的批内追踪与账面写入的单一管辖 |
| `wait_progress.py` | 挂起等待期间的进展监视（停滞看门狗）：无有效进展按指数退避唤醒核对 |
| `furnace_wait.py` | 回炉计时簿记：装好炉先走不算停摆，到点 Mod 叫醒回去取 |
| `chosen_death.py` | 认出"自己选的死"（跑 `suicide` 回重生点），把死讯换成人能安心听的说法 |
| `stop_receipt.py` | 急停/撤单回执：哪些目标停下了、哪个没停住、手上的活撤没撤，拼一句给主播 |

**记忆与上下文**

| 模块 | 职责 |
|---|---|
| `context.py` | 任务历史按预算集中整理（整理后布局：系统说明 → 任务事实 → 分段摘要 → 近期原始调用组） |
| `context_fold.py` | 后台折叠：另发一次请求请模型把用完的区间折成摘要，行动不等摘要 |
| `short_term_memory.py` | 本场短期动态记忆（游戏公屏、折叠摘要、工作文档切片；两字切分 + 可选向量混合召回，开播清空） |
| `repeated_notice.py` | 身体通知重复判断：历史里还留着的通知不再整段重抄 |
| `work_log_snapshot.py` | 读工作文档时现查一次 `observe(self)` 附状态快照，笔记里的数字只当历史参考 |
| `mod_facts.py` | 模组能力清单、连锁挖矿统计、取材留坑回填与带电装备低电量提醒的事实解析 |
| `place_marks.py` | 地点动静（记住/忘掉/走近/离开）发存储，供下播整理认地点 |

**技能与感知**

| 模块 | 职责 |
|---|---|
| `skill_support.py` | 技能世界映射：技能目录段、按连接缓存的装载资料、新任务开局宿主代读资料包 |
| `drawing.py` | 后台建筑图纸生成（`minecraft_drawing` 工具的执行体） |
| `picture.py` | 看画面的额度与分路：限次限间隔，图只挂在下一次请求尾部看一次 |
| `glance.py` | 主播"看一眼游戏"的叙事视图（剥离观察编号、方位与网格） |
| `notice_router.py` | 身体事件通知路由表：每类事件走哪条通道、对模型说什么 |
| `world_identity.py` | 从 MaiCraft 报的 `world` 认出存档，判定"换了存档" |

## 核心意象

**MinecraftAgent = 一个用 MCP 工具玩 Minecraft 的普通 ReAct Agent。**

- 系统提示词 + 工具列表 = 全部"编程"，不发明任何特殊协议
- 需要角色动手的目标在 Mod 里后台推进；宿主用 `events` 长轮询盯着本 Agent 下达的目标，在它结束、提问或被暂停时带着结果唤醒任务，LLM 不用推理步数轮询

## 任务生命周期（事件驱动 ReAct）

```
空闲（事件挂起，零消耗，无 LLM 调用；后台只有附属采集器与一条挂着的事件长轮询）
  │ 主播经 framework_delegate 委派 / 运营递话 /（主控态下）游戏公屏·身体事件·建议·流程单驱动
  ▼
消息入队 → 唤醒
  ▼
任务批次（每步 = 一次 LLM 推理）：
  ├─ flush 新消息（指令 / 通知 / 闲着也开批的通知）→ user 消息
  ├─ 新任务开局：宿主代读自己、周围、能力清单、手上的目标（有笔记时附笔记），
  │   以一条紧跟任务指令的 [开局资料] 宿主消息放进历史（不伪造工具调用）
  ├─ 历史超预算时集中整理
  ├─ LLM 推理（系统提示词 + 对话历史 + 工具列表）→ tool_calls（可多个）
  ├─ 串行执行（局部工具直接落状态；maicraft_* 经 ToolRegistry 透传 = MCP 调用）
  │    └─ execute 返回没结束的目标 → 开始跟踪（goal_id + state）
  ├─ 工具结果作为观察返回（OpenAI tool role + tool_call_id 关联）
  └─ 批次终止语义（全部系统可判定，见下节）
  → 回空闲（todo/notebook/reports 保留，按存档分开；目标跟踪跨批次持续）
```

暂停语义：平台 pause 在步骤间与工具调用间挂起（不打断当前执行中的工具调用），resume 后继续。

### 批次终止语义

主控态与委派态共用一套批次循环，终止判定按是否在做委派分叉。

**委派态**（主播主控下的委派任务）：

| # | 情形 | 行为 |
|---|------|------|
| 1 | LLM 调 `minecraft_report(kind=delivery)` | 还有目标在跑或待办没完成时拒绝交付，返回错误观察；通过门禁后停止 |
| 2 | LLM 调 `minecraft_report(kind=escalation)` | 停止；委派状态置为 `waiting_for_decision`，等递话唤醒 / 恢复续跑 / 硬取消时清除 |
| 3 | 自然终止，无 report、无在跑的目标且待办完成 | 不算做完：系统代发一条 `report_kind=undelivered`（"游戏那边停了，没交付结果，做没做完不确定"），原委派记终态 `undelivered`；模型最后那句只进账面摘要 |
| 4 | 自然终止（不调工具结束话轮），仅剩在跑的目标 | 静默让出回合，等目标事件唤醒 |
| 5 | 有目标在等回答、被暂停或待办没推进，提醒后仍不行动 | 挂起并上报 escalation，保留原任务等继续指令 |

补充规则：

- 在做委派却没调交付就停：先提醒一次"做完了交付、没做成就报卡住"；提醒过仍不交付才记停了没交付
- 卡住时问主播的问题还没人答、被计时或决策叫醒接着做的批次：停下时重新挂回等定夺，不记成停了没交付；同一问题挂回次数达上限（`MAX_REHOLDS_PER_QUESTION`，在 agent.py）后，计时到点不再叫醒续原任务，只等主播回答（防模型反复自挂计时周期性空转）
- 批内目标全部终态、待办清空、无回炉等待而委派没交付时，按目标回执自动收账（执行方零交付的账面兜底）；目标里有 cancelled 的不自动收账
- 一整轮只在查询还在跑的目标（`maicraft_goal` get/list）时，先提醒结束这一轮等通知；连续第二轮直接替它让出，不再推理。带 `path` 的 get 是在读结束了的目标里折起来的证据，不算查询
- 图纸生成完成的瞬间到其通知注入进对话之间有一个窗口，这段窗口按"在跑"对待：此时自然终止不判"停了没交付"

**主控态**（自主领航）的终止差异：

- **绝不发 undelivered**——主控态没有委派要交付，"没交付"无从谈起
- 有在跑目标 / 图纸生成中 / 回炉等待 → 静默让出等事件唤醒
- 有待回答/暂停目标或未完成待办 → 提醒自主推进；连续空轮达上限（`_LEAD_MAX_EMPTY_TURNS`，在 agent.py）后挂起等外部事件，绝不死循环空烧 LLM
- 确实无事可做 → 进入有界短休眠（时长 `_LEAD_IDLE_WAKEUP_S`，在 agent.py；由事件页巡检到点投递"自主决定下一步"提示）或纯事件等待；收到任何外部事件即清零空转计数
- 本步全部只调了 `framework_commentary` 且解说已受理、后台有运行中目标、无新事件排队 → 直接让出等待，不空烧一轮推理

## 主控权交出与收回（自主领航态）

运行时主控态两态严格二选一：**主播主控态**（默认，覆盖待机、杂谈、遥控型/委派型游戏、下播与降级）与**游戏主控态**（开播 + 流程单处于游玩环节 + 本 Agent 在线健康时进入）。

主从移交的调度（`LeadCoordinator`、状态机、12 时刻职责矩阵、Dashboard 手动切换）是框架层机制，见 [dynamic-lead.md](dynamic-lead.md) 与 [ADR-081](../decisions/081-dynamic-lead-handover.md)；这里只写本 Agent 侧的协议与行为差异。

协议成员（BaseAgent 探测，未覆写即不支持、完全向下兼容）：

| 成员 | 行为 |
|---|---|
| `can_take_lead()` | 已开播 + MCP 已连且工具可用 + LLM 可用未熔断，三者齐才可接权 |
| `enter_lead(context)` | 校验就绪 → 幂等（已在主控态只更新简报）→ 推进收手纪元、清空队列与各临时缓冲 → 置位主控 → 把 `[主控交接简报]` 入队并唤醒自主循环 |
| `yield_lead(reason)` | 先提取客观收尾摘要（当前进度与未完事项）→ 退出主控态 → 取消在跑图纸与目标 → 持久化工作文档、清空临时缓冲回空闲；摘要返回给主播接住话头 |
| `receive_suggestion(...)` | 非强杀观众建议：包装成 `[直播间建议]` 入队唤醒，不建委派账、不取消在跑目标；仅在主控态或活跃任务态受理 |
| `receive_spoken_echo(...)` | 主播每播出一句面向观众的话，同步写入有界去重缓冲；零唤醒，下次正常推理步以 `[直播间刚说过]` 只读附尾 |

主控期收到的其他框架转达（都不堆积，同类未读通知原地替换）：

- `receive_rundown_update`：流程单切段 → 原地刷新固定层交接简报与任务指令，再追加紧凑 `[流程单切换]` 通知唤醒转向新环节目标
- `receive_silence_nudge`：直播间长时间无人发声 → `[直播间静默提醒]` 入队，游戏侧可顺手提交一句解说而不打断在跑目标
- `receive_rundown_nudge`：环节临近到期/超时 → `[流程单超时提醒]` 入队，由游戏侧自主决定调 `rundown_control` 推进或续期

**两态行为差异**：

| 维度 | 委派态（主播主控） | 主控态（自主领航） |
|---|---|---|
| 推进权 | 委派/递话/计时/决策唤醒，做完即停 | 自驱：待办与工作笔记驱动持续规划，无人发单也在游玩环节推进 |
| 游戏公屏 | 旁听（活跃任务期间入队，空闲零消耗） | 分级唤醒（见[游戏公屏分级唤醒](#游戏公屏分级唤醒)），游戏 Agent 自主回话与社交 |
| 开局资料 | 开局代读 + 存档公共规矩 | 另加权威承诺表快照与世界设施短目录（尾部任务状态，绝不改写 msg[0] 前缀） |
| 被动记忆 | 无 | `[脑中闪过]` 只读闪回（开局意图、走近地标、环节切换三个时机） |
| LLM 故障 | 挂起 + `game.error`，等主播恢复 | 连续失败达阈值或致命故障 → 主动通知协调器平滑降级回主播主控并本机兜底收手 |
| MCP 断连 | 挂起等恢复续跑 | 立即降级回主播主控 |
| 终止 | 见批次终止语义表 | 绝不发 undelivered；防空转计数 + 有界短休眠 |

主控态健康降级：MCP 断连、LLM 致命故障（余额/鉴权/连不上）或连续推理失败达阈值（`_LEAD_LLM_FAILURE_THRESHOLD`，恢复冷却 `_LEAD_LLM_COOLDOWN_S`，均在 agent.py）时，本 Agent 主动上报协调器降级回主播主控。

恢复健康后发就绪通知，`auto` 模式下由协调器重评交回。

### 主控态的上下文增强（尾部只读注入）

主控态在推理步前按需往**尾部**追加只读材料（绝不改写已发出的历史前缀，保住前缀缓存）：

- **权威承诺表快照**：经协调器按当前存档与在场/近期交互玩家拉取（短超时保护，失败跳过不阻塞行动步）；玩家在游戏公屏当面向角色承诺过、还没兑现的事，模型据此言行一致。变更经 `streamer_memory_correct` 工具落库后下一步刷新
- **世界设施短目录**：`observe(world_memory)` 压成名称+分类的精简目录树（总条目硬上限，当前维度优先；写档目标在跑或跨维度后标记脏、下一步重读）
- **主播台词回执**：`[直播间刚说过]`——主播刚对观众说了什么，游戏侧顺带参考、不重复解说；上一步自己刚触发过解说时，剥离自产回声、放行并发到达的外部发言
- **远处旁听滑窗**：游戏公屏门控拦下的远处非点名消息，行动步顺带扫一眼，绝不主动唤醒
- **`[脑中闪过]` 被动记忆闪回**：跨场档案（经协调器检索）与本场短期记忆的条数各有上限（额度默认值在 agent.py 的闪回拉取处），带短超时保护
- 闪回时机有三个：新任务/接权开局按环节意图检索、流程单切换按新环节意图检索、走近记住的地标按地名检索；排除已展示的公共规矩与近期原话，未命中零字节

## 委派账与换活结单契约

主播主控态下，主播交给本 Agent 的每一件委派走框架侧统一账本（[ADR-076：委派改为一件在做、其余待办，换活必须结单](../decisions/076-delegation-one-in-hand-and-settle-on-switch.md)）。游戏侧的对应机制：

- **一手在握**：本 Agent 任何时刻至多一件"在做"；第二件委派到来时由框架按 `when_busy` 处置（排队或换活），本 Agent 不自己排队
- **换活契约**：换活按"收手 → 试送新件 → 结旧落新"执行——框架先调本 Agent 的 `release_delegation` 让手上的活收手并拿到一句进度摘要，再送新件；新件被拒收时原件连同进度摘要原样送回，不丢单也不卡队列
- **收手纪元隔离**：本 Agent 每次收手推进一个递增纪元号。在途推理或工具返回跨越了一次收手后立即中止收尾——旧批次丢弃回复、刚下达的新目标一并取消，防止旧批次把新单误记进展
- **停手回执**：急停或撤单之后，把哪些目标停下了、哪个没停住（附原因）、手上的活撤没撤拼成一句，作为 `game.attention_required` 发给主播——主播照播前知道实际停干净没有
- **账面归属**：批内委派的受理、标 running、写终态统一走 `DelegationBook`（经注入的任务账本写框架账）；目标账本只给批内委派写游戏侧进展备注，不动委派终态——委派终态只由交付上报、撤单、下播兜底写
- **主播侧决断兜底**（归属主播包 `src/agents/streamer/delegation_stall_watch.py`）：停滞告警按同一委派计数进决策回合材料；停着等定夺的委派连续 N 次告警仍无决断时自动撤单并补场记
- 排队等待阶段的委派不产生告警材料；running 的委派不自动杀，由活动看门狗与目标停滞通路覆盖

## 游戏公屏与玩家社交

### 采集与归属

- `chat_collector` 常驻长轮询聊天流，转出 `game.chat.received`；角色自己的话 Mod 已滤掉，服务器当系统消息下发的回显按角色账号名认出丢弃
- `chat_attribution` 按消息自带凭据附说明：借角色名义发的机器消息（`shown_as`）、有在线核对凭据的照转、无凭据的注明"不一定是这个人说的"
- 私聊照转并标 `private`：只拿来做决定，不当直播素材念出
- 消息里点到的她（人设名/账号名）补一条 `type="self"` 点名词条目——只做事实标注，不判该不该回

### 游戏公屏分级唤醒（`chat_gate`，主控态）

高噪声的游戏公屏按客观信号分级，既不漏掉找她的消息，又不让全服刷屏唤醒空转：

| 分级 | 判定 | 去向 |
|---|---|---|
| 私聊 | `/msg` 对她说 | 唤醒合批 |
| 点名 | 正文提到她 | 唤醒合批 |
| 身边 | 发言者距离在近距离阈值内（`DEFAULT_NEARBY_DISTANCE`，在 chat_gate.py；无距离数据时按身边玩家降级） | 唤醒合批 |
| 近期互动同伴 | 近期（互动窗口时长 `DEFAULT_PARTNER_WINDOW_MS`，在 chat_gate.py）内与她互动过 | 唤醒合批 |
| 远处其余 | 不满足以上 | 有界旁听滑窗，仅行动步顺带取出，绝不唤醒 |

机制细节：

- 事实标签 `[游戏公屏·私聊/点名你/身边N格/远处]` 原样保留采集器附的未核验说明
- 短合并窗（窗长 `DEFAULT_BATCH_WINDOW_MS`，在 chat_gate.py）：单窗条数默认上限（`DEFAULT_MAX_BATCH_ITEMS`），按「私聊 > 点名 > 身边/同伴 > 其余抽样」截留；同人同内容与单人高频连发折叠计数
- 持续刷屏期间启用最小唤醒冷却（时长 `DEFAULT_WAKE_COOLDOWN_MS`，在 chat_gate.py），新消息继续并入同一待发窗
- 唤醒批次以 `[游戏公屏合批]` 入队；非私聊正文同时送本场短期记忆与跨场被动检索预取（私聊正文绝不送外部检索）
- 近期人类原话滚动窗（游戏公屏原话与主播建议各有上限，`DEFAULT_MAX_RECENT_CHATS` / `DEFAULT_MAX_RECENT_SUGGESTIONS`，在 chat_gate.py）供集中整理与重开上下文复用

### 委派态的游戏公屏旁听

非主控时严格空闲零消耗：没有活跃委派任务不入队、不唤醒。活跃任务期间游戏公屏通知入队，且仅在任务挂起等待时唤醒；主播直发工具替角色说的话以 `[游戏公屏·主播替你说]` 回注（自己经 `maicraft:chat` 发的不回注）。

### 玩家肢体动作与互动

- Mod 把 16 格内冲角色做的肢体动作（蹲起、挥手、拍一下、扔东西）按人归段发事件流
- `player_body_language` 读成给主播的一句话（"你"换"我"、去观察编号）与给游戏 Agent 的通知（可用 `body_language` 回一个动作，回完即收尾、不交付不上报）
- 按人叫醒节流（`interaction_throttle`）：同一玩家叫醒过一次后，间隔未达下限（`INTERACTION_WAKE_WINDOW_S`，在 interaction_throttle.py）的先攒着，到点合成一条，单位时间内叫醒次数随之有上限
- 闲着时只为回应互动开的那批不算数，防"一直蹲就一直调模型"
- 切磋（`fight(spar=true)`）收手时按怎么收的手、各打中几下汇总一句给主播；切磋是玩，输赢都俏皮带过
- 从别人家拿吃的、动别人放的东西：Mod 事件两头记账——主播拿回应口径（问起来坦然认下），游戏侧按世界记最近几笔放进任务事实对号
- 结构化互动台账（`player_interactions`）只落库 `game.player.interacted`：打招呼、送东西、切磋对手，供游戏玩家档案跨场使用；主播不订阅这条（当场叙事已从肢体动作那条拿到）

### 公共社交规矩与生物名册

- `social_rules` 按存档缓存多人世界的公共规矩（共享惯例、环境形态、成员关系、记着的事），新任务开局作为固定层事实附上；只放行有游戏公屏逐字引文依据或运营者人工锁定的现行行，拦掉无引文归纳与观众弹幕作证行（防直播间弹幕投毒改游戏内规矩）
- 生物名册（`creature_book`）跨快照记住自家的与起过名的生物：驯服关系自动建档、路人只有被起名才进；死讯与墓碑优先按 UUID 认领，认出后以 `game.milestone` 告知主播。按世界编号分册，旧编号名册在首次按新编号读前整份搬移

## 身体事件与叙事素材

事件流里事件种类三分（判据见 `attention_matrix`）：

- **身体遭遇**（值得向观众叙述）：挨打、换维度、生存需求临时任务开始/结束、处理不了的需求、角色死亡、装备用坏、探索发现、跟随处境变化、天色变化、玩家肢体动作——`attention_collector` 转成 `game.body.*` 与 `game.milestone`
- **任务通道事件**：目标开始/提问/暂停/继续——留给游戏 Agent
- **目标完成/失败/卡住**：不经分类表——完成与失败走 `game.report`（自带主播唤醒），卡住由游戏 Agent 记账本中间态并转发

通知路由（`notice_router`）一张表定每类事件的通道与文案，Agent 执行器只认通道不认种类：天色只挂队列不唤醒（只留最新）、计时与 Mod 决策闲着也开批、肢体动作开只回应互动的批、跟随与身体急事与死亡任务中注入、未知种类走注意流转告主播。

Mod 加事件种类只加一行。

素材发布（`attention_material`）保 Mod 原话：超阈值的长排查说明先压成保信息摘要再发（概述进 `message`、原文留档 `scene`）；概述拿不到时降级为首句斩尾——素材宁可降级也不能不发。

与对话历史或待读队列里一字不差重复的通知只注入一句指回（`repeated_notice`：指回的前提是那份原文模型还看得到）。

- **Mod 挂出的决策**（死亡恢复，`goal_id` 为负数）提问时读一次、带着问题与选项唤醒任务（空闲时也唤醒，没人回答角色就停在死亡屏幕）；模型按选项用 `maicraft_goal(answer)` 回答
- **自己选的死**（`chosen_death`）：手上正在跑 `suicide`（或注意流记下的 `suicide` 目标窗口内）的死是回家手段，给主播的话换成"回家"的说法——先说清为什么死、东西没丢，再轻松带过
- **步进完成**（`game.milestone`）是画面在动、Mod 自带结论句的进展；计时到点本身不算叙事素材，到点后的取货交付才走步进完成

## 后台目标跟踪（events → 唤醒）

- **登记**：`maicraft_execute` 返回没结束的目标（running / awaiting_answer / paused）即开始跟踪；当场完成的目标不跟踪；`maicraft_goal(resume)` 解除暂停的旧目标也纳入
- **读事件**：`GoalWatch` 带游标长轮询 `events`（等待时长走配置键 `events_wait_ms`），事件流只给宿主读、不进模型工具列表；首次读取只建游标
- **事实核实**：事件只说明"变了"，宿主再 `goal(get)` 取它此刻的样子交给任务；finished 事件另带的 `self_brief`（结束时在哪、血、饱食、手上拿着什么）查询里没有，随结果一起交
- **唤醒**：结束（含 `result.folded` 证据按 `path` 读回的指引）、提问、被暂停才唤醒；恢复推进静默记账
- **换世界**：事件流编号变了旧游标作废，逐个重查在跟踪的目标；查不到按"不在了"注入，不让任务等不来
- **挂起等待的停滞看门狗**（`wait_progress`）：让出等待后站桩监视目标是否仍在推进——目标状态事件、自身步数或动作阶段变化、非急事抢占下的实际位移达到有效位移阈值（`POSITION_PROGRESS_BLOCKS`，在 wait_progress.py）或跨维度都算有效进展，刷新计时
- 连续无进展的唤醒等待按指数退避拉长（起点与上限见 `WAIT_PROGRESS_LIMIT_S` / `WAIT_PROGRESS_MAX_LIMIT_S`，序列在 wait_progress.py），目标集合变化或有进展即复位
- 唤醒只是请模型核对现场，长途赶路仍属正常推进时不替它取消目标
- **回炉等待**（`furnace_wait`）：obtain 装好炉先走、挂回炉计时当场以 partial 收场——东西在烧、到点 Mod 叫醒回去取，不算停摆也不代发停了没交付；计时到点、timer 结果与换存档各自划掉对应计时
- **认存档**（`world_identity`）：每页事件与 `observe(self)` 的 `world{id, name}` 喂给三态判定（旧版 Mod / 不明 / 认出）；"换了存档"只认上一个认出的编号被另一个取代
- 认出后：工具面 `current_save()` 报给主播、待办/笔记/近期上报按存档分开（切换只经 `switch_world`）、换档时手上有任务就告知任务是旧存档里接的、生物名册按世界编号分册
- 决策依据见 [ADR-069](../decisions/069-game-memory-per-save.md)
- **任务登记镜像**：目标同时记进通用任务登记，主播任务查询看得到；长时间无进展告警回到 Agent 注入提醒

## 运营干预入口（递话 / 硬取消 / 断连恢复）

- **递话 `receive_prompt`**：纯文本留言（主播经 `framework_prompt`、运营经 REST），不派新任务、不进任务登记——文本入消息队列 + 唤醒；入队按来源加 `[运营原话]` / `[主播补充]` 标签，冲突时原话与游戏结果优先于转述
- **硬取消 `cancel_task_now`**：先向游戏直连取消在跑目标，再结清委派账；直连取消失败照实记日志——停手回执里写明哪个没停住，不静默
- **MCP 断连恢复续跑**：私有 MCP 恢复循环装配成功后注入"连接已恢复"通知 + 解锁挂起态 + 唤醒；能力清单与事件游标按新连接重新读取

入口设计见 [ADR-034](../decisions/034-agent-intervention-primitives.md)；escalation 账面语义见 [ADR-035](../decisions/035-escalation-ledger-semantics.md)。

## 工具契约

**注册名 = `<Provider名字>_<工具名字>`**，分隔符 `_`；前缀由模块声明、ToolRegistry 一处拼接。Provider 名全局唯一、用全名（`minecraft` 禁缩写）。

**决策工具列表**（LLM 可见，注册名）：

| 工具 | 说明 |
|---|---|
| `minecraft_todo` | 待办文档（read/write 全量读写）。任务分解与推进由 LLM 自主决策 |
| `minecraft_notebook` | 工作笔记（read/write）。对话历史会整理、笔记不会——只写跨批次还站得住的结论；当下会变的数字不写。长期要记的地点用 MaiCraft 的 `remember` 记成命名地点 |
| `minecraft_rules` | 独立于笔记的抗折叠钉住规矩槽（按条增删、合计有上限）：同伴交代的共用设施流程、领地边界、工具禁忌等常驻固定层不被历史折叠稀释；durable 条目按当前存档跨场次持久化，临时条目仅本场有效 |
| `minecraft_report` | 上报通道（玩家→主播唯一上报出口）：delivery 交付总结 / escalation 升级决策 |
| `minecraft_skill` | 按名读技能正文；目录由本地直播策略与 MaiCraft `resources/list` 合并，另含 Agent 自建技能卡（本服设施流程/通用玩法，持久化于存储层）。MaiCraft 没连上时远端技能消失 |
| `minecraft_drawing` | 建筑图纸委派（异步）：当场返回任务编号不等画完；同一时间最多一张。后台生成 → Mod 校验（被拒带错误重画，上限按配置）→ [系统] 通知注入唤醒；委派收手时取消生成 |
| `minecraft_context_fold` | 后台折叠工具：整理请求里模型只能调它，调任何游戏动作都丢弃 |
| `minecraft_context_recall` | 按块号读整块、或按关键词取折叠/收起原文的命中片段，结果作为一次工具回执出现在尾部；同时查本场短期记忆（游戏公屏/折叠摘要/笔记规矩） |
| `maicraft_observe` / `maicraft_lookup` / `maicraft_execute` / `maicraft_goal` | MaiCraft v1 的工具，registry 动态发现（每任务重新拉取） |

补充说明：

- **看画面**（`observe(what="picture")`）：文字块当观察进历史；图按额度挂在下一次请求尾部看一次，不能看图的模型经视觉模型转文字。每任务次数与两次最小间隔的额度（`[agents.minecraft.picture]` 配置段），超了不调 Mod
- `maicraft_events` 只给宿主读；截桌面的 `vision_look_at_screen` 不交给游戏 Agent 的模型（截的是整块显示器，看游戏画面用 picture 视图）
- **主控期动态放行的跨 Agent 工具**：`framework_commentary`（提交解说意图，经协调器直通主播轻量 Replyer 润色播出，完全绕过重型规划器）、`rundown_control`（环节 `next`/`renew` 推进流程单）、`streamer_memory_correct`（更正权威承诺表）
- 三者仅主控态可见，每步重算工具定义即时生效

**对外工具**（经 ToolRegistry 注册、主播工具列表可见，不进玩家 LLM 工具列表）：

- `framework_delegate` / `framework_prompt` / `framework_suggest`：委派、递话与非强杀建议通道
- 游戏主控态下指向主控游戏的委派（含 replace 强杀）与结单工具被隐藏或执行期拒绝（[ADR-081](../decisions/081-dynamic-lead-handover.md) 越界禁令）
- `minecraft_get_work_log`：只读返回眼下存档的 `{todo, notebook, recent_reports}`；笔记后面附服务端现查的当前状态快照（位置、背包、装备、数量），读的人以快照为准、笔记数字只当历史参考
- `minecraft_glance`：主播看一眼游戏——叙事事实（身体状态、背包、身边生物与设施、时段天气、记住的地点名）+ 身体手头的工作；观察编号、方位与网格是游戏 Agent 规划用的，不交给主播
- `minecraft_chat_send`：主播直发游戏公屏（被动手脚，不进本 Agent 的 ReAct 循环，仅文本输出）；游戏主控态下对主播隐藏——替角色表态归游戏 Agent 自己

## 对话管理与压缩

- 消息累积：任务内多轮，经 `LLMManager.generate(messages, profile="minecraft", tools=...)`（OpenAI 格式）
- 同一逻辑任务跨后台等待沿用历史。整理分三道，都不让角色站着等（关掉 `active_fold` 时只剩第二道，超预算同步等摘要）：
  1. **后台折叠**：新消息按编号 `[m0012]` 标注；历史过折叠线后另发一次与行动步同前缀、同工具声明的整理请求，请模型用 `minecraft_context_fold` 折出已用完区间的摘要，写好后在下一步开始前按编号套上。系统说明、任务事实、摘要段、开任务的原话与最近几组不能折；整理请求里调的游戏动作一律丢弃
  2. **集中整理**：历史过提前整理线后后台把旧调用组压成一段摘要（行动前或让出等待时发起，与后台折叠互斥）。原始指令、待办、笔记、在跑的目标与最近结束的目标独立保留
  3. **收起**：到预算线还没现成摘要时，把最老的大条记录换成占位、瞬间降到回落线；仍超预算才同步整理
- 每次集中整理只压缩上次整理以来的记录、追加新段不重写旧段；旧段总量超预算两成才并入
- 被折叠或收起的原文进块库，`minecraft_context_recall` 读回；开新任务时块库清空
- 折叠摘要与保留的人类原话同时收录本场短期记忆，供字面未命中时同义回捞
- 工具返回的截图不进历史：历史里只有文字回执，图挂下一次推理请求尾部看一次
- 唤醒续做只补交相对上一份完整任务状态的变化
- 流式不做：决策 Agent 非聊天 Agent；对外叙事经事件→主播侧已有流式表达
- 机制取舍见 [ADR-040](../decisions/040-context-layering-and-checkpoints.md) 与 [ADR-070](../decisions/070-minecraft-background-fold.md)

## 本场短期记忆（`short_term_memory`）

纯内存级，开播/下播清空，绝不把玩家游戏公屏写进跨场数据库：

- 收录三类高信号短文本：玩家游戏公屏/私聊（有界滚动，默认上限 `DEFAULT_MAX_PLAYER_CHATS`，在 short_term_memory.py）、主动折叠块摘要与保留原话、当前存档工作文档切片（笔记段落、钉住规矩，按内容指纹增量更新）
- 召回：中文两字切分关键词 + 可选向量余弦混合；未配嵌入或向量未就绪时无缝退化为关键词路径
- 消费入口：主控态 `[脑中闪过]` 被动闪回、`minecraft_context_recall` 工具、合批时的被动检索预取（私聊正文除外）
- 时间衰减用 Agent 注入的单调时钟域，避免事件自带的墙钟毫秒与计时错位

## 事件契约（确定性系统事件，无 LLM 自觉汇报）

| 事件 | 触发 |
|---|---|
| `game.report` | LLM 调 `minecraft_report`（delivery/escalation），或委派态批次自然终止时代发停了没交付（undelivered）；主控态不发 |
| `game.attention_required` | 身体处理不了的生存需求、角色死亡、任务期间身体先处理的急事、急停与撤单的停手回执；只叙事、不打断任务 |
| `game.error` | 工具执行异常 / LLM 调用失败 / 无 LLM fail-fast |
| `game.milestone` | 步进完成（Mod 自带结论句的进展）与名册认出的死讯 |
| `game.body.*` / `game.chat.received` / `game.self.chatted` / `game.player.interacted` / `game.place.*` | 由两条附属采集器与 Agent 的落库转发发出（语义见 [事件系统](event-system.md)） |

事件 payload 复用 `GamePayload`（`game="minecraft"`）；上报同时进内存 `recent_reports`（保留条数上限 `MAX_REPORTS`，在 state.py）。

## 配置

```toml
[agents.minecraft]
events_wait_ms = 25000            # 跟踪后台目标时一次读事件流最多等多久（MaiCraft 上限 60 秒）
events_retry_interval_ms = 5000   # 附属采集器连不上 Mod 或读失败后的重试间隔（逐次加长，最长 60 秒）

[agents.minecraft.context]        # 工作上下文预算（token，按模型窗口比例换算）
window_ratio = 0.5                # 预算 = min(模型窗口 × window_ratio, max_tokens)
max_tokens = 200000
prepare_ratio = 0.7               # 超过这一比例就在后台提前做集中整理
target_ratio = 0.6                # 整理或收起后压回到这一比例
active_fold = true                # 后台折叠与到预算线先收起；关掉回到只靠集中整理
fold_ratio = 0.4                  # 超过这一比例开始在后台请模型折叠（须低于 prepare_ratio）

[agents.minecraft.mcp]            # Agent 私有 MCP（位置即归属，owner_agent="minecraft"）
enabled = true
url = "http://127.0.0.1:8766/mcp"

[agents.minecraft.picture]        # 看画面的额度
max_per_task = 6                  # 每个任务最多看几次（0 = 不让看）
min_interval_ms = 10000           # 两次至少隔多久

[agents.minecraft.drawing_builder] # 建筑图纸生成（minecraft_drawing 工具）
enabled = true                     # 总开关，关闭时 minecraft_drawing 不进模型工具列表
max_validation_retries = 3         # 图纸被 Mod 校验拒绝后的重画上限
```

- `lead_mode`（`"dynamic"` / `"streamer_only"`）是框架侧主从移交配置，不在此段（见 [ADR-081](../decisions/081-dynamic-lead-handover.md)）
- 主控态的防空转上限、有界短休眠时长、LLM 失败阈值与冷却、被动闪回超时是代码常量（构造参数默认值），不在配置文件
- 游戏公屏门控的合并窗、截留上限、冷却与旁听滑窗大小，玩家互动节流的间隔与次数，均为代码常量

配版本同步规则：配置结构变更升该文件 `[meta].version` 并保证漂移写回（见 [ADR-014](../decisions/014-config-six-file-refactor.md)）。

## 解耦边界

- 通用 MCP 层只归一化协议数据；MaiCraft v1 的返回格式、目标运行状态与事件流语义只在 `maicraft.py` 解读一次
- 工具失败作为错误观察返回 LLM（`error.code` 与 `error.fields` 原样保留），由模型按错误原因调整行动，确实无法推进时上报具体阻塞
- 内容特有逻辑内聚 `src/agents/minecraft/` 包（加内容=加包+配置，框架零改动）；框架与主播侧不出现游戏名（[ADR-018](../decisions/018-game-agnostic-boundary.md)）
- 两条附属采集器挂装配期、随本 Agent 一同开关，不进 `collectors.toml`（见 [ADR-038](../decisions/038-agent-companion-collectors.md)）
- 附属采集器的连接与游标自带，不与 Agent 共用——事件流按读者各自的游标读，互不影响
- 游戏侧请求播音只经 `framework_commentary` 单向提交，绝不直连 TTS/字幕，也不订阅直播间弹幕（[ADR-081](../decisions/081-dynamic-lead-handover.md) 越界禁令）

## 相关文档

- 组件图与目录结构以代码为唯一事实源（`src/`、`ToolRegistry`）
- [架构叙事](v2-architecture.md) — Agent/Tool 判据推导
- [主从移交](dynamic-lead.md) — LeadCoordinator 状态机、12 时刻职责矩阵与 Dashboard 控制面
- [事件系统](event-system.md) — game.* 事件语义单一事实源
- [组件开发指南](../guides/component/README.md) — 游戏 Agent 范式
- [使用指南](../guides/minecraft-agent.md) — 前置清单、配置与排障操作
- [ADR-013](../decisions/013-async-task-infrastructure-and-delegation.md) — 委派基建
- [ADR-034](../decisions/034-agent-intervention-primitives.md) — 干预入口
- [ADR-035](../decisions/035-escalation-ledger-semantics.md) — escalation 账面语义
- [ADR-043](../decisions/043-game-agent-interaction-shapes.md) — 交互形态
- [ADR-076](../decisions/076-delegation-one-in-hand-and-settle-on-switch.md) — 换活结单契约
- [ADR-081](../decisions/081-dynamic-lead-handover.md) — 动态主从移交
- [ADR-018](../decisions/018-game-agnostic-boundary.md) — 游戏无关边界
- [ADR-038](../decisions/038-agent-companion-collectors.md) — 附属采集器
- [ADR-069](../decisions/069-game-memory-per-save.md) — 按存档分记忆
- [ADR-040](../decisions/040-context-layering-and-checkpoints.md) / [ADR-070](../decisions/070-minecraft-background-fold.md) — 上下文分层与后台折叠
