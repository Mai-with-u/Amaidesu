# 模拟直播间调试工具

## 现状（务必先读）

v1 的 `SimulatorService`（独立输入模拟服务）已从组合根脱线，是候选移除项。当前模拟直播间输入**全部**由**模拟采集器** `MockCollector`（`src/modules/collectors/mock/mock_collector.py`）承担，`MockCollector` 继承 `BaseCollector`，作为持续流型感知者通过 EventBus 发布 `room.message.*` 语义事件，与真实弹幕走完全相同的数据通路。

具体脱线说明：

- `src/modules/simulator/simulator.py`：`LiveStreamSimulator` 已退化为最小 stub（`async for message in simulator.collect()` 返回空 async iterator，**不**产生任何消息）。模块顶部 docstring 明确指出 "真实模拟（人设池/节奏生成/礼物生成）的复杂逻辑由 `collectors/mock/` 取代"。
- `src/modules/simulator/service.py`：`SimulatorService` 类仍存在（保留向后兼容接口供 Dashboard API 调用），但内部实例化的 `LiveStreamSimulator` 是 stub，所以即便构造并 `start()`，实际也不会向 EventBus 推送任何消息。
- `main.py` 组合根：`grep SimulatorService main.py` 无实例化代码。`create_app_components()` 只走 CollectorManager → AgentManager → Dashboard 装配链，不创建 `SimulatorService`。

若需旧 `SimulatorService` 的"LLM 驱动人设池 / 节奏生成 / 礼物模拟"能力，请改用 `MockCollector` 的 `mode="simulator"`（不引入 LLM 依赖，使用内置节奏 + 素材池）；若需要 LLM 驱动生成，当前请走真实采集器（如 `bili_danmaku_official`）接入真实直播间或自定义 Collector。

## 历史沿革（背景）

- **v1**：模拟器是独立服务 `SimulatorService`，订阅 `output.intent.finished` 作元控制信号，通过 ContextService pull 模式读取主播最近发言作为 LLM 生成上下文，emit `room.message.*` 事件。
- **v2（Wave 5 合并迁移）**：旧三阶段架构下的 mock 采集器与 `src/modules/simulator/` 合并到 `src/modules/collectors/mock/`，统一为 `MockCollector`，继承 `BaseCollector`，双模式（`jsonl` / `simulator`）。
- **v2（Wave 6 §1.46）**：`SimulatorService` 退化为 stub 占位，仅保留接口兼容。

下文统一按 v2 现状描述。

## 数据流

```text
MockCollector.collect()
  ├─ mode=jsonl    → 解析 data/msg_default.jsonl → NormalizedMessage(simulated=True)
  └─ mode=simulator→ 内置节奏 + 素材池          → NormalizedMessage(simulated=True)
                      │
                      ▼
       emit room.message.{danmaku,gift,super_chat,enter}
                      │
                      ▼
            StreamerAgent 订阅消费（与其他采集器同链路）
```

## 快速开始

### 启用方式

`MockCollector` 是采集器，按 v2 采集器约定在 `config/tools.toml` 的 `[tools.perception.config]` 下启用，**不**使用旧的 `config/simulator.toml` 或 `config/input.toml` 的 `collectors.enabled` 列表。

```toml
# config/tools.toml
[tools]
enabled = ["perception"]

[tools.perception.config]
enabled = ["mock_danmaku"]                # 启用模拟采集器
                                           # 注意是配置在 [tools.perception.config] 下，不是顶层 [collectors]

[tools.perception.config.mock_danmaku]
mode = "jsonl"                             # "jsonl"（默认）| "simulator"
log_file_path = "msg_default.jsonl"        # JSONL 文件名（相对 data_dir）
send_interval = 1.0                        # JSONL 发送间隔（秒，ge=0.1）
loop_playback = true                       # JSONL 到末尾是否循环
emit_semantic_events = true                # 是否 emit room.message.* 语义事件

# simulator 模式字段（mode=simulator 时生效）
base_rate_per_minute = 6.0                 # 基础消息率（条/分钟，ge=0.1, le=60）
burst_multiplier = 3.0                     # 突发期倍率（ge=1.0, le=10）
warmup_duration_s = 0.0                    # 启动暖场期时长（秒）
gift_probability = 0.05                    # 礼物概率（ge=0.0, le=0.5）
sc_probability = 0.01                      # SC 概率（ge=0.0, le=0.1）
enable_hater = false                       # 是否启用黑粉人设
gifts_toml = "simulator_gifts.toml"        # 礼物清单文件名（相对 data_dir）
residents_toml = "simulator_residents.toml"# 常驻人设文件名（相对 data_dir）
```

