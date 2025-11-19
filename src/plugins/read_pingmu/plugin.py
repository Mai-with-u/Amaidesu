import asyncio

# import logging # 移除标准logging导入
import sys
import time
import base64
from io import BytesIO
from typing import Any, Dict, Optional
from dataclasses import dataclass

# 导入全局logger - 在确认不再需要后移除
# from src.utils.logger import logger # 将在确认后移除

# --- 依赖检查 ---
try:
    import mss
    import mss.tools
except ImportError:
    mss = None

try:
    # 导入 openai 库
    import openai
    from openai import AsyncOpenAI  # 明确导入 AsyncOpenAI
except ImportError:
    openai = None
    AsyncOpenAI = None  # type: ignore

try:
    from PIL import Image
except ImportError:
    Image = None

# --- 远程流支持 ---
try:
    from src.plugins.remote_stream.plugin import RemoteStreamService

    REMOTE_STREAM_AVAILABLE = True
except ImportError:
    REMOTE_STREAM_AVAILABLE = False
    print("提示: 未找到 remote_stream 插件，将使用本地屏幕捕获。", file=sys.stderr)

try:
    import obsws_python as obswspy
    from obsws_python import reqs as obsreq
except ImportError:
    obswspy = None

from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore
from maim_message import MessageBase, UserInfo, BaseMessageInfo, GroupInfo, FormatInfo, Seg, TemplateInfo
from src.utils.logger import get_logger


@dataclass
class ScreenMessage:
    """屏幕描述消息类"""

    description: str
    timestamp: int
    raw_data: Dict[str, Any]

    def __post_init__(self):
        """初始化后设置logger"""
        self.logger = get_logger(self.__class__.__name__)

    def _create_user_info(self, core, config: Dict[str, Any]) -> UserInfo:
        """创建用户信息对象"""
        return UserInfo(
            platform=core.platform,
            user_id=config.get("user_id", "screen_monitor"),
            user_nickname=config.get("user_nickname", "屏幕监控"),
            user_cardname=config.get("user_cardname", "Screen Monitor"),
        )

    def _generate_message_id(self) -> str:
        """生成消息ID"""
        return f"screen_{self.timestamp}_{hash(self.description) % 10000}"

    async def _create_base_message_info(
        self,
        core,
        config: Dict[str, Any],
        context_tags: Optional[list] = None,
        template_items: Optional[Dict[str, Any]] = None,
    ) -> BaseMessageInfo:
        """创建基础消息信息对象"""

        user_info = self._create_user_info(core, config)
        message_id = self._generate_message_id()
        monitor_id = "screen_monitor"  # 相当于bili的room_id

        # 群组信息（可选）
        group_info = None
        if config.get("enable_group_info", False):
            group_info = GroupInfo(
                platform=core.platform,
                group_id=config.get("group_id", str(monitor_id)),
                group_name=config.get("group_name", f"screen_{monitor_id}"),
            )

        # 格式信息
        format_info = FormatInfo(
            content_format=config.get("content_format", config.get("accept_format", ["text"])),
            accept_format=config.get("accept_format", config.get("content_format", ["text"])),
        )

        # 附加配置
        additional_config = config.get("additional_config", {}).copy()

        # 模板信息（可选）
        template_info = await self._create_template_info(core, config, context_tags, template_items, monitor_id)

        return BaseMessageInfo(
            platform=core.platform,
            message_id=message_id,
            time=self.timestamp,
            user_info=user_info,
            group_info=group_info,
            template_info=template_info,
            format_info=format_info,
            additional_config=additional_config,
        )

    async def _create_template_info(
        self,
        core,
        config: Dict[str, Any],
        context_tags: Optional[list] = None,
        template_items: Optional[Dict[str, Any]] = None,
        monitor_id: str = "",
    ) -> Optional[TemplateInfo]:
        """创建模板信息对象"""
        if not config.get("enable_template_info", False) or not template_items:
            return None

        # 获取并追加 Prompt 上下文
        modified_template_items = template_items.copy()

        if prompt_ctx_service := core.get_service("prompt_context"):
            try:
                additional_context = await prompt_ctx_service.get_formatted_context(tags=context_tags)
                if additional_context:
                    # 修改主 Prompt
                    main_prompt_key = "reasoning_prompt_main"
                    if main_prompt_key in modified_template_items:
                        original_prompt = modified_template_items[main_prompt_key]
                        modified_template_items[main_prompt_key] = original_prompt + "\n" + additional_context
            except Exception as e:
                self.logger.error(f"创建模板信息时发生错误: {e}", exc_info=True)

        return TemplateInfo(
            template_items=modified_template_items,
            template_name=config.get("template_name", f"screen_monitor_{monitor_id}"),
            template_default=False,
        )

    async def to_message_base(
        self,
        core,
        config: Dict[str, Any],
        context_tags: Optional[list] = None,
        template_items: Optional[Dict[str, Any]] = None,
    ) -> MessageBase:
        """构建MessageBase对象"""

        # 创建基础消息信息
        message_info = await self._create_base_message_info(core, config, context_tags, template_items)

        # 添加屏幕监控特有的附加配置
        message_info.additional_config["source"] = "screen_monitor"
        message_info.additional_config["image_data"] = self.raw_data.get("image_base64", "")
        message_info.additional_config["model_name"] = self.raw_data.get("model_name", "")
        message_info.additional_config["vl_prompt"] = self.raw_data.get("vl_prompt", "")

        # 构建消息段
        text = f"[屏幕描述更新] {self.description}"
        message_segment = Seg(type="text", data=text)

        return MessageBase(message_info=message_info, message_segment=message_segment, raw_message=text)


