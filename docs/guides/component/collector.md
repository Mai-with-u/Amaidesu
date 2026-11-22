# 添加采集器（Collector）

> 归类判据、端到端消息流与通用规范见[组件开发指南](README.md)。

## 添加采集器

### 适用场景

需要把**外部世界**（B 站直播弹幕 / 控制台输入 / 语音转文字 / JSONL 回放 / 第三方平台 webhook 等）转化为系统可消费的 `room.message.*` 语义事件时，新增一个采集器。

采集器是**流型感知者**——长驻后台、被动等待或主动抓取外部信号，直接构造 `RoomMessagePayload` 等事件载荷并 emit 到 EventBus（自产自发）。

### 基类速览

继承自 [`BaseCollector`](../../../src/modules/collectors/base.py)（位于 `src/modules/collectors/base.py`）。

| 成员 | 必填 | 说明 |
|------|------|------|
| `name: str`（类属性） | ✓ | 唯一标识（与配置段名一致）；缺省时基类兜底为类名小写（Agent 基类无此兜底） |
| `description: str`（类属性） | ✓ | 人类可读描述 |
| `__init__(event_bus=...)` | ✓ | 必须调 `super().__init__(event_bus=event_bus)` |
| `start()` / `stop()` / `cleanup()` | 可覆写 | 基类有状态机实现，子类多覆写以挂自己的后台任务 |
| `_on_start()` / `_on_stop()` / `_on_cleanup()` | 可覆写钩子 | 子类的真实启动/停止/清理逻辑放这里 |
| `collect() -> AsyncIterator[Any]` | **必须覆写** | 数据流出口；在生成器内自行构造事件载荷并 emit（自产自发，基类不兜底转发） |
| `emit_event(name, payload, source=...)` | 工具方法 | 封装 emit，bus 为 None 时安全跳过 |
| `set_event_bus(bus)` | 工具方法 | 生命周期内事后注入 EventBus |
| 状态机 | — | `CREATED → STARTING → RUNNING → STOPPING → STOPPED → ERRORED`（`CollectorState` 枚举） |

> **关键**：基类**不强制**子类覆写方法（不挂 `abc.ABC`），所有方法都有合理默认实现；子类按需覆写。`collect()` 抛 `NotImplementedError`——子类必须实现。

### 最小骨架代码

下面给出**两种范式**的最小骨架——选其一即可。

#### 范式 A：子类自行 emit 语义事件（推荐用于需精细控制 payload 的采集器）

参考：`src/modules/collectors/console/console_input_collector.py`（控制台采集器覆写 `start/stop/cleanup` 开自己的 `_run_input_loop` 后台任务）。

```python
"""
MyCollector —— 示例采集器（范式 A：子类自行 emit room.message.*）
放 src/modules/collectors/<your_name>/<your_name>_collector.py
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, Optional

from src.modules.collectors.base import BaseCollector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms


class MyCollector(BaseCollector):
    name = "my_collector"  # 与 config/collectors.toml 顶层 enabled 列表项 + 子段 [my_collector] 同名
    description = "示例采集器：从外部源 X 采集并 emit room.message.danmaku"

    class ConfigSchema(BaseConfig):
        """Pydantic 配置（自动校验）。"""
        user_id: str = "my_user"
        user_nickname: str = "示例用户"
        poll_interval_s: float = 1.0

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
    ):
        super().__init__(event_bus=event_bus)
        self.logger = get_logger(self.__class__.__name__)
        self.config = config or {}
        self.typed_config = self.ConfigSchema.from_dict(self.config)
        self.is_started = False
        self._task: Optional[asyncio.Task] = None

    # ---------- 覆写生命周期：自己开后台任务（不走基类 _start_collect_task）----------

    async def start(self) -> None:
        """覆写：开后台循环任务（基类默认实现也会被替换）。"""
        if self.is_started:
            return
        self.is_started = True
        self._task = asyncio.create_task(self._my_loop())
        self.logger.info("MyCollector 后台循环已启动")

    async def stop(self) -> None:
        if not self.is_started:
            return
        self.is_started = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def cleanup(self) -> None:
        await self.stop()

    # ---------- 子类实现 collect()（外部消费者为空时仅作兼容口保留）----------

    async def collect(self) -> AsyncIterator[Any]:
        """主动推事件模式下，外部消费者可为空；保留此方法只为兼容旧接口。"""
        while self.is_started:
            await asyncio.sleep(self.typed_config.poll_interval_s)
            # 真实采集逻辑在外层 _my_loop 里完成
            yield payload  # 已在本生成器内 emit

    # ---------- 子类自带后台任务：直接 emit 语义事件（基类无兜底转发） ----------

    async def _my_loop(self) -> None:
        """后台任务：实时抓外部源 + emit room.message.danmaku。"""
        while self.is_started:
            try:
                payload_data = await self._fetch_external()
                if payload_data is None:
                    await asyncio.sleep(self.typed_config.poll_interval_s)
                    continue

                payload = RoomMessagePayload(
                    live_session_id="my_collector",
                    message_type="danmaku",  # text/gift/super_chat/enter
                    user=RoomMessageUser(
                        id=str(payload_data["user_id"]),
                        name=str(payload_data["user_nickname"]),
                    ),
                    content=str(payload_data["text"]),
                    timestamp_ms=now_ms(),
                )
                await self.emit_event(CoreEvents.ROOM_MESSAGE_DANMAKU, payload)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"MyCollector 循环异常: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    async def _fetch_external(self) -> Optional[Dict[str, Any]]:
        """外部数据源对接（WS/HTTP/SDK 调用等）。"""
        # TODO: 实现你的真实数据源对接
        return None
```

