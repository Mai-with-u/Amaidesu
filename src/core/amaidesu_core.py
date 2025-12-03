import asyncio
from typing import Callable, Dict, Any, Optional

# 注意：需要安装 aiohttp
# pip install aiohttp
from aiohttp import web

from maim_message import Router, RouteConfig, TargetConfig, MessageBase
from src.utils.logger import get_logger
from .pipeline_manager import PipelineManager
from .context_manager import ContextManager  # 导入ContextManager


class AmaidesuCore:
    """
    Amaidesu 核心模块，负责与 MaiCore 的通信以及消息的分发。
    """

    def __init__(
        self,
        platform: str,
        maicore_host: str,
        maicore_port: int,
        http_host: Optional[str] = None,
        http_port: Optional[int] = None,
        http_callback_path: str = "/callback",
        pipeline_manager: Optional[PipelineManager] = None,
        context_manager: Optional[ContextManager] = None,  # 修改为接收ContextManager实例
    ):
        """
        初始化 Amaidesu Core。

        Args:
            platform: 平台标识符 (例如 "amaidesu_default")。
            maicore_host: MaiCore WebSocket 服务器的主机地址。
            maicore_port: MaiCore WebSocket 服务器的端口。
            http_host: (可选) 监听 HTTP 回调的主机地址。如果为 None，则不启动 HTTP 服务器。
            http_port: (可选) 监听 HTTP 回调的端口。
            http_callback_path: (可选) 接收 HTTP 回调的路径。
            pipeline_manager: (可选) 已配置的管道管理器。如果为None则禁用管道处理。
            context_manager: (可选) 已配置的上下文管理器。如果为None则创建默认实例。
        """
        # 初始化 AmaidesuCore 自己的 logger
        self.logger = get_logger("AmaidesuCore")
        self.logger.debug("AmaidesuCore 初始化开始")

        self.platform = platform
        self.ws_url = f"ws://{maicore_host}:{maicore_port}/ws"
        self._router: Optional[Router] = None
        self._message_handlers: Dict[
            str, list[Callable[[MessageBase], asyncio.Task]]
        ] = {}  # 按消息类型或其他标识符存储处理器
        self._http_request_handlers: Dict[str, list[Callable[[web.Request], asyncio.Task]]] = {}  # 用于 HTTP 请求处理
        self._services: Dict[str, Any] = {}  # 新增：用于存储已注册的服务
        self._is_connected = False
        self._connect_lock = asyncio.Lock()  # 防止并发连接

        # HTTP 服务器相关配置
        self._http_host = http_host
        self._http_port = http_port
        self._http_callback_path = http_callback_path
        self._http_runner: Optional[web.AppRunner] = None
        self._http_site: Optional[web.TCPSite] = None
        self._http_app: Optional[web.Application] = None

        self._ws_task: Optional[asyncio.Task] = None  # 添加用于存储 WebSocket 运行任务的属性
        self._monitor_task: Optional[asyncio.Task] = None  # 添加用于监控 ws_task 的任务

        # 管道管理器
        self._pipeline_manager = pipeline_manager
        if self._pipeline_manager is None:
            self.logger.info("管道处理功能已禁用")
        else:
            self.logger.info("管道处理功能已启用")
            # 为管道管理器设置core引用
            self._pipeline_manager.core = self
            # 为所有已加载的管道设置core引用
            self._pipeline_manager._set_core_for_all_pipelines()

        # 设置上下文管理器
        self._context_manager = context_manager if context_manager is not None else ContextManager({})
        self.register_service("prompt_context", self._context_manager)  # 兼容以前通过服务发现调用上下文管理器的插件
        self.logger.info("上下文管理器已注册为服务")

        self._setup_router()
        if self._http_host and self._http_port:
            self._setup_http_server()
        self.logger.debug("AmaidesuCore 初始化完成")

    def _setup_router(self):
        """配置 maim_message Router。"""
        route_config = RouteConfig(
            route_config={
                self.platform: TargetConfig(
                    url=self.ws_url,
                    token=None,  # 根据需要配置 Token
                )
            }
        )
        self._router = Router(route_config)
        # 注册内部处理函数，用于接收所有来自 MaiCore 的消息
        self._router.register_class_handler(self._handle_maicore_message)
        self.logger.info(f"Router 配置完成，目标 MaiCore: {self.ws_url}")

    def _setup_http_server(self):
        """配置 aiohttp 应用和路由。"""
        if not (self._http_host and self._http_port):
            return
        self._http_app = web.Application()
        # 注册统一的 HTTP 回调处理入口
        self._http_app.router.add_post(self._http_callback_path, self._handle_http_request)
        self.logger.info(f"HTTP 服务器配置完成，监听路径: {self._http_callback_path}")

    async def connect(self):
        """启动 WebSocket 连接后台任务和 HTTP 服务器（如果配置了）。"""
        async with self._connect_lock:
            if self._is_connected or self._ws_task:
                self.logger.warning("核心已连接或正在连接中，无需重复连接。")
                return
            if not self._router:
                self.logger.error("Router 未初始化，无法连接 WebSocket。")
                return

            connect_tasks = []
            http_server_task = None

            # 准备启动 WebSocket 连接任务
            self.logger.info(f"准备启动 MaiCore WebSocket 连接 ({self.ws_url})...")
            # 注意：这里不直接 await，而是创建任务
            self._ws_task = asyncio.create_task(self._run_websocket(), name="WebSocketRunTask")

            # 添加监控任务
            self._monitor_task = asyncio.create_task(self._monitor_ws_connection(), name="WebSocketMonitorTask")

            # 立即乐观地设置状态 (或等待一小段时间让连接有机会建立)
            # self._is_connected = True # 过于乐观，可能连接尚未成功
            # self.logger.info("WebSocket 连接任务已启动 (状态暂标记为连接中/成功)")
            # 更准确的状态应由 _monitor_ws_connection 或 Router 回调设置
            # 这里可以先不设置 _is_connected，等监控任务确认

            # 启动 HTTP 服务器 (如果配置了)
            if self._http_host and self._http_port:
                self.logger.info(f"正在启动 HTTP 服务器 ({self._http_host}:{self._http_port})...")
                http_server_task = asyncio.create_task(self._start_http_server_internal(), name="HttpServerStartTask")
                connect_tasks.append(http_server_task)

            # 等待 HTTP 服务器启动完成 (如果启动了)
            if connect_tasks:
                results = await asyncio.gather(*connect_tasks, return_exceptions=True)
                # 检查 HTTP 启动结果
                for i, task in enumerate(connect_tasks):
                    if task.get_coro().__name__ == "_start_http_server_internal":
                        if isinstance(results[i], Exception):
                            self.logger.error(f"启动 HTTP 服务器失败: {results[i]}", exc_info=results[i])
                        else:
                            self.logger.info(
                                f"HTTP 服务器成功启动于 http://{self._http_host}:{self._http_port}{self._http_callback_path}"
                            )
                        break

            # 注意：现在 connect 方法会很快返回，WebSocket 在后台连接
            self.logger.info("核心连接流程启动完成 (WebSocket 在后台运行)。")
            # 实际连接状态由 _monitor_ws_connection 更新

    async def _run_websocket(self):
        """内部方法：运行 WebSocket router.run()。"""
        if not self._router:
            self.logger.error("Router 未初始化，无法运行 WebSocket。")
            return  # 或者 raise
        try:
            self.logger.info("WebSocket run() 任务开始运行...")
            # 第一次成功连接时，可以在这里或通过 Router 回调设置 _is_connected = True
            # 但为了简化，我们让监控任务处理状态
            await self._router.run()  # 这个会一直运行直到断开
        except asyncio.CancelledError:
            self.logger.info("WebSocket run() 任务被取消。")
        except Exception as e:
            self.logger.error(f"WebSocket run() 任务异常终止: {e}", exc_info=True)
            # 异常退出也意味着断开连接，监控任务会处理状态
        finally:
            self.logger.info("WebSocket run() 任务已结束。")
            # 确保任务结束后状态被标记为 False (虽然监控任务也会做)
            # self._is_connected = False

    async def _monitor_ws_connection(self):
        """内部方法：监控 WebSocket 连接任务的状态。"""
        if not self._ws_task:
            return
        self.logger.info("WebSocket 连接监控任务已启动。")
        try:
            # 初始时认为未连接，等待 run() 任务稳定运行
            # 可以增加一个短暂的延迟，或者依赖 Router 的内部状态/回调（如果可用）
            # 简单起见，我们假设任务启动后一小段时间就算连接成功
            await asyncio.sleep(1)  # 等待 1 秒尝试连接
            if self._ws_task and not self._ws_task.done():
                self.logger.info("WebSocket 连接初步建立，标记核心为已连接。")
                self._is_connected = True

                # 通知所有管道连接已建立
                if self._pipeline_manager is not None:
                    try:
                        await self._pipeline_manager.notify_connect()
                        self.logger.info("已通知所有管道连接已建立")
                    except Exception as e:
                        self.logger.error(f"通知管道连接事件时出错: {e}", exc_info=True)
            else:
                self.logger.warning("WebSocket 任务在监控开始前已结束，连接失败。")
                self._is_connected = False
                return  # 任务启动失败，监控结束

            # 等待任务结束 (表示断开连接)
            await self._ws_task
            self.logger.warning("检测到 WebSocket 连接任务已结束，标记核心为未连接。")

        except asyncio.CancelledError:
            self.logger.info("WebSocket 连接监控任务被取消。")
        except Exception as e:
            self.logger.error(f"WebSocket 连接监控任务异常退出: {e}", exc_info=True)
        finally:
            # 通知所有管道连接已断开（如果之前已连接）
            if self._is_connected and self._pipeline_manager is not None:
                try:
                    await self._pipeline_manager.notify_disconnect()
                    self.logger.info("已通知所有管道连接已断开")
                except Exception as e:
                    self.logger.error(f"通知管道断开连接事件时出错: {e}", exc_info=True)

            self.logger.info("WebSocket 连接监控任务已结束。")
            self._is_connected = False  # 最终确保状态为未连接
            self._ws_task = None  # 清理任务引用
            self._monitor_task = None  # 清理自身引用

    async def _start_http_server_internal(self):
        """内部方法：启动 aiohttp 服务器。"""
        if not self._http_app or not self._http_host or not self._http_port:
            raise ConnectionError("HTTP 服务器未正确配置")
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
            raise ConnectionError(f"无法启动 HTTP 服务器: {e}") from e

    async def disconnect(self):
        """取消 WebSocket 任务并停止 HTTP 服务器。"""
        async with self._connect_lock:
            # 如果已连接，主动通知管道断开连接
            if self._is_connected and self._pipeline_manager is not None:
                try:
                    await self._pipeline_manager.notify_disconnect()
                    self.logger.info("已通知所有管道连接即将断开")
                except Exception as e:
                    self.logger.error(f"通知管道断开连接事件时出错: {e}", exc_info=True)

            tasks = []
            # 停止 WebSocket 任务
            if self._ws_task and not self._ws_task.done():
                self.logger.info("正在取消 WebSocket run() 任务...")
                self._ws_task.cancel()
                tasks.append(self._ws_task)  # 等待任务实际结束
            # 停止监控任务
            if self._monitor_task and not self._monitor_task.done():
                self.logger.debug("正在取消 WebSocket 监控任务...")
                self._monitor_task.cancel()
                tasks.append(self._monitor_task)  # 等待任务实际结束

            # 停止 HTTP 服务器
            if self._http_runner:
                self.logger.info("正在停止 HTTP 服务器...")
                tasks.append(asyncio.create_task(self._stop_http_server_internal()))

            if not tasks:
                self.logger.warning("核心没有活动的任务需要停止。")
                return

            # 等待所有任务结束
            self.logger.debug(f"等待 {len(tasks)} 个任务结束...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            self.logger.debug(f"所有停止任务已完成，结果: {results}")

            # 清理状态
            self._is_connected = False
            self._ws_task = None
            self._monitor_task = None
            # self._http_runner, self._http_site 等在 _stop_http_server_internal 中被清理
            self.logger.info("核心服务已断开并清理。")

    async def _stop_http_server_internal(self):
        """内部方法：停止 aiohttp 服务器。"""
        if self._http_runner:
            await self._http_runner.cleanup()
            self._http_runner = None
            self._http_site = None  # 标记已停止
            self._http_app = None  # 可以考虑是否需要重置 app

    async def send_to_maicore(self, message: MessageBase):
        """
        将消息发送到 MaiCore，在发送前会通过出站管道处理。

        Args:
            message: 要发送的消息对象
        """
        if not self._router:
            self.logger.error("Router 未初始化，无法发送消息。")
            return
        if not self._is_connected:
            self.logger.warning("核心未连接，消息无法发送。")
            return

        # --- 通过管道处理消息 ---
        self.logger.debug(f"准备向 MaiCore 发送消息: {message.message_info.message_id}")
        message_to_send = message
        if self._pipeline_manager:
            try:
                # 使用出站管道处理
                processed_message = await self._pipeline_manager.process_outbound_message(message)
                if processed_message is None:
                    self.logger.info(f"消息 {message.message_info.message_id} 已被出站管道丢弃，将不会发送。")
                    return  # 消息被管道丢弃
                message_to_send = processed_message
            except Exception as e:
                self.logger.error(f"处理出站管道时发生错误: {e}", exc_info=True)
                # 出现错误时，可以选择是发送原始消息还是阻止发送
                # 当前策略: 阻止发送以避免意外行为
                # 设计决策：对于出站消息（例如，即将被TTS朗读的文本），如果处理管道
                # （如内容审查、格式化）失败，发送原始消息可能导致问题（例如，读出
                # 未经处理的标签）。因此，更安全的做法是直接阻止消息发送。
                self.logger.warning("由于出站管道错误，消息将不会被发送。")
                return

        # --- 发送消息 ---
        try:
            # 确保 message_to_send 是有效的 MessageBase 对象
            if isinstance(message_to_send, MessageBase):
                self.logger.debug(f"准备通过 Router 发送消息: {message_to_send.message_info.message_id}")
                await self._router.send_message(message_to_send)
                self.logger.info(f"消息 {message_to_send.message_info.message_id} 已发送至 MaiCore。")
            else:
                self.logger.error(f"管道处理后返回了无效类型 {type(message_to_send)}，期望是 MessageBase。消息未发送。")
        except Exception as e:
            self.logger.error(f"发送消息到 MaiCore 时发生错误: {e}", exc_info=True)

    async def _handle_maicore_message(self, message_data: Dict[str, Any]):
        """
        处理从 MaiCore 接收到的消息，先通过入站管道，再分发给插件。
        """
        try:
            # 从字典构建 MessageBase 对象
            message = MessageBase.from_dict(message_data)
        except Exception as e:
            self.logger.error(f"从 MaiCore 接收到的消息无法解析为 MessageBase 对象: {e}", exc_info=True)
            self.logger.debug(f"原始消息数据: {message_data}")
            return

        # --- 通过入站管道处理消息 ---
        message_to_broadcast = message
        if self._pipeline_manager:
            try:
                processed_message = await self._pipeline_manager.process_inbound_message(message)
                if processed_message is None:
                    self.logger.info(f"消息 {message.message_info.message_id} 已被入站管道丢弃，将不会分发给插件。")
                    return  # 消息被管道丢弃
                message_to_broadcast = processed_message
            except Exception as e:
                self.logger.error(f"处理入站管道时发生错误: {e}", exc_info=True)
                # 错误策略：继续分发原始消息
                # 设计决策：对于入站消息（通常包含来自LLM的指令），如果处理管道
                # （如CommandProcessor）失败，完全丢弃消息可能会丢失重要的非指令性
                # 文本内容。回退到原始消息可以确保其他不依赖此管道的插件（如字幕插件）
                # 仍然能收到完整的原始文本。
                self.logger.warning("由于入站管道错误，将尝试分发原始消息给插件。")
                message_to_broadcast = message  # Fallback to original message

        # --- 分发给插件处理器 ---
        # 确定分发键 (例如, 消息段类型)
        dispatch_key = "*"  # 通配符，默认所有消息都发送给通配符处理器
        specific_key = None
        if message_to_broadcast.message_segment and message_to_broadcast.message_segment.type:
            specific_key = message_to_broadcast.message_segment.type

        # 获取所有相关处理器 (通配符 + 特定类型)
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
            # gather 会自动处理异常，我们可以在这里添加日志记录
            # for i, task in enumerate(results):
            #     if isinstance(task, Exception):
            #         self.logger.error(f"Handler {handlers_to_call[i].__name__} failed: {task}", exc_info=task)

    def register_websocket_handler(self, message_type_or_key: str, handler: Callable[[MessageBase], asyncio.Task]):
        """
        注册一个消息处理器。

        插件或其他模块可以使用此方法来监听特定类型的消息。

        Args:
            message_type_or_key: 标识消息类型的字符串 (例如 "text", "vts_command", "danmu", 或自定义键, "*" 表示所有消息)。
            handler: 一个异步函数，接收 MessageBase 对象作为参数。
        """
        if not asyncio.iscoroutinefunction(handler):
            self.logger.warning(f"注册的 WebSocket 处理器 '{handler.__name__}' 不是一个异步函数 (async def)。")
            # raise TypeError("Handler must be an async function")

        if message_type_or_key not in self._message_handlers:
            self._message_handlers[message_type_or_key] = []
        self._message_handlers[message_type_or_key].append(handler)
        self.logger.info(f"成功注册 WebSocket 消息处理器: Key='{message_type_or_key}', Handler='{handler.__name__}'")

    async def _handle_http_request(self, request: web.Request) -> web.Response:
        """
        内部方法，处理所有到达指定回调路径的 HTTP POST 请求。
        负责初步解析请求并将其分发给已注册的 HTTP 处理器。
        """
        self.logger.info(f"收到来自 {request.remote} 的 HTTP 请求: {request.method} {request.path}")
        # --- HTTP 请求分发逻辑 ---
        # 这里需要更复杂的分发策略，例如根据 request.path, headers, 或请求体内容
        # 简单示例：使用固定 key "http_callback" 分发给所有注册的 HTTP 处理器
        dispatch_key = "http_callback"  # 或者从 request 中提取更具体的 key

        response_tasks = []
        if dispatch_key in self._http_request_handlers:
            handlers = self._http_request_handlers[dispatch_key]
            self.logger.info(f"为 HTTP 请求找到 {len(handlers)} 个 '{dispatch_key}' 处理器")
            # 让每个 handler 处理请求，它们应该返回 web.Response 或引发异常
            for handler in handlers:
                response_tasks.append(asyncio.create_task(handler(request)))
        else:
            self.logger.warning(f"没有找到适用于 HTTP 回调 Key='{dispatch_key}' 的处理器")
            return web.json_response(
                {"status": "error", "message": "No handler configured for this request"}, status=404
            )

        # --- 处理来自 handlers 的响应 ---
        # 策略：
        # 1. 如果只有一个 handler，直接返回它的响应。
        # 2. 如果有多个 handlers，如何合并响应？或者只取第一个成功的？
        #    目前简单起见，假设只有一个主要的 handler 应该返回实际响应，其他的可能是后台任务。
        #    这里我们仅等待所有任务完成，并尝试找到第一个有效响应。
        gathered_responses = await asyncio.gather(*response_tasks, return_exceptions=True)

        final_response: Optional[web.Response] = None
        first_exception: Optional[Exception] = None

        for result in gathered_responses:
            if isinstance(result, web.Response):
                if final_response is None:  # 取第一个有效响应
                    final_response = result
            elif isinstance(result, Exception):
                self.logger.error(f"处理 HTTP 请求时，某个 handler 抛出异常: {result}", exc_info=result)
                if first_exception is None:
                    first_exception = result

        if final_response:
            self.logger.info(f"HTTP 请求处理完成，返回状态: {final_response.status}")
            return final_response
        elif first_exception:
            # 如果有异常但没有成功响应，返回 500
            return web.json_response(
                {"status": "error", "message": f"Error processing request: {first_exception}"}, status=500
            )
        else:
            # 如果没有 handler 返回响应也没有异常 (可能 handler 设计为不返回)，返回一个默认成功响应
            self.logger.info("HTTP 请求处理完成，没有显式响应，返回默认成功状态。")
            return web.json_response({"status": "accepted"}, status=202)  # 202 Accepted 表示已接受处理

    def register_http_handler(self, key: str, handler: Callable[[web.Request], asyncio.Task]):
        """
        注册一个处理 HTTP 回调请求的处理器。

        Args:
            key: 用于匹配请求的键 (当前简单实现只支持固定 key)。
            handler: 一个异步函数，接收 aiohttp.web.Request 对象，并应返回 aiohttp.web.Response 对象。
        """
        if not asyncio.iscoroutinefunction(handler):
            self.logger.warning(f"注册的 HTTP 处理器 '{handler.__name__}' 不是一个异步函数 (async def)。")
            # raise TypeError("Handler must be an async function")

        if key not in self._http_request_handlers:
            self._http_request_handlers[key] = []
        self._http_request_handlers[key].append(handler)
        self.logger.info(f"成功注册 HTTP 请求处理器: Key='{key}', Handler='{handler.__name__}'")

    # --- 服务注册与发现 ---
    def register_service(self, name: str, service_instance: Any):
        """
        注册一个服务实例，供其他插件或模块使用。

        Args:
            name: 服务的唯一名称 (例如 "text_cleanup", "vts_client")。
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

    # --- 插件管理占位符 (可移除) ---
    # ...

    # --- 未来可以添加内部事件分发机制 ---
    # async def dispatch_event(self, event_name: str, **kwargs): ...
    # def subscribe_event(self, event_name: str, handler: Callable): ...

    def get_context_manager(self) -> ContextManager:
        """
        获取上下文管理器实例。
        这是一个便捷方法，等同于 get_service("prompt_context")，但提供了类型提示。

        Returns:
            上下文管理器实例
        """
        return self._context_manager
