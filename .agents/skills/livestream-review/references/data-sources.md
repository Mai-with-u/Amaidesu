# 数据源约定（Amaidesu）

> 直播复盘 skill 的本仓库配套：运行时数据不入版本管理，只存在于实际开播的机器上。其他项目移植时按同类结构自行探测。事件名的权威定义在 `src/modules/events/names.py` 的 `CoreEvents`，本文只列复盘用到的。

## 四类数据源

| 数据 | 位置 | 用途 |
|------|------|------|
| 场次记录 | `data/amaidesu.db` 的 `live_sessions` 表（场次号 = `id`） | 场次定位、开播/结束时间、场次标题 |
| 事件日志 | `logs/events/YYYY-MM-DD/N.jsonl`（按小时轮转：N = 小时） | 主要分析对象：全部事件的追加式记录，一行一个 JSON |
| LLM 请求全文 | `logs/llm_requests/YYYY-MM-DD/N.jsonl` | 用量、缓存命中率、单笔大小、失败；缓存在每行 `usage_raw_json` 的 `prompt_tokens_details.cached_tokens` |
| 应用日志 | `logs/app/amaidesu_YYYY-MM-DD.jsonl` | 进程启停、运行期 WARNING/ERROR 的原因原文与堆栈（事件日志只有摘要） |
| 付费与舰队记录 | `data/amaidesu.db` 的 `gifts`、`super_chats` 表与 `live_chat` 表（guard 行） | 礼物/超级留言/舰长的致谢核对与付费分析（记账层自动分流，订阅关系见 StorageLedger） |

补充：

- 场次边界：`live.started` → `live.ended`；缺下播事件时用 `core.shutdown` 确定结束边界并在报告注明。进程退出兜底会把结束时间补写进场次记录表，但写入发生在**下次启动**的残留清理——查记录的时点不同会看到不同状态。
- 事件日志的行结构：`event_name`（事件名）、`timestamp_ms`（毫秒 Unix 时刻）、`level`、`source`（来源模块）、`data`（载荷）。带场次号的事件在载荷里有 `live_session_id`。时间换算本地 UTC+8。
- 应用日志的行结构：`timestamp`（带时区的完整时刻）、`level`、`module`、`message`，出错时另有 `exception`（堆栈）。
- 事件语义与订阅契约的权威文档：`docs/architecture/event-system.md`。

## 开工先导出

`scripts/session_export.py --session <场次号>` 按场次记录表的边界取事件（认载荷里的 `live_session_id`，跨零点不受影响），产出文件的清单与含义写在该脚本文件头的说明里。导出目录里 `summary.md` 的五节：

- **数据范围**：场次边界、事件文件与行数、导出时刻。进程还在运行或刚重启时看到的是中间态，结论要等最终状态。
- **运行锚点**：开播前的进程启动、启动前最近一笔提交、启动行里的装配信息（语音服务、采集器）、流程单与主控切换。**只是线索**——定性由复盘写；跑这一场的是开播前最后一次启动。
- **固定指标**：每场同口径的一组数字，只量不判（名单见下文）。
- **基础设施表**：语音合成配对、挂起唤醒、工具回执成败与错误码、连接类事件、应用日志告警归并。
- **覆盖缺口**：已结束、达到正场时长、复盘目录的文件名里却找不到的场次。

事件分布、生命周期、模型用量与费用另跑 `scripts/overview_stats.py`，窗口写完整的日期时间（命令在 `summary.md` 的"下一步"里）。

## 说话的链路与核对点（游戏主控期间）

主播说的话在这条链路上一站一站传下去；每一站出错，表现都是"说的和做的不对得上"，但归属不同。

| 环节 | 事件与字段 | 核对什么 |
|------|-----------|---------|
| 游戏 Agent 提交目标 | `tool.result.maicraft_execute`：`round_id`（模型轮次）、`result.data.goal_id`（带目标编号的才是目标提交）、`status`、`error_message` | 那一轮提交了什么，Mod 当场回了什么 |
| 目标受理与终态 | `task.changed`：`status`（accepted / succeeded / failed / cancelled）、`snapshot.goal_id` / `ability` / `purpose`、`summary`（终态摘要） | 做没做成，什么时候有的结果 |
| 游戏 Agent 写解说要点 | `agent.lead.commentary`：`summary`（要点原文）、`mood`、`about_player`、`correction`、`reason`；`tool.result.framework_commentary`：`round_id` | 要点原文与所在的模型轮次。`reason` 的三个取值：dispatched 已出口、throttled_recorded 被节流仅记录、same_round_deduped 同轮去重 |
| 回复器成句 | `streamer.speech`：`utterance_id`、`text`、`emotion`、`source`、`reply_to_user_name`、`reply_player_game` | 正式发言与解说要点是否一致（成败、对象有没有翻） |
| 语音出声 | `tts.utterance.started` / `playback_started` / `finished` / `failed` / `dropped`，按 `utterance_id` 配对 | 观众什么时候听到、有没有丢句。**出声锚点是 playback_started**，started 在合成前就发了 |
| 现场与互动 | `game.chat.received`（`kind` = player / system、`sender`、`content`）、`game.self.chatted`（`message`）、`game.player.interacted`（`player_name`、`actions`）、`game.milestone`、`game.attention_required`（`message`、`notable`） | 世界记录：谁做了什么、游戏里发生了什么 |
| 流程单与主控切换 | `rundown.changed`（`action`、`segment_title`、`index` / `total`、`by`）、`agent.lead.changed`（`lead_agent`、`reason`） | 环节的任务描述是否在诱发某类发言；谁在主导说话 |

