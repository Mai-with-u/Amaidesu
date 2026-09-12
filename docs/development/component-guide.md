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
- **Agent 专属推进**（如 `text_adv_choose_option`）——只服务于某个游戏 Agent，放该 Agent 自家包内 `src/agents/<name>/tools.py`

### 数据契约与协议速览

| 类型 | 位置 | 关键字段 |
|------|------|----------|
| `ToolSpec` | `src/modules/tools/models.py` | `name`（声明名，**不带前缀**）, `description`, `parameters_schema`, `kind` (`"sync"`/`"async"`), `provider`（提供者短名）, `result_event`, `output_schema`；对外全名 = `full_name` 派生（`<provider>_<name>`，唯一实现、不存下来） |
| `ToolInvocation` | `src/modules/tools/models.py` | `tool_name`, `arguments`, `call_id`, `invoked_at_ms`, `source` |
| `ToolExecutionResult` | `src/modules/tools/models.py` | `tool_name`, `success`, `content`, `blocks` (`ResultBlock` 列表), `error_message`, `structured_content`, `duration_ms`, `timestamp_ms` |
| `ResultBlock` | `src/modules/tools/models.py` | `kind` (`"text"`/`"image"`), `text`, `data` (base64), `mime_type` |
| `ToolProvider`（Protocol） | `src/modules/tools/provider.py` | `name` 属性（与全部 spec 的 `provider` 同值同源，注册期校验）、`list_tools()`、`async invoke(invocation) -> ToolExecutionResult`（**永不抛异常**）；可选钩子 `query_task` / `subscribe_task_notifications`（任务适配器，默认不支持） |
| `BaseToolProvider`（ABC） | `src/modules/tools/provider.py` | 所有经 `register_provider` 装配的 Provider 的继承基类；带 `category` ClassVar 与 `health_check` 探活钩子默认实现 |
| `ToolRegistry` | `src/modules/tools/registry.py` | `register(spec, impl)` / `register_provider(provider, *, visible_to=...)`（可见名单，ADR-012）/ `invoke(invocation)` / `invoke_many(invocations)` / `to_llm_definitions()` / `has(name)` / `list_tools(for_agent=...)`（按名单计算工具列表）/ `visible_to_of(name)` / `probe_tool(name)`（熔断器探活入口） |
| `as_tool_impl` / `make_provider_from_specs` | `src/modules/tools/provider.py` | 简单工具正典路径：普通 async 函数 + spec 组装标准 Provider（样板 `src/modules/memory/query_tool.py`） |

### Provider 探活契约（与熔断器配套）

有外部连接（WebSocket / HTTP / stdio 子进程等）的 Provider 继承 `BaseToolProvider` 并**重写** `health_check`（连接可用才返回 True）；无状态工具沿用基类默认（返回 True，语义为"无可检查之物，让流量决定"——熔断后冷却期满即恢复，再失败再熔断）。`ToolRegistry.probe_tool(name)` 统一按名定位 provider 并调用 `health_check`，熔断器配套的 `ToolHealthMonitor` 走这一条路径，无须再做反射式存在性探测。维护外部连接的 Provider 实际覆写 `health_check` 由架构测试 `tests/architecture/test_provider_health_contract.py` 强制约束。

### 简单工具正典路径（唯一路径）

无状态、轻量的简单工具**不必手写 Provider 类**——正典路径三件套：

1. `ToolSpec`（声明名裸名，全名自动派生）
2. 普通 async 函数（经 `as_tool_impl` 包装：返回值/异常/计时归一为
   `ToolExecutionResult`）
3. `make_provider_from_specs` 组装 Provider，装配处一行
   `registry.register_provider(provider, visible_to=...)` 注册

样板：`src/modules/memory/query_tool.py`（QueryMemory）。

