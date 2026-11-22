# 添加 Agent

> 归类判据、端到端消息流与通用规范见[组件开发指南](README.md)。

## 添加 Agent

### 适用场景

需要**订阅事件 → 内部编排 → 调工具 → 出可执行回复**时，新增一个 Agent。Agent 是三范式中的**决策主体**——可以拥有私有状态（`state` 字段）、私有工具（`list_tools`）、后台循环和持久化。

典型用例：

- **业务 Agent**（如 `streamer`）——主播规划器
- **游戏 Agent**（如 `text_adv` / `minecraft`）——感知游戏画面 → 推进剧情/操作。与主播的交互分两种形态：委派型（主播交代目标，覆写 `receive_delegation`）与遥控型（主播直接调它的工具，不接委派）；形态由它的工具面说明（`guide_for`）告诉主播，见 [ADR-043：游戏 Agent 交互形态](../../decisions/043-game-agent-interaction-shapes.md)
- **自定义 Agent**（如 `custom`）——任何不归属业务/游戏的特殊决策体

### 协议六项（最小契约）

继承自 [`BaseAgent`](../../../src/modules/agents/base.py)（位于 `src/modules/agents/base.py`）。

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
| `increment_restart_counter()` / `restart_count` | 重启 | 崩溃自愈计数（实例重建走 AgentManager 工厂，基类无 clone 方法） |
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
    description = "示例 Agent —— 演示 Agent 三范式"

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
        """所有依赖通过构造器注入（基类不兜底 name——务必显式声明）。"""
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
        #   decision = await self._planner.plan([payload])
        #   if decision.should_reply:
        #       await self._tool_registry.invoke(ToolInvocation(...))
        self.logger.debug(f"收到 {event_name}: {getattr(payload, 'content', '')[:40]}")
```

**同包内工具**（`src/agents/<name>/my_tool.py`，参见「添加工具」章 路径 ①）：

```python
# my_tool.py（放在同包内，provider 按提供者归属取 Agent 名或 "framework"）
from src.modules.tools import ToolSpec, ToolInvocation, ToolExecutionResult
from src.modules.tools.provider import ToolProvider

def build_my_tool_spec() -> ToolSpec:
    return ToolSpec(name="my_agent_tool", ..., provider="game")  # 或 "framework"

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
| ④ 装配调用 | `main.py._register_agents_from_config` 或 `AgentManager.enable_agent(name, config, ...)` | 工厂实例化 → 构造器注入依赖 → `manager.register(agent, spec_provider="<注册名>")`（工具来源溯源：值 = Agent 注册名，缺省 `framework`，`mcp` 预留） → `manager.start_agent(name)` |
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
- 工具接线：装配根 `main.py` 在 `start_all` 之前显式 `bind_core_tools(registry, slice)`；`start_all` 触发各 Agent 子类 `_register_tools()` 自注册；结束后 `audit_tools(registry)` 仅做只读审计

### 测试要点

- **构造器注入 mock**：LLM/Prompt/EventBus/ToolRegistry 全部 mock 注入；Agent 内部逻辑独立可测
- **`list_tools()` 断言**：调用 `agent.list_tools()` 断言返回的 `ToolSpec` 列表内容
- **后台循环**：起停后断言 `agent.state == AgentState.RUNNING / STOPPED`；重复 `start()` 幂等
- **事件驱动**：手动 `event_bus.emit(ROOM_MESSAGE_DANMAKU, payload)` 后断言 Agent 内部状态变化（用 `note_heartbeat` + 自定义 counter）
- **工具调用**：注入 Mock `ToolRegistry`，断言 Agent 通过 `tool_registry.invoke(...)` 调工具
- **真实范例测试**：`tests/agents/streamer/test_speech_dispatcher.py`、`tests/agents/test_text_adv.py`

### 真实范例指引

| 范例 | 文件 | 说明 |
|------|------|------|
| StreamerAgent（业务 Agent） | `src/agents/streamer/streamer_agent.py` | 完整范例：订阅事件 + 后台双任务 + 流程单 + 三工具 Provider |
| StreamerAgent 工具 | `src/agents/streamer/tools/{reply_tool,rundown_tool}.py` | reply 为 `provider="streamer"`、rundown_control 为 `provider="rundown"`；StreamerAgent 内部用 |
| TextAdvGameAgent（游戏 Agent） | `src/agents/text_adv/agent.py` | 自包含包；`provider="text_adv"`（分类 game）；感知游戏画面并推进游戏 |
| TextAdvGameAgent 工具 | `src/agents/text_adv/tools.py` | `provider="text_adv"`（分类 game）；Agent 专属推进工具 |

---

