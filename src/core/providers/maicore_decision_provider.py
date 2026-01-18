"""
MaiCoreDecisionProvider - MaiCore决策提供者

职责:
- 管理WebSocket连接
- 管理HTTP服务器
- 管理Router
- 将CanonicalMessage转换为决策结果(MessageBase)
"""

import asyncio
from typing import Callable, Dict, Any, Optional, TYPE_CHECKING

from aiohttp import web
from maim_message import Router, RouteConfig, TargetConfig, MessageBase

from src.core.providers.decision_provider import DecisionProvider
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.canonical.canonical_message import CanonicalMessage


class MaiCoreDecisionProvider(DecisionProvider):
    """
    MaiCore决策提供者

    通过WebSocket与MaiCore通信，支持HTTP回调。

    职责:
    - WebSocket连接管理
    - HTTP服务器管理
    - Router消息路由
    - CanonicalMessage到MessageBase的转换

    配置示例:
        ```toml
        [decision.maicore]
        host = "localhost"
        port = 8000
        platform = "amaidesu_default"
        http_host = "localhost"
        http_port = 8080
        http_callback_path = "/callback"
        ```
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化MaiCoreDecisionProvider

        Args:
            config: 配置字典，包含:
                - host: MaiCore WebSocket服务器主机
                - port: MaiCore WebSocket服务器端口
                - platform: 平台标识符
                - http_host: (可选) HTTP服务器主机
                - http_port: (可选) HTTP服务器端口
                - http_callback_path: (可选) HTTP回调路径，默认"/callback"
        """
        self.config = config
        self.logger = get_logger("MaiCoreDecisionProvider")

        # WebSocket配置
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 8000)
        self.platform = config.get("platform", "amaidesu_default")
        self.ws_url = f"ws://{self.host}:{self.port}/ws"

        # HTTP配置
        self.http_host = config.get("http_host")
        self.http_port = config.get("http_port")
        self.http_callback_path = config.get("http_callback_path", "/callback")

        # Router
        self._router: Optional[Router] = None

        # 连接状态
        self._is_connected = False
        self._connect_lock = asyncio.Lock()

        # WebSocket任务
        self._ws_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None

        # HTTP服务器
        self._http_runner: Optional[web.AppRunner] = None
        self._http_site: Optional[web.TCPSite] = None
        self._http_app: Optional[web.Application] = None

        # HTTP处理器
        self._http_request_handlers: Dict[str, list[Callable[[web.Request], asyncio.Task]]] = {}

        # EventBus引用（用于事件通知）
        self._event_bus: Optional["EventBus"] = None

    async def setup(self, event_bus: "EventBus", config: Dict[str, Any]) -> None:
        """
        设置MaiCoreDecisionProvider

        Args:
            event_bus: EventBus实例
            config: Provider配置（忽略，使用__init__传入的config）
        """
        self._event_bus = event_bus
        self.logger.info("初始化MaiCoreDecisionProvider...")

        # 配置Router
        self._setup_router()

        # 配置HTTP服务器（如果启用了）
        if self.http_host and self.http_port:
            self._setup_http_server()
        else:
            self.logger.info("HTTP服务器未配置")

        self.logger.info("MaiCoreDecisionProvider初始化完成")

    def _setup_router(self):
        """配置maim_message Router"""
        route_config = RouteConfig(
            route_config={
                self.platform: TargetConfig(
                    url=self.ws_url,
                    token=None,  # 根据需要配置Token
                )
            }
        )
        self._router = Router(route_config)
        # 注册内部处理函数，用于接收所有来自MaiCore的消息
        self._router.register_class_handler(self._handle_maicore_message)
        self.logger.info(f"Router配置完成，目标MaiCore: {self.ws_url}")

    def _setup_http_server(self):
        """配置aiohttp应用和路由"""
        self._http_app = web.Application()
        # 注册统一的HTTP回调处理入口
        self._http_app.router.add_post(self.http_callback_path, self._handle_http_request)
        self.logger.info(f"HTTP服务器配置完成，监听路径: {self.http_callback_path}")

    async def connect(self):
        """启动WebSocket连接后台任务和HTTP服务器（如果配置了）"""
        async with self._connect_lock:
            if self._is_connected or self._ws_task:
                self.logger.warning("MaiCore已连接或正在连接中，无需重复连接。")
                return
            if not self._router:
                self.logger.error("Router未初始化，无法连接WebSocket。")
                return

            connect_tasks = []

            # 启动WebSocket连接任务
            self.logger.info(f"准备启动MaiCore WebSocket连接 ({self.ws_url})...")
            self._ws_task = asyncio.create_task(self._run_websocket(), name="WebSocketRunTask")

            # 添加监控任务
            self._monitor_task = asyncio.create_task(self._monitor_ws_connection(), name="WebSocketMonitorTask")

            # 启动HTTP服务器（如果配置了）
            if self.http_host and self.http_port:
                self.logger.info(f"正在启动HTTP服务器 ({self.http_host}:{self.http_port})...")
                http_server_task = asyncio.create_task(self._start_http_server_internal(), name="HttpServerStartTask")
                connect_tasks.append(http_server_task)

            # 等待HTTP服务器启动完成（如果启动了）
            if connect_tasks:
                results = await asyncio.gather(*connect_tasks, return_exceptions=True)
                # 检查HTTP启动结果
                for i, task in enumerate(connect_tasks):
                    if task.get_coro().__name__ == "_start_http_server_internal":
                        if isinstance(results[i], Exception):
                            self.logger.error(f"启动HTTP服务器失败: {results[i]}", exc_info=results[i])
                        else:
                            self.logger.info(
                                f"HTTP服务器成功启动于 http://{self.http_host}:{self.http_port}{self.http_callback_path}"
                            )
                        break

            self.logger.info("MaiCore连接流程启动完成 (WebSocket在后台运行)。")

    async def _run_websocket(self):
        """内部方法：运行WebSocket router.run()"""
        if not self._router:
            self.logger.error("Router未初始化，无法运行WebSocket。")
            return
        try:
            self.logger.info("WebSocket run()任务开始运行...")
            await self._router.run()  # 这个会一直运行直到断开
        except asyncio.CancelledError:
            self.logger.info("WebSocket run()任务被取消。")
        except Exception as e:
            self.logger.error(f"WebSocket run()任务异常终止: {e}", exc_info=True)
        finally:
            self.logger.info("WebSocket run()任务已结束。")

    async def _monitor_ws_connection(self):
        """内部方法：监控WebSocket连接任务的状态"""
        if not self._ws_task:
            return
        self.logger.info("WebSocket连接监控任务已启动。")
        try:
            # 等待一小段时间尝试连接
            await asyncio.sleep(1)
            if self._ws_task and not self._ws_task.done():
                self.logger.info("WebSocket连接初步建立，标记为已连接。")
                self._is_connected = True

                # 通过EventBus发布连接事件
                if self._event_bus:
                    try:
                        await self._event_bus.emit("decision_provider.connected", {"provider": "maicore"})
                    except Exception as e:
                        self.logger.error(f"发布连接事件失败: {e}", exc_info=True)
            else:
                self.logger.warning("WebSocket任务在监控开始前已结束，连接失败。")
                self._is_connected = False
                return

            # 等待任务结束（表示断开连接）
            await self._ws_task
            self.logger.warning("检测到WebSocket连接任务已结束，标记为未连接。")

        except asyncio.CancelledError:
            self.logger.info("WebSocket连接监控任务被取消。")
        except Exception as e:
            self.logger.error(f"WebSocket连接监控任务异常退出: {e}", exc_info=True)
        finally:
            # 通过EventBus发布断开事件
            if self._is_connected and self._event_bus:
                try:
                    await self._event_bus.emit("decision_provider.disconnected", {"provider": "maicore"})
                except Exception as e:
                    self.logger.error(f"发布断开事件失败: {e}", exc_info=True)

            self.logger.info("WebSocket连接监控任务已结束。")
            self._is_connected = False
            self._ws_task = None
            self._monitor_task = None

    async def _start_http_server_internal(self):
        """内部方法：启动aiohttp服务器"""
        if not self._http_app or not self.http_host or not self.http_port:
            raise ConnectionError("HTTP服务器未正确配置")
        try:
            self._http_runner = web.AppRunner(self._http_app)
            await self._http_runner.setup()
            self._http_site = web.TCPSite(self._http_runner, self.http_host, self.http_port)
            await self._http_site.start()
        except Exception as e:
            # 清理可能已部分创建的资源
            if self._http_runner:
                await self._http_runner.cleanup()
                self._http_runner = None
            self._http_site = None
            raise ConnectionError(f"无法启动HTTP服务器: {e}") from e

    async def disconnect(self):
        """取消WebSocket任务并停止HTTP服务器"""
        async with self._connect_lock:
            tasks = []

            # 停止WebSocket任务
            if self._ws_task and not self._ws_task.done():
                self.logger.info("正在取消WebSocket run()任务...")
                self._ws_task.cancel()
                tasks.append(self._ws_task)
            # 停止监控任务
            if self._monitor_task and not self._monitor_task.done():
                self.logger.debug("正在取消WebSocket监控任务...")
                self._monitor_task.cancel()
                tasks.append(self._monitor_task)

            # 停止HTTP服务器
            if self._http_runner:
                self.logger.info("正在停止HTTP服务器...")
                tasks.append(asyncio.create_task(self._stop_http_server_internal()))

            if not tasks:
                self.logger.warning("没有活动的任务需要停止。")
                return

            # 等待所有任务结束
            self.logger.debug(f"等待{len(tasks)}个任务结束...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            self.logger.debug(f"所有停止任务已完成，结果: {results}")

            # 清理状态
            self._is_connected = False
            self._ws_task = None
            self._monitor_task = None
            self.logger.info("MaiCore服务已断开并清理。")

    async def _stop_http_server_internal(self):
        """内部方法：停止aiohttp服务器"""
        if self._http_runner:
            await self._http_runner.cleanup()
            self._http_runner = None
            self._http_site = None
            self._http_app = None

    async def decide(self, canonical_message: "CanonicalMessage") -> MessageBase:
        """
        进行决策（发送消息到MaiCore）

        Args:
            canonical_message: 标准化消息

        Returns:
            MessageBase: 决策结果（实际上是将消息发送到MaiCore）

        Raises:
            RuntimeError: 如果未连接
            ConnectionError: 如果发送失败
        """
        if not self._is_connected:
            raise RuntimeError("MaiCore未连接，无法发送消息")

        # 转换CanonicalMessage为MessageBase
        message = canonical_message.to_message_base()
        if not message:
            self.logger.error("转换为MessageBase失败，无法发送消息")
            raise RuntimeError("无法将CanonicalMessage转换为MessageBase")

        # 发送消息
        if not self._router:
            self.logger.error("Router未初始化，无法发送消息")
            raise RuntimeError("Router未初始化")

        try:
            self.logger.debug(f"准备发送消息到MaiCore: {message.message_info.message_id}")
            await self._router.send_message(message)
            self.logger.info(f"消息{message.message_info.message_id}已发送至MaiCore")
            return message
        except Exception as e:
            self.logger.error(f"发送消息到MaiCore时发生错误: {e}", exc_info=True)
            raise ConnectionError(f"发送消息失败: {e}") from e

    async def _handle_maicore_message(self, message_data: Dict[str, Any]):
        """
        处理从MaiCore接收到的消息

        通过EventBus发布消息事件。

        Args:
            message_data: 消息数据（字典格式）
        """
        try:
            # 从字典构建MessageBase对象
            message = MessageBase.from_dict(message_data)
        except Exception as e:
            self.logger.error(f"从MaiCore接收到的消息无法解析为MessageBase对象: {e}", exc_info=True)
            self.logger.debug(f"原始消息数据: {message_data}")
            return

        # 通过EventBus发布消息事件
        if self._event_bus:
            try:
                await self._event_bus.emit("maicore.message", {"message": message})
            except Exception as e:
                self.logger.error(f"发布消息事件失败: {e}", exc_info=True)

    async def _handle_http_request(self, request: web.Request) -> web.Response:
        """
        内部方法，处理所有到达指定回调路径的HTTP POST请求

        Args:
            request: aiohttp Request对象

        Returns:
            aiohttp Response对象
        """
        self.logger.info(f"收到来自{request.remote}的HTTP请求: {request.method}{request.path}")

        # HTTP请求分发逻辑
        dispatch_key = "http_callback"

        response_tasks = []
        if dispatch_key in self._http_request_handlers:
            handlers = self._http_request_handlers[dispatch_key]
            self.logger.info(f"为HTTP请求找到{len(handlers)}个'{dispatch_key}'处理器")
            # 让每个handler处理请求
            for handler in handlers:
                response_tasks.append(asyncio.create_task(handler(request)))
        else:
            self.logger.warning(f"没有找到适用于HTTP回调Key='{dispatch_key}'的处理器")
            return web.json_response(
                {"status": "error", "message": "No handler configured for this request"}, status=404
            )

        # 处理来自handlers的响应
        gathered_responses = await asyncio.gather(*response_tasks, return_exceptions=True)

        final_response: Optional[web.Response] = None
        first_exception: Optional[Exception] = None

        for result in gathered_responses:
            if isinstance(result, web.Response):
                if final_response is None:  # 取第一个有效响应
                    final_response = result
            elif isinstance(result, Exception):
                self.logger.error(f"处理HTTP请求时，某个handler抛出异常: {result}", exc_info=result)
                if first_exception is None:
                    first_exception = result

        if final_response:
            self.logger.info(f"HTTP请求处理完成，返回状态: {final_response.status}")
            return final_response
        elif first_exception:
            # 如果有异常但没有成功响应，返回500
            return web.json_response(
                {"status": "error", "message": f"Error processing request: {first_exception}"}, status=500
            )
        else:
            # 如果没有handler返回响应也没有异常
            self.logger.info("HTTP请求处理完成，没有显式响应，返回默认成功状态。")
            return web.json_response({"status": "accepted"}, status=202)  # 202 Accepted

    def register_http_handler(self, key: str, handler: Callable[[web.Request], asyncio.Task]):
        """
        注册一个处理HTTP回调请求的处理器

        Args:
            key: 用于匹配请求的键
            handler: 异步函数，接收aiohttp.web.Request对象，应返回aiohttp.web.Response对象
        """
        if not asyncio.iscoroutinefunction(handler):
            self.logger.warning(f"注册的HTTP处理器'{handler.__name__}'不是一个异步函数 (async def)。")

        if key not in self._http_request_handlers:
            self._http_request_handlers[key] = []
        self._http_request_handlers[key].append(handler)
        self.logger.info(f"成功注册HTTP请求处理器: Key='{key}', Handler='{handler.__name__}'")

    async def cleanup(self) -> None:
        """
        清理资源

        断开连接并清理所有资源。
        """
        self.logger.info("清理MaiCoreDecisionProvider...")
        await self.disconnect()
        self.logger.info("MaiCoreDecisionProvider已清理")

    @property
    def is_connected(self) -> bool:
        """获取连接状态"""
        return self._is_connected

    @property
    def router(self) -> Optional[Router]:
        """获取Router实例"""
        return self._router

    def get_info(self) -> Dict[str, Any]:
        """
        获取Provider信息

        Returns:
            Provider信息字典
        """
        return {
            "name": "MaiCoreDecisionProvider",
            "version": "1.0.0",
            "host": self.host,
            "port": self.port,
            "platform": self.platform,
            "http_host": self.http_host,
            "http_port": self.http_port,
            "is_connected": self._is_connected,
        }
