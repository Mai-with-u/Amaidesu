"""
BaseAgent —— Agent 协议六面

框架对 Agent 的**唯一要求**（最小契约）；Agent 内部完全自由。

## 协议六面（最小契约）
- 生命周期：start / stop / cleanup + 可重建性（工厂重建崩溃实例）
- 工具提供：list_tools() → 声明暴露的工具
- 事件上报：自由 emit + 可选声明事件族
- 状态读写：框架给**状态写入口**，按 Agent 名字空间隔离
- 健康：统一心跳协议
- 元数据：name / description

## 接入方式：继承 + 构造注入
```python
class MinecraftAgent(BaseAgent):
    def __init__(self, engine: MinecraftEngine):
        self._engine = engine
        super().__init__()

    def list_tools(self): ...
```

## 关键决策
- 框架级统一控制（pause/resume/shutdown）：由 ``AgentControl`` 工具提供
  （provider="builtin"——框架内置提供，非独立源）
- 无子 Agent（用户定）：Agent 只有一层
"""

from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Optional

from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolSpec

logger = get_logger("BaseAgent")


# 简化 EventBus 类型提示（避免循环依赖；实际注入由 AgentManager 完成）
EventBusLike = Any


class AgentState(str, Enum):
    """Agent 状态机（默认实现，子类钩子可选覆盖）"""

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERRORED = "errored"


@dataclass(slots=True)
class AgentHeartbeat:
    """心跳记录（统一心跳协议）"""

    agent_name: str
    last_heartbeat_ms: int = 0
    is_alive: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)


