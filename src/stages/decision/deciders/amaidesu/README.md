# AmaidesuDecider

直播专用决策器，归属 Decision 阶段,默认注册名 `amaidesu`。本目录是该组件的随码文档,覆盖使用方式、配置与扩展点。

## 概览

AmaidesuDecider 把直播场景下的"是否参与 + 怎么回复"拆成两阶段决策:**Planner**(战术决策,快速模型,零人设注入)产出 `DecisionPlan`,**Replyer**(回复生成,高质量模型,注入人设)产出 `Intent`。采用聚合缓冲 → 批量决策模式:弹幕经 `MessageBuffer` 暂存,后台 `_flush_loop`(默认 300ms tick)按窗口/条数/空窗补偿取批后驱动两阶段流程。每批**只**发布 `decision.intent.generated` 一个事件,由 `DeciderManager` 调度(`decide()` 入口,组件不订阅任何输入事件)。

2026-08 起新增**主动发言**(Proactive Speech)能力:主播不依赖弹幕也可开口,触发由纯规则组件 `ProactiveTrigger` 负责,触发后以空批次走同一两阶段链路。

## 工作方式

```
NormalizedMessage ─decide()► MessageBuffer ─tick► _maybe_flush (锁内分支)
                                              ├─ buffer 非空:should_flush? → Planner → Replyer → emit Intent
                                              └─ buffer 为空:ProactiveTrigger.should_trigger? → 同上 (batch=[])
```

- **Stage 1 聚合**:`MessageBuffer` 按 `batch_window_ms` / `batch_max_size` / 空窗补偿折算判定取出时机,`TimingGate` 仅保留强制触发判定(SC/Guard/Gift 立即响应)。
- **Stage 2 决策**:`Planner.plan(batch, forced, proactive)` 渲染模板 `decision/amaidesu_planner`,产出 `DecisionPlan`(`should_reply` / `target` / `topic_summary` / `reply_guidance` / `confidence`);`should_reply=False` 直接 no-op。`Replyer.generate(plan, batch, persona)` 渲染模板 `decision/amaidesu_replyer`,产出 `Intent` 并回填 `IntentPayload` 发布。
- **房间态势**:`RoomState`(纯规则,60s 滑动窗口)负责热度/词频/冷场判定,供 `Planner` 注入 prompt;`RoomStateLoop` 后台低频调用独立 LLM profile `llm_summary` 填充 `topic_summary`,冷场自动暂停以控制成本。
- **失败降级**:Planner 异常 / 脏 JSON → silent(`planner_failures+1`);Replyer 同理(`replyer_failures+1`)。两者用独立 LLM client,避免连接池共享。

生命周期、事件表、数据流规则不在此处重复,见[架构总览](../../../../../docs/architecture/overview.md)与[事件系统](../../../../../docs/architecture/event-system.md)。

## 主动发言能力

直播间冷场或定时话题窗口时,主播主动开口。三种触发源,优先级 `external > schedule > cold`:

| 触发源 | 含义 | 典型场景 |
|--------|------|----------|
| `external` | 外部 API 一次性信号(置 `_external_proactive_pending=True`) | Dashboard 手动触发 |
| `schedule` | 距上次主动发言 ≥ `schedule_interval_ms` 且(总是 / 冷场,受 `schedule_only_cold` 控制) | 定时话题 |
| `cold` | 房间持续无弹幕 ≥ `cold_timeout_ms` | 冷场救场 |

判定逻辑收敛在 `ProactiveTrigger.should_trigger()`(`proactive_trigger.py`),无 I/O / 无 LLM / 无 asyncio,时钟靠 `now_ms` 注入便于测试。四个公共前置按短路顺序:总开关 → `min_interval_ms` 防接龙(对照 `room_state.last_speech_ms`)→ `max_per_hour` 滑窗(`collections.deque`)→ `topic_required` 话题缺失则 no-op(防无话找话)。任一失败返回 `None`,否则返回原因字符串。

触发后以**空批次**调用共有链路:`Planner.plan([], proactive=True)` → `Replyer` → 发布 Intent。`source_message_id` 用占位 `"proactive"`(避免 `batch[-1]` 索引崩溃),不伪造 `NormalizedMessage`。发言成功后 `AmaidesuDecider` 更新 `room_state.record_speech()`(所有发言)与 `ProactiveTrigger.record_trigger()`(仅主动),双频率限制生效。

互斥保证:`_maybe_flush` 把 `is_empty` 提前 return 改为**锁内分支判断**,主动发言与弹幕决策共用 `_flush_lock`,批次不交叠。

## 配置项

`[deciders.amaidesu]` 段(详见 `config/decision.toml`),仅列关键字段:

