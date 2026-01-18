"""
WebSocket 连接管理器

职责:
- 管理与 MaiCore 的 WebSocket 连接
- 监控连接状态
- 通过 EventBus 发布连接事件
"""

import asyncio
from typing import Optional, TYPE_CHECKING

from src.core.events.names import CoreEvents
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from maim_message import Router


class WebSocketConnector:
    """
    WebSocket 连接管理器

    负责管理与 MaiCore 的 WebSocket 连接生命周期和状态监控。
    """

    def __init__(
        self,
        ws_url: str,
        router: "Router",
        event_bus: Optional["EventBus"] = None,
        provider_name: str = "maicore",
    ):
        """
        初始化 WebSocket 连接管理器

        Args:
            ws_url: WebSocket 服务器 URL
            router: maim_message Router 实例
            event_bus: EventBus 实例（用于发布连接事件）
            provider_name: Provider 名称（用于事件标识）
        """
        self.ws_url = ws_url
        self._router = router
        self._event_bus = event_bus
        self.provider_name = provider_name
        self.logger = get_logger("WebSocketConnector")

        # 连接状态
        self._is_connected = False

        # WebSocket 任务
        self._ws_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None

    async def connect(self):
        """
        启动 WebSocket 连接

        启动后台任务来运行 WebSocket 和监控连接状态。
        """
        if self._is_connected or self._ws_task:
            self.logger.warning(f"{self.provider_name} 已连接或正在连接中，无需重复连接。")
            return

        if not self._router:
            self.logger.error("Router 未初始化，无法连接 WebSocket。")
            return

        # 启动 WebSocket 连接任务
        self.logger.info(f"准备启动 {self.provider_name} WebSocket 连接 ({self.ws_url})...")
        self._ws_task = asyncio.create_task(self._run_websocket(), name=f"{self.provider_name}WebSocketRunTask")

        # 添加监控任务
        self._monitor_task = asyncio.create_task(
            self._monitor_ws_connection(), name=f"{self.provider_name}WebSocketMonitorTask"
        )

        self.logger.info(f"{self.provider_name} WebSocket 连接流程已启动 (后台运行)")

    async def _run_websocket(self):
        """内部方法：运行 WebSocket router.run()"""
        if not self._router:
            self.logger.error("Router 未初始化，无法运行 WebSocket。")
            return

        try:
            self.logger.info(f"{self.provider_name} WebSocket run() 任务开始运行...")
            await self._router.run()  # 这个会一直运行直到断开
        except asyncio.CancelledError:
            self.logger.info(f"{self.provider_name} WebSocket run() 任务被取消。")
        except Exception as e:
            self.logger.error(f"{self.provider_name} WebSocket run() 任务异常终止: {e}", exc_info=True)
        finally:
            self.logger.info(f"{self.provider_name} WebSocket run() 任务已结束。")

    async def _monitor_ws_connection(self):
        """内部方法：监控 WebSocket 连接任务的状态"""
        if not self._ws_task:
            return

        self.logger.info(f"{self.provider_name} WebSocket 连接监控任务已启动。")

        try:
            # 等待一小段时间尝试连接
            await asyncio.sleep(1)

            if self._ws_task and not self._ws_task.done():
                self.logger.info(f"{self.provider_name} WebSocket 连接初步建立，标记为已连接。")
                self._is_connected = True

                # 通过 EventBus 发布连接事件
                if self._event_bus:
                    try:
                        await self._event_bus.emit(CoreEvents.DECISION_PROVIDER_CONNECTED, {"provider": self.provider_name})
                    except Exception as e:
                        self.logger.error(f"发布连接事件失败: {e}", exc_info=True)
            else:
                self.logger.warning(f"{self.provider_name} WebSocket 任务在监控开始前已结束，连接失败。")
                self._is_connected = False
                return

            # 等待任务结束（表示断开连接）
            await self._ws_task
            self.logger.warning(f"检测到 {self.provider_name} WebSocket 连接任务已结束，标记为未连接。")

        except asyncio.CancelledError:
            self.logger.info(f"{self.provider_name} WebSocket 连接监控任务被取消。")
        except Exception as e:
            self.logger.error(f"{self.provider_name} WebSocket 连接监控任务异常退出: {e}", exc_info=True)
        finally:
            # 通过 EventBus 发布断开事件
            if self._is_connected and self._event_bus:
                try:
                    await self._event_bus.emit(CoreEvents.DECISION_PROVIDER_DISCONNECTED, {"provider": self.provider_name})
                except Exception as e:
                    self.logger.error(f"发布断开事件失败: {e}", exc_info=True)

            self.logger.info(f"{self.provider_name} WebSocket 连接监控任务已结束。")
            self._is_connected = False
            self._ws_task = None
            self._monitor_task = None

    async def disconnect(self):
        """
        断开 WebSocket 连接

        取消所有后台任务并清理资源。
        """
        tasks = []

        # 停止 WebSocket 任务
        if self._ws_task and not self._ws_task.done():
            self.logger.info(f"正在取消 {self.provider_name} WebSocket run() 任务...")
            self._ws_task.cancel()
            tasks.append(self._ws_task)

        # 停止监控任务
        if self._monitor_task and not self._monitor_task.done():
            self.logger.debug(f"正在取消 {self.provider_name} WebSocket 监控任务...")
            self._monitor_task.cancel()
            tasks.append(self._monitor_task)

        if not tasks:
            self.logger.warning(f"{self.provider_name} 没有活动的任务需要停止。")
            return

        # 等待所有任务结束
        self.logger.debug(f"等待 {len(tasks)} 个任务结束...")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self.logger.debug(f"所有停止任务已完成，结果: {results}")

        # 清理状态
        self._is_connected = False
        self._ws_task = None
        self._monitor_task = None
        self.logger.info(f"{self.provider_name} WebSocket 服务已断开并清理。")

    @property
    def is_connected(self) -> bool:
        """获取连接状态"""
        return self._is_connected