class BaseAgent(abc.ABC):
    """Agent 协议基类。

    子类必须实现：
    - ``list_tools()``（工具提供）
    - 至少 ``start()`` 的核心动作（生命周期）
    - 提供 ``name`` / ``description``（元数据）

    子类**可选**覆盖：
    - ``_on_pause`` / ``_on_resume`` / ``_on_shutdown`` 钩子
    - ``emit_event`` 事件族来源（默认使用构造器注入的 EventBus）
    - ``_cleanup()`` 额外清理
    """

    # ----- 元数据（子类**必须**覆写）-----
    name: str = ""  # 子类必填
    description: str = ""  # 子类必填

    # ----- 事件族声明（可选）-----
    emits_events: Iterable[str] = ()  # 子类可声明自己发哪些事件族

    # ----- 内部状态 -----
    def __init__(self, *, event_bus: Optional[EventBusLike] = None) -> None:
        # name 由子类显式声明（class attr）；不在 __init__ 兜底，避免掩盖错误
        # AgentManager.register 会拒绝空名（更明确的错误位置）
        self._state: AgentState = AgentState.CREATED
        self._event_bus = event_bus
        # 用子类声明的 name 初始化心跳（如未声明 → 防御用空串；Manager 拒绝）
        self._heartbeat = AgentHeartbeat(agent_name=self.name or "", last_heartbeat_ms=now_ms())
        self._restart_count: int = 0
        # task.changed 唤醒订阅句柄（start 订阅 / stop 退订；None = 未订阅）
        self._task_wakeup_handler: Any = None
        # 异步锁用于状态转移
        # 注：asyncio.Lock 在同步 __init__ 创建后，到第一次 await 才会在 loop 上绑定
        self._lock = asyncio.Lock()
        logger.debug(f"Agent '{self.name or '<未命名>'}' 构造完成（描述: {self.description[:40] or '<空>'}…）")

    # ---------------- 生命周期 ----------------

    async def start(self) -> None:
        """默认实现：状态机；子类应 ``super().start()`` 或覆写 _on_start。"""
        async with self._lock:
            if self._state not in (AgentState.CREATED, AgentState.STOPPED, AgentState.ERRORED):
                logger.warning(f"Agent '{self.name}' start() 状态不正确: {self._state}")
                return
            self._state = AgentState.STARTING

        try:
            await self._on_start()
        except Exception as exc:  # noqa: BLE001 - 边界
            self._state = AgentState.ERRORED
            logger.error(f"Agent '{self.name}' 启动失败: {exc}", exc_info=True)
            raise

        self._subscribe_task_wakeup()

        async with self._lock:
            self._state = AgentState.RUNNING
        self._heartbeat.last_heartbeat_ms = now_ms()
        logger.info(f"Agent '{self.name}' 已启动")

    async def stop(self) -> None:
        """默认实现：状态机；子类覆写 _on_stop 可做额外工作。"""
        async with self._lock:
            if self._state == AgentState.STOPPED:
                return
            self._state = AgentState.STOPPING

        try:
            await self._on_stop()
        except Exception as exc:  # noqa: BLE001 - 边界
            self._state = AgentState.ERRORED
            logger.error(f"Agent '{self.name}' 停止失败: {exc}", exc_info=True)
            raise

        self._unsubscribe_task_wakeup()

        async with self._lock:
            self._state = AgentState.STOPPED
        logger.info(f"Agent '{self.name}' 已停止")

    async def cleanup(self) -> None:
        """资源释放（连接/后台任务）。默认实现: 调用 _on_cleanup 钩子。"""
        await self._on_cleanup()
        logger.debug(f"Agent '{self.name}' 资源已清理")

    # ----- 内部钩子（子类可选覆写）-----

    async def _on_start(self) -> None:
        """子类启动钩子（打开连接、恢复状态）"""
        return None  # 默认空操作：子类覆写

    async def _on_stop(self) -> None:
        """子类停止钩子（优雅退出）"""
        return None  # 默认空操作：子类覆写

    def receive_delegation(self, *, instruction: str, task_id: str) -> Optional[str]:
        """接收委派入口（framework_delegate 调用；**默认拒收**）。

        Returns:
            拒收原因字符串（None = 已接收）。子类按需实现——典型做法：
            指令入队 + 唤醒自己的执行循环 + 队列项携带任务号（供后续
            把任务推进/终态写回任务记录表）。
        """
        return f"{self.name or type(self).__name__} 不接收委派（未实现接收入口或明确拒收）"

    def on_task_notification(self, payload) -> None:
        """任务变化通知钩子（``task.changed``，仅发起方是自己时被调）。

        默认实现只记日志。子类覆写做**注入消息 + 唤醒**（把任务变化送进
        自己的决策/执行循环；通知是提示，需要事实再查任务记录表）。
        """
        logger.debug(
            f"Agent '{self.name}' 收到任务变化通知: task_id={getattr(payload, 'task_id', '?')} "
            f"status={getattr(payload, 'status', '?')}"
        )

    def _subscribe_task_wakeup(self) -> None:
        """订阅 ``task.changed``（start 尾部调用；按发起方过滤后派发钩子）。"""
        if self._event_bus is None or self._task_wakeup_handler is not None:
            return
        from src.modules.events.names import CoreEvents
        from src.modules.events.payloads.tasks import TaskChangedPayload

        agent = self

        async def _on_task_changed(event_name: str, payload: TaskChangedPayload, source: str) -> None:
            if payload.initiator != agent.name:
                return  # 只唤醒发起方（其他人任务的变化与本 Agent 无关）
            agent.on_task_notification(payload)

        self._task_wakeup_handler = _on_task_changed
        self._event_bus.on(CoreEvents.TASK_CHANGED, _on_task_changed, model_class=TaskChangedPayload)

    def _unsubscribe_task_wakeup(self) -> None:
        """退订 ``task.changed``（stop 尾部调用；幂等）。"""
        if self._event_bus is None or self._task_wakeup_handler is None:
            return
        from src.modules.events.names import CoreEvents

        try:
            self._event_bus.off(CoreEvents.TASK_CHANGED, self._task_wakeup_handler)
        except Exception as exc:  # noqa: BLE001 - 退订失败不阻断停机
            logger.warning(f"Agent '{self.name}' task.changed 退订异常（忽略）: {exc}")
        self._task_wakeup_handler = None

    async def _on_cleanup(self) -> None:
        """子类资源清理钩子"""
        return None  # 默认空操作：子类覆写

    async def _on_pause(self) -> None:
        """子类暂停钩子（默认空操作，仅切状态）"""
        return None  # 默认空操作：子类覆写

    async def _on_resume(self) -> None:
        """子类恢复钩子"""
        return None  # 默认空操作：子类覆写

    async def _on_shutdown(self) -> None:
        """子类停机钩子（更严格，可能停止外部资源）"""
        return None  # 默认空操作：子类覆写

    # ----- 控制操作（框架统一控制，由 AgentControl 工具调用） -----

    async def pause(self) -> None:
        async with self._lock:
            if self._state != AgentState.RUNNING:
                logger.debug(f"Agent '{self.name}' pause() 状态非 RUNNING: {self._state}")
            self._state = AgentState.PAUSED
        await self._on_pause()
        logger.info(f"Agent '{self.name}' 已暂停")

    async def resume(self) -> None:
        async with self._lock:
            if self._state != AgentState.PAUSED:
                logger.debug(f"Agent '{self.name}' resume() 状态非 PAUSED: {self._state}")
            self._state = AgentState.RUNNING
        await self._on_resume()
        logger.info(f"Agent '{self.name}' 已恢复")

    async def shutdown(self) -> None:
        """比 stop 更严格的停机（外部资源/进程）"""
        await self.stop()
        await self._on_shutdown()
        logger.info(f"Agent '{self.name}' 已 shutdown")

    # ---------------- 工具提供 ----------------

    @abc.abstractmethod
    def list_tools(self) -> Iterable[ToolSpec]:
        """声明本 Agent 暴露的工具。空集合表示不暴露。"""

    # ---------------- 事件上报 ----------------

    async def emit_event(
        self,
        event_name: str,
        payload,
        source: Optional[str] = None,
    ) -> None:
        """封装 emit：子类直接调，无需关心 bus 是否为 None。"""
        if self._event_bus is None:
            logger.debug(f"Agent '{self.name}' 无 EventBus，跳过 emit: {event_name}")
            return
        await self._event_bus.emit(
            event_name,
            payload,
            source=source or self.name,
        )

    # ---------------- 状态读写 ----------------

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def heartbeat(self) -> AgentHeartbeat:
        return self._heartbeat

    def note_heartbeat(self) -> None:
        """统一心跳：写入当前时刻。"""
        self._heartbeat.last_heartbeat_ms = now_ms()
        self._heartbeat.is_alive = True

    def is_alive(self, *, dead_threshold_ms: int = 60_000) -> bool:
        """粗略存活判定（last_heartbeat 距今超过阈值 → 死）。"""
        delta = now_ms() - self._heartbeat.last_heartbeat_ms
        return delta < dead_threshold_ms

    # ----- 工厂重建（崩溃重启前提） -----

    def clone(self) -> "BaseAgent":
        """默认重建：同对象类型构造。
        子类可覆写（依赖注入需重建）。"""
        return self.__class__()

    def increment_restart_counter(self) -> None:
        self._restart_count += 1

    @property
    def restart_count(self) -> int:
        return self._restart_count

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name='{self.name}' state={self._state.value}>"


__all__ = ["AgentState", "AgentHeartbeat", "BaseAgent"]
