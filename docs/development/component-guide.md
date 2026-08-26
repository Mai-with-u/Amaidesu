# 组件开发指南（v2 三范式）

本指南介绍如何在 Amaidesu 项目中扩展三类核心组件：**采集器（Collector）**、**工具（Tool）**、**Agent**。

> **v2 范式说明**：项目已从旧版"Input / Decision / Output 三阶段 + Decider / OutputHandler 装饰器"重构为"**采集器 + Agent + 工具**"三件套：
> - **采集器** = 流型感知者（外部 → 系统入口，主动推 `room.message.*` 事件）
> - **Agent** = 决策主体（订阅事件 → 内部编排 Planner/Replyer → 调工具 → 出回复）
> - **工具** = 单一能力契约（被 Agent 调，可被任意 Agent 复用）
>
> 旧 `InputCollector / Decider / OutputHandler / Intent / @pipeline` 等概念在本范式下**全部废弃**——新代码不应再使用旧阶段类名。

## 目录

- [添加采集器](#添加采集器)
- [添加工具](#添加工具)
- [添加 Agent](#添加-agent)
- [端到端消息流](#端到端消息流)
- [通用规范与引用](#通用规范与引用)

---

## 添加采集器

### 适用场景

需要把**外部世界**（B 站直播弹幕 / 控制台输入 / 屏幕变化 / 语音转文字 / JSONL 回放 / 第三方平台 webhook 等）转化为系统可消费的 `room.message.*` 语义事件时，新增一个采集器。

采集器是**流型感知者**——长驻后台、被动等待或主动抓取外部信号，构造 `NormalizedMessage`（v2 兼容期）或直接构造 `RoomMessagePayload` 并 emit 到 EventBus。

### 基类速览

继承自 [`BaseCollector`](../../src/modules/collectors/base.py)（位于 `src/modules/collectors/base.py`）。

| 成员 | 必填 | 说明 |
|------|------|------|
| `name: str`（类属性） | ✓ | 唯一标识（与配置段名一致） |
| `description: str`（类属性） | ✓ | 人类可读描述 |
| `__init__(event_bus=...)` | ✓ | 必须调 `super().__init__(event_bus=event_bus)` |
| `start()` / `stop()` / `cleanup()` | 可覆写 | 基类有状态机实现，子类多覆写以挂自己的后台任务 |
| `_on_start()` / `_on_stop()` / `_on_cleanup()` | 可覆写钩子 | 子类的真实启动/停止/清理逻辑放这里 |
| `collect() -> AsyncIterator[Any]` | **必须覆写** | 数据流出口；要么自带 emit，要么返回 NormalizedMessage 由基类兜底转发 |
| `emit_event(name, payload, source=...)` | 工具方法 | 封装 emit，bus 为 None 时安全跳过 |
| `set_event_bus(bus)` | 工具方法 | 生命周期内事后注入 EventBus |
| `_emit_semantic_events: bool`（实例属性） | 可选 | `True` 表示子类在 `collect()` 里自行 emit 语义事件；`False`/缺省 → 基类 `_emit_normalized_message` 按 `data_type` 自动转发 |
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
    name = "my_collector"  # 与 [tools.perception.config.my_collector] 段名一致
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

    # ---------- 子类实现 collect()（外部消费者为空时仅作 v2 兼容口保留）----------

    async def collect(self) -> AsyncIterator[Any]:
        """v2 主动推事件模式下，外部消费者可为空；保留此方法只为兼容旧接口。"""
        while self.is_started:
            await asyncio.sleep(self.typed_config.poll_interval_s)
            # 真实采集逻辑在外层 _my_loop 里完成
            yield  # 视需要返回 NormalizedMessage

    # ---------- 子类自带 emit：标记 _emit_semantic_events=True 让基类跳过兜底 ----------

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

#### 范式 B：基类兜底转发（推荐用于返回 NormalizedMessage 流的采集器）

参考：`src/modules/collectors/mock/mock_collector.py`（默认走基类 `_start_collect_task()` + `_consume_collect()`）。

```python
"""
MyStreamCollector —— 示例采集器（范式 B：基类按 data_type 自动映射）
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

from pydantic import Field

from src.modules.collectors.base import BaseCollector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.types.base.normalized_message import NormalizedMessage


class MyStreamCollector(BaseCollector):
    name = "my_stream"
    description = "示例采集器：基类兜底转发 NormalizedMessage → room.message.*"

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

    # ---------- 必实现：返回 NormalizedMessage 流；不设 _emit_semantic_events → 基类兜底 ----------

    async def collect(self) -> AsyncIterator[NormalizedMessage]:
        """持续 yield NormalizedMessage；基类按 data_type 自动映射到 room.message.*：
        - text        → room.message.danmaku
        - gift        → room.message.gift
        - super_chat  → room.message.super_chat
        - guard       → room.message.enter
        """
        while self.is_started:
            await asyncio_sleep_ms(self.typed_config.interval_ms)
            yield NormalizedMessage(
                text="示例弹幕",
                source=self.name,
                data_type="text",  # 决定 emit 哪个事件
                importance=0.5,
                timestamp_ms=now_ms(),
                user_id="u1",
                user_nickname="示例",
                platform="my_stream",
            )


async def asyncio_sleep_ms(ms: int) -> None:
    import asyncio
    await asyncio.sleep(ms / 1000)
```

> **范式选择**：
> - 需要精细控制 `RoomMessagePayload` 字段（如带礼物信息、上舰详情）→ 范式 A
> - 数据形态已是 `NormalizedMessage` → 范式 B（最少代码）
> - 都不需要时：基类 `_emit_normalized_message` 已能覆盖标准 text/gift/super_chat/guard 四种 data_type 的兜底转发

### 装配路径

| 步骤 | 位置 | 操作 |
|------|------|------|
| ① 放代码 | `src/modules/collectors/<your_name>/<your_name>_collector.py` | 类名 `XxxCollector`，`name = "<注册名>"` |
| ② 注册工厂 | `src/modules/collectors/factory.py` | 加一行 `if name == "<注册名>":` → `return XxxCollector(config, event_bus)`；同时把 `<注册名>` 加进 `SUPPORTED_COLLECTORS` 元组 |
| ③ 写配置 | `config/tools.toml` 的 `[tools.perception.config]` | `enabled = ["<注册名>"]` + `[tools.perception.config.<注册名>]` 子段放具体参数 |
| ④ 启停接口 | 自动接入 | `CollectorManager.enable_collector(name, config, event_bus)` 会走工厂 `instantiate_collector` 实例化；`disable_collector` 停止+移除 |
| ⑤ Dashboard | 自动可见 | 组件管理页从 `SUPPORTED_COLLECTORS` 拉清单；通过 `src/modules/dashboard/api/components.py` 的 `_sync_enabled_config` 写回 `enabled` 列表 |

**谁调用注册？** `main.py` 的 `_register_collectors_from_config` 在启动装配时遍历 `[tools.perception.config].enabled` 列表逐个 `instantiate_collector` → `CollectorManager.register` → `CollectorManager.start_all`。

### 测试要点

- **隔离 EventBus**：测试时构造 `MockEventBus()` 或直接 `EventBus()`（空 bus 即可，Collector 会跳过 emit）
- **断言事件**：订阅 `room.message.danmaku` 等到 bus 后断言 `RoomMessagePayload` 字段
- **状态机**：起停后断言 `collector.state == CollectorState.RUNNING / STOPPED`
- **后台任务幂等**：重复 `start()` 不应产生多个后台任务（参考 `BaseCollector._start_collect_task` 的幂等设计）
- **真实范例测试**：`tests/collectors/test_console_input_collector.py`、`tests/collectors/test_mock_collector.py`

### 真实范例指引

| 范例 | 文件 | 范式 |
|------|------|------|
| ConsoleInputCollector（控制台输入） | `src/modules/collectors/console/console_input_collector.py` | A（子类自开 `_run_input_loop`） |
| MockCollector（JSONL/Simulator） | `src/modules/collectors/mock/mock_collector.py` | B（走基类 `_start_collect_task`） |
| ScreenChangeCollector（屏幕变化检测） | `src/modules/collectors/screen/screen_change_collector.py` | A（子类自管后台循环） |
| STTCollector（语音转文字） | `src/modules/collectors/stt/stt_collector.py` | A |
| BiliDanmakuCollector（官方/legacy） | `src/modules/collectors/bilibili/{official,legacy}/` | A |

---

## 添加工具

### 适用场景

需要为 Agent 提供**单一可调用的能力**时，新增一个工具。工具是**契约层**——只声明能力（`ToolSpec` + 工具实现函数），不持有会话、不发起事件（除 `async` 工具的结果事件外）。

工具的典型形态：

- **公用感知**（如 `look_at_screen`）——任何 Agent 都可能需要，放 `src/modules/tools/<domain>/`
- **Agent 专属推进**（如 `choose_option`）——只服务于某个游戏 Agent，放该 Agent 自家包内 `src/agents/game/<name>/tools.py`

### 数据契约与协议速览

| 类型 | 位置 | 关键字段 |
|------|------|----------|
| `ToolSpec` | `src/modules/tools/models.py` | `name`, `description`, `parameters_schema`, `kind` (`"sync"`/`"async"`), `provider` (`"builtin"`/`"game"`/`"mcp"`), `result_event`, `output_schema` |
| `ToolInvocation` | `src/modules/tools/models.py` | `tool_name`, `arguments`, `call_id`, `invoked_at_ms`, `source` |
| `ToolExecutionResult` | `src/modules/tools/models.py` | `tool_name`, `success`, `content`, `blocks` (`ResultBlock` 列表), `error_message`, `structured_content`, `duration_ms`, `timestamp_ms` |
| `ResultBlock` | `src/modules/tools/models.py` | `kind` (`"text"`/`"image"`), `text`, `data` (base64), `mime_type` |
| `ToolProvider`（Protocol） | `src/modules/tools/provider.py` | `name` 属性、`list_tools()`、`async invoke(invocation) -> ToolExecutionResult`（**永不抛异常**） |
| `ToolRegistry` | `src/modules/tools/registry.py` | `register(spec, impl)` / `register_provider(provider)` / `invoke(invocation)` / `invoke_many(invocations)` / `to_llm_definitions()` / `has(name)` / `list_tools(provider=)` |
| `default_tool_registry()` | `src/modules/tools/registry.py` | 进程内单例；`@tool` 装饰器默认注册到这里 |

### 两条路径的现实取舍

项目提供两种添加工工具的方式。**生产推荐路径 ①**（所有现存生产工具均此模式）；路径 ② 为轻量声明，仅供测试或未来探索使用。

| 维度 | 路径 ① `ToolProvider` 类 + `registry.register_provider()` | 路径 ② `@tool` 装饰器 |
|------|--------------------------------------------------------|------------------------|
| 状态/资源管理 | ✅ 构造器注入依赖（后端 / 引擎 / 配置） | ❌ 函数本体，外部状态需闭包/全局变量 |
| 注册到指定 registry | ✅ 传任意 `ToolRegistry` 实例 | ❌ 默认进 `default_tool_registry()` 单例；要隔离需 `registry=...` 参数 |
| 多工具聚合 | ✅ 一个 Provider 可声明多个 `ToolSpec` | ❌ 一函数一工具 |
| 测试隔离 | ✅ Fake 后端构造后注入 Provider，干净 | ⚠️ 默认污染全局 registry，需 `clear()` |
| 现有生产工具 | ✅ `look_at_screen` / `choose_option` / `get_story` / `reply` / `should_speak_proactively` / `parse_command` / ContentEngine 控制面 | ❌ **生产零使用**；仅 `look_at_screen` 旧测试 / `src/modules/tools/output/tts/__init__.py` 等轻量声明 |
| 推荐主路径 | ✅ **推荐** | ⚠️ 轻量声明 path（测试/未来用） |

### 最小骨架代码

#### 路径 ①：`ToolProvider` 类（**生产推荐**）

参考：`src/modules/tools/perception/look_at_screen.py`（公用 builtin 工具）、`src/agents/game/text_adv/tools.py`（game 专属 Provider）。

```python
"""
my_tool.py —— 示例工具（路径 ①：ToolProvider 类）
放 src/modules/tools/<domain>/my_tool.py 或 src/agents/<family>/<name>/tools.py
"""
from __future__ import annotations

import time
from typing import Iterable, Optional

from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import ToolProvider

logger = get_logger("my_tool")


# ---------------------------------------------------------------------------
# 1. ToolSpec 工厂（返回 LLM 看的工具定义）
# ---------------------------------------------------------------------------

MY_TOOL_SPEC = ToolSpec(
    name="my_tool",
    description="示例工具：把传入的文本翻译为大写（同步工具，调用即返回结果）。",
    parameters_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要转换的文本",
            },
        },
        "required": ["text"],
    },
    kind="sync",                # "sync"=gather 等齐；"async"=fire-and-forget + result_event
    provider="builtin",         # "builtin"/"game"/"mcp"——来源溯源
    output_schema={
        "type": "object",
        "properties": {"result": {"type": "string"}},
    },
)


def build_my_tool_spec() -> ToolSpec:
    """构造 ToolSpec（工厂方法，便于将来参数化）。"""
    return MY_TOOL_SPEC


# ---------------------------------------------------------------------------
# 2. ToolProvider 实现（满足 ToolProvider 协议）
# ---------------------------------------------------------------------------

class MyToolProvider(ToolProvider):
    """my_tool 的 Provider；构造器注入依赖（这里演示空依赖版）。"""

    @property
    def name(self) -> str:
        return "MyToolProvider"

    def list_tools(self) -> Iterable[ToolSpec]:
        return [build_my_tool_spec()]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """契约：永不抛异常；失败转为 ToolExecutionResult(success=False, error_message=...)。"""
        started_ms = int(time.time() * 1000)
        try:
            args = invocation.arguments or {}
            text = str(args.get("text", "") or "")
            if not text:
                return ToolExecutionResult(
                    tool_name="my_tool",
                    success=False,
                    error_message="缺少必填参数 text",
                    timestamp_ms=int(time.time() * 1000),
                    duration_ms=int(time.time() * 1000) - started_ms,
                )
            # TODO: 真实业务逻辑
            upper = text.upper()
            return ToolExecutionResult(
                tool_name="my_tool",
                success=True,
                content=upper,
                structured_content={"result": upper},
                timestamp_ms=int(time.time() * 1000),
                duration_ms=int(time.time() * 1000) - started_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 边界处兜底
            logger.warning(f"my_tool 执行失败: {exc}", exc_info=True)
            return ToolExecutionResult(
                tool_name="my_tool",
                success=False,
                error_message=f"{type(exc).__name__}: {exc}",
                timestamp_ms=int(time.time() * 1000),
                duration_ms=int(time.time() * 1000) - started_ms,
            )


# ---------------------------------------------------------------------------
# 3. 注册到 ToolRegistry（由装配处调用）
# ---------------------------------------------------------------------------

def register_my_tool(registry, *, provider_name: str = "MyToolProvider") -> int:
    """便捷函数：构造 Provider 并注册到指定 registry。返回新注册数。"""
    return registry.register_provider(MyToolProvider())


__all__ = ["MyToolProvider", "MY_TOOL_SPEC", "build_my_tool_spec", "register_my_tool"]
```

**注册调用**（通常在装配处 / `Agent._register_tools` / `main.py` 中）：

```python
from src.modules.tools.registry import default_tool_registry
from my_tool import register_my_tool

registry = default_tool_registry()  # 或 main.py 持有的 ToolRegistry 实例
register_my_tool(registry)
```

#### 路径 ②：`@tool` 装饰器（轻量声明，测试/未来用）

参考：`src/modules/tools/decorator.py`（装饰器实现）。

```python
"""
my_lightweight_tool.py —— 示例工具（路径 ②：@tool 装饰器）
注意：进程内单例 default_tool_registry()；隔离测试需 registry=... 参数。
"""
from src.modules.tools import tool, ToolInvocation, ToolExecutionResult


@tool(
    name="my_lightweight_tool",
    description="示例轻量工具：返回当前时刻（毫秒）。",
    parameters_schema={"type": "object", "properties": {}, "required": []},
    kind="sync",
    provider="builtin",  # 默认 builtin
)
async def my_lightweight_tool(invocation: ToolInvocation) -> ToolExecutionResult:
    import time
    now_ms = int(time.time() * 1000)
    return ToolExecutionResult(
        tool_name="my_lightweight_tool",
        success=True,
        content=str(now_ms),
        structured_content={"now_ms": now_ms},
        timestamp_ms=now_ms,
    )
```

> **装饰器特性**（详见 `src/modules/tools/decorator.py`）：
> - `name` 默认用函数名；`description` 默认用 `inspect.getdoc(fn)`
> - `kind="async"` 自动把 `result_event` 设为 `"tool.result.<name>"`（来自 `DEFAULT_RESULT_EVENT_PREFIX`）
> - 自动包装返回值/异常为 `ToolExecutionResult`
> - **不取代** `ToolProvider`：有状态/多步骤/需要构造器注入的请走路径 ①

### 装配路径

| 步骤 | 位置 | 操作 |
|------|------|------|
| ① 放代码 | `src/modules/tools/<domain>/my_tool.py`（公用 builtin 工具）或 `src/agents/<family>/<name>/tools.py`（Agent 专属） | Provider 类 `XxxToolProvider` + `build_xxx_spec()` |
| ② 注册 | Agent 专属：在该 Agent 的 `_register_tools` 中 `self._tool_registry.register_provider(provider)`；公用 builtin：在装配根 `main.py` 或专门的 wiring 模块中注册 | |
| ③ 配置（可选） | Agent 专属工具一般无独立配置段（行为由 Agent 配置决定）；公用 builtin 工具若需要开关，放 `[tools.perception.config.<tool_name>]` 或 `[tools.output.config.<tool_name>]` | |
| ④ 列出与转换 | `ToolRegistry.to_llm_definitions()` 自动从 `ToolSpec.parameters_schema` 派生 OpenAI 风格 function calling 定义供 LLM 看 | |
| ⑤ 调用 | `ToolRegistry.invoke(ToolInvocation(tool_name, arguments, call_id, source))`；**永不抛异常**——失败返回 `ToolExecutionResult(success=False, error_message=...)` | |

**谁调用注册？**

- **Agent 专属工具**：Agent 子类 `_register_tools()` 方法（参考 `StreamerAgent._register_tools`、`TextAdvGameAgent._register_tools`）。在 Agent `_on_start` 阶段调用。
- **公用 builtin 工具**：在装配根（`main.py` 或专用 wiring 模块）调 `register_xxx_tool(registry)`，通常 `AudioStreamChannel` 启动后 / `LLMManager.setup` 之后立即注册。
- **Agent 工具聚合**：`AgentManager.register_all_tools()` 兜底——`_make_agent_tool_bridge` 默认返回失败 result 占位（无实际 dispatch），但 Agent 子类手动调 `_register_tools()` 才会**真正生效**（基类的桥接函数是占位实现）。

### 测试要点

- **Fake 后端 / Mock Provider**：构造一个返回固定值的 Provider 注入测试
- **断言 `ToolExecutionResult`**：`assert result.success` / `result.error_message` / `result.structured_content`
- **`ToolRegistry.clear()`**：每个测试开头清空，避免污染
- **永不抛异常**：构造故意抛异常的 impl，断言返回的是失败 result 而非异常
- **`to_llm_definitions()` 形状**：断言包含 `name` / `description` / `parameters` 字段（OpenAI function calling 兼容）
- **真实范例测试**：`tests/tools/test_look_at_screen_provider.py`、`tests/tools/test_text_adv_tools.py`

### 真实范例指引

| 范例 | 文件 | 路径 |
|------|------|------|
| `look_at_screen`（公用 builtin，屏幕快照） | `src/modules/tools/perception/look_at_screen.py` | 路径 ① |
| `choose_option` / `get_story`（Agent 专属 game Provider） | `src/agents/game/text_adv/tools.py` | 路径 ①（`provider="game"`） |
| `reply`（Agent 专属 builtin Provider） | `src/agents/streamer/reply_tool.py` | 路径 ①（`provider="builtin"`） |
| `should_speak_proactively` / `parse_command`（Agent 专属 builtin Provider） | `src/agents/streamer/proactive_tool.py`、`src/agents/streamer/command_tool.py` | 路径 ① |
| ContentEngine 控制面（`provider="builtin"`） | `src/modules/tools/content_engine.py` | 路径 ① |
| `@tool` 装饰器示例 | `src/modules/tools/decorator.py`（内含使用范例） | 路径 ② |

---

## 添加 Agent

### 适用场景

需要**订阅事件 → 内部编排 → 调工具 → 出可执行回复**时，新增一个 Agent。Agent 是 v2 范式的**决策主体**——可以拥有私有状态（`state` 字段）、私有工具（`list_tools`）、后台循环和持久化。

典型用例：

- **业务 Agent**（如 `streamer`）——主播决策核心
- **游戏 Agent**（如 `text_adv` / `minecraft`）——感知游戏画面 → 推进剧情/操作
- **自定义 Agent**（如 `custom`）——任何不归属业务/游戏的特殊决策体

### 协议六面（最小契约）

继承自 [`BaseAgent`](../../src/modules/agents/base.py)（位于 `src/modules/modules/agents/base.py`）。

| # | 面 | 必填 | 内容 |
|---|------|------|------|
| 1 | 生命周期 | `start/stop/cleanup` 默认实现 | 子类覆写 `_on_start` / `_on_stop` / `_on_cleanup` 钩子；可选 `pause/resume/shutdown` |
| 2 | 工具提供 | **`list_tools() -> Iterable[ToolSpec]`**（**@abstractmethod**） | 暴露 Agent 专属工具；空集合表示不暴露 |
| 3 | 事件上报 | 默认 `emit_event(name, payload, source=...)` 工具方法 | 可选声明类属性 `emits_events: Iterable[str]` |
| 4 | 状态读写 | 默认 `@property state` 暴露 `AgentState`；Agent 内部自由 | 框架无强约束 |
| 5 | 健康 | 默认 `note_heartbeat()` / `is_alive()` / `restart_count` | 子类按需心跳 |
| 6 | 元数据 | **`name: str` / `description: str`**（类属性，**必填**） | 唯一标识 + 人类可读描述 |

> **关键**：`name` **必须**在类属性显式声明（基类 `__init__` 不会兜底）；空名会被 `AgentManager.register` 显式拒绝。
> `description` **必须**填（dashboard / 日志显示用）。

#### 完整协议成员速览

| 成员 | 类型 | 说明 |
|------|------|------|
| `name: str`（类属性） | ✓ 必填 | 唯一标识；AgentManager.register 检查非空 |
| `description: str`（类属性） | ✓ 必填 | 人类可读描述 |
| `emits_events: Iterable[str]`（类属性） | 可选 | 声明自己 emit 的事件族（仅声明/文档作用，不强制） |
| `__init__(event_bus=None)` | ✓ | 必须 `super().__init__(event_bus=event_bus)`；其余依赖通过关键字参数注入 |
| `list_tools() -> Iterable[ToolSpec]` | ✓ **抽象** | `@abstractmethod`；返回本 Agent 暴露的工具 spec |
| `start()` / `stop()` / `cleanup()` | 默认实现 | 状态机；子类覆写 `_on_start` / `_on_stop` / `_on_cleanup` 钩子 |
| `_on_start()` / `_on_stop()` / `_on_cleanup()` | 可覆写 | 真实启停逻辑（注册工具 / 订阅事件 / 起后台任务） |
| `_on_pause()` / `_on_resume()` / `_on_shutdown()` | 可覆写钩子 | 配合 `pause()` / `resume()` / `shutdown()` 状态切换 |
| `emit_event(name, payload, source=...)` | 工具方法 | bus 为 None 时安全跳过 |
| `note_heartbeat()` / `is_alive(dead_threshold_ms=60000)` | 健康 | 心跳协议 |
| `clone()` | 重启支持 | 默认 `self.__class__()`；子类可覆写（依赖注入需重建） |
| `increment_restart_counter()` / `restart_count` | 重启 | 崩溃自愈计数 |
| `@property state -> AgentState` | 状态 | `CREATED / STARTING / RUNNING / PAUSED / STOPPING / STOPPED / ERRORED` |

### 最小骨架代码

参考：`src/agents/streamer/streamer_agent.py`（业务 Agent 完整范例）、`src/agents/game/text_adv/agent.py`（游戏 Agent 自包含包范例）。

```python
"""
my_agent.py —— 示例 Agent
放 src/agents/<family>/<name>/agent.py
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from pydantic import Field as _PydField

from src.modules.agents.base import BaseAgent
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.logging import get_logger
from src.modules.tools import ToolSpec
from src.modules.tools.registry import ToolRegistry

from .my_tool import MyToolProvider, build_my_tool_spec  # 同包内工具


# ---------------------------------------------------------------------------
# 配置 Schema（必填：给 Pydantic 自动校验 + dashboard 自动渲染表单）
# ---------------------------------------------------------------------------

class MyAgentConfig(BaseConfig):
    """Agent 配置。"""
    poll_interval_ms: int = _PydField(default=1000, ge=100, description="后台循环间隔（毫秒）")
    enable_event_emission: bool = _PydField(default=True, description="是否 emit 事件")


# ---------------------------------------------------------------------------
# Agent 实现
# ---------------------------------------------------------------------------

class MyAgent(BaseAgent):
    # ----- 协议 6：元数据（必填）-----
    name = "my_agent"
    description = "示例 Agent —— 演示 v2 Agent 范式"

    # ----- 协议 3：事件族声明（可选；仅文档作用）-----
    emits_events = (
        CoreEvents.ROOM_MESSAGE_DANMAKU,  # 示例：本 Agent 也会 emit（实际订阅者决定）
    )

    def __init__(
        self,
        config: MyAgentConfig,
        *,
        event_bus: Optional[EventBus] = None,
        tool_registry: Optional[ToolRegistry] = None,
        llm_manager: Optional[Any] = None,        # 按需注入
        prompt_manager: Optional[Any] = None,      # 按需注入
        **extra: Any,
    ) -> None:
        """所有依赖通过构造器注入（基类不再兜底 name——务必显式声明）。"""
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._llm = llm_manager
        self._prompt = prompt_manager

        # Agent 内部自由状态（不受基类约束）
        self._counter = 0

        # 工具 Provider 引用（在 _on_start 实例化）
        self._my_provider: Optional[MyToolProvider] = None

        # 后台任务
        self._task: Optional[asyncio.Task] = None
        self._running = False

        self.logger = get_logger(self.__class__.__name__)

    # ----- 协议 1：生命周期（覆写钩子）-----
    async def _on_start(self) -> None:
        """启动：注册工具 + 订阅事件 + 开后台循环。"""
        self._running = True
        if self._tool_registry is not None:
            self._my_provider = MyToolProvider()
            self._tool_registry.register_provider(self._my_provider)
        if self._event_bus is not None:
            self._event_bus.on(
                CoreEvents.ROOM_MESSAGE_DANMAKU,
                self._on_danmaku,
                priority=50,
            )
        self._task = asyncio.create_task(self._my_loop())

    async def _on_stop(self) -> None:
        """停止：取消后台循环。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _on_cleanup(self) -> None:
        """清理资源。"""
        # TODO: 释放外部资源（连接 / 引擎等）
        pass

    # ----- 协议 2：工具提供（**@abstractmethod 必填**）-----
    def list_tools(self) -> Iterable[ToolSpec]:
        """声明本 Agent 暴露的工具（一般 return 工厂方法列表）。"""
        return [build_my_tool_spec()]

    # ----- 后台循环（Agent 内部自由）-----
    async def _my_loop(self) -> None:
        interval = self.typed_config.poll_interval_ms / 1000
        try:
            while self._running:
                await asyncio.sleep(interval)
                self._counter += 1
                self.note_heartbeat()
        except asyncio.CancelledError:
            raise

    # ----- 事件订阅（业务逻辑）-----
    async def _on_danmaku(
        self,
        event_name: str,
        payload,  # RoomMessagePayload（具体类型由 model_class 参数决定）
        source: str,
    ) -> None:
        """弹幕事件回调——Agent 内部自由实现。"""
        # TODO: 把事件内容送入决策；典型流程：
        #   message = NormalizedMessage.from_room_payload(payload)
        #   decision = await self._planner.plan([message])
        #   if decision.should_reply:
        #       await self._tool_registry.invoke(ToolInvocation(...))
        self.logger.debug(f"收到 {event_name}: {getattr(payload, 'content', '')[:40]}")
```

**同包内工具**（`src/agents/<family>/<name>/my_tool.py`，参见「添加工具」章 路径 ①）：

```python
# my_tool.py（放在同包内，provider="game" 或 "builtin" 按业务归属）
from src.modules.tools import ToolSpec, ToolInvocation, ToolExecutionResult
from src.modules.tools.provider import ToolProvider

def build_my_tool_spec() -> ToolSpec:
    return ToolSpec(name="my_agent_tool", ..., provider="game")  # 或 "builtin"

class MyToolProvider(ToolProvider):
    @property
    def name(self) -> str: return "MyToolProvider"

    def list_tools(self): return [build_my_tool_spec()]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        # TODO: 实现
        ...
```

### 装配路径

| 步骤 | 位置 | 操作 |
|------|------|------|
| ① 放代码 | `src/agents/<family>/<name>/` 自包含包（`agent.py` + `tools.py` + `state.py` + 业务模块） | 类名 `XxxAgent(BaseAgent)`；`name = "<注册名>"` |
| ② 注册工厂 | `src/modules/agents/factory.py` | `SUPPORTED_AGENTS` 元组加 `<注册名>`；加 `if name == "<注册名>":` 分支做 `instantiate_agent` |
| ③ 写配置 | `config/agents.toml` 的 `[agents]` | `enabled = ["<注册名>"]` + `[agents.<注册名>]` 子段（参考 `StreamerAgentConfig` 字段） |
| ④ 装配调用 | `main.py._register_agents_from_config` 或 `AgentManager.enable_agent(name, config, ...)` | 工厂实例化 → 构造器注入依赖 → `manager.register(agent, spec_provider="<builtin\|game\|mcp>")` → `manager.start_agent(name)` |
| ⑤ 工具自动注入 | `AgentManager.register_all_tools(registry)` | 在 Agent 注册后调用一次，遍历所有 Agent 的 `list_tools()` 注入到 `ToolRegistry`（基类桥接是占位，**真实注册需子类 `_register_tools`**） |
| ⑥ Dashboard | 自动可见 | 组件管理页从 `SUPPORTED_AGENTS` 拉清单 |

**业务包放置规范**（防"插件换皮"红线）：

- **业务包**放 `src/agents/<family>/<name>/`（`family` ∈ {`streamer`, `game`, ...}）
- **内容特有逻辑**必须**内聚**在该包内（state / tools / prompts / 业务子组件），**框架零改动**——证明范式的关键
- **游戏类**放 `src/agents/game/<game>/`（`text_adv` / `minecraft` / ...）；`provider="game"`
- **公用感知/控制工具**放 `src/modules/tools/<domain>/`（不要散落到各业务包）

**谁调用注册？**

- 启动装配：`main.py._register_agents_from_config` 遍历 `[agents].enabled` 列表逐个 `instantiate_agent` → `AgentManager.register` → `AgentManager.start_all`
- 动态启停：Dashboard / API 通过 `AgentManager.enable_agent(name, config, ...)` / `disable_agent(name)`（内部走 `instantiate_agent`）
- 工具聚合：`AgentManager.register_all_tools(registry)` 在装配根一次性调用，把所有 Agent `list_tools()` 收集后注册到 `ToolRegistry`

### 测试要点

- **构造器注入 mock**：LLM/Prompt/EventBus/ToolRegistry 全部 mock 注入；Agent 内部逻辑独立可测
- **`list_tools()` 断言**：调用 `agent.list_tools()` 断言返回的 `ToolSpec` 列表内容
- **后台循环**：起停后断言 `agent.state == AgentState.RUNNING / STOPPED`；重复 `start()` 幂等
- **事件驱动**：手动 `event_bus.emit(ROOM_MESSAGE_DANMAKU, payload)` 后断言 Agent 内部状态变化（用 `note_heartbeat` + 自定义 counter）
- **工具调用**：注入 Mock `ToolRegistry`，断言 Agent 通过 `tool_registry.invoke(...)` 调工具
- **真实范例测试**：`tests/agents/test_streamer_agent.py`、`tests/agents/test_text_adv_game_agent.py`

### 真实范例指引

| 范例 | 文件 | 说明 |
|------|------|------|
| StreamerAgent（业务 Agent） | `src/agents/streamer/streamer_agent.py` | 完整范例：订阅事件 + 后台双任务 + Agenda + 三工具 Provider |
| StreamerAgent 工具 | `src/agents/streamer/{reply_tool,proactive_tool,command_tool}.py` | `provider="builtin"`；StreamerAgent 内部用 |
| TextAdvGameAgent（游戏 Agent） | `src/agents/game/text_adv/agent.py` | 自包含包；`provider="game"`；感知-推进闭环 |
| TextAdvGameAgent 工具 | `src/agents/game/text_adv/tools.py` | `provider="game"`；Agent 专属推进工具 |
| StreamerAgent 便捷工厂 | `src/agents/streamer/streamer_agent.py::build_streamer_agent` | 构造 + register 一站式 |

---

## 端到端消息流

下面以**控制台输入**为例，串起**采集器 → 事件拦截器 → Agent → 工具**的完整数据流。

```
1. 用户在 stdin 输入 "你好" + 回车
        ↓
2. ConsoleInputCollector._run_input_loop() 读到行
        ↓ 构造 NormalizedMessage(data_type="text") 并 _emit_semantic_event()
3. emit room.message.danmaku(payload=RoomMessagePayload{user, content, ...})
        ↓
4. EventBus 分发 → [拦截器链] RateLimitInterceptor / SimilarFilterInterceptor
        ↓ （返回 None = 丢弃；返回 dict = 放行）
5. StreamerAgent._on_danmaku_received(payload) → handle_message(msg)
        ↓ 进入 MessageBuffer；TimingGate 判定是否强制响应
6. StreamerAgent._flush_loop 周期检查 → MessageBuffer.should_flush()
        ↓ 取出一批弹幕
7. _make_two_stage_decision(batch) → Planner.plan(batch, llm=llm_fast)
        ↓ Planner 输出 DecisionPlan{should_reply, topic_summary, reply_guidance, confidence}
        ↓ 注：confidence ≥ 0.3 才进入下一步（具体阈值看 Planner 实现）
8. DecisionPlan.should_reply=True → _make_reply_invocation(plan, batch)
        ↓ ToolInvocation(tool_name="reply", arguments={...}, source="streamer_agent")
9. ToolRegistry.invoke(invocation) → ReplyToolProvider.invoke(invocation)
        ↓ 注入依赖：persona / history / agenda
10. Replyer.generate(plan, batch, persona, history, agenda, profanity_filter)
        ↓ LLM（llm profile，高质量模型）+ 人设 prompt + 敏感词净化
11. ToolExecutionResult{success=True, content=json({speech, emotion, action, metadata})}
        ↓
12. Agent 收到 result.success → 落账（统计 + 持久化 + 房间状态更新）
        ↓
13. 下游渲染（**已知缺口**）：TTS 渲染需要**显式**注册并调用 edge_tts_synthesize 工具；
    该渲染工具当前**不在** StreamerAgent 自动链路上——需要在装配处显式注册并由业务侧调用
        ↓
14. edge_tts_synthesize → 音频 → 皮套 / 远端播放
```

**关键要点**：

| 环节 | 实现位置 | 备注 |
|------|----------|------|
| 采集器 emit `room.message.danmaku` | `src/modules/collectors/console/console_input_collector.py::_emit_semantic_event` | 数据源换 = 替换采集器（`mock_danmaku` / `bili_danmaku_official` 等） |
| 拦截器配置 | `config/core.toml` 的 `[interceptors.rate_limit]` / `[interceptors.similar_filter]` | 启停由 `enabled` 标志控制 |
| Agent 订阅 | `src/agents/streamer/streamer_agent.py::_subscribe_events` | 在 `_on_start` 中挂；priority=50 |
| 弹幕聚合 | `src/agents/streamer/message_buffer.py` + `timing_gate.py` | 批窗口 / 强制响应规则 |
| Planner 决策 | `src/agents/streamer/planner.py` | 调用 `llm_fast` profile；输出 `DecisionPlan` |
| Reply 工具 | `src/agents/streamer/reply_tool.py` | 调 Replyer 表达引擎 |
| Replyer 表达 | `src/agents/streamer/replyer.py` | 调 `llm` profile + ProfanityFilter |
| 渲染 TTS（**已知缺口**） | 需在装配处显式注册 `edge_tts_synthesize` 工具（路径 ① `ToolProvider` 类）；当前不在 StreamerAgent 自动链路 | 见下方"已知缺口" |

### 已知缺口

- **TTS 渲染工具需显式注册**：`edge_tts_synthesize` 工具当前未自动接入 StreamerAgent 成功后的数据流。要让文本 → 音频，需在 `main.py` 或专用 wiring 模块显式 `register_provider(EdgeTTSProvider(...))` 到 `ToolRegistry`，并由业务侧（外部脚本 / Dashboard / 另一个 Agent）显式 `tool_registry.invoke(invocation)` 调用。
- **Agent 工具聚合的基类桥接是占位**：`AgentManager.register_all_tools` 默认返回失败 `ToolExecutionResult`（参见 `manager.py::_make_agent_tool_bridge`）——**真实工具注册必须在 Agent 子类的 `_register_tools()` 中手动 `registry.register_provider(provider)`**。基类方法只保证 `list_tools()` 声明的工具 spec 进入 registry，impl 由 Agent 自身管理。

---

## 通用规范与引用

无论你开发哪种组件，以下规范统一适用（与具体范式无关）：

- **时间字段**：统一用 `int` Unix 毫秒（13 位整数）。**禁止**用秒。命名 `<name>_ms`（如 `timestamp_ms`、`render_timeout_ms`）。使用 `from src.modules.time_utils import now_ms, elapsed_ms, format_duration_ms, ms_to_datetime`
- **事件常量**：禁止硬编码事件名字符串，使用 `from src.modules.events.names import CoreEvents` 常量
- **日志**：使用 `from src.modules.logging import get_logger`；`get_logger("ClassName")` 或 `get_logger(self.__class__.__name__)`
- **配置 Schema**：每个组件**必须**定义 `class ConfigSchema(BaseConfig)`，字段用 Pydantic `Field(default=..., description="...")` 标注
- **错误处理**：`ToolRegistry.invoke()` 和 `ToolProvider.invoke()` **永不抛异常**——失败转为 `ToolExecutionResult(success=False, error_message=...)`；Agent / Collector 在边界处 `try/except` 后 `logger.error(..., exc_info=True)`
- **类型**：Pydantic `BaseModel` 用于数据模型 / 配置 Schema / 事件 Payload；`dataclass(slots=True)` 用于简单内部包装类；`Protocol` 用于接口契约
- **测试**：使用 `pytest`；异步用 `@pytest.mark.asyncio`；构造器注入便于 mock；测试用 `FakeBackend` / `FakeProvider` / `MockEventBus`

### 相关文档

- [开发规范](../development-guide.md) — 代码风格与命名约定
- [测试指南](testing-guide.md) — 测试规范与技巧
- [3阶段架构总览](../architecture/overview.md) — 顶层架构
- [事件系统](../architecture/event-system.md) — EventBus 与事件拦截器
- [事件命名规范](../architecture/event-naming-convention.md) — 事件名动词链
- [数据流规则](../architecture/data-flow.md) — 单向数据流约束

---

*最后更新：2026-08-25（v2.0.0 三范式重写：彻底替换旧"阶段参与者（InputCollector/Decider/OutputHandler）"叙事，统一为「添加采集器 / 添加工具 / 添加 Agent」三范式；以 src/modules/{collectors,tools,agents}/ 与 src/agents/ 为权威基类/协议；删除 Intent / @pipeline / src/stages 引用；保留对配置 [agents]/[tools.perception.config]/[tools.output.config] 的指引）*