其他核对点：

- **请求全文**（`llm_requests` 行的 `request_params`）：注入层核对的唯一数据源——某条递话/事实/事件"进没进模型、进了多少笔"靠全文检索；`profile_name` 区分决策、台词、游戏档；游戏主控期间，核对游戏档的请求里有没有终态回执，以及回复器请求里的状态块原文。请求全文里的"主播此刻……"句多为历史话题摘要的习惯开头，**不是实时状态块**，抽取前先定位章节。
- **感知载荷**（`tool.result.minecraft_glance` 的 `brief` 与 `tool.result.maicraft_observe` 的 `result.data`）：感知层核对的数据源——关键物体/实体"在不在主播的感知输入里"。`brief` 是摘要（列表取前几项、其余以"等"收尾），资产类核对用 `observe self` 的完整 `inventory`，设施类用 `observe scene` 的 `facilities`；实体是否带本名看 `entities[].name`。感知只报"在场"不报"离场"，实体消失要靠逐轮 diff。
- **人在游戏里一眼能看到、模型看不到的东西**：机器状态查询的回执不一定把每个部件都当作有运行状态的机器给出；把模型当时的感知载荷还原成"它看到的世界"，再和人的现场对照，差在哪往往就是根因。
- **规划器事件字段**（`planner.decision.data`，规划器跑的场次才有多轮）：`trigger_reason`（触发来源）、`should_reply` / `speech` / `silent_reason`（开口判定——"0 沉默"要核这三个字段而非只数发言）、`reply_guidance`（发言要点）、`emotion`、`planner_duration_ms` / `total_duration_ms`（规划器时延）、`round_id`。游戏主控期间规划器每场只跑收播等少数几轮，全场情绪分布看 `streamer.speech.emotion`。
- **工具结果状态**（`tool.result.*.data`）：`status`（success / error）、`error_message`、`round_id`（模型轮次）——"N/N 全成"类断言核到每条 `status`；`tool.health.*` 记熔断开合。

## 固定指标

每个指标只定义一次，算法在 `scripts/session_export.py`，各场引用 `summary.md` 里的同名数字，不另定口径；只量不判，判定由用户做。

| 指标 | 量的什么 |
|------|---------|
| 发言与解说条数 | 发言按来源分（游戏解说驱动、进房欢迎、规划器）；解说分已出口、被节流仅记录、同轮去重 |
| 相邻出声间隔与静默 | 按出声时刻算间隔的中位与最大；各档门槛以上的静默段数与合计；出声时长占首末出声跨度的比例 |
| 发言到出声时延 | 发言事件到观众听到，中位、p90、最大 |
| 目标与受理到终态时延 | 目标的成功、失败、取消数；从受理到终态的时延分布 |
| 模型轮次 | 含解说的轮次、其中同一轮还提交了新目标的、只提交目标整轮没有解说的 |
| 言行对齐两类 | 已出口的解说之前同轮已提交的目标里，已失败或取消的、出口时还没有结果的 |
| 负面事件后的发言 | 受击、死亡、装备损坏、需求未处理、他人状态播报之后，紧随的一段时间内有没有发言 |
| 逐字重复 | 与之前若干条发言的字符重合率；只认得出逐字重复 |
| 台词里的英文词 | 多为玩家账号名、物品或工具标识 |

## 统计标准说明

- 缓存命中率 = Σ`cached_tokens` / Σ`prompt_tokens`，按 `profile_name` 分组（字段区分用途：决策、游戏、台词等，各自的路径与预算不同，分开看）。
- 发言频率以场次窗口内的条数除以窗口分钟数；窗口缺下播事件时按进程关闭确定结束边界并注明。
- "开口前新事件"的对齐标准：取上一条发言之后到达的进展/遭遇/任务类事件；纯统计类推送（房间状态帧、画面帧）不算新事件。
- 费用口径：请求记录本身不落费用字段，由复盘脚本以 usage + `config/model.toml` 价格表现算（每百万 token；分段缓存 = 命中×`cache_price_in` + 未命中×`price_in` + 输出×`price_out`，与 `src/modules/llm/observation.calculate_cost` 同口径）；报告注明价格表数字与统计窗口。
- 致谢核对口径：每笔付费消息（礼物/超级留言/舰长）对应一次面向该观众的致谢发言，对照发言记录逐笔核对有无漏谢。