#### 范式 B：基类后台消费 + 生成器内 emit（推荐用于持续流的采集器）

参考：`src/modules/collectors/stt/stt_collector.py`（走基类 `_start_collect_task()` + `_consume_collect()`）。

```python
"""
MyStreamCollector —— 示例采集器（范式 B：生成器内直发事件，基类驱动后台消费）
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

from pydantic import Field

from src.modules.collectors.base import BaseCollector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms


class MyStreamCollector(BaseCollector):
    name = "my_stream"
    description = "示例采集器：collect() 内直发 room.message.danmaku"

    class ConfigSchema(BaseConfig):
        interval_ms: int = Field(default=1000, ge=100)

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Optional[EventBus] = None,
    ):
        super().__init__(event_bus=event_bus)
        self.logger = get_logger(self.__class__.__name__)
        self.config = config or {}
        self.typed_config = self.ConfigSchema.from_dict(self.config)
        self.is_started = False

    # ---------- 走基类后台消费（_start_collect_task） ----------

    async def start(self) -> None:
        if self.is_started:
            return
        self.is_started = True
        await self._start_collect_task()  # 基类方法：创建 _consume_collect 后台任务

    async def stop(self) -> None:
        if self.is_started:
            self.is_started = False
            await self._stop_collect_task()

    async def cleanup(self) -> None:
        await self.stop()

    # ---------- 必实现：生成器内自行构造载荷并 emit（自产自发） ----------

    async def collect(self) -> AsyncIterator[RoomMessagePayload]:
        """持续产出事件载荷；emit 由本生成器自行完成，基类不兜底转发。"""
        while self.is_started:
            await asyncio_sleep_ms(self.typed_config.interval_ms)
            payload = RoomMessagePayload(
                message_type="danmaku",
                user=RoomMessageUser(id="u1", name="示例"),
                content="示例弹幕",
                timestamp_ms=now_ms(),
            )
            await self.emit_event(CoreEvents.ROOM_MESSAGE_DANMAKU, payload)
            yield payload


async def asyncio_sleep_ms(ms: int) -> None:
    import asyncio
    await asyncio.sleep(ms / 1000)
```

> **范式选择**：
> - 采集器全走同一形态：`collect()` 内自行构造事件载荷并 emit（自产自发，基类零转换零兜底）
> - 走基类后台消费（`_start_collect_task`）还是自开后台循环（如 console 的 `_run_input_loop`）由采集器自行选择，两者都要求生成器/循环内完成 emit

### 装配路径

