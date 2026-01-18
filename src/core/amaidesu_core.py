"""
Amaidesu Core - 核心模块（Phase 3重构后版本）

职责:
- 管理插件系统
- 提供服务注册与发现
- 集成管道管理器
- 集成决策管理器（DecisionManager）
- 集成上下文管理器
- 集成事件总线（EventBus）
- 分发消息给插件和管道

注意:
- WebSocket/HTTP/Router功能已迁移到MaiCoreDecisionProvider
- 此版本从原来的641行简化到约350行
"""

import asyncio
from typing import Callable, Dict, Any, Optional, TYPE_CHECKING

# 注意：需要安装 aiohttp
# pip install aiohttp
from aiohttp import web

from maim_message import MessageBase
from src.utils.logger import get_logger
from .pipeline_manager import PipelineManager
from .context_manager import ContextManager
from .event_bus import EventBus
from .decision_manager import DecisionManager, DecisionProviderFactory

# 类型检查时的导入
if TYPE_CHECKING:
    from .avatar.avatar_manager import AvatarControlManager
    from .llm_client_manager import LLMClientManager
    from .canonical.canonical_message import CanonicalMessage


class AmaidesuCore:
    """
    Amaidesu 核心模块，负责插件管理和消息分发。

    重构变化（Phase 3）:
    - WebSocket/HTTP/Router功能迁移到MaiCoreDecisionProvider
    - 新增DecisionManager支持
    - 简化为约350行代码
    """

    @property
    def event_bus(self) -> Optional[EventBus]:
        """获取事件总线实例（供插件使用）"""
        return self._event_bus

    @property
    def avatar(self) -> Optional["AvatarControlManager"]:
        """获取虚拟形象控制管理器实例（供插件使用）"""
        return self._avatar

    @property
    def llm_client_manager(self) -> Optional["LLMClientManager"]:
        """获取 LLM 客户端管理器实例（供插件使用）"""
        return self._llm_client_manager

    def __init__(
        self,
        platform: str,
        maicore_host: str,
        maicore_port: int,
        http_host: Optional[str] = None,
        http_port: Optional[int] = None,
        http_callback_path: str = "/callback",
        pipeline_manager: Optional[PipelineManager] = None,
        context_manager: Optional[ContextManager] = None,
        event_bus: Optional[EventBus] = None,
        avatar: Optional["AvatarControlManager"] = None,
        llm_client_manager: Optional["LLMClientManager"] = None,
        decision_manager: Optional[DecisionManager] = None,
    ):
        """
        初始化 Amaidesu Core（重构版本）。

        Args:
            platform: 平台标识符 (例如 "amaidesu_default")。
            maicore_host: MaiCore WebSocket 服务器的主机地址。
            maicore_port: MaiCore WebSocket 服务器的端口。
            http_host: (可选) 监听 HTTP 回调的主机地址。
            http_port: (可选) 监听 HTTP 回调的端口。
            http_callback_path: (可选) 接收 HTTP 回调的路径。
            pipeline_manager: (可选) 已配置的管道管理器。
            context_manager: (可选) 已配置的上下文管理器。
            event_bus: (可选) 已配置的事件总线。
            avatar: (可选) 已配置的虚拟形象控制管理器。
            llm_client_manager: (可选) 已配置的 LLM 客户端管理器。
            decision_manager: (可选) 已配置的决策管理器（Phase 3新增）。
        """
        # 初始化 Logger
        self.logger = get_logger("AmaidesuCore")
        self.logger.debug("AmaidesuCore 初始化开始")

        self.platform = platform

        # 消息处理器（插件注册）
        self._message_handlers: Dict[str, list[Callable[[MessageBase], asyncio.Task]]] = {}
        # HTTP 请求处理器（插件注册）
        self._http_request_handlers: Dict[str, list[Callable[[web.Request], asyncio.Task]]] = {}
        # 服务注册表
        self._services: Dict[str, Any] = {}

        # HTTP 服务器相关配置（用于插件注册的HTTP回调）
        self._http_host = http_host
        self._http_port = http_port
        self._http_callback_path = http_callback_path
        self._http_app: Optional[web.Application] = None
        self._http_runner: Optional[web.AppRunner] = None
        self._http_site: Optional[web.TCPSite] = None

        # 管道管理器
        self._pipeline_manager = pipeline_manager
        if self._pipeline_manager is None:
            self.logger.info("管道处理功能已禁用")
        else:
            self.logger.info("管道处理功能已启用")
            self._pipeline_manager.core = self
            self._pipeline_manager._set_core_for_all_pipelines()

        # 设置上下文管理器
        self._context_manager = context_manager if context_manager is not None else ContextManager({})
        self.register_service("prompt_context", self._context_manager)
        self.logger.info("上下文管理器已注册为服务")

        # 设置事件总线（可选功能）
        self._event_bus = event_bus
        if event_bus is None:
            self._event_bus = EventBus()
            self.logger.info("创建了默认EventBus实例")
        else:
            self.logger.info("已使用外部提供的事件总线")

        # 设置虚拟形象控制管理器（可选功能）
        self._avatar = avatar
        if avatar is not None:
            avatar.core = self
            self.logger.info("已使用外部提供的虚拟形象控制管理器")

        # 设置 LLM 客户端管理器（可选功能）
        self._llm_client_manager = llm_client_manager
        if llm_client_manager is not None:
            self.logger.info("已使用外部提供的 LLM 客户端管理器")
        else:
            self.logger.warning("未提供 LLM 客户端管理器，LLM 相关功能将不可用")

        # 设置决策管理器（Phase 3新增）
        self._decision_manager = decision_manager
        if decision_manager is not None:
            self.logger.info("已使用外部提供的决策管理器")

        # HTTP 服务器（用于插件HTTP回调）
        if self._http_host and self._http_port:
            self._setup_http_server()

        self.logger.debug("AmaidesuCore 初始化完成")

    def _setup_http_server(self):
        """配置 aiohttp 应用和路由（用于插件HTTP回调）"""
        if not (self._http_host and self._http_port):
            return
        self._http_app = web.Application()
        self._http_app.router.add_post(self._http_callback_path, self._handle_http_request)
        self.logger.info(f"HTTP 服务器配置完成，监听路径: {self._http_callback_path}")

    async def connect(self):
        """启动核心服务（HTTP服务器等）"""
        if self._http_host and self._http_port:
            self.logger.info(f"正在启动 HTTP 服务器 ({self._http_host}:{self._http_port})...")
            try:
                self._http_runner = web.AppRunner(self._http_app)
                await self._http_runner.setup()
                self._http_site = web.TCPSite(self._http_runner, self._http_host, self._http_port)
                await self._http_site.start()
                self.logger.info(
                    f"HTTP 服务器成功启动于 http://{self._http_host}:{self._http_port}{self._http_callback_path}"
                )
            except Exception as e:
                self.logger.error(f"启动 HTTP 服务器失败: {e}", exc_info=True)
                raise ConnectionError(f"无法启动 HTTP 服务器: {e}") from e

        # 如果有决策管理器，启动DecisionProvider
        if self._decision_manager:
            provider = self._decision_manager.get_current_provider()
            if hasattr(provider, "connect"):
                try:
                    await provider.connect()
                    self.logger.info("DecisionProvider 连接已启动")
                except Exception as e:
                    self.logger.error(f"DecisionProvider 连接失败: {e}", exc_info=True)

    async def disconnect(self):
        """停止核心服务"""
        # 停止 HTTP 服务器
        if self._http_runner:
            self.logger.info("正在停止 HTTP 服务器...")
            await self._http_runner.cleanup()
            self._http_runner = None
            self._http_site = None
            self._http_app = None

        # 如果有决策管理器，断开DecisionProvider
        if self._decision_manager:
            provider = self._decision_manager.get_current_provider()
            if hasattr(provider, "disconnect"):
                try:
                    await provider.disconnect()
                    self.logger.info("DecisionProvider 连接已断开")
                except Exception as e:
                    self.logger.error(f"DecisionProvider 断开失败: {e}", exc_info=True)

        self.logger.info("核心服务已停止")

    async def send_to_maicore(self, message: MessageBase):
        """
        将消息发送到 MaiCore，通过DecisionManager或Router（向后兼容）。

        Args:
            message: 要发送的消息对象
        """
        # 优先使用DecisionManager
        if self._decision_manager:
            try:
                # 转换MessageBase为CanonicalMessage
                from src.canonical.canonical_message import MessageBuilder

                canonical_message = MessageBuilder.build_from_message_base(message)

                # 通过DecisionManager发送
                result = await self._decision_manager.decide(canonical_message)
                self.logger.info(f"消息通过DecisionManager发送: {result.message_info.message_id}")
                return
            except Exception as e:
                self.logger.error(f"通过DecisionManager发送消息失败: {e}", exc_info=True)
                # 降级：尝试通过其他方式发送

        # 向后兼容：如果有Router，直接使用
        if hasattr(self, "_router") and self._router:
            try:
                await self._router.send_message(message)
                self.logger.info(f"消息通过Router发送: {message.message_info.message_id}")
                return
            except Exception as e:
                self.logger.error(f"通过Router发送消息失败: {e}", exc_info=True)

        self.logger.warning("没有可用的发送方式（DecisionManager或Router），消息未发送")

    async def broadcast_message(self, message: MessageBase):
        """
        广播消息给插件处理器

        Args:
            message: 要广播的消息对象
        """
        # 通过管道处理消息
        message_to_broadcast = message
        if self._pipeline_manager:
            try:
                processed_message = await self._pipeline_manager.process_inbound_message(message)
                if processed_message is None:
                    self.logger.info(f"消息 {message.message_info.message_id} 已被入站管道丢弃")
                    return
                message_to_broadcast = processed_message
            except Exception as e:
                self.logger.error(f"处理入站管道时发生错误: {e}", exc_info=True)
                message_to_broadcast = message

        # 触发 Avatar 自动表情
        if self._avatar:
            try:
                text = None
                if message_to_broadcast.message_segment and hasattr(message_to_broadcast.message_segment, "data"):
                    data = message_to_broadcast.message_segment.data
                    if isinstance(data, str):
                        text = data

                if text:
                    await self._avatar.try_auto_expression(text)
            except Exception as e:
                self.logger.error(f"触发 avatar 自动表情时出错: {e}", exc_info=True)

        # 分发给插件处理器
        dispatch_key = "*"
        specific_key = None
        if message_to_broadcast.message_segment and message_to_broadcast.message_segment.type:
            specific_key = message_to_broadcast.message_segment.type

        handlers_to_call = self._message_handlers.get(dispatch_key, [])
        if specific_key and specific_key != dispatch_key:
            handlers_to_call = handlers_to_call + self._message_handlers.get(specific_key, [])

        if not handlers_to_call:
            self.logger.debug(f"没有找到消息类型为 '{specific_key or 'N/A'}' 的处理器。")
            return

        self.logger.info(
            f"将消息 {message_to_broadcast.message_info.message_id} 分发给 {len(handlers_to_call)} 个处理器..."
        )

        # 并发执行所有处理器
        tasks = [handler(message_to_broadcast) for handler in handlers_to_call]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def register_websocket_handler(self, message_type_or_key: str, handler: Callable[[MessageBase], asyncio.Task]):
        """
        注册一个消息处理器。

        Args:
            message_type_or_key: 标识消息类型的字符串 (例如 "text", "vts_command", "danmu", 或 "*")。
            handler: 一个异步函数，接收 MessageBase 对象作为参数。
        """
        if not asyncio.iscoroutinefunction(handler):
            self.logger.warning(f"注册的 WebSocket 处理器 '{handler.__name__}' 不是一个异步函数 (async def)。")

        if message_type_or_key not in self._message_handlers:
            self._message_handlers[message_type_or_key] = []
        self._message_handlers[message_type_or_key].append(handler)
        self.logger.info(f"成功注册 WebSocket 消息处理器: Key='{message_type_or_key}', Handler='{handler.__name__}'")

    async def _handle_http_request(self, request: web.Request) -> web.Response:
        """
        处理HTTP请求（用于插件回调）

        Args:
            request: aiohttp Request对象

        Returns:
            aiohttp Response对象
        """
        self.logger.info(f"收到来自 {request.remote} 的 HTTP 请求: {request.method} {request.path}")

        dispatch_key = "http_callback"

        response_tasks = []
        if dispatch_key in self._http_request_handlers:
            handlers = self._http_request_handlers[dispatch_key]
            self.logger.info(f"为 HTTP 请求找到 {len(handlers)} 个 '{dispatch_key}' 处理器")
            for handler in handlers:
                response_tasks.append(asyncio.create_task(handler(request)))
        else:
            self.logger.warning(f"没有找到适用于 HTTP 回调 Key='{dispatch_key}' 的处理器")
            return web.json_response(
                {"status": "error", "message": "No handler configured for this request"}, status=404
            )

        gathered_responses = await asyncio.gather(*response_tasks, return_exceptions=True)

        final_response: Optional[web.Response] = None
        first_exception: Optional[Exception] = None

        for result in gathered_responses:
            if isinstance(result, web.Response):
                if final_response is None:
                    final_response = result
            elif isinstance(result, Exception):
                self.logger.error(f"处理 HTTP 请求时，某个 handler 抛出异常: {result}", exc_info=result)
                if first_exception is None:
                    first_exception = result

        if final_response:
            self.logger.info(f"HTTP 请求处理完成，返回状态: {final_response.status}")
            return final_response
        elif first_exception:
            return web.json_response(
                {"status": "error", "message": f"Error processing request: {first_exception}"}, status=500
            )
        else:
            self.logger.info("HTTP 请求处理完成，没有显式响应，返回默认成功状态。")
            return web.json_response({"status": "accepted"}, status=202)

    def register_http_handler(self, key: str, handler: Callable[[web.Request], asyncio.Task]):
        """
        注册一个处理 HTTP 回调请求的处理器。

        Args:
            key: 用于匹配请求的键
            handler: 一个异步函数，接收 aiohttp.web.Request 对象，并应返回 aiohttp.web.Response 对象。
        """
        if not asyncio.iscoroutinefunction(handler):
            self.logger.warning(f"注册的 HTTP 处理器 '{handler.__name__}' 不是一个异步函数 (async def)。")

        if key not in self._http_request_handlers:
            self._http_request_handlers[key] = []
        self._http_request_handlers[key].append(handler)
        self.logger.info(f"成功注册 HTTP 请求处理器: Key='{key}', Handler='{handler.__name__}'")

    # ==================== 服务注册与发现 ====================

    def register_service(self, name: str, service_instance: Any):
        """
        注册一个服务实例，供其他插件或模块使用。

        Args:
            name: 服务的唯一名称 (例如 "text_cleanup", "vts_control")。
            service_instance: 提供服务的对象实例。
        """
        if name in self._services:
            self.logger.warning(f"服务名称 '{name}' 已被注册，将被覆盖！")
        self._services[name] = service_instance
        self.logger.info(f"服务已注册: '{name}' (类型: {type(service_instance).__name__})")

    def get_service(self, name: str) -> Optional[Any]:
        """
        根据名称获取已注册的服务实例。

        Args:
            name: 要获取的服务名称。

        Returns:
            服务实例，如果找到的话；否则返回 None。
        """
        service = self._services.get(name)
        if service:
            self.logger.debug(f"获取服务 '{name}' 成功。")
        else:
            self.logger.warning(f"尝试获取未注册的服务: '{name}'")
        return service

    def get_context_manager(self) -> ContextManager:
        """
        获取上下文管理器实例。

        Returns:
            上下文管理器实例
        """
        return self._context_manager

    # ==================== LLM 客户端管理 ====================

    def get_llm_client(self, config_type: str = "llm"):
        """
        获取 LLM 客户端实例（委托给 LLMClientManager）

        Args:
            config_type: 配置类型，可选值：
                - "llm": 标准 LLM 配置（默认）
                - "llm_fast": 快速 LLM 配置（低延迟场景）
                - "vlm": 视觉语言模型配置

        Returns:
            LLMClient 实例

        Raises:
            ValueError: 如果 LLMClientManager 未提供或配置无效
        """
        from src.openai_client.llm_request import LLMClient

        if self._llm_client_manager is None:
            raise ValueError("LLM 客户端管理器未初始化！请在 main.py 中创建 LLMClientManager 并传入 AmaidesuCore。")

        return self._llm_client_manager.get_client(config_type)

    # ==================== 决策管理器（Phase 3新增） ====================

    @property
    def decision_manager(self) -> Optional[DecisionManager]:
        """获取决策管理器实例"""
        return self._decision_manager

    def set_decision_manager(self, decision_manager: DecisionManager):
        """
        设置决策管理器

        Args:
            decision_manager: DecisionManager实例
        """
        self._decision_manager = decision_manager
        self.logger.info("决策管理器已设置")
