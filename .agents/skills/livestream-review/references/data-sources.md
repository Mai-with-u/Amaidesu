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
- 事件语义与订阅契约的权威文档：`docs/architecture/event-system.md`。

## 统计标准说明

- 缓存命中率 = Σ`cached_tokens` / Σ`prompt_tokens`，按 `profile_name` 分组（字段区分用途：决策、游戏、台词等，各自的路径与预算不同，分开看）。
- 发言频率以场次窗口内的条数除以窗口分钟数；窗口缺下播事件时按进程关闭确定结束边界并注明。
- "开口前新事件"的对齐标准：取上一条发言之后到达的进展/遭遇/任务类事件；纯统计类推送（房间状态帧、画面帧）不算新事件。
- 致谢核对口径：每笔付费消息（礼物/SC/舰长）对应一次面向该观众的致谢发言，对照发言记录逐笔核对有无漏谢。