**升级为手写 Provider 类的判据**（出现任一条才升级）：需要连接重连
（覆写 `connect`/`health_check`）/ 共享状态 / 动态工具表 / 任务适配器
（回执型工具覆写 `query_task` / `subscribe_task_notifications`）。
手写类同样继承 `BaseToolProvider`，`name` 属性必须与其全部 spec 的
`provider` 同值（注册期 fail-fast 校验）。

### 最小骨架代码（正典路径）

参考样板：`src/modules/memory/query_tool.py`（QueryMemory——无连接、无状态的最纯形态）。

```python
# my_tool.py —— 简单工具正典路径样板
# 放 src/modules/tools/<domain>/my_tool.py（公用）或 src/agents/<name>/tools.py（Agent 专属）
from src.modules.tools.models import ToolSpec
from src.modules.tools.provider import ToolProvider, as_tool_impl, make_provider_from_specs

PROVIDER_NAME = "my"  # 提供者短名（三合一身份：全名前缀 / 注册名 / 定位键）

MY_TOOL_SPEC = ToolSpec(
    name="my_tool",  # 声明名裸名（不带前缀）；对外全名 = my_my_tool 派生
    description="示例工具：把传入的文本翻译为大写。",
    parameters_schema={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "要转换的文本"}},
        "required": ["text"],
    },
    kind="sync",
    provider=PROVIDER_NAME,
)


async def _run(invocation) -> str:
    """执行体：普通 async 函数。

    返回 str/None → 成功 content；抛异常 → 失败结果（含异常信息）；计时自动包。
    """
    args = invocation.arguments or {}
    return str(args.get("text", "")).upper()


def build_my_tool() -> ToolProvider:
    """组装 Provider（as_tool_impl 归一化 + make_provider_from_specs 包装）。"""
    return make_provider_from_specs(
        PROVIDER_NAME,
        [(MY_TOOL_SPEC, as_tool_impl(MY_TOOL_SPEC.full_name, _run))],
        category="my_domain",  # 可选分类（Dashboard 分组用）
    )
```

**注册调用**（装配处 / `Agent._register_tools` / `main.py`）：

```python
registry.register_provider(
    build_my_tool(),
    # 名单（ADR-012）：默认 ["*"] 全员可省略；Agent 专属工具填 ["<Agent 注册名>"]
    visible_to={"my_my_tool": ["*"]},
)
```

### 装配路径

| 步骤 | 位置 | 操作 |
|------|------|------|
| ① 放代码 | `src/modules/tools/<domain>/my_tool.py`（公用 builtin 工具）或 `src/agents/<name>/tools.py`（Agent 专属） | Provider 类 `XxxToolProvider` + `build_xxx_spec()` |
| ② 注册（带名单） | Agent 专属：在该 Agent 的 `_register_tools` 中 `self._tool_registry.register_provider(provider, visible_to=...)`；公用：在装配根 `main.py` 注册（默认全员） | 名单是注册处代码事实（ADR-012）：值 = Agent 注册名列表或 `["*"]`；未列工具默认全员 |
| ③ 配置（可选） | Agent 专属工具一般无独立配置段（行为由 Agent 配置决定）；公用工具按分类开关（`[tools.<domain>.<key>].enabled`） | |
| ④ 列出与转换 | `ToolRegistry.to_llm_definitions()` 自动从 `ToolSpec.parameters_schema` 派生 OpenAI 风格 function calling 定义供 LLM 看 | |
| ⑤ 调用 | `ToolRegistry.invoke(ToolInvocation(tool_name, arguments, call_id, source))`；**永不抛异常**——失败返回 `ToolExecutionResult(success=False, error_message=...)` | |

**谁调用注册？**

