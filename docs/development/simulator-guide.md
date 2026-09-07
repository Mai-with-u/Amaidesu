*最后更新：2026-09-06（录制回放数据源迁移：`data/events/*.jsonl` 文件 → SQLite `event_history` 表；读回 API 由模块级函数改为 `SQLiteStore.list_event_dates()` / `get_day_events(date, event_name=...)`，历史 JSONL 已全量导入真实库后文件退役）*

# 世界模拟器开发基础设施

> **世界发射器统一（ADR-006 修订版）**：`SimulatorService` 是唯一的模拟消息发射器，三模式切换——
> `generate`（LLM 生成）/ `replay`（录制回放）/ `off`。旧 `MockCollector`（确定性 JSONL 回放采集器）
> 已删除，其回放职责由 `mode=replay` 承载。详见 [ADR-006](../architecture/adr/006-simulator-is-dev-infrastructure.md)
> 及其修订记录。

## 1. 定位与架构位置

**模拟器 = 开发基础设施**（与 Dashboard / `--dry` / 日志系统同类），不属于生产直播组件：

- **默认关闭**：`[simulator].enabled = false`（生产零沾染）；
- **按需装配**：组合根 `main.create_app_components` 在步骤 4b（CollectorManager 之后、AgentManager 之前）实例化 `SimulatorService` 并挂入生命周期，注入 SQLiteStore；
- **数据二等**：模拟器产生的事件 payload `simulated=True` 溯源标记贯穿，统计与入库一律排除（详见 §5）。

**主体性判据检验**（AGENTS.md 红线）：模拟器不采集任何东西（不是采集器），不被调才干活（不是工具）；四态节奏与人设池自我驱动——按判据是 Agent 形态，但服务于开发者而非观众，故归入**开发工具分类**以可选装配的开发服务形态存在。

## 2. 快速上手

### 2.1 启用方式

在 `config/core.toml` 的 `[simulator]` 段设 `enabled = true`：

```toml
# config/core.toml
[simulator]
enabled = true                                  # 启用模拟器（开发期临时开启）
mode = "generate"                               # generate=LLM 生成 / replay=录制回放 / off
llm_client_type = "llm_fast"                    # 用 llm_fast profile（便宜/快）
llm_temperature = 0.9                           # 创造性稍高
token_budget_per_hour = 50000                   # 1 小时滑动窗口 token 硬上限
```

启动 `uv run python main.py`，观察到以下日志即装配成功：

```
INFO | SimulatorService - 模拟器配置已启用（mode=generate），自动启动中...
INFO | SimulatorService - 模拟器服务已启动（mode=generate）
```

`--dry` 模式下 `simulator_auto_start = False`，组合根**不**调用 `start()`，**不**产生任何 LLM 调用，验证 wiring 后立即退出。

### 2.2 三模式说明

| 模式 | 行为 | 适用场景 |
|------|------|---------|
| `generate` | 四态节奏驱动：选人设 → 读世界窗口 → LLM 生成弹幕/概率礼物/SC | 开放性行为锻炼，暴露 Agent 真实表现 |
| `replay` | 读 SQLite `event_history` 表录制，按原节奏重放（可调速度） | 确定性回归测试、bug 复现 |
| `off` | 装配但不运行世界 | 只用人设/礼物 CRUD 与观测，不产生消息 |

replay 模式的录制日期可在启动时通过配置 `replay_date` 指定，或在 Dashboard「世界模拟器」页运行期选择；`replay_speed` 倍率加速，`replay_gap_cap_s` 截断超长冷场。

### 2.3 配置字段

| 字段 | 默认 | 说明 |
|------|------|------|
| `enabled` | `false` | 总开关；生产保持 `false` |
| `mode` | `"generate"` | 世界模式：`generate` / `replay` / `off` |
| `replay_date` | `null` | replay 默认回放的录制日期（`YYYY-MM-DD`） |
| `replay_speed` | `1.0` | 回放速度倍率（`ge=0.1, le=100`） |
| `replay_gap_cap_s` | `60.0` | 回放相邻消息间隔上限（秒），截断超长冷场 |
| `replay_simulated_only` | `false` | 回放时是否仅回放录制中已标记 simulated 的消息 |
| `base_rate_per_minute` | `6.0` | 基础消息率（条/分钟），`ge=0.1, le=60` |
| `burst_multiplier` | `3.0` | BURST 态倍率（`ge=1.0, le=10`） |
| `burst_min_interval_s` | `30.0` | 两次突发最小间隔（秒） |
| `burst_cooldown_s` | `60.0` | 突发态持续时间（秒） |
| `temp_passerby_ratio` | `0.3` | 路人比例（`ge=0.0, le=1.0`） |
| `gift_probability` | `0.05` | 每条消息是礼物的概率 |
| `sc_probability` | `0.01` | 每条消息是 SC 的概率 |
| `context_window_size` | `5` | 世界窗口兜底条数（角色/人设未指定时） |
| `idle_threshold_s` | `300.0` | 主播无活动进入 IDLE 的阈值 |
| `idle_rate_multiplier` | `0.2` | IDLE 态生成率倍率 |
| `warmup_duration_s` | `300.0` | 启动暖场期时长 |
| `max_message_chars` | `50` | 单条消息最大字符数 |
| `llm_client_type` | `"llm_fast"` | LLM profile（`llm` / `llm_fast` / `vlm` 等） |
| `llm_temperature` | `0.9` | LLM 采样温度 |
| `token_budget_per_hour` | `50000` | 1 小时滑动窗口 token 硬上限 |
| `max_concurrent_llm` | `8` | 最大并发 LLM 请求数 |
| `enable_hater` | `false` | 是否启用黑粉人设（仅 dev） |
| `language` | `"zh"` | 生成消息语言 |
| `session_strategy` | `"smart"` | session 选择策略 |
| `fallback_session_id` | `"simulated_viewers"` | 兜底场次 ID |
| `cadence_mode` | `"uniform"` | 节奏模式：uniform / fixed / auto |
| `fixed_interval_s` | `10.0` | fixed 模式固定间隔（秒） |