`data_dir` 由 `MockCollector` 内部固定为 `src/modules/collectors/mock/data/`（即 `Path(__file__).resolve().parent / "data"`，**不**从 `config/` 根目录读取）。

### 启动

```bash
uv run python main.py
```

`CollectorManager` 自动按 `enabled` 列表实例化 `MockCollector` 并 `start_all()`，无需手动调用。

### 查看效果

- **WebUI 控制面板**：http://127.0.0.1:60214 → 侧栏"模拟直播间"（Dashboard 端兼容老 `SimulatorService` API，但实际数据来源是 `MockCollector`）
- 实时消息流通过 `room.message.*` 事件订阅，与其他采集器共用同一通路

## 双模式说明

### `jsonl`（默认）

从 `data/msg_default.jsonl` 按速率逐行回放，每行一条 JSON：

```json
{"text": "弹幕内容", "user_name": "用户名", "user_id": "user_001"}
```

- `send_interval` 控制两条之间的间隔（秒）
- `loop_playback = true` 时到末尾自动重置索引
- 零 LLM 依赖，最轻量，适合 CI / 单元测试与回放固定剧本

### `simulator`

内置节奏 + 素材池生成，不引入 LLM（避免与已脱线的 `SimulatorService` 重复）。

- 简化泊松间隔：`60 / base_rate_per_minute` 秒的 0.5~1.5 倍抖动
- `warmup_duration_s`：启动后前 N 秒视为暖场期（当前实现为静默跳过，不输出消息）
- 随机掷骰子决定 `data_type`：`< sc_probability` 产出 SC；`< sc_probability + gift_probability` 产出礼物；其余产出文本弹幕（内置文案："模拟弹幕消息"、"主播好可爱！" 等）

## 数据溯源：simulated 标记

所有 `MockCollector` 产出的 `NormalizedMessage` 均带 `simulated=True`：

- 事件 Payload (`RoomMessagePayload`) 的 `live_session_id` 默认为 `"simulated_default"`
- `user.id` 来自 JSONL `user_id` 字段或 `sim_xxxx` 随机数；`simulator` 模式文本弹幕固定 user_id 形如 `sim_1000~9999`、user_name `"模拟观众"`

存储层在 `live_chat` / `gifts` / `super_chats` 三张表均带 `simulated INTEGER NOT NULL DEFAULT 0` 贯穿列，约定由 Collector 在写入时打标。消费方做统计时应 `WHERE simulated = 0` 排除模拟样本，避免污染真实观众数据指标。

## 素材池

`MockCollector` 加载路径固定为 `src/modules/collectors/mock/data/`，**不**从 `config/` 根目录读取同名文件。

| 文件 | 实际消费方 | 备注 |
|------|------------|------|
| `src/modules/collectors/mock/data/simulator_gifts.toml` | `MockCollector._load_gifts()`（`simulator` 模式） | 礼物清单，按 `category`（`normal` / `medium` / `premium` / `sc`）分组，字段：`gift_id` / `gift_name` / `category` / `weight` / `data_type`（SC 加 `sc_amount_rmb`） |
| `src/modules/collectors/mock/data/simulator_residents.toml` | 当前**无**消费方 | `MockCollector` 配置字段 `residents_toml` 已声明，但 `_collect_simulator()` 用内置文本弹幕，**未**读取人设池。该字段是后续 LLM 驱动扩展的预留位 |
| `config/simulator_gifts.toml`（项目根 `config/`） | 当前**无**消费方 | v1 遗留文件，`MockCollector` 不从此处读取 |
| `config/simulator_residents.toml`（项目根 `config/`） | 当前**无**消费方 | v1 遗留文件，根 `config/` 与 `src/.../data/` 双份不一致，**根 `config/` 版已无人消费** |

**操作建议**：

- 修改 `simulator_gifts.toml`（礼物清单）：编辑 `src/modules/collectors/mock/data/simulator_gifts.toml`（运行时实际加载此份）
- 修改 `simulator_residents.toml`（人设清单）：编辑 `src/modules/collectors/mock/data/simulator_residents.toml`，但当前 `simulator` 模式未消费该文件，改动不会生效；待未来接入人设池后会自动生效
- `config/` 根目录下的同名文件已无人消费，可按需清理或保留作为历史参考

## 如何造一批测试弹幕

两种路径，按需选用。

### 路径 A：JSONL 回放（推荐，零 LLM 依赖）

1. 准备 JSONL 文件，每行一条：

   ```jsonl
   {"text": "测试弹幕 1", "user_name": "测试用户 A", "user_id": "test_001"}
   {"text": "测试弹幕 2", "user_name": "测试用户 B", "user_id": "test_002"}
   {"text": "主播好可爱", "user_name": "测试用户 A", "user_id": "test_001"}
   ```