- **Agent 专属工具**：Agent 子类 `_register_tools()` 方法（参考 `StreamerAgent._register_tools`、`TextAdvGameAgent._register_tools`）。在 Agent `_on_start` 阶段调用。
- **公用 builtin 工具**：在装配根（`main.py` 或专用 wiring 模块）调 `register_xxx_tool(registry)`，通常在 `LLMManager.setup` 之后立即注册。
- **工具注册聚合**：生产路径下不存在任何 manager 级聚合函数——Agent 子类在 `_register_tools()` 中自己 `registry.register_provider(provider, visible_to=...)`；avatar/studio 分类工具由 `main.py` 的 `bind_core_tools(registry, tools_cfg)` 按域开关装配；启动结束后 `audit_tools(registry)` 只做只读审计（声明与注册按派生全名对账），不参与注入。

### 测试要点

- **Fake 后端 / Mock Provider**：构造一个返回固定值的 Provider 注入测试
- **断言 `ToolExecutionResult`**：`assert result.success` / `result.error_message` / `result.structured_content`
- **`ToolRegistry.clear()`**：每个测试开头清空，避免污染
- **永不抛异常**：构造故意抛异常的 impl，断言返回的是失败 result 而非异常
- **`to_llm_definitions()` 形状**：断言包含 `name` / `description` / `parameters` 字段（OpenAI function calling 兼容）
- **真实范例测试**：`tests/tools/test_look_at_screen_provider.py`、`tests/tools/test_text_adv_tools.py`

### 真实范例指引

| 范例 | 文件 | 形态 |
|------|------|------|
| `memory_query_memory`（公用查询，正典样板） | `src/modules/memory/query_tool.py` | 正典路径（`as_tool_impl` + `make_provider_from_specs`） |
| `vision_look_at_screen`（公用感知） | `src/modules/vision/look_at_screen.py` | 手写 Provider（DI 后端） |
| `text_adv_choose_option` / `text_adv_get_story`（Agent 专属） | `src/agents/text_adv/tools.py` | 手写 Provider（`provider="text_adv"`） |
| `streamer_reply`（主播发言出口，注册 + 名单 `["streamer"]`） | `src/agents/streamer/tools/reply_tool.py` | 手写 Provider（thinking 槽位） |
| `rundown_control`（动态工具，条件追加例外） | `src/agents/streamer/tools/rundown_tool.py` | 正典路径包装直连执行器 |
| `framework_delegate` / `framework_task_status`（委派原语） | `src/modules/agents/control.py` | 手写 Provider（`provider="framework"`） |

### 三个反直觉点（先读这里，防止按旧红线重新推错）

1. **为何全部工具都注册？** 旧红线"Agent 内部件不注册"写于"注册=全局可见"的旧世界；
   现在可见性由名单隔离（ADR-012）——注册带来统一观测（`tool.result` 事件）、
   停用/熔断治理、工具页管理，全部复用注册表既有机制，不做第二路径。
   注册的判据 = **"是不是工具"**（LLM/调用方会去调的能力）。
2. **为何名单在生产侧（注册处代码声明）而不是消费侧（每 Agent 配清单）？**
   本系统 Agent 与工具都是代码、Agent 只有一层无子代理；工具的生产者最清楚
   它给谁用（出生时一处声明），消费侧清单要求每个 Agent 持有全局工具知识且
   已被约束禁止。新 Agent 零维护自动正确。
3. **内部件判据（哪些不是工具）**：代码直接调用的部件不算工具、不进表——
   如主动发言判定（ProactiveTrigger）、命令解析原语（command/ 包）；它们
   被特意排除在 LLM 工具列表之外，没有 `ToolSpec`、不经注册表。

**红线三分**（AGENTS.md 同款表述）：

| 类别 | 处置 | 例子 |
|------|------|------|
| LLM 可调的内部工具 | **注册 + 名单隔离**（名单填自己或指定受众） | `streamer_reply`、`minecraft_todo` |
| Planner / Replyer 类本体 | **留在 Agent 内部，不进表**（Agent 的决策/表达部件，代码直连） | Planner、Replyer |
| 代码直连的内部件 | **不是工具、不进表**（无 ToolSpec，被调才干活的纯代码部件） | ProactiveTrigger、命令解析原语 |

