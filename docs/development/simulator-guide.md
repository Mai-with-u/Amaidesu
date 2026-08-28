# 模拟直播间开发基础设施

> **v2.0.7+（ADR-006 已落地）**：本文按 [ADR-006：LLM 模拟器是官方开发基础设施，mock 采集器仅承担确定性回放](../architecture/adr/006-simulator-is-dev-infrastructure.md) 重写。**模拟器（`SimulatorService`）与 mock 采集器（`MockCollector`）是两种互补的开发工具——前者是 LLM 驱动的生成式仿真（开放性行为锻炼），后者是确定性 JSONL 回放（固定剧本与 bug 复现）。** 不要把它们当成"二选一"。

## 1. 定位与架构位置

**模拟器 = 开发基础设施**（与 Dashboard / `--dry` / 日志系统同类），不属于生产直播组件：

- **默认关闭**：`[simulator].enabled = false`（生产零沾染）；
- **按需装配**：组合根 `main.create_app_components` 在步骤 4b（CollectorManager 之后、AgentManager 之前）实例化 `SimulatorService` 并挂入生命周期；
- **数据二等**：模拟器产生的事件 payload `simulated=True` 溯源标记贯穿，统计与入库一律排除（详见 §4）。

**主体性判据检验**（AGENTS.md 红线）：模拟器不采集任何东西（不是采集器），不被调才干活（不是工具）；四态节奏与人设池自我驱动——按判据是 Agent 形态，但服务于开发者而非观众，故归入**开发工具域**以可选装配的开发服务形态存在。详见 ADR-006 第 33 行。

## 2. 快速上手

### 2.1 启用方式

在 `config/core.toml` 的 `[simulator]` 段设 `enabled = true`：

```toml
# config/core.toml
[simulator]
enabled = true                                  # 启用模拟器（开发期临时开启）
llm_client_type = "llm_fast"                    # 用 llm_fast profile（便宜/快）
llm_temperature = 0.9                           # 创造性稍高
token_budget_per_hour = 50000                   # 1 小时滑动窗口 token 硬上限
```

启动 `uv run python main.py`，观察到以下日志即装配成功：

```
INFO | SimulatorService - 模拟器配置已启用，自动启动中...
INFO | SimulatorService - 模拟器服务已启动
```

`--dry` 模式下 `simulator_auto_start = False`，组合根**不**调用 `start()`，**不**产生任何 LLM 调用，验证 wiring 后立即退出。

### 2.2 配置字段（22 个）

| 字段 | 默认 | 说明 |
|------|------|------|
| `enabled` | `false` | 总开关；生产保持 `false` |
| `base_rate_per_minute` | `6.0` | 基础消息率（条/分钟），`ge=0.1, le=60` |
| `burst_multiplier` | `3.0` | BURST 态倍率（`ge=1.0, le=10`） |
| `burst_min_interval_s` | `30.0` | 两次突发最小间隔（秒） |
| `burst_cooldown_s` | `60.0` | 突发态持续时间（秒） |
| `temp_passerby_ratio` | `0.3` | 路人比例（`ge=0.0, le=1.0`） |
| `gift_probability` | `0.05` | 每条消息是礼物的概率 |
| `sc_probability` | `0.01` | 每条消息是 SC 的概率 |
| `context_window_size` | `5` | 读取主播上下文消息数（`ge=1, le=20`） |
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
| `fallback_session_id` | `"simulated_viewers"` | 无活跃 session 时使用的 fallback |
| `stats_persistence` | `false` | 是否持久化统计（v1 仅 in-memory） |
| `cadence_mode` | `"uniform"` | 节奏模式：`uniform` / `fixed` / `auto` |
| `fixed_interval_s` | `10.0` | `fixed` 模式的固定间隔（秒，`ge=1.0, le=120.0`） |

> **JSONL 数据文件**：`gifts.toml` / `residents.toml` 位于 `data/simulator/`（运行时数据，由 simulator 包通过 `Path(__file__).resolve().parents[3] / "data" / "simulator"` 引用，可编辑）。**与 mock 采集器的数据目录分离**——mock 采集器的 `data/` 不再承载 simulator 字段。

## 3. 工作原理（六组件编排）

`SimulatorService` 是**生命周期管理器**，不亲自生成消息；它实例化本包 8 个核心实现类构建生成循环：