| 字段 | 默认 | 说明 |
|------|------|------|
| `planner_client` | `llm_fast` | Planner 使用的 LLM profile(快速模型) |
| `replyer_client` | `llm` | Replyer 使用的 LLM profile(高质量模型) |
| `batch_window_ms` | `3000` | 弹幕聚合时间窗口 |
| `batch_max_size` | `20` | 单批最多条数 |
| `tick_interval_ms` | `300` | 后台聚合检查间隔 |
| `enable_idle_compensation` | `true` | 空窗补偿:人少时不冷场 |
| `room_state_enabled` | `true` | 启用房间状态后台摘要 |
| `room_state_summary_client` | `llm_summary` | 摘要专用 LLM profile(与 Planner 独立) |
| `proactive_enabled` | `false` | 主动发言总开关(默认关闭,显式开启) |
| `proactive_cold_timeout_ms` | `45000` | 冷场判定阈值(毫秒) |
| `proactive_min_interval_ms` | `120000` | 两次主动发言最小间隔(防接龙) |
| `proactive_schedule_interval_ms` | `300000` | 定时触发间隔,`0` 关闭 |
| `proactive_schedule_only_cold` | `true` | 定时仅在冷场时触发 |
| `proactive_max_per_hour` | `6` | 每小时主动发言次数上限 |
| `proactive_topic_required` | `true` | 话题摘要缺失时跳过触发 |

## 外部 API

Dashboard 提供 `POST /api/v1/proactive/speak` 手动触发发言:

```bash
curl -X POST http://127.0.0.1:60214/api/v1/proactive/speak \
  -H "Content-Type: application/json" \
  -d '{"topic_hint": "聊聊最近读的书"}'
```

请求 body `{"topic_hint": "可选,≤200字符"}`,响应 `{"status": "queued", "deciders": [...]}`。Dashboard handler → `DeciderManager.trigger_proactive(topic_hint)` → `AmaidesuDecider.trigger_proactive(topic_hint)` 仅置一次性标志,实际触发受上述频率限制约束,不会立刻交叠到当前弹幕决策。`topic_hint` 仅日志标注,不参与触发决策。

## 直播大纲机制（Outline）

主播用 TOML 预定义直播环节流程，系统按**时间驱动 + AI 顺带评估**自动推进，零观众时也能按计划直播；弹幕可打断但保持大纲对齐。

**组件**（均位于本目录）：

| 文件 | 职责 |
|------|------|
| `outline.py` | 数据契约（StreamOutline/OutlineSegment/OutlineBranch）+ TOML 解析 |
| `outline_loader.py` | TOML 加载 + 每环节动态 AI 扩展（`llm_outline` profile） |
| `outline_state.py` | 运行时状态机 + 手动控制 + 整场时间轴锚点 |
| `outline_scheduler.py` | 后台调度循环（时间到期推进 + 评估消费） |

**推进机制**：Planner 每次决策顺带输出 `may_advance` / `need_more_time` / `branch_id`（定义于 `plan.py`，零额外 LLM 成本），由 `OutlineScheduler.note_plan_assessment()` 消费——提前/延长/分支跳转。时间到期自动推进兜底。

**触发优先级**：`ProactiveTrigger` 新增 `outline` 触发源，链为 `external > outline > schedule > cold`；`topic_required` 对 outline 不适用（大纲本身提供话题）。

**整场时间轴**：`outline_started_at_ms` 在 `start()` 时记录，`get_elapsed_live_ms()` / `get_total_planned_ms()` / `get_progress_percent()` 供提示词与 Dashboard 展示。

**Dashboard 接口**：`/api/v1/outline/{state,load,control,file,segments}`（详见 `src/modules/dashboard/api/outline.py`）。

**设计文档**：[`docs/architecture/outline-mechanism.md`](../../../../../docs/architecture/outline-mechanism.md)

## 如何扩展

- **新增触发源**:在 `ProactiveTrigger.should_trigger()` 末尾加新分支,优先级位于 `cold` 之后(或不参与优先级链,直接返回新 reason);新增配置字段在 `AmaidesuDecider.ConfigSchema` 同步定义并在 `__init__` 手工映射到 unprefixed sub-dict。
- **其他 Decider 支持外部触发**:实现 `async def trigger_proactive(self, topic_hint: Optional[str] = None) -> None`,`DeciderManager.trigger_proactive()` 用 `hasattr` 鸭子类型跳过未实现的 Decider,无需注册中心介入。
- **替换两阶段流水线**:Pluggable 体现在 `Planner` / `Replyer` 是独立组件类,若需切到不同模型/提示词,实现 `plan()` / `generate()` 同形接口并在 `__init__` 替换即可。

## 相关文档

- 阶段参与者开发:[`docs/development/component-guide.md`](../../../../../docs/development/component-guide.md)
- 架构总览:[`docs/architecture/overview.md`](../../../../../docs/architecture/overview.md)
- 数据流规则:[`docs/architecture/data-flow.md`](../../../../../docs/architecture/data-flow.md)
- 事件系统:[`docs/architecture/event-system.md`](../../../../../docs/architecture/event-system.md)
- 开发规范:[`docs/development-guide.md`](../../../../../docs/development-guide.md)
- 提示词管理:[`docs/development/prompt-management.md`](../../../../../docs/development/prompt-management.md)
- Dashboard API 实现:[`src/modules/dashboard/api/proactive.py`](../../../../dashboard/api/proactive.py)

---

*最后更新:2026-08-13(新增直播大纲机制 outline 组件说明)*