### 可见名单机制（ADR-012）

名单是**注册处的代码事实**（生产侧逐工具声明）：

```python
registry.register_provider(
    provider,
    visible_to={
        "minecraft_todo": ["minecraft"],       # 仅自己
        "maicraft_perceive": ["streamer", "minecraft"],  # 读工具放开给主播
        # 未列出的工具默认 ["*"]（全员，共享常态）
    },
)
```

| 规则 | 说明 |
|------|------|
| 值 | Agent 注册名列表或 `["*"]`（单独出现 = 全员） |
| 默认 | `["*"]`——不写即全员，全局注册零负担（fail-open，有意取舍） |
| 校验（fail-fast） | 值非空、`"*"` 单独出现、键必须命中本注册项声明的工具全名（拼错即报错） |
| 计算接口 | `list_tools(for_agent="<Agent 名>")`——该 Agent 的工具列表；全体消费方统一从这里拿 |
| 运营全集 | `list_tools()`（不传 `for_agent`）——Dashboard 工具页看一切 |
| 调用边界 | `invoke()` 不查名单——LLM 幻觉编名直调是已知边界（封死需调用方身份治理） |

**LLM 工具列表公式**（`for_agent` 的过滤结果）：

```
<Agent> 工具列表 = 全部注册工具 − 停用(disabled_tools) − 熔断中(tripped) − 名单不含该 Agent 的工具
```

已知例外：动态工具（如 `rundown_control` 按流程单激活状态出现）名单静态，
靠工具列表内条件追加，记录为例外。

### 三事件分工（防混淆）

| 事件 | 语义 | 发射点 |
|------|------|--------|
| `planner.verdict` | 决定时刻（reply 工具被调用、表达生成之前） | ReplyToolProvider |
| `tool.result.<全名>` | 调用完成（成功/失败，含入参回显） | `ToolRegistry.invoke` 统一发射 |
| `streamer.speech` | 业务事实（一条发言已生成，与 TTS 启用正交） | StreamerAgent 发言管线 |

reply 走注册表后三事件都发——观测冗余是**有意接受**的（统一规则优先，
三事件各答一个不同的问题）。

### 异步任务基建与委派（ADR-013）

回执型工具（调用拿任务号而非结果，如 `maicraft_execute`）与跨 Agent 委派
共用一套基建（`src/modules/tools/tasks.py`）：

- **受理约定**：结构化结果 `accepted=true + task_id`（回合不阻塞）
- **任务记录表**（`TaskLedger`，内存）：任务号 → 状态/快照/发起方/执行者；
  终态移除；`accepted↔running` 组内迁移静默（不唤醒），决策点与终态才发
  `task.changed`
- **跟踪循环**（`TaskTracker`，照 ToolHealthMonitor）：通知触发 + 周期兜底；
  订阅按提供者复用；无进展提醒（`wait_timeout_ms` 停滞告警，不杀任务）
- **适配器钩子**（默认不支持，回执型 provider 覆写）：`query_task(task_id)`
  查事实；`subscribe_task_notifications(callback)` 订提示
- **委派原语**：`framework_delegate(agent, instruction)` → 回执 + task_id
  （名册校验、禁自派、BaseAgent 接收入口默认拒收）；`framework_task_status
  (task_id)` 查进度。受理失败与任务失败分开
- **唤醒**：`task.changed`（仅真变化/告警）→ BaseAgent 默认按
  `payload.initiator == self.name` 过滤 → 子类覆写 `on_task_notification`
  注入消息 + 唤醒

接入回执型工具：绑定处声明适配器（查询工具全名 + 状态映射 + 通知 URI），
范例见 `MinecraftAgent._bind_agent_owned_mcp`；接入委派：Agent 覆写
`receive_delegation`（默认拒收），范例见 `MinecraftAgent.receive_delegation`。

### 已知边界（有意取舍，勿当缺陷上报）

