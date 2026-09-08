"""
工具健康监控（熔断器探活循环）

按 ``[tools.health].probe_interval_ms`` 节拍扫描 ``ToolRegistry.tripped_tools``，
对每个熔断工具调用 ``registry.probe_tool(name)``：

- 返回 True → ``recover_tool(name)`` 复位熔断器
- 返回 False → 维持熔断（下一节拍再试）

熔断后最小驻留时长复用 ``probe_interval_ms``：未到时长时本轮不尝试恢复，
避免抖动恢复导致反复熔断。健康判定统一走 ``registry.probe_tool`` 一条路径——
所有经 ``register_provider`` 注册的 provider 都继承 ``BaseToolProvider`` 并拥有
``health_check`` 方法，无状态 Provider 沿用基类默认实现（返回 True），行为等价
于旧"无 health_check → 定时恢复"路径——熔断后冷却期满即恢复，再失败再熔断。

本服务**不**订阅任何事件——熔断与恢复事件统一由 ``ToolRegistry`` 广播；
monitor 仅做消费方驱动。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus
    from src.modules.tools.registry import ToolRegistry


logger = get_logger("ToolHealthMonitor")


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


__all__ = ["ToolHealthMonitor"]
