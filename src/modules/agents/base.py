"""
BaseAgent —— Agent 协议六项

框架对 Agent 的**唯一要求**（最小契约）；Agent 内部完全自由。

## 协议六项（最小契约）
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
  （provider="framework"——框架内置提供，非独立源）
- 无子 Agent（用户定）：Agent 只有一层
"""

from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Optional

from src.modules.events.payloads.base import BasePayload
from src.modules.events.payloads.tasks import TaskChangedPayload

from src.modules.config.core_schemas import AgentSupervisorConfig
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms
from src.modules.tools.models import ToolSpec

logger = get_logger("BaseAgent")

# 简化 EventBus 类型提示（避免循环依赖；实际注入由 AgentManager 完成）
EventBusLike = Any

# 守护参数默认值的唯一权威：AgentSupervisorConfig（infra.toml
# [agent_supervisor] 段的 Schema）。构造未显式传参时从此处解析，
# 避免框架代码里散落魔法数。
_SUPERVISOR_DEFAULTS = AgentSupervisorConfig()


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
    def __init__(
        self,
        *,
        event_bus: Optional[EventBusLike] = None,
        heartbeat_interval_ms: Optional[int] = None,
    ) -> None:
        # name 由子类显式声明（class attr）；不在 __init__ 兜底，避免掩盖错误
        # AgentManager.register 会拒绝空名（更明确的错误位置）
        self._state: AgentState = AgentState.CREATED
        self._event_bus = event_bus
        # 心跳间隔（毫秒）；未显式传入时用守护配置默认值；<=0 关闭心跳任务
        self._heartbeat_interval_ms: int = (
            heartbeat_interval_ms if heartbeat_interval_ms is not None else _SUPERVISOR_DEFAULTS.heartbeat_interval_ms
        )
        # 心跳后台任务（start 创建 / stop 取消；None = 未运行）
        self._heartbeat_task: Optional["asyncio.Task[None]"] = None
        # 用子类声明的 name 初始化心跳（如未声明 → 防御用空串；Manager 拒绝）
        self._heartbeat = AgentHeartbeat(agent_name=self.name or "", last_heartbeat_ms=now_ms())
        self._restart_count: int = 0
        # task.changed 唤醒订阅句柄（start 订阅 / stop 退订；None = 未订阅）
        self._task_wakeup_handler: Any = None
        # 本 Agent 注册进 ToolRegistry 的 provider 登记（(provider, registry) 列表；
        # 经 register_tool_provider 登记，unregister_tool_providers 逐一摘除）
        self._registered_providers: list[Any] = []
        # 本 Agent 持有的 MCP 客户端（经 register_mcp_client 登记；
        # close_mcp_clients 统一关闭，供 stop/重建/disable 路径调用）
        self._registered_mcp_clients: list[Any] = []
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
            logger.exception(f"Agent '{self.name}' 启动失败: {exc}")
            raise

        self._subscribe_task_wakeup()
        self._start_heartbeat_loop()

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
        await self._stop_heartbeat_loop()

        try:
            await self._on_stop()
        except Exception as exc:  # noqa: BLE001 - 边界
            self._state = AgentState.ERRORED
            logger.exception(f"Agent '{self.name}' 停止失败: {exc}")
            raise

        self._unsubscribe_task_wakeup()

        async with self._lock:
            self._state = AgentState.STOPPED
        logger.info(f"Agent '{self.name}' 已停止")

    async def cleanup(self) -> None:
        """资源释放（连接/后台任务）。默认实现: 调用 _on_cleanup 钩子。"""
        # 心跳任务兜底取消（stop 未被调用的异常路径）
        await self._stop_heartbeat_loop()
        await self._on_cleanup()
        logger.debug(f"Agent '{self.name}' 资源已清理")

    # ----- 心跳后台任务 -----

    def _start_heartbeat_loop(self) -> None:
        """创建心跳后台任务（start 成功路径调用；幂等）。

        ``heartbeat_interval_ms <= 0`` 时不创建（心跳关闭）；已有存活任务
        时跳过（重复 start 防御）。
        """
        if self._heartbeat_interval_ms <= 0:
            logger.debug(f"Agent '{self.name}' 心跳已关闭（interval_ms<=0），不创建心跳任务")
            return
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name=f"heartbeat-{self.name}")

    async def _heartbeat_loop(self) -> None:
        """周期写心跳；任何异常只记日志，不拖垮 Agent（任务退出由 stop 取消）。"""
        interval_s = self._heartbeat_interval_ms / 1000
        while True:
            await asyncio.sleep(interval_s)
            try:
                self.note_heartbeat()
            except Exception as exc:  # noqa: BLE001 - 心跳异常不拖垮 Agent
                logger.warning(f"Agent '{self.name}' 心跳写入异常（忽略）: {type(exc).__name__}: {exc}")

    async def _stop_heartbeat_loop(self) -> None:
        """取消心跳后台任务（stop / cleanup 路径调用；幂等）。"""
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # 预期取消路径

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

    def on_task_notification(self, payload: TaskChangedPayload) -> None:
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
                logger.warning(f"Agent '{self.name}' pause() 非法转移，拒绝（当前状态: {self._state}）")
                return
            self._state = AgentState.PAUSED
        await self._on_pause()
        logger.info(f"Agent '{self.name}' 已暂停")

    async def resume(self) -> None:
        async with self._lock:
            if self._state != AgentState.PAUSED:
                logger.warning(f"Agent '{self.name}' resume() 非法转移，拒绝（当前状态: {self._state}）")
                return
            self._state = AgentState.RUNNING
        await self._on_resume()
        logger.info(f"Agent '{self.name}' 已恢复")

    async def shutdown(self) -> None:
        """比 stop 更严格的停机（外部资源/进程）"""
        await self.stop()
        await self._on_shutdown()
        logger.info(f"Agent '{self.name}' 已 shutdown")

    # ---------------- 工具提供 ----------------

    def register_tool_provider(
        self,
        provider: Any,
        *,
        registry: Any,
        visible_to: Any = None,
    ) -> int:
        """经基类入口把 provider 注册进 ToolRegistry 并登记归属。

        Agent 子类在启动期用本方法替代直调 ``registry.register_provider``；
        登记后 ``unregister_tool_providers`` 可在 stop 路径逐一摘除，
        避免 disable/重建时 registry 里残留本 Agent 的工具。
        registry 为 None（未注入）时不注册也不登记，返回 0。
        ``visible_to`` 形态随 registry 契约：静态名单 dict，或
        ``(specs) -> dict`` 策略 callable（工具集刷新时按来源重派，见
        ``ToolRegistry.register_provider``）；透传不解释。
        """
        if registry is None:
            return 0
        count = registry.register_provider(provider, visible_to=visible_to)
        if not any(p is provider for p, _ in self._registered_providers):
            self._registered_providers.append((provider, registry))
        return count

    def unregister_tool_providers(self) -> int:
        """从 registry 摘除本 Agent 登记的全部 provider（stop/清理路径调用；幂等）。"""
        removed = 0
        for provider, registry in self._registered_providers:
            try:
                removed += registry.unregister_provider(provider)
            except Exception as exc:  # noqa: BLE001 - 摘除失败不阻断停机
                logger.warning(f"Agent '{self.name}' 摘除 provider '{provider.name}' 异常: {exc}")
        self._registered_providers.clear()
        return removed

    def register_mcp_client(self, client: Any) -> None:
        """登记本 Agent 持有的 MCP 客户端（重复登记同一实例自动去重）。"""
        if client is not None and not any(c is client for c in self._registered_mcp_clients):
            self._registered_mcp_clients.append(client)

    async def close_mcp_clients(self) -> None:
        """关闭本 Agent 登记的全部 MCP 客户端（重建/disable 前调用；幂等）。

        单个客户端关闭失败仅记日志，不影响其余客户端关闭。
        """
        for client in self._registered_mcp_clients:
            try:
                await client.close()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不阻断停机
                logger.warning(f"Agent '{self.name}' 关闭 MCP 客户端异常: {type(exc).__name__}: {exc}")
        self._registered_mcp_clients.clear()

    @abc.abstractmethod
    def list_tools(self) -> Iterable[ToolSpec]:
        """声明本 Agent 暴露的工具。空集合表示不暴露。"""

    # ---------------- 事件上报 ----------------

    async def emit_event(
        self,
        event_name: str,
        payload: BasePayload,
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

    def increment_restart_counter(self) -> None:
        self._restart_count += 1

    def carry_restart_count(self, count: int) -> None:
        """继承前实例的重启计数（AgentManager.rebuild 换实例后调用）。

        重建产出的是全新实例（计数归零）；由 manager 把累计次数搬运到
        新实例上，保证 restart_count 观测面跨重建连续。
        """
        self._restart_count = max(0, count)

    @property
    def restart_count(self) -> int:
        return self._restart_count

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name='{self.name}' state={self._state.value}>"


__all__ = ["AgentState", "AgentHeartbeat", "BaseAgent"]