- 编名直调不拦：`invoke` 不查名单/身份（可见性治理为主防线）
- 重启丢跟踪：任务记录表内存态，重启后进行中任务的跟踪消失（执行侧仍在跑）
- 无排队上限：委派/回执任务积压无限流
- 无发起方取消：执行侧可用其工具取消（如 maicraft task cancel）；跨 Agent
  取消按需再加
- 动态工具列表内条件追加：`rundown_control` 按激活状态出现（唯一已知例外）
- 停用边界作用于全部工具：停用关键内部件（如 `minecraft_todo`）会直接破坏
  宿主 Agent 运行——管理界面有警示标注（确认制，非硬禁）
- 任务基建节拍配置 `[tools.tasks]` 为兜底读取（新键 → 旧键 → 默认），正式
  配置段由配置线落

### MCP 二分表（通用 vs Agent 私有）

| 通道 | 配置位置 | 工具注册时 `provider` | 装配入口 | 适用 |
|------|---------|---------------------|----------|------|
| 通用 MCP | `tools.toml` 的 `[tools.mcp.config.servers.<别名>]` | `<server 名>`（如 `maicraft`） | 组合根 `bind_mcp_tools(registry, ...)` | 任何 Agent 都可调用，类 Claude Code 全局工具源 |
| Agent 私有 MCP | `agents.toml` 的 `[agents.<Agent 名>.mcp]` | `<server 名>` | Agent `_on_start` 自行装配，逐工具名单（fail-closed：默认仅自己，读工具放开） | 名单内的 Agent 可见；域内查询 `list_tools(provider=...)` 照常 |

原则：**位置即归属，装配即声明，调度与基建全局统一**——通用 MCP 与 Agent 私有 MCP 共用 `McpToolProvider` / `ToolRegistry` / 熔断器 / 探活等基建，唯一区别是归属标记与配置宿主文件。Agent 私有 MCP 的 `enabled=false` 时不装配（Agent 是命令驱动，MCP 不可用只降级）。

典型范例：MinecraftAgent 在 `[agents.minecraft].mcp` 声明其 maicraft server，启动时以逐工具名单注册（fail-closed：执行类工具仅 minecraft；读工具 perceive 放开给主播直读）——其它 Agent 的工具列表不被 maicraft 执行工具污染。

---

## 添加 Agent

### 适用场景

需要**订阅事件 → 内部编排 → 调工具 → 出可执行回复**时，新增一个 Agent。Agent 是 v2 范式的**决策主体**——可以拥有私有状态（`state` 字段）、私有工具（`list_tools`）、后台循环和持久化。

典型用例：

- **业务 Agent**（如 `streamer`）——主播决策核心
- **游戏 Agent**（如 `text_adv` / `minecraft`）——感知游戏画面 → 推进剧情/操作
- **自定义 Agent**（如 `custom`）——任何不归属业务/游戏的特殊决策体

### 协议六项（最小契约）

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

参考：`src/agents/streamer/streamer_agent.py`（业务 Agent 完整范例）、`src/agents/text_adv/agent.py`（游戏 Agent 自包含包范例）。

