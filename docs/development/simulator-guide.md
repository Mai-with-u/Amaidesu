# 模拟直播间调试工具

## 简介

模拟直播间是 Amaidesu 内置的调试工具，通过 **LLM 实时生成观众消息**（弹幕/礼物/SC），用于：

- **功能演示**：无需真实直播流即可展示系统功能
- **压力测试**：可控频率和消息量，测试系统吞吐
- **Prompt 调试**：在隔离环境中调试 LLM 决策效果

### 工作原理

```
模拟器（LLM）→ NormalizedMessage → EventBus → Decision → Output
                    ↑ pull 模式
             ContextService（主播最近发言）
```

模拟器作为 InputCollector 运行，通过 **ContextService pull 模式**读取主播最近发言作为上下文。在 `auto` 节奏模式下订阅 `output.intent.finished` 作为元控制信号触发突发期（符合架构规则中 Input 阶段可订阅元控制信号的例外），不参与数据平面环路。

## 快速开始

### 最小配置

在 `config/input.toml` 的 `[collectors]` 中添加：

```toml
[collectors]
enabled = ["console_input", "simulated_live_stream"]
```

> 注意：使用默认配置即可启动（不需要增加 `[collectors.simulated_live_stream]` 段）。
> 模拟器需要 LLM 配置（`[llm_fast]` provider）。

### 启动

```bash
uv run python main.py
```

启动后模拟器自动运行：
- 启动前 5 分钟为 **暖场期**，发送简单问候
- 之后进入 **正常模式**，按泊松分布间隔生成弹幕
- 主播有新发言时触发 **突发期**，消息频率提升 3 倍

### 查看效果

- **WebUI 控制面板**：http://127.0.0.1:60214 → 侧栏"模拟直播间"
- 实时消息流通过 WebSocket（`/api/v1/ws/simulator/stream`）推送
- 支持运行时参数调整、礼物雨触发、话题注入、Token 预算重置

## 完整配置说明

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `false` | 是否启用 |
| `base_rate_per_minute` | `6.0` | 基础消息率（条/分钟） |
| `burst_multiplier` | `3.0` | 突发期倍率 |
| `burst_min_interval_s` | `30.0` | 突发最小间隔（防反馈放大） |
| `burst_cooldown_s` | `60.0` | 突发期持续时间（秒） |
| `gift_probability` | `0.05` | 礼物消息概率 |
| `sc_probability` | `0.01` | SC 消息概率 |
| `temp_passerby_ratio` | `0.3` | 临时路人消息比例 |
| `context_window_size` | `5` | 读取主播上下文消息数 |
| `token_budget_per_hour` | `50000` | 每小时 token 硬上限（滑动窗口） |
| `max_concurrent_llm` | `8` | 最大并发 LLM 请求 |
| `max_message_chars` | `50` | 单条消息最大字符数 |
| `enable_hater` | `false` | 黑粉人设（仅 dev） |
| `warmup_duration_s` | `300.0` | 启动暖场期时长（秒） |
| `idle_threshold_s` | `300.0` | 主播无活动进入 idle 的阈值（秒） |
| `idle_rate_multiplier` | `0.2` | idle 状态下消息生成率倍率 |
| `cadence_mode` | `uniform` | 节奏模式：`uniform`（均匀随机）、`fixed`（固定间隔）、`auto`（自适应突发） |
| `fixed_interval_s` | `10.0` | fixed 模式的固定间隔（秒） |
| `llm_client_type` | `llm_fast` | LLM client 类型 |
| `language` | `zh` | 生成消息语言 |
| `session_strategy` | `smart` | session 选择策略 |
| `fallback_session_id` | `simulated_viewers` | 无活跃 session 时的默认 ID |

## 节奏模式

| 模式 | 行为 |
|------|------|
| `uniform` | 基于泊松分布的均匀随机间隔（有界范围 [mean×0.3, mean×2.0]） |
| `fixed` | 固定间隔（`fixed_interval_s` 秒一条） |
| `auto` | 同 uniform，但监听 `output.intent.finished` 事件在主播发言后触发突发期 |

## 四态机说明

| 状态 | 触发条件 | 生成率 |
|------|---------|--------|
| WARMUP | 启动后持续 `warmup_duration_s` 秒 | base × 0.5 |
| NORMAL | 暖场结束，主播有近期活动 | base |
| BURST | 主播发言触发（auto 模式）或手动触发 | base × burst_multiplier |
| IDLE | 距上次主播活动超过 `idle_threshold_s` | base × idle_rate_multiplier |

## 人设定制

编辑 `config/simulator_residents.toml`：

```toml
[[residents.items]]
user_id = "resident_fan_001"
user_nickname = "麦芽糖不加冰"
role = "fan"
personality = "对主播十分热情..."
speaking_style = "捧场积极、爱用感叹号"
fans_medal_level = 32
guard_level = 2
is_temporary = false
```

- `role`: `fan` / `teaser` / `newcomer` / `hater` / `veteran`
- `fans_medal_level`: 0-40（影响消息重要性）
- `guard_level`: 0=无, 1=总督, 2=提督, 3=舰长

## 架构说明

### 与单向数据流的关系

- ✅ **ContextService pull 模式**：合法读取主播发言（ContextService 是共享服务）
- ✅ **auto 模式订阅 `output.intent.finished`**：合法元控制信号订阅（遵循 AGENTS.md 中 Input 阶段可订阅元控制信号的规则，仅用于触发状态机而非读取数据）
- ✅ **不向 ContextService 写入**：只读消费者
- ✅ **不修改 Decider/Handler**：完全隔离

### MaiBot 模式降级

当使用 MaiBot 作为决策引擎时，ContextService 不会被写入 ASSISTANT 消息。
模拟器自动降级到 EventHistoryService 读取 `output.intent.finished` 事件的 summary 字段。
降级后不返回 emotion 信息。

## 故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| 模拟器不生成消息 | LLM 未配置或 Token 超限 | 检查 `config/model.toml`、Dashboard Token 统计 |
| 消息全是暖场问候 | ContextService 为空 | 等待主播发言（模拟器 pull 模式依赖发言） |
| 突发期不触发 | burst_min_interval_s 限制 | 降低突发最小间隔 |
| Dashboard 看不到按钮 | 未启用 `simulated_live_stream` | 检查 `input.toml` 配置 |
| 消息太短/太长 | max_message_chars 配置 | 调整字符限制 |

## v1 限制

- 不直播多人设演化
- 不直播观众间对话
- 不直播语音合成
- 不直播多语言支持（仅中文）
- 不支持录制/回放
- 纯内存统计（重启重置）