```text
┌─────────────────────────────────────────────────────────────┐
│ SimulatorService._run() 主循环（src/modules/simulator/service.py）│
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
 CadenceGenerator      PersonaPool       SessionSelector
（节奏状态机）         （人选池）          （活跃会话选择）
  uniform/fixed/auto    常驻+路人加权       smart 策略
        │                   │            fallback_session_id
        │                   ▼
        │              pick_one() → Persona
        ▼
 next_delay_seconds()  ┌──────────────────────┐
        │              │ 概率分支             │
        ▼              │  < gift_probability  │
 (按间隔等待)          │    → GiftGenerator   │
                       │  否则                │
                       │    → SimulatorLLMWrapper│
                       └──────────────────────┘
                                  │
                                  ▼
                     RoomMessagePayload(simulated=True)
                                  │
                                  ▼
                  EventBus.emit(ROOM_MESSAGE_DANMAKU, source="simulated_live_stream")
```

### 3.1 八个核心实现类

| 类 | 职责 | 文件 |
|----|------|------|
| `PersonaPool` | 常驻 + 临时路人混合池，加权选择 | `persona_pool.py` |
| `CadenceGenerator` | WARMUP/NORMAL/BURST/IDLE 四态节奏机 | `cadence.py` |
| `GiftGenerator` | 礼物/SC 生成（含 SC 文本 LLM 调用） | `gift_generator.py` |
| `SimulatorLLMWrapper` | LLM 调用包装（prompt 渲染 + 信号量 + 响应清洗 + token 累计） | `llm_wrapper.py` |
| `SessionSelector` | ContextService 活跃 session 智能选择（60s TTL 缓存） | `session_selector.py` |
| `TokenBudgetController` | 1 小时滑动窗口 + 80%/100% 两级阈值 | `token_budget.py` |
| `StreamerContextSnapshot` | 主播上下文快照数据结构 | `types.py` |
| `SimulatorConfigSchema` | 配置 Pydantic Schema | `config_schema.py` |

### 3.2 主循环行为（service.py `_run()`）

1. **预算硬上限**：`token_budget.is_budget_exceeded()` → 跳过生成但保留循环可被 `stop_event` 唤醒
2. **节奏间隔**：`cadence.next_delay_seconds()` → 用 `asyncio.wait_for(stop_event.wait(), timeout=delay)` 实现"可中断的间隔等待"
3. **人选**：`persona_pool.pick_one()` → 按 `temp_passerby_ratio` 权重在常驻与路人之间选择
4. **会话**：`session_selector.select_session(fallback_id)` → 选活跃 session 或 fallback
5. **概率分支**：掷骰子 `< gift_probability` → `gift_generator.generate_gift()`；否则 → `llm_wrapper.generate_viewer_message()`
6. **token 累计**：成功调用后 `token_budget.record_usage(tokens)`
7. **emit**：`RoomMessagePayload(simulated=True, ...)` → `EventBus.emit(ROOM_MESSAGE_DANMAKU, source="simulated_live_stream")`

### 3.3 LLM 缺失降级

`setup()` 通过 duck-type 在 `services_by_type` 探测 LLMManager（需 `chat` + `chat_fast` + `setup` 三属性）。**未注入时 warning 降级**：

```
WARNING | SimulatorService - simulator: LLMManager 未通过 services_by_type 注入，
         LLM 生成循环将被禁用（仅数据平面就绪）
```

数据平面（人设池/节奏/礼物清单）正常就绪，但 `_llm_wrapper is None` → `start()` 不启动（"模拟器实例未创建" warning）。**主循环不抛异常**，可独立调试数据平面。

### 3.4 取消传播语义

- 主循环 `_run()`：`except asyncio.CancelledError: debug 日志后 raise`——保持 task 取消语义
- `stop()`：`asyncio.wait_for(self._task)` + `current_task.cancelling() > 0` 区分"stop() 主动 cancel" vs "stop() 被外层 cancel"，后者必须 `raise` 让外层看到取消
- 关闭链：`CollectorManager.stop_all → SimulatorService.stop → AgentManager.stop_all → EventRecorder.stop → EventBus.cleanup → LLMManager.cleanup → ContextService.cleanup`

## 4. simulated 溯源

模拟器与 mock 采集器产出的事件 payload 全部携带 `simulated=True` 数据溯源标记（`RoomMessagePayload.simulated: bool = Field(default=False, ...)`）。

**§1.6 用户拍板定案**（mock 采集器删除 simulator 半吊子模式后的统一约定）：

- **存储表**：`live_chat` / `gifts` / `super_chats` 三表均带 `simulated INTEGER NOT NULL DEFAULT 0` 贯穿列（`src/modules/storage/schema.py:149/163/177`）
- **统计查询**：`WHERE simulated = 0` 排除模拟样本，避免污染真实观众数据指标
- **payload 层**：当前 simulator 与 mock_collector 已贯穿；存储记账器从 payload 读取 `simulated` 写入对应列的改造属存储侧任务（**不升 SCHEMA_VERSION**，表结构已就位）