```python
"""
my_agent.py —— 示例 Agent
放 src/agents/<name>/agent.py
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

**同包内工具**（`src/agents/<name>/my_tool.py`，参见「添加工具」章 路径 ①）：

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
| ① 放代码 | `src/agents/<name>/` 自包含包（`agent.py` + `tools.py` + `state.py` + 业务模块） | 类名 `XxxAgent(BaseAgent)`；`name = "<注册名>"` |
| ② 注册工厂 | `src/modules/agents/factory.py` | `SUPPORTED_AGENTS` 元组加 `<注册名>`；加 `if name == "<注册名>":` 分支做 `instantiate_agent` |
| ③ 写配置 | `config/agents.toml` 的 `[agents]` | `enabled = ["<注册名>"]` + `[agents.<注册名>]` 子段（参考 `StreamerAgentConfig` 字段） |
| ④ 装配调用 | `main.py._register_agents_from_config` 或 `AgentManager.enable_agent(name, config, ...)` | 工厂实例化 → 构造器注入依赖 → `manager.register(agent, spec_provider="<builtin\|game\|mcp>")` → `manager.start_agent(name)` |
| ⑤ 工具接线 | `bind_core_tools` / Agent 子类 `_register_tools()` | 装配根 `main.py` 先按域开关调 `bind_core_tools(registry, tools_cfg)` 装 avatar/studio 分类工具并启动任务跟踪循环；`start_all` 触发每个 Agent 子类 `_register_tools()` 自己 `registry.register_provider(provider, visible_to=...)`；结束后 `audit_tools(registry)` 按派生全名对账（零警告 = 声明与注册一致） |
| ⑥ Dashboard | 自动可见 | 组件管理页从 `SUPPORTED_AGENTS` 拉清单 |

**业务包放置规范**（防"插件换皮"红线）：

- **业务包**放 `src/agents/<name>/`（目录名 = Agent 注册名，如 `streamer` / `minecraft` / `text_adv`）
- **内容特有逻辑**必须**内聚**在该包内（state / tools / prompts / 业务子组件），**框架零改动**——证明范式的关键
- **游戏类**放 `src/agents/<game>/`（`minecraft` / `text_adv` / ...）；工具 `provider` 用注册名，`category` 声明 `game`
- **公用感知/控制工具**放 `src/modules/tools/<domain>/`（不要散落到各业务包）

**谁调用注册？**

- 启动装配：`main.py._register_agents_from_config` 遍历 `[agents].enabled` 列表逐个 `instantiate_agent` → `AgentManager.register` → `AgentManager.start_all`
- 动态启停：Dashboard / API 通过 `AgentManager.enable_agent(name, config, ...)` / `disable_agent(name)`（内部走 `instantiate_agent`）
- 工具接线：装配根 `main.py` 在 `start_all` 之前显式 `bind_core_tools(registry, slice)` 与 `bind_pending_tools(registry)`；`start_all` 触发各 Agent 子类 `_register_tools()` 自注册；结束后 `audit_tools(registry)` 仅做只读审计

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
| StreamerAgent（业务 Agent） | `src/agents/streamer/streamer_agent.py` | 完整范例：订阅事件 + 后台双任务 + 流程单 + 三工具 Provider |
| StreamerAgent 工具 | `src/agents/streamer/tools/{reply_tool,proactive_tool,command_tool}.py` | `provider="builtin"`；StreamerAgent 内部用 |
| TextAdvGameAgent（游戏 Agent） | `src/agents/text_adv/agent.py` | 自包含包；`provider="text_adv"`（分类 game）；感知-推进闭环 |
| TextAdvGameAgent 工具 | `src/agents/text_adv/tools.py` | `provider="text_adv"`（分类 game）；Agent 专属推进工具 |
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
7. _make_two_stage_decision(batch) → Planner.plan(batch, llm=planner_llm 默认 llm)
         ↓ Planner ReAct 循环：chat_messages + 工具列表（ToolRegistry 全量 + reply 局部工具）
         ↓ 每步 tool_calls 串行执行（查游戏状态/记忆等 → 观察 tool role 作为观察返回），max_steps=8 防失控
8. LLM 调 reply(意图参数 {topic_summary, reply_guidance, target, confidence})
         ↓ Planner 循环内直连 _reply_provider.invoke（局部工具，不进 ToolRegistry）
9. ReplyToolProvider.invoke(invocation)
         ↓ 注入依赖：persona / history / rundown
10. Replyer.generate(plan, batch, persona, history, rundown)
         ↓ LLM（llm profile，高质量模型）+ 人设 prompt + 敏感词净化；LLM 只见 reply
11. ToolExecutionResult{success=True, structured_content={speech, emotion, metadata}}
         ↓
12. Planner 循环收到 reply 成功即收尾 → outcome{replied/speech/...} → Agent 解析 speech 生成 utterance_id 入 UtteranceQueue（fire-and-forget，不阻塞决策循环）；
    emotion → 直调 vts_set_expression 工具（仍是 ToolRegistry 中的工具）
         ↓
13. 队列 worker 串行 await speak(text, utterance_id)（speak 是构造期注入的适配器，绑定装配期由 build_tts_infrastructure 选中的 tts_engine.handle_speech）→ 引擎合成 + 播放
    （TTS 引擎自身——基础模块，非工具——发布 tts.utterance.started/finished/failed 事件供字幕等消费者订阅；ToolRegistry 中零 TTS 条目）
         ↓
14. 音频经 AudioDeviceManager（src/modules/audio/）输出到扬声器
```

