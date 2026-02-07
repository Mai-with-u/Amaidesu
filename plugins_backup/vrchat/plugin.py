# src/plugins/vrchat/plugin.py

import asyncio
from typing import Any, Dict, Optional

from maim_message.message_base import MessageBase

# 尝试导入 python-osc 库
try:
    from pythonosc.udp_client import SimpleUDPClient
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer

    PYTHON_OSC_AVAILABLE = True
except ImportError:
    PYTHON_OSC_AVAILABLE = False
    SimpleUDPClient = None
    Dispatcher = None
    ThreadingOSCUDPServer = None

# 从 core 导入基类和核心类
from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore

# 导入适配器
from src.plugins.vrchat.avatar_adapter import VRCAdapter


class VRChatPlugin(BasePlugin):
    """
    通过 OSC 协议连接到 VRChat，控制虚拟形象参数。

    功能：
    - OSC 客户端：发送参数控制命令到 VRChat
    - OSC 服务器（可选）：接收来自 VRChat 的数据
    - 集成到通用虚拟形象控制系统
    """

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.config = self.plugin_config

        # --- 初始化 enabled 属性 ---
        # 从配置中读取 enabled 状态，默认为 True
        self.enabled = self.config.get("enabled", True)

        # --- 依赖检查 ---
        if not PYTHON_OSC_AVAILABLE:
            self.logger.error(
                "python-osc library not found. Please install it (`pip install python-osc`). VRChatPlugin disabled."
            )
            self.enabled = False
            return

        # --- OSC 客户端 ---
        self.osc_client: Optional[SimpleUDPClient] = None
        self.osc_server: Optional[ThreadingOSCUDPServer] = None
        self._server_task: Optional[asyncio.Task] = None
        self._is_connected = False

        # --- 适配器实例 ---
        self.vrc_adapter: Optional[VRCAdapter] = None

        # --- 加载配置 ---
        # VRChat OSC 默认地址
        self.vrc_host = self.config.get("vrc_host", "127.0.0.1")
        self.vrc_out_port = self.config.get("vrc_out_port", 9000)  # VRChat 发送端口
        self.vrc_in_port = self.config.get("vrc_in_port", 9001)  # VRChat 接收端口

        # 本地监听地址（接收 VRChat 的数据）
        self.local_host = self.config.get("local_host", "127.0.0.1")
        self.local_in_port = self.config.get("local_in_port", 9001)  # 本地接收端口

        # 是否启动 OSC 服务器（接收来自 VRChat 的数据）
        self.enable_server = self.config.get("enable_server", False)

        # 自动发送参数更新
        self.auto_send_params = self.config.get("auto_send_params", True)

        self.logger.info(f"VRChatPlugin 初始化 - VRChat OSC: {self.vrc_host}:{self.vrc_out_port}")

    async def setup(self):
        """插件设置"""
        if not self.enabled:
            return

        # 初始化 OSC 客户端
        try:
            self.osc_client = SimpleUDPClient(self.vrc_host, self.vrc_out_port)
            self._is_connected = True
            self.logger.info(f"OSC 客户端已创建: {self.vrc_host}:{self.vrc_out_port}")
        except Exception as e:
            self.logger.error(f"创建 OSC 客户端失败: {e}", exc_info=True)
            self.enabled = False
            return

        # 启动 OSC 服务器（可选）
        if self.enable_server:
            self._start_osc_server()

        # 设置 Avatar 适配器
        self._setup_avatar_adapter()

        # 注册 WebSocket 处理器
        self.core.register_websocket_handler("vrc_control", self.handle_vrc_control)
        self.core.register_websocket_handler("vrc_expression", self.handle_vrc_expression)

        self.logger.info("VRChatPlugin 设置完成")

    def _start_osc_server(self):
        """启动 OSC 服务器（接收来自 VRChat 的数据）"""
        try:
            dispatcher = Dispatcher()

            # 注册默认处理器
            dispatcher.set_default_handler(self._osc_default_handler)

            # 创建服务器
            self.osc_server = ThreadingOSCUDPServer((self.local_host, self.local_in_port), dispatcher)

            # 在后台线程中运行服务器
            import threading

            server_thread = threading.Thread(target=self.osc_server.serve_forever)
            server_thread.daemon = True
            server_thread.start()

            self.logger.info(f"OSC 服务器已启动: {self.local_host}:{self.local_in_port}")

        except Exception as e:
            self.logger.error(f"启动 OSC 服务器失败: {e}", exc_info=True)

    def _osc_default_handler(self, address, *args):
        """OSC 消息默认处理器"""
        self.logger.debug(f"收到 OSC 消息: {address} {args}")

        # 可以在这里处理来自 VRChat 的数据
        # 例如：触发事件、更新状态等

    def _setup_avatar_adapter(self):
        """设置 Avatar 适配器并注册到核心"""
        try:
            # 获取 AvatarControlManager（已在核心初始化时创建）
            avatar_control = self.core.avatar
            if avatar_control is None:
                self.logger.warning("AvatarControlManager 未在核心中初始化，跳过适配器设置")
                return

            self.logger.info("使用核心中的 AvatarControlManager")

            # 创建适配器
            self.vrc_adapter = VRCAdapter(self)

            # 注册适配器
            avatar_control.register_adapter(self.vrc_adapter)

            # 如果没有活跃适配器，设置为活跃
            if avatar_control.get_active_adapter() is None:
                avatar_control.set_active_adapter("vrc")
                self.logger.info("VRC 适配器已设置为活跃适配器")
            else:
                self.logger.info("VRC 适配器已注册（当前有其他活跃适配器）")

            # 注册常用手势作为动作
            common_gestures = ["Wave", "Peace", "ThumbsUp", "Point"]
            for gesture in common_gestures:
                self.vrc_adapter.register_gesture_as_action(gesture)

        except Exception as e:
            self.logger.error(f"设置 Avatar 适配器失败: {e}", exc_info=True)

    async def handle_vrc_control(self, message: MessageBase):
        """处理 VRChat 控制消息"""
        if not self._is_connected or not self.osc_client:
            self.logger.warning("OSC 客户端未连接")
            return

        data = message.message_segment.data

        # 支持多种格式
        if isinstance(data, dict):
            # 格式: {"parameter": "EyeOpen", "value": 0.5}
            param_name = data.get("parameter")
            value = data.get("value", 0.0)

            if param_name:
                await self.send_osc(f"/avatar/parameters/{param_name}", value)
        elif isinstance(data, str):
            # 简单文本命令: "EyeOpen 0.5"
            parts = data.split()
            if len(parts) >= 2:
                param_name = parts[0]
                try:
                    value = float(parts[1])
                    await self.send_osc(f"/avatar/parameters/{param_name}", value)
                except ValueError:
                    self.logger.warning(f"无效的参数值: {parts[1]}")

    async def handle_vrc_expression(self, message: MessageBase):
        """处理 VRChat 表情消息（通过 AvatarControlManager）"""
        avatar_control = self.core.avatar
        if not avatar_control:
            self.logger.warning("AvatarControlManager 不可用")
            return

        # 获取文本内容
        text = message.message_segment.data
        if not isinstance(text, str):
            return

        # 使用 AvatarControlManager 自动分析文本并设置表情
        result = await avatar_control.set_expression_from_text(text, adapter_name="vrc")

        if result["success"]:
            self.logger.info(f"✓ VRC 表情设置成功: {result.get('expression', 'unknown')}")
        else:
            self.logger.warning(f"✗ VRC 表情设置失败: {result.get('error', 'unknown')}")

    async def send_osc(self, address: str, value: float) -> bool:
        """发送 OSC 消息到 VRChat

        Args:
            address: OSC 地址（如 "/avatar/parameters/EyeOpen"）
            value: 参数值

        Returns:
            是否发送成功
        """
        if not self._is_connected or not self.osc_client:
            self.logger.warning("OSC 客户端未连接")
            return False

        try:
            # python-osc 的 send_message 需要在 asyncio 事件循环中同步调用
            # 它是非阻塞的，所以可以直接调用
            self.osc_client.send_message(address, value)
            self.logger.debug(f"发送 OSC: {address} = {value}")
            return True
        except Exception as e:
            self.logger.error(f"发送 OSC 消息失败: {e}", exc_info=True)
            return False

    async def trigger_hotkey(self, hotkey_name: str) -> bool:
        """触发 VRChat 热键（实际上是手势）

        Args:
            hotkey_name: 热键/手势名称

        Returns:
            是否触发成功
        """
        if not self._is_connected or not self.osc_client:
            self.logger.warning("OSC 客户端未连接")
            return False

        # 通过适配器触发动作
        if self.vrc_adapter:
            return await self.vrc_adapter.trigger_action(hotkey_name)

        return False

    async def set_parameter_value(self, param_name: str, value: float) -> bool:
        """设置参数值（供适配器调用）

        Args:
            param_name: 参数名称
            value: 参数值

        Returns:
            是否设置成功
        """
        return await self.send_osc(f"/avatar/parameters/{param_name}", value)

    async def get_parameter_value(self, param_name: str) -> Optional[float]:
        """获取参数值（OSC 不支持，返回 None）

        Args:
            param_name: 参数名称

        Returns:
            None
        """
        # VRChat OSC 不支持读取参数
        return None

    def is_connected(self) -> bool:
        """检查是否已连接到 VRChat

        Returns:
            是否已连接
        """
        return self._is_connected and self.osc_client is not None

    async def cleanup(self):
        """清理资源"""
        self.logger.info("VRChatPlugin 清理中...")

        # 停止 OSC 服务器
        if self.osc_server:
            try:
                self.osc_server.shutdown()
                self.logger.info("OSC 服务器已停止")
            except Exception as e:
                self.logger.error(f"停止 OSC 服务器失败: {e}")

        # OSC 客户端无需特殊清理

        self._is_connected = False
        self.logger.info("VRChatPlugin 清理完成")


# --- Plugin Entry Point ---
plugin_entrypoint = VRChatPlugin