| 步骤 | 位置 | 操作 |
|------|------|------|
| ① 放代码 | `src/modules/collectors/<your_name>/<your_name>_collector.py` | 类名 `XxxCollector`，`name = "<注册名>"`。读的是某个**游戏/具体内容**的流时，它是那个 Agent 的附属采集器，不走本表，见下方"Agent 附属采集器" |
| ② 注册工厂 | `src/modules/collectors/factory.py` | 加一行 `if name == "<注册名>":` → `return XxxCollector(config, event_bus)`；同时把 `<注册名>` 加进 `SUPPORTED_COLLECTORS` 元组 |
| ③ 写配置 | `config/collectors.toml` | 顶层 `enabled` 加 `"<注册名>"`；同名子段 `[<注册名>]` 放具体参数（由该采集器包内 ConfigSchema 校验） |
| ④ 启停接口 | 自动接入 | `CollectorManager.enable_collector(name, config, event_bus)` 会走工厂 `instantiate_collector` 实例化；`disable_collector` 停止+移除 |
| ⑤ Dashboard | 自动可见 | 组件管理页从 `SUPPORTED_COLLECTORS` 拉清单；通过 `src/modules/dashboard/api/components.py` 的 `_sync_enabled_config` 写回 `enabled` 列表 |

本表只管**通用采集器**（直播间与操作者的输入：弹幕、控制台、语音转写），它们谁都能用，在 `collectors.toml` 里单独开关。

### Agent 附属采集器

读某个 Agent 所接外部世界的持续流（例如 Minecraft 的身体事件流、游戏聊天流）的采集器，离开那个 Agent 就没有意义——Agent 没开时只会对着连不上的游戏一直重试。它们是该 Agent 的**附属采集器**：

- **代码**放在 Agent 包内（`src/agents/<Agent 名>/`），仍是 `BaseCollector` 子类，事件照样自产自发；
- **构造**由 Agent 包提供一个函数（例如 `src/agents/minecraft/companions.py` 的 `build_companion_collectors`），配置从 Agent 自己的配置段换算——连接复用 Agent 的 MCP 配置，不在别处另抄一份；
- **登记**在 `src/modules/agents/companions.py`：`COMPANION_COLLECTORS` 加一行 `"<Agent 名>": ("<采集器名>", ...)`，`build_companion_collectors` 加一个分支指向 Agent 包的构造函数（这是装配登记表，和 Agent 工厂同一类位置）；
- **不进** `collectors.toml`、不进采集器工厂与配置注册表；
- **开关**只看所属 Agent 在不在 `agents.enabled`：启动时随 Agent 装上，Dashboard 启停 Agent 时一并启停，Agent 重启或心跳自愈（重建）时不动——附属采集器重新接入只对齐游标、不补发，跟着重建就会漏掉重建期间的事；
- **Dashboard** 照常列出运行状态，`owner` 字段写所属 Agent，单独开关会被拒绝并提示去开关 Agent。

**谁调用注册？** `main.py` 的 `_register_collectors_from_config` 在启动装配时遍历 `collectors.toml` 顶层 `enabled` 列表逐个 `instantiate_collector` → `CollectorManager.register` → `CollectorManager.start_all`；未知名走 warn + 跳过，不 raise。

### 测试要点

- **隔离 EventBus**：测试时构造 `MockEventBus()` 或直接 `EventBus()`（空 bus 即可，Collector 会跳过 emit）
- **断言事件**：订阅 `room.message.danmaku` 等到 bus 后断言 `RoomMessagePayload` 字段
- **状态机**：起停后断言 `collector.state == CollectorState.RUNNING / STOPPED`
- **后台任务幂等**：重复 `start()` 不应产生多个后台任务（参考 `BaseCollector._start_collect_task` 的幂等设计）
- **真实范例测试**：`tests/modules/collectors/test_collector_framework.py`、`tests/modules/collectors/test_bili_payload_fields.py`

### 真实范例指引

| 范例 | 文件 | 范式 |
|------|------|------|
| ConsoleInputCollector（控制台输入） | `src/modules/collectors/console/console_input_collector.py` | A（子类自开 `_run_input_loop`） |
| STTCollector（语音转文字） | `src/modules/collectors/stt/stt_collector.py` | A |
| BiliDanmakuCollector（官方/legacy） | `src/modules/collectors/bilibili/{official,legacy}/` | A |

---