**关键要点**：

| 环节 | 实现位置 | 备注 |
|------|----------|------|
| 采集器 emit `room.message.danmaku` | `src/modules/collectors/console/console_input_collector.py::_emit_semantic_event` | 数据源换 = 替换采集器（`mock_danmaku` / `bili_danmaku_official` 等） |
| 拦截器配置 | `config/infra.toml` 的 `[interceptors.rate_limit]` / `[interceptors.similar_filter]` | 启停由 `enabled` 标志控制 |
| Agent 订阅 | `src/agents/streamer/streamer_agent.py::_subscribe_events` | 在 `_on_start` 中挂；priority=50 |
| 弹幕聚合 | `src/agents/streamer/message_buffer.py` + `timing_gate.py` | 批窗口 / 强制响应规则 |
| Planner ReAct 决策 | `src/agents/streamer/planner.py` | `planner_llm`（默认 llm 高质量模型）；工具列表=registry 全量+reply；`planner_max_steps=8` |
| Reply 工具 | `src/agents/streamer/tools/reply_tool.py` | 局部工具（不进 ToolRegistry）；Planner 循环内直连 invoke |
| Replyer 表达 | `src/agents/streamer/replyer.py` | 调 `llm` profile + ProfanityFilter；LLM 只见 reply |
| 发声队列 | `src/agents/streamer/utterance_queue.py` | FIFO 串行；满时丢最旧；单条 render_timeout_ms 看门狗；构造期注入 `speak` 适配器（绑定 `tts_engine.handle_speech`） |
| TTS 播出 | `src/modules/tts/` 基础模块（4 引擎 Provider）+ `build_tts_infrastructure` 装配入口 | 引擎（`handle_speech`）发 `tts.utterance.*` 事件；详见 ADR-007 |

### 已知缺口

- **TTS 已基础模块化（原缺口已闭环 + v2.0.12 §8 修正）**：`infra.toml [tts].enabled = true` 后，`build_tts_infrastructure` 装配期按 `[tts].provider` 单选构造引擎实例，StreamerAgent 构造期接收并把 `engine.handle_speech` 注入 UtteranceQueue；reply 产出的 speech 经 UtteranceQueue → 引擎 `handle_speech` 播出（不走 ToolRegistry，零 TTS 工具条目）。设计决策见 [ADR-007](../architecture/adr/007-tts-infrastructure-pipeline.md)。
- **工具注册路径唯一**：`AgentManager` 不聚合工具注册——真实注册只走两条：① Agent 子类 `_register_tools()` 中自己 `registry.register_provider(provider, visible_to=...)`；② 分类工具在 `main.py` 由 `bind_core_tools(registry, tools_cfg)` 按域开关装配。装配结束后 `AgentManager.audit_tools(registry)` 按派生全名对账（缺失即 warning），不写任何工具实现。

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

### 配置 Schema 约定（包内权威）

