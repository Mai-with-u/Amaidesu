"""SimulatorService - 模拟直播间服务的生命周期管理器

与 InputCollectorManager 并列的独立一等公民，职责：
- 创建模拟器实例（DI 注入 LLM / Prompt / ContextService / EventHistoryService）
- 在独立 asyncio task 中驱动 ``start() → collect() → stop()`` 生命周期
- 将模拟消息发布到 ``CoreEvents.INPUT_MESSAGE_RECEIVED`` 事件
- 暴露 ``.simulator`` 属性供 Dashboard API 访问控制面

设计决策：
- **不经过 Input Pipeline**：模拟器自带节奏控制（CadenceGenerator）和人设管理，
  Input Pipeline 的限流/去重对模拟器冗余且有损，故直接 ``emit`` 到 EventBus。
- **独立生命周期**：模拟器的启停与 ``collectors.enabled`` 列表无关，
  由 ``simulator.toml`` 的 ``[simulator].enabled`` 控制自动启动。
- **安装顺序**：在 main.py 中应于 InputCollectorManager 之后、DashboardServer 之前创建。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.modules.di.instantiation import instantiate_with_di
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.input import MessageReadyPayload
from src.modules.logging import get_logger
from src.modules.simulator.simulator import LiveStreamSimulator

if TYPE_CHECKING:
    from src.modules.config.service import ConfigService


class SimulatorService:
    """模拟直播间服务 — 生命周期管理器

    管理单个 ``LiveStreamSimulator`` 实例，职责等价于一个"Manager of one"。
    """

    def __init__(
        self,
        event_bus: EventBus,
        services_by_type: Optional[Dict[type, Any]] = None,
    ) -> None:
        self.event_bus = event_bus
        self._services_by_type = services_by_type or {}
        self._simulator: Optional[LiveStreamSimulator] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._is_started = False
        self.logger = get_logger("SimulatorService")

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def setup(self, config_service: "ConfigService") -> None:
        """从 ConfigService 加载配置并创建模拟器实例

        创建后立即初始化数据平面（人设池 / LLM 包装器），
        因此人设管理 API 不依赖模拟器运行状态；
        若 ``simulator.enabled = true`` 则自动启动。
        """
        simulator_config = config_service.main_config.get("simulator", {})
        if not isinstance(simulator_config, dict):
            self.logger.warning("simulator 配置不是 dict，跳过创建")
            return

        services = {EventBus: self.event_bus, **self._services_by_type}
        self._simulator = instantiate_with_di(
            LiveStreamSimulator,
            config=simulator_config,
            services_by_type=services,
        )
        if self._simulator is not None:
            await self._simulator.setup()

        # 自动启动
        enabled = simulator_config.get("enabled", False)
        if enabled:
            self.logger.info("模拟器配置已启用，自动启动中...")
            await self.start()

    async def start(self) -> None:
        """启动模拟器（幂等）"""
        if self._is_started:
            self.logger.debug("模拟器已运行，忽略重复 start")
            return
        if self._simulator is None:
            self.logger.warning("模拟器实例未创建，请先调用 setup()")
            return

        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="SimulatorService")
        self._is_started = True
        self.logger.info("模拟器服务已启动")

    async def _run(self) -> None:
        """驱动模拟器主循环并发布 INPUT_MESSAGE_RECEIVED 事件"""
        assert self._simulator is not None
        try:
            await self._simulator.start()
            async for message in self._simulator.collect():
                if self._stop_event.is_set():
                    break
                payload = MessageReadyPayload.from_normalized_message(message)
                await self.event_bus.emit(
                    CoreEvents.INPUT_MESSAGE_RECEIVED,
                    payload,
                    source="simulated_live_stream",
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"模拟器主循环异常: {e}", exc_info=True)
        finally:
            try:
                await self._simulator.stop()
            except Exception as e:
                self.logger.warning(f"模拟器 stop 失败: {e}")

    async def stop(self) -> None:
        """停止模拟器"""
        if not self._is_started:
            return
        self._is_started = False
        self._stop_event.set()

        if self._task is not None:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._task = None
        self.logger.info("模拟器服务已停止")

    async def cleanup(self) -> None:
        """清理资源"""
        if self._is_started:
            await self.stop()
        if self._simulator is not None:
            try:
                await self._simulator.cleanup()
            except Exception as e:
                self.logger.warning(f"模拟器 cleanup 失败: {e}")
            self._simulator = None
        self.logger.info("模拟器服务已清理")

    # ------------------------------------------------------------------
    # Dashboard 访问面
    # ------------------------------------------------------------------

    @property
    def simulator(self) -> Optional[LiveStreamSimulator]:
        """暴露模拟器实例供 Dashboard API 调用控制面方法"""
        return self._simulator

    @property
    def is_running(self) -> bool:
        """模拟器是否正在运行"""
        return self._is_started