### 2.4 运行期控制（Dashboard）

侧边栏「世界模拟器」（`/simulator`）工作台：

- **世界控制**：启停世界循环、选择录制日期启动回放、查看回放进度（`total/remaining`）；
- **常驻人设**：表格 CRUD（写穿 SQLite `sim_personas`）；
- **礼物目录**：表格 CRUD（写穿 SQLite `sim_gifts`）；
- **配置与说明**：只读配置摘要。

对应 API 挂在 `/api/v1/simulator/*`（status / start / stop / replay/dates / personas CRUD / gifts CRUD）。

## 3. 运行时数据：人设与礼物（SQLite）

常驻人设与礼物目录是**存储层运行时数据**，不使用配置文件：

- 表：`sim_personas`（`user_id` 唯一）/ `sim_gifts`（`gift_id` 唯一），`SCHEMA_VERSION=2`；
- **内置种子**：启动期 `seed_simulator_data` 检测空表时导入内置默认值（`src/modules/simulator/seed_data.py`），非空表一律不动（幂等）；全新安装无需任何手工配置；
- **写穿**：`PersonaPool` / `GiftGenerator` 持内存缓存，Dashboard CRUD 即时落库并刷新缓存；
- **临时路人**（`temp_passerby_ratio` 控制比例，池上限 50）是瞬时对象，仅存内存、不持久化——身份生命周期分层：常驻=持久实体，路人=瞬时对象。

**per-persona 上下文窗口**：`sim_personas.context_window_size`（可选字段）覆盖角色默认窗口（veteran 12 / fan 10 / teaser 8 / newcomer 5 / hater 8 / passerby 2），表达"该角色对直播间的关注度"这一性格属性；两级都未指定时回落全局 `context_window_size`。

## 4. 观众上下文：世界窗口

观众生成消息前读取"这个观众眼中的直播间"——**SQLite `live_chat` 公共流最近窗口**（观众弹幕 + 主播发言同表，场次隔离）：

- 窗口读取：`SQLiteStore.list_recent_live_chat(live_session_id, limit)`，`live_session_id` 由 `session_pk_to_int(session_id)` 从 payload 场次字符串映射（与 `StorageLedger` 写入共用同一映射函数）；
- 一致性语义：弱一致——emit 与落库之间有毫秒级时序差，秒级生成节奏下可忽略；
- 重启不丢：世界状态唯一事实源在 SQLite，模拟器不维护第二份内存状态；
- **token 预算**：窗口注入按"每字符 2 token"粗估计入 `TokenBudgetController`，与生成消耗共享同一硬上限。

## 5. simulated 溯源定案

模拟世界（generate 生成 + replay 回放）产出的事件 payload 全部携带 `simulated=True` 溯源标记（`RoomMessagePayload.simulated: bool`）。

- **存储层**：`StorageLedger` 订阅 `room.message.#` 落库时写 `live_chat` / `gifts` / `super_chats` 的 `simulated` 贯穿列；统计查询 `WHERE simulated = 0` 排除模拟数据；
- **语义**：`simulated=True` = "非真实观众数据"，消费方无需区分生成或回放来源；
- **回放的特殊点**：回放消息落库时间戳刷新为当前时刻（进入"最近窗口"查询语义），原始时刻保留在录制文件与日志中；`live_session_id` 替换为当前场次（回放内容作为"现在的输入流"注入）。

## 6. 录制与回放

- **录制源**：EventHistoryService 落库的 SQLite `event_history` 表（全量事件、payload 完整 model_dump JSON）——录制即世界快照，无需第二种录制格式；
- **读回 API**：`SQLiteStore.list_event_dates()` / `get_day_events(date, event_name=...)`（按本地日期过滤，时间正序）；
- **回放队列**：`ReplayEngine` 过滤 `room.message.danmaku` 事件、还原 payload、按相邻毫秒时间戳差值调度；
- **典型用法**：真实直播一晚 → 次日用 replay 模式重放给主播 Agent 锻炼（`replay_simulated_only=false` 回放全部真实弹幕）。

## 7. 停止语义

`stop()` 由用户主动发起时吞掉 `self._task` 的 `CancelledError`（正常返回）；`stop()` 协程自身被外层 `cancel()` 时（`cancelling() > 0`）必须重新 `raise` 传播取消状态。关闭链中 `SimulatorService.stop` 位于 CollectorManager.stop_all 之后、AgentManager.stop_all 之前（EventBus.cleanup 之前解绑订阅，顺序安全）。

## 相关文档

- [ADR-006：LLM 模拟器是官方开发基础设施](../architecture/adr/006-simulator-is-dev-infrastructure.md) — 定位与修订记录
- [事件系统](../architecture/event-system.md) — `room.message.*` / `streamer.speech` 事件表
- [数据流规则](../architecture/data-flow.md) — 模拟数据在数据面的二等地位

---

*最后更新：2026-09-05（世界发射器统一重写：三模式架构（generate/replay/off）替代"simulator + MockCollector 互补"叙事；运行时数据迁 SQLite（sim_personas/sim_gifts + 内置种子，删 TOML 数据文件说明）；新增世界窗口（观众上下文）节；replay 控制参数与 Dashboard 工作台说明；删除 mock vs simulator 对照表与 stats_persistence 死字段）*
