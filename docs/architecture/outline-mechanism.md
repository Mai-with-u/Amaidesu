# 直播大纲机制（Outline Mechanism）

> 本文档描述直播大纲机制的设计与实现，是 `src/stages/decision/deciders/amaidesu/outline*.py` 组件的权威设计说明。
> 事件表、数据流规则等单一事实源不在此重复，见[事件系统](./event-system.md)与[数据流规则](./data-flow.md)。

## 概述

直播大纲机制为 AmaidesuDecider 增加"战略层"：主播用 TOML 预定义直播环节流程，系统在**时间驱动 + AI 顺带评估**下自动推进，即使零观众也能按计划直播；弹幕可打断但保持大纲对齐；支持主播通过 Dashboard 手动干预与在线编辑。

## 状态机

```
INACTIVE ──start()──▶ RUNNING ──skip()到末段──▶ COMPLETED ──unload()──▶ UNLOADED
                        │
                        ├─pause()──▶ PAUSED（子状态，status 仍为 RUNNING）──resume()──▶ RUNNING
                        │
                        └─unload()──▶ UNLOADED
```

- **INACTIVE**：未激活（初始 / 卸载后未启动）
- **LOADING**：加载中（保留位，加载期间使用）
- **RUNNING**：运行中（含 PAUSED 子状态）
- **COMPLETED**：已完成（最后一段走完）
- **UNLOADED**：已卸载（清除锚点，等下一次 start）

实现位于 `outline_state.py` 的 `OutlineState` 类，纯内存状态，无持久化。

## TOML 大纲格式

```toml
outline_id = "live"
title = "通用直播大纲"
fallback_segment_id = "outro"  # 分支未命中时回退

[[segments]]
id = "opening"
title = "开场"
task_description = "欢迎观众，介绍今天主题"  # AI 自由发挥的任务指引
duration_ms = 300000                        # 默认时长（毫秒，ge=1000）
min_duration_ms = 120000                    # 最少停留时长（可选，防御 AI 过早推进）
key_points = ["自我介绍", "今日主题", "近况分享"]

[[segments.branches]]                       # 分支列表（可选）
branch_id = "hot_topic"
description = "观众提到热门话题时跳转"       # 给 LLM 的分支触发条件描述
target_segment_id = "deep_chat"
```

数据契约（Pydantic，`extra="forbid"`）定义在 `outline.py`：`StreamOutline` / `OutlineSegment` / `OutlineBranch`。

## 推进机制（混合推进）

| 机制 | 触发 | 说明 |
|------|------|------|
| **时间驱动** | 环节 `duration_ms` 到期 | 零观众时也能自动推进（`OutlineScheduler._tick`） |
| **AI 顺带评估** | Planner 每次决策顺带输出 | `may_advance` / `need_more_time` / `branch_id`（零额外 LLM 调用成本） |

AI 评估字段定义在 `plan.py` 的 `DecisionPlan`（version 1.1），由 `OutlineScheduler.note_plan_assessment()` 消费：

- `need_more_time=True` 且未到硬超时 → 延长当前段 duration 一次（重新计时）
- `may_advance=True` 且已达 `min_duration_ms` → 推进到下一段
- `branch_id` 命中 → 跳转到分支目标段；分支目标不存在 → fallback 到 `fallback_segment_id`（无则顺序下一段）

## 触发优先级链

`ProactiveTrigger.should_trigger()` 优先级：`external > outline > schedule > cold`

- **outline 触发源**：大纲激活 + 当前环节存在 → 返回 `"outline"`（优先级高于 schedule/cold，低于 external）
- **topic_required 对 outline 不适用**：大纲本身提供话题，不依赖 `topic_summary`

## 与 RoomState / ProactiveTrigger 的关系

| 组件 | 定位 | 关系 |
|------|------|------|
| `RoomState` | 弹幕态势（战术层） | 大纲不替代它；两者正交，大纲是战略层 |
| `ProactiveTrigger` | 主动发言触发判定 | 大纲通过新增 outline 触发源接入其优先级链 |
| `OutlineState` | 大纲运行时状态（战略层） | 独立状态对象，仿 RoomState 的可注入时钟 + snapshot 范式 |
| `OutlineScheduler` | 后台调度循环 | 仿 RoomStateLoop 生命周期，时间驱动推进 + 评估消费 |

## 整场时间轴

- **锚点**：`outline_started_at_ms` 在 `OutlineState.start()` 时记录
- **已进行时长**：`get_elapsed_live_ms()` = `now - outline_started_at_ms`
- **总计划时长**：`get_total_planned_ms()` = 各环节 `duration_ms` 累加
- **进度百分比**：`get_progress_percent()` 夹取到 [0, 100]
- **卸载重载**：`unload()` 清除锚点，`start(new_outline)` 视为新一轮，不累计旧时长

## AI 扩展

每环节进入时用独立 `llm_outline` profile 调用 LLM 生成 `{opening_line, topic_guidance, talking_points}`，缓存到 `OutlineState.expanded_cache`，本环节所有 Planner/Replyer 提示词注入（`$outline` 变量）。

**失败兜底**：LLM 调用失败 → 1 次重试 → fallback 任务描述原文，绝不中断直播。

## 弹幕关系

规划主导，弹幕可打断：弹幕永远被响应，但 Planner/Replyer 提示词注入当前环节上下文（`$outline`），让回复"顺着主线"，回应后回到计划。

## Dashboard 接口

| 端点 | 说明 |
|------|------|
| `GET /api/v1/outline/state` | 当前状态（含整场进度） |
| `POST /api/v1/outline/load` | 加载指定大纲文件 |
| `POST /api/v1/outline/control` | 手动控制 skip/pause/resume/rewind/jump |
| `PUT /api/v1/outline/file` | 写回 TOML（下一段生效） |
| `GET /api/v1/outline/segments` | 完整环节列表（供编辑页） |

## 配置字段

`[deciders.amaidesu]` 段（详见 `config/decision.toml`）：

| 字段 | 默认 | 说明 |
|------|------|------|
| `outline_enabled` | `false` | 大纲总开关（默认关闭，显式开启） |
| `outline_path` | `""` | 大纲 TOML 文件路径 |
| `outline_expand_client` | `llm_outline` | AI 扩展用 profile（独立连接池） |
| `outline_advance_eval_enabled` | `true` | Planner 顺带评估开关 |
| `outline_scheduler_tick_ms` | `1000` | 调度循环 tick 间隔 |
| `outline_auto_start` | `true` | setup 时自动加载并启动 |

## 架构约束

- 大纲组件全部位于 `src/stages/decision/deciders/amaidesu/`（决策阶段内部契约）
- 不新增任何事件类型（复用决策阶段既有的事件通路，见[事件系统](./event-system.md)）
- 不订阅 Output 事件（数据流红线，见[数据流规则](./data-flow.md)）
- 调度器通过 `on_advance` 回调通知 Decider，由 Decider 走正常决策链（不直接 emit）

---

*最后更新：2026-08-13（新增直播大纲机制设计文档）*