**语义区分**：模拟器与 mock 采集器共享 `simulated=True` 标记——它们都是"非真实观众"。消费方无需区分来源，只需过滤"是真实观众数据 vs 是开发期模拟/回放"。

## 5. mock_collector 与模拟器的关系

**两种互补的开发工具，不互斥**：

| 维度 | MockCollector（确定性 JSONL 回放） | SimulatorService（LLM 驱动仿真） |
|------|------------------------------------|--------------------------------|
| **目的** | 回归测试、bug 复现、固定剧本演练 | Agent 开放性行为锻炼、压力测试、真实交互模拟 |
| **数据来源** | `data/msg_default.jsonl` 等 JSONL 文件 | LLM 实时生成（基于 persona + 主播上下文） |
| **LLM 依赖** | 零 | 必需（profile 由 `llm_client_type` 配置） |
| **Token 成本** | 0 | 受 `token_budget_per_hour` 控制 |
| **启用方式** | `[tools.perception.config].enabled` 含 `mock_danmaku` | `[simulator].enabled = true` |
| **关闭默认** | enabled 由 `[tools.perception.config].enabled` 控制 | `enabled = false`（生产零沾染） |
| **Payload 标记** | `simulated=True`（同 simulator） | `simulated=True` |

**何时用哪个**：

- **CI 自动化测试**：用 `MockCollector` JSONL 模式——确定性、可复现、零 LLM 成本
- **开发期手工调试主播 Agent 行为**：用 `SimulatorService`——需要看到 Agent 对"开放性观众消息"的反应，硬编码 5 句模板做不到
- **bug 复现**：用 `MockCollector` + 现场采集的 JSONL——把当时观众消息录下来反复重放
- **压力测试**：用 `SimulatorService` + 高 `base_rate_per_minute`（如 60 条/分钟）——生成足够多观众消息暴露 Agent 边界条件

**ADR-006 决策基础**：两者不可互相替代——固定世界测确定性，生成世界测开放性行为。硬编码模板对 Replyer 语境理解零锻炼价值。详见 ADR-006 第 30 行（"硬约束："段）与第 34 行（"对原拍板的再审判"段）。

## 6. 与 v1 的差异

| 维度 | v1（已废弃） | v2（当前） |
|------|--------------|------------|
| **架构位置** | `SimulatorService` 独立服务 + `LiveStreamSimulator` 类本体 | `SimulatorService` 生命周期管理器 + 8 个核心实现类协作 |
| **生命周期** | 通过 ContextService pull 模式读主播上下文 + 订阅 `output.intent.finished` 元控制信号 | 独立 `[simulator].enabled` 控制 + 直接 emit 到 EventBus |
| **关闭状态** | 在 v2 Wave 6 退化为 stub（`async for message in simulator.collect()` 返回空），未装配 | 按 ADR-006 恢复，组合根步骤 4b 装配，`enabled=false` 零沾染 |
| **LLM 调用** | 由 `LiveStreamSimulator` 内聚调用 | 通过 `SimulatorLLMWrapper` 解耦，支持 prompt 渲染/响应清洗/token 累计/信号量 |
| **数据治理** | 模拟数据混在真实流中 | `simulated=True` 贯穿 payload → 存储，统计一律排除 |
| **配套 mock 采集器** | `src/stages/input/collectors/mock_danmaku/`（旧三阶段） | `src/modules/collectors/mock/MockCollector`（v2 BaseCollector 子类），纯 JSONL 回放 |

---

## 相关文档

- [ADR-006：LLM 模拟器是官方开发基础设施，mock 采集器仅承担确定性回放](../architecture/adr/006-simulator-is-dev-infrastructure.md) — 本文档的决策定案
- [架构总览 - 组件清单](../architecture/overview.md#组件清单) — 速查
- [数据流规则](../architecture/data-flow.md) — 模拟器不订阅下游事件（采集器红线不适用，但语义等价）
- [事件系统](../architecture/event-system.md) — `room.message.*` 事件事实表
- [组件开发指南](../development/component-guide.md) — Agent/工具/采集器三范式

---

*最后更新：2026-08-28（按 ADR-006 重写：定位从"调试工具候选移除"翻转回"开发基础设施"；删除 stub 时代叙事 + simulator 模式过时引用；新增六组件编排图、配置字段表、LLM 缺失降级说明、simulated 溯源定案引用、mock vs simulator 对照表、v1 vs v2 差异表；§3 取消传播语义显式标注；§5 强调互补不互斥）*