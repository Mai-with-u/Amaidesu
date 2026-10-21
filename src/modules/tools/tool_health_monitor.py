"""
工具健康子系统：熔断器状态（``ToolCircuitBreaker``）+ 探活恢复循环（``ToolHealthMonitor``）

熔断器由 ``ToolRegistry`` 构造时创建并持有，registry 的 invoke 主路径把每次
执行结果交给它记账：连续失败计数达阈值则摘除工具（tripped），并通过事件总线
广播 ``tool.health.<name>`` 跃迁（open/closed）。

探活循环按 ``[tools.health].probe_interval_ms`` 节拍扫描 ``ToolRegistry.tripped_tools``，
对每个熔断工具调用 ``registry.probe_tool(name)``：

- 返回 True → ``recover_tool(name)`` 复位熔断器
- 返回 False → 维持熔断（下一节拍再试）

熔断后最小驻留时长复用 ``probe_interval_ms``：未到时长时本轮不尝试恢复，
避免抖动恢复导致反复熔断。健康判定统一走 ``registry.probe_tool`` 一条路径——
所有经 ``register_provider`` 注册的 provider 都继承 ``BaseToolProvider`` 并拥有
``health_check`` 方法，无状态 Provider 沿用基类默认实现（返回 True），行为等价
于旧"无 health_check → 定时恢复"路径——熔断后冷却期满即恢复，再失败再熔断。

探活循环**不**订阅任何事件——熔断与恢复事件统一由熔断器经 registry 挂载的
EventBus 广播；monitor 仅做消费方驱动。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional

from src.modules.events.payloads.tool_health import ToolHealthPayload
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms
from src.modules.tools.models import ToolExecutionResult, ToolSpec

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus
    from src.modules.tools.registry import ToolRegistry


logger = get_logger("ToolHealthMonitor")


# =============================================================================
# 单工具健康状态（熔断器内部数据）
# =============================================================================


@dataclass(slots=True)
class ToolHealthState:
    """单工具熔断器状态（``ToolCircuitBreaker`` 内部使用）。

    字段全部为零值起步；invocation 完成后由 ``record`` 维护；外部仅通过
    ``tool_health_snapshot`` / ``is_tripped`` / ``recover`` 触达。``provider``
    在首次记账时落账（结果总是携带 spec），快照与恢复事件由此取归属提供者，
    不回查注册表。
    """

    provider: str = ""
    consecutive_failures: int = 0
    tripped: bool = False
    tripped_at_ms: int = 0
    last_error: str = ""


# =============================================================================
# 熔断器：单工具失败计数 + 熔断启停 + 恢复
# =============================================================================


class ToolCircuitBreaker:
    """单工具熔断器（``ToolRegistry`` 持有并委托）。

    Attributes:
        event_bus: 可选事件总线；熔断/恢复跃迁经其广播 ``tool.health.<name>``
        failure_threshold: 连续失败达阈值即熔断（<=0 关闭熔断：状态仍记录、绝不跳闸）
    """

    def __init__(
        self,
        event_bus: Optional["EventBus"] = None,
        *,
        failure_threshold: int = 3,
    ) -> None:
        self._health: Dict[str, ToolHealthState] = {}
        self._event_bus = event_bus
        self._failure_threshold = failure_threshold

    # -------------------- 记账（invoke 主路径委托） --------------------

    def state(self, name: str) -> ToolHealthState:
        """取单工具健康状态（懒初始化：首次失败/恢复前不存在）。"""
        health = self._health.get(name)
        if health is None:
            health = ToolHealthState()
            self._health[name] = health
        return health

    async def record(self, spec: ToolSpec, result: ToolExecutionResult) -> None:
        """在 ``invoke`` 主路径上更新单工具熔断器状态。

        - 成功或明确业务拒绝 → 服务已应答，重置连续故障
        - 执行故障或未分类失败 → 递增计数；达到阈值且未跳闸 → 触发熔断并广播
        """
        health = self.state(spec.full_name)
        # 业务拒绝仍作为失败结果返回和广播；仅健康计数认定服务已正常应答。
        if result.success or result.failure_kind == "business":
            health.consecutive_failures = 0
            health.last_error = ""
            health.provider = spec.provider
            return
        health.provider = spec.provider
        health.consecutive_failures += 1
        health.last_error = result.error_message or ""
        threshold = self._failure_threshold
        if threshold > 0 and not health.tripped and health.consecutive_failures >= threshold:
            health.tripped = True
            health.tripped_at_ms = now_ms()
            logger.warning(
                f"工具 '{spec.full_name}' 连续失败 {health.consecutive_failures} 次（阈值 {threshold}），"
                "已熔断摘除，等待探活恢复"
            )
            await self._emit_tool_health(
                spec.full_name,
                spec.provider,
                state="open",
                failure_count=health.consecutive_failures,
                last_error=health.last_error,
            )

    async def _emit_tool_health(
        self,
        tool_name: str,
        provider: str,
        *,
        state: Literal["open", "closed"],
        failure_count: int,
        last_error: str,
    ) -> None:
        """广播工具健康切换事件（``tool.health.<name>``）；事件总线缺失或广播失败仅记日志。"""
        if self._event_bus is None:
            return
        try:
            payload = ToolHealthPayload(
                tool_name=tool_name,
                provider=provider,
                state=state,
                failure_count=failure_count,
                last_error=last_error,
            )
            await self._event_bus.emit(
                f"tool.health.{tool_name}",
                payload,
                source="ToolRegistry",
            )
        except Exception as exc:  # noqa: BLE001 - 观测旁路
            logger.warning(f"tool.health 事件广播失败（工具: {tool_name}）: {exc}")

    # -------------------- 查询与复位 --------------------

    def is_tripped(self, name: str) -> bool:
        """工具是否处于熔断状态。"""
        health = self._health.get(name)
        return bool(health and health.tripped)

    @property
    def tripped_tools(self) -> List[str]:
        """当前熔断中的工具名（排序快照，供探活循环遍历）。"""
        return sorted(name for name, h in self._health.items() if h.tripped)

    def tool_health_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """返回对 Dashboard 有意义的工具健康快照。

        仅包含熔断中或近期有失败的工具；其余工具按 "健康" 处理（Dashboard
        按缺席键推断）。键为注册名，值包含 provider/state/failure_count/
        last_error/tripped_at_ms 字段。
        """
        snapshot: Dict[str, Dict[str, Any]] = {}
        for name, health in self._health.items():
            if not health.tripped and health.consecutive_failures <= 0:
                continue
            snapshot[name] = {
                "provider": health.provider,
                "state": "tripped" if health.tripped else "healthy",
                "failure_count": health.consecutive_failures,
                "last_error": health.last_error,
                "tripped_at_ms": health.tripped_at_ms,
            }
        return snapshot

    def recover(self, name: str) -> bool:
        """手动/探活通过时复位熔断器；返回是否真做了恢复动作。

        同步语义：状态在调用瞬间翻转；``tool.health.<name>`` 切换事件通过
        ``asyncio.create_task`` 后台调度（调用方在 async 上下文时生效，
        无运行循环时静默丢弃——此情形下事件消费方也不会启动）。
        """
        health = self._health.get(name)
        if health is None or not health.tripped:
            return False
        health.consecutive_failures = 0
        health.tripped = False
        health.tripped_at_ms = 0
        health.last_error = ""
        provider_id = health.provider
        logger.info(f"工具 '{name}' 已恢复可用（探活通过）")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 无运行循环：事件总线也无法调度；状态已翻转即可
            return True
        loop.create_task(
            self._emit_tool_health(
                name,
                provider_id,
                state="closed",
                failure_count=0,
                last_error="",
            )
        )
        return True

    # -------------------- 生命周期 --------------------

    def discard(self, name: str) -> None:
        """移除单工具健康状态（工具注销/换血消失时由注册表调用）。"""
        self._health.pop(name, None)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._health

    def clear(self) -> None:
        """清空全部健康状态（注册表清空时联动）。"""
        self._health.clear()


class ToolHealthMonitor:
    """ToolRegistry 熔断器配套的探活服务。

    Attributes:
        registry: 监控目标（含熔断器状态与 provider 列表）
        event_bus: 构造参数保留对称（monitor 不直接订阅/发布事件，
            跃迁事件统一由 ``ToolRegistry`` 经其挂载的 EventBus 广播）
        probe_interval_ms: 探活节拍毫秒；同时作为熔断后最小驻留时长
    """

    def __init__(
        self,
        registry: "ToolRegistry",
        event_bus: Optional["EventBus"] = None,
        *,
        probe_interval_ms: int = 30000,
    ) -> None:
        self._registry = registry
        self._event_bus = event_bus  # 保留构造对称；monitor 不订阅事件
        self._probe_interval_ms = probe_interval_ms
        self._task: Optional[asyncio.Task[None]] = None
        self._stopping = False

    @property
    def probe_interval_ms(self) -> int:
        return self._probe_interval_ms

    def start(self) -> None:
        """启动探活循环（非阻塞；已启动则跳过）。"""
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="tool-health-monitor")

    async def stop(self) -> None:
        """停止探活循环（取消 task 并等待完成）。

        等待带 5 秒上限：探活可能正 awaiting 一个挂死的外部连接
        （如 MCP stdio 子进程），无上限会阻塞整个停机链。
        """
        self._stopping = True
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("ToolHealthMonitor 探活循环取消等待超时（5s），放弃等待继续停机")
        self._task = None

    async def _loop(self) -> None:
        """探活主循环：每节拍调一次 ``probe_cycle``，遇取消即退出。"""
        try:
            while not self._stopping:
                await self.probe_cycle()
                await asyncio.sleep(self._probe_interval_ms / 1000)
        except asyncio.CancelledError:
            # 正常取消路径（stop / 进程退出）
            pass

    async def probe_cycle(self) -> None:
        """执行一次探活轮询——拆出供测试直接驱动，无需 sleep。

        遍历 ``registry.tripped_tools``：
        1. 跳过未到最小驻留时长的工具
        2. ``healthy = await registry.probe_tool(name)``
        3. True → ``recover_tool(name)``；False → 维持熔断（记日志）
        """
        registry = self._registry
        snapshot = registry.tool_health_snapshot()
        for name in registry.tripped_tools:
            entry = snapshot.get(name)
            if entry is None:
                # 熔断刚清掉（理论并发）；跳过
                continue
            tripped_at_ms = int(entry.get("tripped_at_ms", 0) or 0)
            dwell_ms = now_ms() - tripped_at_ms
            if dwell_ms < self._probe_interval_ms:
                logger.debug(
                    f"工具 '{name}' 熔断后驻留 {dwell_ms}ms，小于节拍 {self._probe_interval_ms}ms，跳过本轮探活"
                )
                continue
            spec = registry.get(name)
            if spec is None:
                # 工具已注销：保守起见复位，避免遗留 tripped 状态
                logger.info(f"工具 '{name}' 已注销但仍标记熔断，复位")
                registry.recover_tool(name)
                continue
            try:
                healthy = await registry.probe_tool(name)
            except Exception as exc:  # noqa: BLE001 - 探活失败=不健康，继续熔断
                logger.warning(f"工具 '{name}' 探活抛出异常，按不健康处理: {type(exc).__name__}: {exc}")
                healthy = False
            if healthy:
                logger.info(f"工具 '{name}' 探活通过，恢复可用")
                registry.recover_tool(name)
            else:
                logger.info(f"工具 '{name}' 探活未通过，继续熔断")


__all__ = ["ToolHealthState", "ToolCircuitBreaker", "ToolHealthMonitor"]
