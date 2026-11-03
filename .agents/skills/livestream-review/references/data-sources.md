# 数据源约定（Amaidesu）

> 本文件是直播复盘 skill 的本仓库配套：运行时数据不入版本管理，只存在于实际开播的机器上。其他项目移植时按同类结构自行探测。

## 四类数据源

| 数据 | 位置 | 用途 |
|------|------|------|
| 场次记录 | `data/amaidesu.db` 的 `live_sessions` 表（场次号 = `id`） | 场次定位、开播/结束时间、场次标题 |
| 事件日志 | `logs/events/YYYY-MM-DD/N.jsonl`（按小时轮转：N = 小时） | 主要分析对象：全部事件的追加式记录，一行一个 JSON |
| LLM 请求全文 | `logs/llm_requests/YYYY-MM-DD/N.jsonl` | 用量、缓存命中率、单笔大小、失败；缓存在每行 `usage_raw_json` 的 `prompt_tokens_details.cached_tokens` |
| 应用日志 | `logs/app/amaidesu_YYYY-MM-DD.jsonl` | 进程启停、运行期 WARNING/ERROR 的原因原文与堆栈（事件日志只有摘要） |
| 付费与舰队记录 | `data/amaidesu.db` 的 `gifts`、`super_chats` 表与 `live_chat` 表（guard 行） | 礼物/SC/舰长的致谢核对与付费分析（记账层自动分流，订阅关系见 StorageLedger） |

补充：

- 场次边界：`live.started` → `live.ended`；缺下播事件时用 `core.shutdown` 确定结束边界并在报告注明。进程退出兜底会把结束时间补写进场次记录表，但写入发生在**下次启动**的残留清理——查记录的时点不同会看到不同状态。
- 事件日志的行结构：`event_name`（事件名）、`timestamp_ms`（毫秒 Unix 时刻）、`level`、`source`（来源模块）、`data`（载荷）。时间换算本地 UTC+8。
- 关键事件名速查：`streamer.speech`（发言）、`planner.decision` / `planner.verdict`（决策与话题摘要）、`game.milestone` / `game.attention_required` / `game.body.need_unhandled` / `game.body.attacked` / `game.body.reflex_*`（游戏侧进展与身体反射）、`task.changed`（任务记录）、`room.message.*`（弹幕/礼物/进房）、`tts.utterance.*`（语音合成）、`llm.context.used`（决策请求的上下文水位）、`core.startup` / `core.shutdown` / `core.error`（进程与告警）。事件名的权威定义在 `src/modules/events/names.py` 的 `CoreEvents`。
- **决策事件字段**（`planner.decision.data`）：`trigger_reason`（触发来源）、`should_reply` / `speech` / `silent_reason`（开口判定——"0 沉默"要核这三个字段而非只数发言）、`reply_guidance`（决策环节写给台词环节的指导语，复读源头与情绪偏置的诊断对象）、`emotion`（情绪词，与事件极性对照）、`planner_duration_ms` / `total_duration_ms`（决策时延）、`round_id`（与发言、工具结果关联）。
- **请求全文**（`llm_requests` 行的 `request_params`）：注入层核对的唯一数据源——某条递话/事实/事件"进没进模型、进了多少笔"靠全文检索；`profile_name` 区分决策/台词/游戏档；`usage_raw_json` 里 `prompt_tokens_details.cached_tokens` 算缓存率。请求全文里的"主播此刻……"句多为历史话题摘要的习惯开头，**不是实时状态块**，抽取前先定位章节。
- **感知载荷**（`tool.result.minecraft_glance` 的 `brief` 与 `tool.result.maicraft_observe` 的 `result.data`）：感知层核对的数据源——关键物体/实体"在不在主播的感知输入里"。`brief` 是摘要（列表取前几项、其余以"等"收尾），资产类核对用 `observe self` 的完整 `inventory`，设施类用 `observe scene` 的 `facilities`；实体是否带本名看 `entities[].name`。感知只报"在场"不报"离场"，实体消失要靠逐轮 diff。
- **工具结果状态**（`tool.result.*.data`）：`status`（success/error）、`error_message`、`failure_kind`——"N/N 全成"类断言核到每条 `status`；`tool.health.*` 记熔断开合。
- 事件语义与订阅契约的权威文档：`docs/architecture/event-system.md`。

## 统计标准说明

- 缓存命中率 = Σ`cached_tokens` / Σ`prompt_tokens`，按 `profile_name` 分组（字段区分用途：决策、游戏、台词等，各自的路径与预算不同，分开看）。
- 发言频率以场次窗口内的条数除以窗口分钟数；窗口缺下播事件时按进程关闭确定结束边界并注明。
- "开口前新事件"的对齐标准：取上一条发言之后到达的进展/遭遇/任务类事件；纯统计类推送（房间状态帧、画面帧）不算新事件。
- 费用口径：请求记录本身不落费用字段，由复盘脚本以 usage + `config/model.toml` 价格表现算（每百万 token；分段缓存 = 命中×`cache_price_in` + 未命中×`price_in` + 输出×`price_out`，与 `src/modules/llm/observation.calculate_cost` 同口径）；报告注明价格表数字与统计窗口。
- 致谢核对口径：每笔付费消息（礼物/SC/舰长）对应一次面向该观众的致谢发言，对照发言记录逐笔核对有无漏谢。