2. 放入 `src/modules/collectors/mock/data/<your_file>.jsonl`（如 `msg_test.jsonl`）

3. 编辑 `config/tools.toml`，将 `mock_danmaku` 段改为：

   ```toml
   [tools.perception.config.mock_danmaku]
   mode = "jsonl"
   log_file_path = "msg_test.jsonl"
   send_interval = 0.5
   loop_playback = true
   ```

4. 启动 `uv run python main.py`，观察 Dashboard 弹幕面板

### 路径 B：内置节奏（零配置）

适合临时看效果或做粗略压力测试：

```toml
[tools.perception.config.mock_danmaku]
mode = "simulator"
base_rate_per_minute = 30.0      # 提高频率
gift_probability = 0.1
sc_probability = 0.02
warmup_duration_s = 0.0
```

### 路径 C：批量单元测试

`MockCollector` 继承 `BaseCollector`，可用 `pytest` 直接驱动 `collect()` 协程，无需启动主程序。参考 `tests/modules/collectors/test_mock_collector.py`。

```python
import pytest
from src.modules.collectors.mock import MockCollector

@pytest.mark.asyncio
async def test_mock_jsonl_replay(event_bus):
    collector = MockCollector(
        config={"mode": "jsonl", "log_file_path": "msg_test.jsonl", "send_interval": 0.0},
        event_bus=event_bus,
    )
    await collector.start()
    seen = []
    async for msg in collector.collect():
        seen.append(msg.text)
        if len(seen) >= 3:
            break
    await collector.cleanup()
    assert len(seen) >= 3
```

## SimulatorService 兼容性说明

为不破坏依赖 `simulator_service.simulator.*` 字段的 Dashboard API，`SimulatorService` 类与 `LiveStreamSimulator` stub 仍保留。但实际无消息产出，新建功能**不要**走该路径，统一改用 `MockCollector`。

Dashboard 侧"模拟直播间"页面部分 UI 控件（如礼物雨触发、话题注入、Token 预算重置）历史上指向 `SimulatorService` 控制面。当前若开启 `MockCollector` 的 `mode=simulator`，UI 上对应按钮不会真正触发额外行为，只由 `MockCollector` 按内置节奏产出弹幕。

## 故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| 没有任何模拟弹幕 | `MockCollector` 未启用 | 检查 `config/tools.toml` 的 `[tools.perception.config].enabled` 列表是否包含 `mock_danmaku` |
| 模拟弹幕全是空文本 | `mode=simulator` 模式下内置文案随机生成，确认 `data_type` 分布正常 | 检查 `base_rate_per_minute` 是否合理（默认 6 条/分钟） |
| 模拟数据混进真实观众统计 | 消费方未过滤 `simulated=0` | SQL 加上 `WHERE simulated = 0`（参见 schema 中 `simulated` 贯穿列约定） |
| 编辑了 `config/simulator_gifts.toml` 不生效 | `MockCollector` 实际加载 `src/modules/collectors/mock/data/simulator_gifts.toml`，不读根 `config/` | 编辑内置 `data/` 下文件，或在 `mock_danmaku` 配置段显式指定 `gifts_toml = "<your_file>.toml"` 后放入 `data/` |
| Dashboard 看不到"模拟直播间"按钮 | Dashboard 配置未启用 | 检查 `config/core.toml` 的 `[dashboard]` 段 |
| 礼物种类不对 | `simulator_gifts.toml` 文件路径错 | 默认值 `simulator_gifts.toml`，必须放 `src/modules/collectors/mock/data/` |

## 与单向数据流的关系

- `MockCollector` 是 Input Domain 采集器，只 publish 不 subscribe，符合数据流规则
- 所有 `room.message.*` 事件携带 `simulated=True` 数据溯源标记，消费方按需过滤
- 不订阅 Output 事件（数据流红线，见[数据流规则](../architecture/data-flow.md)）
- 不写入 ContextService（只发事件，事件回放链路不受影响）

## 相关文档

- 事件语义域表：[事件系统](../architecture/event-system.md)
- 数据流红线：[数据流规则](../architecture/data-flow.md)
- 采集器开发范式：[组件开发指南](../development/component-guide.md)
- Agenda 编排：[节目单编排机制](../architecture/agenda-mechanism.md)

---

*最后更新：2026-08-26（按 v2 现状重写：明确 SimulatorService 已脱线 / 候选移除；模拟输入由 MockCollector 承担；描述 jsonl + simulator 双模式用法、素材池角色、造测试弹幕步骤）*