# --- Helper Function ---
# 移除旧的配置加载函数
# def load_plugin_config() -> Dict[str, Any]:
#     config_path = os.path.join(os.path.dirname(__file__), "config.toml")
#     try:
#         with open(config_path, "rb") as f:
#             if hasattr(tomllib, "load"):
#                 return tomllib.load(f)
#             else:
#                 try:
#                     import toml
#
#                     with open(config_path, "r", encoding="utf-8") as rf:
#                         return toml.load(rf)
#                 except ImportError:
#                     logger.error("toml package needed for Python < 3.11.") # 此处的 logger 也需处理
#                     return {}
#                 except FileNotFoundError:
#                     logger.warning(f"Config file not found: {config_path}") # 此处的 logger 也需处理
#                     return {}
#     except Exception as e:
#         logger.error(f"Error loading config: {config_path}: {e}", exc_info=True) # 此处的 logger 也需处理
#         return {}


# --- Plugin Class ---
class ScreenMonitorPlugin(BasePlugin):
    """
    定期截屏，通过 OpenAI 兼容接口调用 VL 模型获取描述，
    并将最新描述注册为 Prompt 上下文。
    !!! 警告：存在隐私风险和 API 成本 !!!
    """

    _is_amaidesucore_plugin: bool = True

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)

        # 初始化所有必要属性，防止属性不存在的错误
        self.openai_client = None
        self._monitor_task = None
        self.is_running = False
        self.latest_description = "屏幕信息尚未获取。"
        self.description_lock = asyncio.Lock()
        self.initialization_successful = False  # 新增：跟踪初始化是否成功
        self.obs_client = None  # 初始化OBS客户端为None

        # --- 远程流支持 ---
        self.remote_stream_service = None
        self.use_remote_stream = self.plugin_config.get("use_remote_stream", False) and REMOTE_STREAM_AVAILABLE
        self.latest_remote_image = None
        self.remote_image_lock = asyncio.Lock()
        self.remote_image_received = False

        self.config = self.plugin_config  # 直接使用注入的 plugin_config

        # --- 检查核心依赖 ---
        # 截图来源类型
        self.capture_source = self.config.get("capture_source", "screen")  # 默认使用屏幕截图

        # 根据截图来源检查依赖
        if self.capture_source == "screen":
            if mss is None or openai is None or Image is None:
                missing = [lib for lib, name in [(mss, "mss"), (openai, "openai"), (Image, "Pillow")] if lib is None]
                self.logger.error(
                    f"缺少必要的库: {', '.join(missing)}。请运行 `pip install mss openai Pillow`。ScreenMonitorPlugin 已禁用。"
                )
                return
        elif self.capture_source == "obs":
            if obswspy is None or openai is None or Image is None:
                missing = []
                if obswspy is None:
                    missing.append("obsws-python")
                if openai is None:
                    missing.append("openai")
                if Image is None:
                    missing.append("Pillow")
                self.logger.error(
                    f"缺少必要的库: {', '.join(missing)}。请运行 `pip install obsws-python openai Pillow`。ScreenMonitorPlugin 已禁用。"
                )
                return

            # 初始化OBS配置
            self.obs_config = self.config.get("obs_config", {})
            self.obs_host = self.obs_config.get("host", "localhost")
            self.obs_port = self.obs_config.get("port", 4455)
            self.obs_password = self.obs_config.get("password", "")
            self.obs_source_name = self.obs_config.get("source_name", "游戏捕获")
            self.obs_auto_switch_scene = self.obs_config.get("auto_switch_scene", False)
            self.obs_scene_name = self.obs_config.get("scene_name", "游戏")
        else:
            self.logger.error(
                f"不支持的截图来源类型: {self.capture_source}。支持的类型: 'screen', 'obs'。ScreenMonitorPlugin 已禁用。"
            )
            return

        # --- 加载配置 (使用新配置项) ---
        self.interval = self.config.get("screenshot_interval_seconds", 10)
        self.api_key = self.config.get("api_key", None)  # 通用 API Key
        self.base_url = self.config.get("openai_compatible_base_url", None)  # OpenAI 兼容 URL
        self.model_name = self.config.get("model_name", "qwen-vl-plus")  # 模型名称
        self.vl_prompt = self.config.get("vl_prompt", "请用一句话简洁描述这张图片的主要内容和活动窗口标题。")
        self.timeout_seconds = self.config.get("request_timeout", 20)  # 请求超时
        self.context_provider_name = self.config.get("context_provider_name", "screen_content_latest")
        self.context_priority = self.config.get("context_priority", 20)

        # --- 消息发送相关配置 ---
        self.send_messages = self.config.get("send_messages", True)  # 是否发送消息到MaiCore
        self.message_config = self.config.get("message_config", {})  # 消息配置
        self.context_tags = self.config.get("context_tags", [])  # 上下文标签
        self.template_items = self.config.get("template_items", {})  # 模板项

        # --- 检查关键配置 ---
        if not self.api_key or "YOUR_API_KEY_HERE" in self.api_key:
            self.logger.error("API Key 未在 config.toml 中配置！ScreenMonitorPlugin 已禁用。")
            return
        if not self.base_url:
            self.logger.error(
                "OpenAI 兼容 Base URL (openai_compatible_base_url) 未在 config.toml 中配置！ScreenMonitorPlugin 已禁用。"
            )
            return

        # --- 初始化 OpenAI 客户端 ---
        try:
            self.openai_client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                # 可以根据需要添加 max_retries 等参数
            )
            self.logger.info(f"AsyncOpenAI 客户端已为模型 '{self.model_name}' 初始化 (Base URL: {self.base_url})。")
            # 初始化成功的第一步
            self.initialization_successful = True
        except Exception as e:
            self.logger.error(f"初始化 AsyncOpenAI 客户端失败: {e}", exc_info=True)
            return

        # --- 初始化 OBS WebSocket 客户端 (如果使用OBS) ---
        if self.capture_source == "obs":
            try:
                # 创建OBS客户端
                self.obs_client = obswspy.ReqClient(
                    host=self.obs_host,
                    port=self.obs_port,
                    password=self.obs_password if self.obs_password else None,
                    timeout=self.timeout_seconds,
                )

                # 测试连接
                version = self.obs_client.get_version()
                self.logger.info(
                    f"已连接到 OBS Studio (版本: {version.obs_version}) 通过 WebSocket 协议 {version.obs_web_socket_version}"
                )
            except Exception as e:
                self.logger.error(f"无法连接到 OBS WebSocket: {e}", exc_info=True)
                self.initialization_successful = False  # 连接失败，标记初始化失败
                return

        # self.logger.info(f"ScreenMonitorPlugin 初始化完成。截图间隔: {self.interval}s, 模型: {self.model_name}") # 此日志可移除，基类有通用初始化日志

    async def _context_provider_wrapper(self) -> str:
        """Async wrapper method to provide the latest description for context."""
        # This simply calls the existing method that gets the description safely
        return await self.get_latest_description()

    async def setup(self):
        await super().setup()

        # 检查初始化是否成功，如果失败则不启动监控
        if not self.initialization_successful:
            self.logger.warning("ScreenMonitorPlugin 初始化失败，跳过后续设置。")
            return

        # 注册 Remote Stream 图像回调（如果启用）
        if self.use_remote_stream:
            try:
                # 获取 remote_stream 服务
                remote_stream_service = self.core.get_service("remote_stream")
                if remote_stream_service:
                    self.remote_stream_service = remote_stream_service
                    # 注册图像数据回调
                    self.remote_stream_service.register_image_callback("data", self._handle_remote_image)
                    self.logger.info("成功注册 Remote Stream 图像回调")
                else:
                    self.logger.warning("未找到 Remote Stream 服务，将使用本地屏幕捕获")
                    self.use_remote_stream = False
            except Exception as e:
                self.logger.error(f"注册 Remote Stream 回调失败: {e}")
                self.use_remote_stream = False

        # 如果使用OBS，检查OBS连接并设置
        if self.capture_source == "obs" and self.obs_client:
            try:
                # 检查源是否存在
                sources = self.obs_client.get_scene_item_list().scene_items
                source_names = [s.source_name for s in sources]

                if self.obs_source_name not in source_names:
                    self.logger.warning(f"在OBS中找不到源 '{self.obs_source_name}'。可用源: {', '.join(source_names)}")

                # 如果配置了自动切换场景
                if self.obs_auto_switch_scene:
                    scenes = self.obs_client.get_scene_list().scenes
                    scene_names = [s.scene_name for s in scenes]

                    if self.obs_scene_name in scene_names:
                        self.obs_client.set_current_program_scene(self.obs_scene_name)
                        self.logger.info(f"已自动切换到OBS场景: '{self.obs_scene_name}'")
                    else:
                        self.logger.warning(
                            f"在OBS中找不到场景 '{self.obs_scene_name}'。可用场景: {', '.join(scene_names)}"
                        )

            except Exception as e:
                self.logger.error(f"设置OBS时出错: {e}", exc_info=True)

        # 注册 Prompt 上下文提供者
        prompt_ctx_service = self.core.get_service("prompt_context")
        if prompt_ctx_service:
            prompt_ctx_service.register_context_provider(
                provider_name=self.context_provider_name,
                context_info=self._context_provider_wrapper,
                priority=self.context_priority,
                tags=["screen", "context", "vision", "dynamic"],
            )
            self.logger.info(
                f"已向 PromptContext 注册动态屏幕上下文提供者 '{self.context_provider_name}' (优先级: {self.context_priority})。"
            )
        else:
            self.logger.warning("未找到 PromptContext 服务，无法注册屏幕上下文。")

        # 启动后台监控循环
        self.is_running = True
        self._monitor_task = asyncio.create_task(self._monitoring_loop(), name="ScreenMonitorLoop")
        self.logger.info("屏幕监控后台任务已启动。")

    async def cleanup(self):
        self.logger.info("正在清理 ScreenMonitorPlugin...")
        self.is_running = False  # 通知后台任务停止

        # 取消注册 Remote Stream 回调
        if self.use_remote_stream and self.remote_stream_service:
            try:
                self.remote_stream_service.unregister_image_callback("data", self._handle_remote_image)
                self.logger.info("已取消注册 Remote Stream 图像回调")
            except Exception as e:
                self.logger.warning(f"取消注册 Remote Stream 回调失败: {e}")

        # 取消并等待后台任务
        if self._monitor_task and not self._monitor_task.done():
            self.logger.debug("正在取消屏幕监控任务...")
            self._monitor_task.cancel()
            try:
                await asyncio.wait_for(self._monitor_task, timeout=2.0)
            except asyncio.TimeoutError:
                self.logger.warning("屏幕监控任务未能及时取消。")
            except asyncio.CancelledError:
                pass  # 预期行为

        # --- 关闭 OpenAI 客户端 ---
        if self.openai_client:
            try:
                # 使用 openai 库的关闭方法 (如果存在且需要)
                # await self.openai_client.close() # 根据 openai v1.x+ 文档，似乎不需要显式 close
                pass
            except Exception as e:
                self.logger.warning(f"关闭 OpenAI 客户端时出错(通常不需要): {e}")
            self.openai_client = None
            self.logger.info("OpenAI 客户端引用已清除。")

        # --- 关闭 OBS WebSocket 客户端 ---
        if self.obs_client:
            try:
                self.obs_client.disconnect()
                self.logger.info("已断开OBS WebSocket连接。")
            except Exception as e:
                self.logger.warning(f"关闭OBS WebSocket客户端时出错: {e}")
            self.obs_client = None

        # 取消注册 Prompt 上下文（只有在成功初始化的情况下才尝试取消注册）
        if self.initialization_successful:
            prompt_ctx_service = self.core.get_service("prompt_context")
            if prompt_ctx_service:
                try:
                    prompt_ctx_service.unregister_context_provider(self.context_provider_name)
                    self.logger.info(f"已从 PromptContext 取消注册屏幕上下文 '{self.context_provider_name}'。")
                except Exception as e:
                    self.logger.warning(f"尝试取消注册 '{self.context_provider_name}' 时出错: {e}")

        await super().cleanup()
        self.logger.info("ScreenMonitorPlugin 清理完成。")

    async def get_latest_description(self) -> str:
        """(供 PromptContext 调用) 异步安全地获取最新屏幕描述。"""
        # 如果初始化失败，返回默认描述
        if not self.initialization_successful:
            return "屏幕监控插件初始化失败，无法获取屏幕信息。"

        async with self.description_lock:
            return "当前主播的屏幕捕获内容如下：%s\n" % self.latest_description

    async def _monitoring_loop(self):
        """后台任务：定期截图并调用 VL 模型更新描述。"""
        self.logger.info("屏幕监控循环启动。")
        request_image_interval = min(5, max(1, self.interval / 2))  # 远程图像请求间隔为处理间隔的一半，最少1秒，最多5秒
        last_image_request_time = 0

        while self.is_running:
            start_time = time.monotonic()
            try:
                # 如果使用远程流且有一段时间未请求图像，主动请求一次
                if (
                    self.use_remote_stream
                    and self.remote_stream_service
                    and (start_time - last_image_request_time > request_image_interval)
                ):
                    try:
                        await self.remote_stream_service.request_image()
                        last_image_request_time = start_time
                    except Exception as e:
                        self.logger.error(f"请求远程图像失败: {e}")

                # 处理屏幕截图并进行分析
                await self._capture_and_process_screenshot()
            except Exception as e:
                # 捕获截图或处理中的意外错误
                self.logger.error(f"屏幕监控循环中发生错误: {e}", exc_info=True)

            # --- 计算等待时间 ---
            elapsed = time.monotonic() - start_time
            wait_time = max(0, self.interval - elapsed)
            self.logger.debug(f"本次屏幕处理耗时 {elapsed:.2f}s，将等待 {wait_time:.2f}s 进行下一次。")

            try:
                # 使用 asyncio.sleep 进行可中断的等待
                await asyncio.sleep(wait_time)
            except asyncio.CancelledError:
                self.logger.info("屏幕监控循环被取消。")
                break  # 退出循环
        self.logger.info("屏幕监控循环结束。")

    async def _capture_and_process_screenshot(self):
        """执行截图、编码和调用 VL 模型。"""
        # 检查初始化是否成功
        if not self.initialization_successful or not self.openai_client:
            self.logger.debug("初始化未完成或 OpenAI 客户端不可用，跳过截图处理。")
            return

        encoded_image: Optional[str] = None

        # 根据不同的截图源获取图像
        if self.use_remote_stream:
            # 尝试从远程流获取图像
            self.logger.debug("正在尝试从远程流获取图像...")
            # 如果启用了远程流，但尚未收到图像，尝试请求一次
            if not self.remote_image_received and self.remote_stream_service:
                try:
                    self.logger.debug("向远程设备发送图像请求...")
                    await self.remote_stream_service.request_image()
                    # 等待短暂时间，看是否收到响应
                    await asyncio.sleep(0.5)
                except Exception as e:
                    self.logger.error(f"请求远程图像失败: {e}")

            # 检查是否有可用的远程图像
            async with self.remote_image_lock:
                if self.remote_image_received and self.latest_remote_image:
                    self.logger.debug("使用已接收的远程图像...")
                    # 将二进制图像转换为base64编码
                    encoded_image = base64.b64encode(self.latest_remote_image).decode("utf-8")
                else:
                    self.logger.debug("未收到远程图像，尝试使用本地捕获...")
                    # 回退到配置的本地捕获方式
                    self.use_remote_stream = False  # 临时禁用远程流

        # 如果远程流不可用或未收到图像，使用本地捕获
        if not self.use_remote_stream or not encoded_image:
            if self.capture_source == "screen":
                # 使用屏幕截图方法
                self.logger.debug("正在通过屏幕捕获截取屏幕...")
                encoded_image = await self._get_screenshot_from_screen()
            elif self.capture_source == "obs" and self.obs_client:
                # 使用 OBS 截图方法
                self.logger.debug(f"正在通过OBS获取源 '{self.obs_source_name}' 的截图...")
                encoded_image = await self._get_screenshot_from_obs()

            # 恢复远程流设置
            if (
                not self.use_remote_stream
                and REMOTE_STREAM_AVAILABLE
                and self.plugin_config.get("use_remote_stream", False)
            ):
                self.use_remote_stream = True

        if not encoded_image:
            return

        # --- 调用 VL 模型 (使用新方法) ---
        self.logger.debug(f"准备调用 VL 模型: {self.model_name} (通过 OpenAI 兼容接口)")
        new_description = await self._query_vl_model(encoded_image)  # 调用重命名后的方法

        if new_description:
            # 更新上下文描述
            async with self.description_lock:
                self.latest_description = new_description
            self.logger.info(f"屏幕描述已更新: {new_description}...")

            # 发送消息到MaiCore（如果启用）
            if self.send_messages:
                await self._send_screen_message(new_description, encoded_image)
        else:
            self.logger.warning("未能从 VL 模型获取有效描述。")

    async def _query_vl_model(self, base64_image: str) -> Optional[str]:
        """通过 OpenAI 兼容接口调用 VL 模型获取图像描述。"""
        if not self.openai_client:
            return None

        # 构建符合 OpenAI Vision API 格式的 messages
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": self.vl_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            # 使用 Data URL 格式
                            "url": f"data:image/png;base64,{base64_image}"
                        },
                    },
                ],
            }
        ]

        try:
            self.logger.debug(f"向 {self.base_url} 发送 OpenAI 兼容请求 (模型: {self.model_name})...")
            completion = await self.openai_client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_tokens=3000,  # 可以根据需要调整 max_tokens
            )
            self.logger.debug(f"OpenAI 兼容 API 响应: {completion}")

            # 解析响应
            if completion.choices and completion.choices[0].message:
                content = completion.choices[0].message.content
                if content:
                    return content.strip()
                else:
                    self.logger.warning("VL API 响应的消息内容为空。")
                    return None
            else:
                self.logger.warning(f"VL API 响应格式不符合预期: {completion}")
                return None

        except openai.APITimeoutError:
            self.logger.error(f"OpenAI 兼容 API 请求超时 (超时设置: {self.timeout_seconds}s)。")
            return None
        except openai.APIConnectionError as e:
            self.logger.error(f"无法连接到 OpenAI 兼容 API ({self.base_url}): {e}")
            return None
        except openai.RateLimitError as e:
            self.logger.error(f"OpenAI 兼容 API 速率限制错误: {e}")
            return None
        except openai.APIStatusError as e:
            self.logger.error(f"OpenAI 兼容 API 返回错误状态码 {e.status_code}: {e.response}")
            return None
        except Exception as e:
            self.logger.error(f"调用 OpenAI 兼容 API 时发生意外错误: {e}", exc_info=True)
            return None

    async def _send_screen_message(self, description: str, image_base64: str):
        """构造并发送屏幕描述消息到MaiCore"""
        try:
            # 创建屏幕消息对象
            screen_message = ScreenMessage(
                description=description,
                timestamp=int(time.time()),
                raw_data={
                    "image_base64": image_base64,
                    "model_name": self.model_name,
                    "vl_prompt": self.vl_prompt,
                },
            )

            # 构造MessageBase
            message = await screen_message.to_message_base(
                core=self.core,
                config=self.message_config,
                context_tags=self.context_tags,
                template_items=self.template_items,
            )

            # 发送到MaiCore
            await self.core.send_to_maicore(message)
            self.logger.info(f"屏幕描述消息已发送到MaiCore: {description[:50]}...")

        except Exception as e:
            self.logger.error(f"发送屏幕描述消息时出错: {e}", exc_info=True)

    async def _get_screenshot_from_obs(self) -> Optional[str]:
        """从OBS源获取截图，返回Base64编码的图像"""
        if not self.obs_client:
            self.logger.error("OBS客户端未初始化或连接失败")
            return None

        try:
            # 从OBS获取源截图
            screenshot = self.obs_client.get_source_screenshot(
                name=self.obs_source_name,
                img_format="png",
                width=1920,  # 可根据需要调整
                height=1080,  # 可根据需要调整
                quality=90,  # 图像质量
            )

            # 获取Base64图像数据
            if screenshot and hasattr(screenshot, "image_data"):
                # 从响应中获取Base64编码后的图像数据
                encoded_image = (
                    screenshot.image_data.split(",")[1] if "," in screenshot.image_data else screenshot.image_data
                )
                self.logger.debug(f"OBS截图成功并获取为 Base64 (大小: {len(encoded_image)} bytes)")
                return encoded_image
            else:
                self.logger.error("从OBS获取截图失败：响应格式不符合预期")
                return None
        except Exception as e:
            self.logger.error(f"从OBS获取截图失败: {e}", exc_info=True)
            # 尝试重新连接
            try:
                self.logger.debug("尝试重新连接到OBS...")
                self.obs_client = obswspy.ReqClient(
                    host=self.obs_host,
                    port=self.obs_port,
                    password=self.obs_password if self.obs_password else None,
                    timeout=self.timeout_seconds,
                )
                self.logger.info("已重新连接到OBS")
            except Exception as reconnect_err:
                self.logger.error(f"重新连接到OBS失败: {reconnect_err}")
            return None

    async def _get_screenshot_from_screen(self) -> Optional[str]:
        """通过屏幕截图获取图像，返回Base64编码的图像"""
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                buffer = BytesIO()
                img.save(buffer, format="PNG")
                img_bytes = buffer.getvalue()
                encoded_image = base64.b64encode(img_bytes).decode("utf-8")
                self.logger.debug(f"屏幕截图成功并编码为 Base64 (大小: {len(encoded_image)} bytes)")
                return encoded_image
        except Exception as e:
            self.logger.error(f"屏幕截图或编码失败: {e}", exc_info=True)
            return None

    async def _get_screenshot_from_remote(self) -> Optional[str]:
        """从远程设备获取截图，返回Base64编码的图像"""
        if not REMOTE_STREAM_AVAILABLE:
            self.logger.error("远程流插件未加载，无法从远程设备获取截图")
            return None

        try:
            # 使用远程流服务获取截图
            self.logger.debug("正在从远程设备获取屏幕截图...")
            async with RemoteStreamService() as remote_stream:
                # 这里假设远程流服务有一个类似的接口
                screenshot = await remote_stream.get_screenshot(
                    source_name=self.obs_source_name,  # 可能需要根据实际情况调整
                    width=1920,
                    height=1080,
                )

            if screenshot and "image_data" in screenshot:
                # 获取Base64图像数据
                encoded_image = screenshot["image_data"]
                self.logger.debug(f"远程截图成功并获取为 Base64 (大小: {len(encoded_image)} bytes)")
                return encoded_image
            else:
                self.logger.error("从远程设备获取截图失败：响应格式不符合预期")
                return None
        except Exception as e:
            self.logger.error(f"从远程设备获取截图失败: {e}", exc_info=True)
            return None

    async def _handle_remote_image(self, data):
        """处理从 Remote Stream 接收的图像数据

        Args:
            data: 包含图像数据的字典，格式为 {"binary": bytes, "width": int, "height": int}
        """
        try:
            # 获取图像数据
            binary_data = data.get("binary")
            width = data.get("width", 0)
            height = data.get("height", 0)

            if not binary_data:
                self.logger.warning("收到空的远程图像数据")
                return

            self.logger.debug(f"收到远程图像: {len(binary_data)} bytes, 分辨率: {width}x{height}")

            # 将图像数据安全地保存到实例变量
            async with self.remote_image_lock:
                self.latest_remote_image = binary_data
                self.remote_image_received = True

        except Exception as e:
            self.logger.error(f"处理远程图像数据失败: {e}", exc_info=True)


# --- Plugin Entry Point ---
plugin_entrypoint = ScreenMonitorPlugin
