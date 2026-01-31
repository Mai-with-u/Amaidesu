"""
MaiCore决策Provider实现

职责:
- 管理WebSocket连接到MaiCore
- 管理HTTP服务器接收MaiCore回调
- 管理maim_message Router
- 将CanonicalMessage转换为MessageBase并发送
- 接收MaiCore响应并转换为MessageBase
"""

import asyncio
from typing import Dict, Any, Optional, TYPE_CHECKING

from aiohttp import web
from maim_message import Router, RouteConfig, TargetConfig, MessageBase
from maim_message.message_base import BaseMessageInfo, UserInfo, Seg, FormatInfo

from src.core.providers.decision_provider import DecisionProvider
from src.utils.logger import get_logger
from src.canonical.canonical_message import CanonicalMessage

if TYPE_CHECKING:
    from src.core.event_bus import EventBus


class MaiCoreDecisionProvider(DecisionProvider):
    """
    MaiCore决策Provider(默认实现)

    职责:
    - 使用WebSocket连接到MaiCore
    - 使用HTTP服务器接收MaiCore回调
    - 使用maim_message Router进行通信
    - 将CanonicalMessage转换为MessageBase并发送
    - 接收MaiCore响应并转换为MessageBase

    配置:
        - host: MaiCore WebSocket主机地址
        - port: MaiCore WebSocket端口
        - http_host: HTTP回调监听地址
        - http_port: HTTP回调监听端口
        - http_callback_path: HTTP回调路径
        - platform: 平台标识符(默认"amaidesu_default")
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 8000)
        self.http_host = config.get("http_host")
        self.http_port = config.get("http_port")
        self.http_callback_path = config.get("http_callback_path", "/callback")
        self.platform = config.get("platform", "amaidesu_default")

        # Router和WebSocket相关
        self.router: Optional[Router] = None
        self.ws_url = f"ws://{self.host}:{self.port}/ws"

        # HTTP服务器相关
        self._http_runner: Optional[web.AppRunner] = None
        self._http_site: Optional[web.TCPSite] = None
        self._http_app: Optional[web.Application] = None

        # WebSocket任务相关
        self._ws_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._is_connected = False

        # HTTP请求处理器
        self._http_request_handlers: Dict[str, list] = {}

        self.logger = get_logger("MaiCoreDecisionProvider")

    async def setup(self, event_bus: "EventBus", config: dict) -> None:
        """
        初始化MaiCore DecisionProvider

        配置WebSocket、HTTP服务器和Router。
        """
        super().setup(event_bus, config)

        # 设置Router
        self._setup_router()

        # 设置HTTP服务器
        if self.http_host and self.http_port:
            self._setup_http_server()

        # 订阅EventBus事件
        self.event_bus.on("canonical.message_ready", self._on_canonical_message)

        self.logger.info(f"MaiCoreDecisionProvider初始化完成 (WebSocket: {self.ws_url})")

    def _setup_router(self) -> None:
        """配置maim_message Router"""
        route_config = RouteConfig(
            route_config={
                self.platform: TargetConfig(
                    url=self.ws_url,
                    token=None,
                )
            }
        )

        self.router = Router(route_config)
        self.router.register_class_handler(self._handle_maicore_message)

        self.logger.info(f"Router配置完成，目标MaiCore: {self.ws_url}")

    def _setup_http_server(self) -> None:
        """配置aiohttp应用和路由"""
        if not (self.http_host and self.http_port):
            return
        self._http_app = web.Application()
        self._http_app.router.add_post(self.http_callback_path, self._handle_http_request)
        self.logger.info(f"HTTP服务器配置完成，监听路径: {self.http_callback_path}")

    async def _setup_internal(self):
        """内部设置逻辑(子类可选重写)"""
        # 启动WebSocket连接任务
        self.logger.info(f"准备启动MaiCore WebSocket连接 ({self.ws_url})...")
        self._ws_task = asyncio.create_task(self._run_websocket(), name="WebSocketRunTask")

        # 添加监控任务
        self._monitor_task = asyncio.create_task(self._monitor_ws_connection(), name="WebSocketMonitorTask")

        # 启动HTTP服务器(如果配置了)
        if self._http_host and self._http_port:
            self.logger.info(f"正在启动HTTP服务器 ({self.http_host}:{self.http_port})...")
            http_server_task = asyncio.create_task(
                self._start_http_server_internal(),
                name="HttpServerStartTask",
            )
            # 等待HTTP服务器启动
            await asyncio.gather(http_server_task, return_exceptions=True)

    async def _run_websocket(self) -> None:
        """运行WebSocket router.run()"""
        if not self.router:
            self.logger.error("Router未初始化，无法运行WebSocket。")
            return

        try:
            self.logger.info("WebSocket run() 任务开始运行...")
            await self.router.run()
        except asyncio.CancelledError:
            self.logger.info("WebSocket run() 任务被取消。")
        except Exception as e:
            self.logger.error(f"WebSocket run() 任务异常终止: {e}", exc_info=True)

    async def _monitor_ws_connection(self) -> None:
        """监控WebSocket连接任务的状态"""
        if not self._ws_task:
            return

        self.logger.info("WebSocket连接监控任务已启动。")
        try:
            # 等待1秒尝试连接
            await asyncio.sleep(1)
            if self._ws_task and not self._ws_task.done():
                self.logger.info("WebSocket连接初步建立，标记核心为已连接。")
                self._is_connected = True

                # 发布连接建立事件
                await self.event_bus.emit(
                    "decision.maicore.connected",
                    {"host": self.host, "port": self.port},
                    source="MaiCoreDecisionProvider",
                )
            else:
                self.logger.warning("WebSocket任务在监控开始前已结束，连接失败。")
                self._is_connected = False
                return

            # 等待任务结束(表示断开连接)
            await self._ws_task
            self.logger.warning("检测到WebSocket连接任务已结束，标记核心为未连接。")
        except asyncio.CancelledError:
            self.logger.info("WebSocket连接监控任务被取消。")
        except Exception as e:
            self.logger.error(f"WebSocket连接监控任务异常退出: {e}", exc_info=True)
        finally:
            # 发布断开连接事件
            if self._is_connected:
                await self.event_bus.emit(
                    "decision.maicore.disconnected",
                    {"reason": "WebSocket connection closed"},
                    source="MaiCoreDecisionProvider",
                )

            self._is_connected = False
            self._ws_task = None
            self._monitor_task = None
            self.logger.info("WebSocket连接监控任务已结束。")

    async def _start_http_server_internal(self) -> None:
        """内部方法：启动aiohttp服务器"""
        if not self._http_app or not self._http_host or not self._http_port:
            raise ConnectionError("HTTP服务器未正确配置")

        try:
            self._http_runner = web.AppRunner(self._http_app)
            await self._http_runner.setup()
            self._http_site = web.TCPSite(self._http_runner, self._http_host, self._http_port)
            await self._http_site.start()
        except Exception as e:
            # 清理可能已部分创建的资源
            if self._http_runner:
                await self._http_runner.cleanup()
                self._http_runner = None
            self._http_site = None
            raise ConnectionError(f"无法启动HTTP服务器: {e}") from e

    async def _stop_http_server_internal(self) -> None:
        """内部方法：停止aiohttp服务器"""
        if self._http_runner:
            await self._http_runner.cleanup()
            self._http_runner = None
            self._http_site = None
            self._http_app = None

    async def _on_canonical_message(self, event_name: str, event_data: dict, source: str) -> None:
        """处理CanonicalMessage事件"""
        # 支持新格式 {"canonical": ...} 和旧格式 {"data": ...}
        canonical_message = event_data.get("canonical") or event_data.get("data")
        if not canonical_message:
            self.logger.warning(f"收到空的canonical.message_ready事件 (source: {source})")
            return

        # 构建MessageBase
        message = self._build_messagebase(canonical_message)

        # 发送给MaiCore
        if self.router and self._is_connected:
            try:
                await self.router.send_message(message)
                self.logger.info(f"消息已发送至MaiCore: {message.message_info.message_id}")

                # 发布消息已发送事件
                await self.event_bus.emit(
                    "decision.message_sent",
                    {
                        "message_id": message.message_info.message_id,
                        "text": canonical_message.text,
                    },
                    source="MaiCoreDecisionProvider",
                )
            except Exception as e:
                self.logger.error(f"发送消息到MaiCore时发生错误: {e}", exc_info=True)

    def _build_messagebase(self, canonical_message: CanonicalMessage) -> MessageBase:
        """构建MessageBase"""
        # 提取UserInfo
        user_info = UserInfo(
            user_id=canonical_message.metadata.get("user_id", "unknown"),
            nickname=canonical_message.metadata.get("user_nickname", canonical_message.source),
        )

        # 构建FormatInfo
        format_info = FormatInfo(
            font=None,
            color=None,
            size=None,
        )

        # 构建Seg
        seg = Seg(
            type="text",
            data=canonical_message.text,
            format=format_info,
        )

        # 构建MessageBase
        message = MessageBase(
            message_info=BaseMessageInfo(
                message_id=f"decision_{int(canonical_message.timestamp)}",
                platform=canonical_message.source,
                sender=user_info,
                timestamp=canonical_message.timestamp,
            ),
            message_segment=seg,
        )

        return message

    async def _handle_maicore_message(self, message_data: Dict[str, Any]) -> None:
        """处理从MaiCore接收到的消息"""
        try:
            message = MessageBase.from_dict(message_data)
        except Exception as e:
            self.logger.error(
                f"从MaiCore接收到的消息无法解析为MessageBase对象: {e}",
                exc_info=True,
            )
            self.logger.debug(f"原始消息数据: {message_data}")
            return

        # 发布到EventBus
        await self.event_bus.emit(
            "decision.response_generated",
            {"data": message},
            source="MaiCoreDecisionProvider",
        )

        self.logger.info(f"收到MaiCore消息: {message.message_info.message_id}")

    async def _handle_http_request(self, request: web.Request) -> web.Response:
        """内部方法，处理所有到达指定回调路径的HTTP POST请求"""
        self.logger.info(f"收到来自 {request.remote} 的HTTP请求: {request.method} {request.path}")

        # 简化实现：使用固定key "http_callback" 分发给所有注册的HTTP处理器
        dispatch_key = "http_callback"

        response_tasks = []
        if dispatch_key in self._http_request_handlers:
            handlers = self._http_request_handlers[dispatch_key]
            self.logger.info(f"为HTTP请求找到 {len(handlers)} 个 '{dispatch_key}' 处理器")
            # 让每个handler处理请求，它们应该返回web.Response或引发异常
            for handler in handlers:
                response_tasks.append(asyncio.create_task(handler(request)))
        else:
            self.logger.warning(f"没有找到适用于HTTP回调Key='{dispatch_key}' 的处理器")
            return web.json_response(
                {"status": "error", "message": "No handler configured for this request"},
                status=404,
            )

        # 处理来自handlers的响应
        final_response: Optional[web.Response] = None
        first_exception: Optional[Exception] = None

        gathered_responses = await asyncio.gather(*response_tasks, return_exceptions=True)

        for result in gathered_responses:
            if isinstance(result, web.Response):
                if final_response is None:
                    final_response = result
            elif isinstance(result, Exception):
                self.logger.error(
                    f"处理HTTP请求时，某个handler抛出异常: {result}",
                    exc_info=result,
                )
                if first_exception is None:
                    first_exception = result

        if final_response:
            self.logger.info(f"HTTP请求处理完成，返回状态: {final_response.status}")
            return final_response
        elif first_exception:
            return web.json_response(
                {"status": "error", "message": f"Error processing request: {first_exception}"},
                status=500,
            )
        else:
            self.logger.info("HTTP请求处理完成，没有显式响应，返回默认成功状态。")
            return web.json_response({"status": "accepted"}, status=202)

    def register_http_handler(self, key: str, handler) -> None:
        """
        注册一个处理HTTP回调请求的处理器

        Args:
            key: 用于匹配请求的键
            handler: 一个异步函数，接收aiohttp.web.Request对象，并应返回aiohttp.web.Response对象
        """
        if key not in self._http_request_handlers:
            self._http_request_handlers[key] = []
        self._http_request_handlers[key].append(handler)
        self.logger.info(f"成功注册HTTP请求处理器: Key='{key}', Handler='{handler.__name__}'")

    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        """
        根据CanonicalMessage做出决策

        发送消息到MaiCore，但不等待响应(异步)。
        实际响应通过_handle_maicore_message回调。

        Args:
            canonical_message: 标准化消息

        Returns:
            构建的MessageBase
        """
        # 构建MessageBase
        message = self._build_messagebase(canonical_message)

        # 发送给MaiCore(异步，不等待响应)
        if self.router and self._is_connected:
            try:
                asyncio.create_task(self.router.send_message(message))
                self.logger.debug(f"决策完成，消息已发送至MaiCore: {message.message_info.message_id}")
            except Exception as e:
                self.logger.error(f"发送消息到MaiCore时发生错误: {e}", exc_info=True)

        return message

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        # 停止WebSocket任务
        if self._ws_task and not self._ws_task.done():
            self.logger.info("正在取消WebSocket run() 任务...")
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        # 停止监控任务
        if self._monitor_task and not self._monitor_task.done():
            self.logger.debug("正在取消WebSocket监控任务...")
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # 停止HTTP服务器
        if self._http_runner:
            self.logger.info("正在停止HTTP服务器...")
            await self._stop_http_server_internal()

        self._is_connected = False
        self._ws_task = None
        self._monitor_task = None
        self.logger.info("MaiCore资源已清理")

    def get_info(self) -> Dict[str, Any]:
        """
        获取MaiCoreDecisionProvider信息

        Returns:
            Provider信息字典
        """
        return {
            "name": "MaiCoreDecisionProvider",
            "version": "1.0.0",
            "description": "MaiCore WebSocket/HTTP决策Provider",
            "author": "MaiBot",
            "api_version": "1.0",
            "config": {
                "host": self.host,
                "port": self.port,
                "http_host": self.http_host,
                "http_port": self.http_port,
                "platform": self.platform,
            },
        }