组件配置的**单一权威**是组件包内的 `ConfigSchema`（Collector/工具 Provider 为类内嵌定义，Agent 为包内模块级 `*Config`）；中央配置树（`src/modules/config/*_schemas.py`）只留槽位与聚合段，**不内联**组件字段定义——同一字段两处定义必然漂移。

- **定义**：`class ConfigSchema(BaseConfig)`，字段一律 `Field(default=..., description="...")`；具体值/空串哨兵表达（禁 `None`）；时间字段毫秒
- **登记**：新组件在 `src/modules/config/registry.py` 的显式 import 链登记（`EXPECTED_COMPONENTS` 清单内）；漏登记会在启动断言暴露（缺失清单随异常给出）
- **校验**：加载管线按注册表把采集器子段分发给包内 Schema 校验，漂移路径以 `<组件名>.<字段>` 前缀并入宿主文件报告；校验后的干净子段全量写回
- **消费**：组件运行时 `self.ConfigSchema.from_dict(raw)` 得到 typed 配置；缺键补默认、多键剥离并计入漂移
- **WebUI**：dashboard 按 `BaseConfig` 自描述协议（根 Schema 的 `__file_name__` / `__section_label__`）动态分组渲染；写路径统一走加载管线（`update_config_values`），勿自行读改写 TOML

### 相关文档

- [开发规范](../development-guide.md) — 代码风格与命名约定
- [测试指南](testing-guide.md) — 测试规范与技巧
- [3阶段架构总览](../architecture/overview.md) — 顶层架构
- [事件系统](../architecture/event-system.md) — EventBus 与事件拦截器
- [事件命名规范](../architecture/event-naming-convention.md) — 事件名动词链
- [数据流规则](../architecture/data-flow.md) — 单向数据流约束

---

*最后更新：2026-09-08（Provider 探活契约落地：新增 `BaseToolProvider` ABC（`src/modules/tools/provider.py`），收敛 `category` ClassVar 与 `health_check` 探活钩子默认实现；删除 `ToolRegistry.provider_health_check` 反射式探测，新增 `probe_tool(name)` 统一入口；`ToolHealthMonitor.probe_cycle` 收口为单一路径（经 `probe_tool` 判定 → True 复位 / False 维持）；`register_provider` 对非 `BaseToolProvider` 子类每次 register_provider 注册记一条 warning（向后兼容、不抛错；持续出现即迁移未完成的信号）。全部经 `register_provider` 注册的生产 Provider 已迁移（VTS / Warudo / VRChat / OBS / vision / memory / minecraft / text_adv / content_engine / agent_control / mcp）；MCP 重写 `health_check` 委托 `McpClient.probe`，其它 Provider 沿用基类默认（待接入真实探活）。新增架构测试 `tests/architecture/test_provider_health_contract.py` 强制维护外部连接的 Provider 真正覆写 `health_check`（避免默认实现被误算成有效探活）。"两条路径的现实取舍"表新增 Provider 探活契约段落）*

*最后更新：2026-09-05（v2.0.12 §8 概念修正：TTS 提升为基础设施。端到端消息流时序步骤 12-14：worker 改 `await speak(text, utterance_id)`（注入的 speak 适配器，绑定 `tts_engine.handle_speech`）；步骤 12 标注 VTS 仍是 ToolRegistry 中的工具；步骤 13 标注 TTS 引擎自身——基础模块、非工具——发布 utterance 事件 + ToolRegistry 中零 TTS 条目。关键要点表发声队列行补"构造期注入 speak 适配器"；TTS 播出行改写为"`src/modules/tts/` 基础模块 + `build_tts_infrastructure` 装配入口"。已知缺口"TTS 渲染工具需显式注册" → "TTS 已基础模块化（原缺口已闭环 + v2.0.12 §8 修正）"+ 装配期注入直连说明。装饰器两条路径对比表"现有生产工具"行删除 `src/modules/tools/output/tts/__init__.py` 陈旧引用；同日术语统一：'退役出工具池'改为'提升为基础设施'（避免误导为降级））*
