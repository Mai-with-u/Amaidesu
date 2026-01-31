"""
MaiCore决策Provider实现

职责:
- 管理WebSocket连接到MaiCore
- 通过AmaidesuCore的HttpServer注册HTTP回调路由
- 管理maim_message Router
- 将CanonicalMessage转换为MessageBase并发送
- 接收MaiCore响应并转换为MessageBase

设计变更（Phase 5）:
- 移除内部aiohttp HTTP服务器
- 改用AmaidesuCore提供的HttpServer注册路由
- 通过订阅core.ready事件获取AmaidesuCore实例
"""

import asyncio
from typing import Dict, Any, Optional, TYPE_CHECKING

from maim_message import Router, RouteConfig, TargetConfig, MessageBase
from maim_message.message_base import BaseMessageInfo, UserInfo, Seg, FormatInfo

from src.core.providers.decision_provider import DecisionProvider
from src.utils.logger import get_logger
from src.canonical.canonical_message import CanonicalMessage

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.core.amaidesu_core import AmaidesuCore


class MaiCoreDecisionProvider(DecisionProvider):
    """
    MaiCore决策Provider(默认实现)

    职责:
    - 使用WebSocket连接到MaiCore
    - 通过AmaidesuCore的HttpServer注册HTTP回调路由
    - 使用maim_message Router进行通信
    - 将CanonicalMessage转换为MessageBase并发送
    - 接收MaiCore响应并转换为MessageBase

    配置:
        - host: MaiCore WebSocket主机地址
        - port: MaiCore WebSocket端口
        - http_callback_path: HTTP回调路径（注册到HttpServer）
        - platform: 平台标识符(默认"amaidesu_default")

    注意（Phase 5变更）:
        - 不再需要http_host和http_port配置（由AmaidesuCore的HttpServer管理）
        - HTTP路由通过core.register_http_callback()注册
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 8000)
        self.http_callback_path = config.get("http_callback_path", "/maicore/callback")
        self.platform = config.get("platform", "amaidesu_default")

        # Router和WebSocket相关
        self.router: Optional[Router] = None
        self.ws_url = f"ws://{self.host}:{self.port}/ws"

        # AmaidesuCore引用（通过core.ready事件获取）
        self._core: Optional["AmaidesuCore"] = None
        self._http_route_registered = False

        # WebSocket任务相关
        self._ws_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._is_connected = False

        # HTTP请求处理器（供其他组件注册回调）
        self._http_request_handlers: Dict[str, list] = {}

        self.logger = get_logger("MaiCoreDecisionProvider")

    async def setup(self, event_bus: "EventBus", config: dict) -> None:
        """
        初始化MaiCore DecisionProvider

        配置WebSocket和Router。
        HTTP路由通过订阅core.ready事件后注册到AmaidesuCore的HttpServer。
        """
        await super().setup(event_bus, config)

        # 设置Router
        self._setup_router()

        # 订阅core.ready事件，以便获取AmaidesuCore实例并注册HTTP路由
        self.event_bus.on("core.ready", self._on_core_ready, priority=10)

        # 订阅canonical.message_ready事件
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

    async def _on_core_ready(self, event_name: str, event_data: dict, source: str) -> None:
        """
        处理core.ready事件，获取AmaidesuCore实例并注册HTTP路由

        Args:
            event_name: 事件名称
            event_data: 事件数据（包含core实例）
            source: 事件源
        """
        core = event_data.get("core")
        if not core:
            self.logger.warning("core.ready事件中缺少core实例")
            return

        self._core = core
        self.logger.info("已获取AmaidesuCore实例")

        # 注册HTTP回调路由
        if not self._http_route_registered:
            success = core.register_http_callback(
                path=self.http_callback_path,
                handler=self._handle_http_callback,
                methods=["POST"],
                tags=["MaiCore"],
                name="maicore_callback",
            )
            if success:
                self._http_route_registered = True
                self.logger.info(f"HTTP回调路由已注册: POST {self.http_callback_path}")
            else:
                self.logger.warning(f"HTTP回调路由注册失败: {self.http_callback_path}")

    async def _setup_internal(self):
        """
        内部设置逻辑(子类可选重写)

        启动WebSocket连接。
        注意: HTTP路由通过core.ready事件注册，不在此处处理。
        """
        # 启动WebSocket连接任务
        self.logger.info(f"准备启动MaiCore WebSocket连接 ({self.ws_url})...")
        self._ws_task = asyncio.create_task(self._run_websocket(), name="WebSocketRunTask")

        # 添加监控任务
        self._monitor_task = asyncio.create_task(self._monitor_ws_connection(), name="WebSocketMonitorTask")

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

    async def _handle_http_callback(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理MaiCore的HTTP回调请求（FastAPI兼容）

        Args:
            body: 请求体（已由FastAPI解析为JSON）

        Returns:
            响应字典（将被FastAPI转换为JSONResponse）
        """
        self.logger.info("收到MaiCore HTTP回调请求")
        self.logger.debug(f"回调数据: {body}")

        # 简化实现：使用固定key "http_callback" 分发给所有注册的HTTP处理器
        dispatch_key = "http_callback"

        if dispatch_key not in self._http_request_handlers:
            self.logger.info("没有注册的HTTP回调处理器，返回接受状态")
            return {"status": "accepted", "message": "No handlers registered"}

        handlers = self._http_request_handlers[dispatch_key]
        self.logger.info(f"为HTTP回调找到 {len(handlers)} 个处理器")

        # 调用所有注册的处理器
        response_tasks = []
        for handler in handlers:
            response_tasks.append(asyncio.create_task(handler(body)))

        # 处理来自handlers的响应
        final_response: Optional[Dict[str, Any]] = None
        first_exception: Optional[Exception] = None

        gathered_responses = await asyncio.gather(*response_tasks, return_exceptions=True)

        for result in gathered_responses:
            if isinstance(result, dict):
                if final_response is None:
                    final_response = result
            elif isinstance(result, Exception):
                self.logger.error(
                    f"处理HTTP回调时，某个handler抛出异常: {result}",
                    exc_info=result,
                )
                if first_exception is None:
                    first_exception = result

        if final_response:
            self.logger.info("HTTP回调处理完成")
            return final_response
        elif first_exception:
            return {"status": "error", "message": f"Error processing callback: {first_exception}"}
        else:
            self.logger.info("HTTP回调处理完成，没有显式响应，返回默认成功状态")
            return {"status": "accepted"}

    def register_http_handler(self, key: str, handler) -> None:
        """
        注册一个处理HTTP回调请求的处理器

        Args:
            key: 用于匹配请求的键（默认使用 "http_callback"）
            handler: 一个异步函数，接收请求体dict，返回响应dict

        注意（Phase 5变更）:
            - 处理器签名已从 aiohttp.web.Request 改为 dict（请求体）
            - 返回值已从 aiohttp.web.Response 改为 dict（响应体）
        """
        if key not in self._http_request_handlers:
            self._http_request_handlers[key] = []
        self._http_request_handlers[key].append(handler)
        self.logger.info(f"成功注册HTTP回调处理器: Key='{key}', Handler='{handler.__name__}'")

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
        """
        内部清理逻辑

        停止WebSocket连接和监控任务。
        注意: HTTP路由由AmaidesuCore的HttpServer管理，不在此处清理。
        """
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

        self._is_connected = False
        self._ws_task = None
        self._monitor_task = None
        self._core = None
        self._http_route_registered = False
        self.logger.info("MaiCore资源已清理")

    def get_info(self) -> Dict[str, Any]:
        """
        获取MaiCoreDecisionProvider信息

        Returns:
            Provider信息字典
        """
        return {
            "name": "MaiCoreDecisionProvider",
            "version": "2.0.0",  # Phase 5: 迁移到HttpServer
            "description": "MaiCore WebSocket/HTTP决策Provider（使用AmaidesuCore的HttpServer）",
            "author": "MaiBot",
            "api_version": "1.0",
            "config": {
                "host": self.host,
                "port": self.port,
                "http_callback_path": self.http_callback_path,
                "platform": self.platform,
            },
            "status": {
                "connected": self._is_connected,
                "http_route_registered": self._http_route_registered,
            },
        }
