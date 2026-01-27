# src/plugins/obs_control/obs_control_plugin.py
from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore
from typing import Dict, Any
from maim_message.message_base import MessageBase
import asyncio
import obsws_python as obs


class ObsControlPlugin(BasePlugin):
    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.obs_connection = None
        self.enabled = True

        # 从配置中获取 OBS 连接参数
        obs_config = self.plugin_config.get("obs", {})
        self.host = obs_config.get("host", "localhost")
        self.port = obs_config.get("port", 4455)
        self.password = obs_config.get("password", "")
        self.text_source_name = obs_config.get("text_source_name", "text")

        # 从行为配置中获取逐字显示参数
        behavior_config = self.plugin_config.get("behavior", {})
        self.typewriter_enabled = behavior_config.get("typewriter_effect", False)
        self.typewriter_speed = behavior_config.get("typewriter_speed", 0.1)  # 每个字符间隔秒数
        self.typewriter_delay = behavior_config.get("typewriter_delay", 0.5)  # 每次完整显示后的延迟

        # 检查必要配置
        if not self.password:
            self.logger.error("OBS WebSocket 密码未配置！请在 config.toml 中设置 obs.password")
            self.enabled = False
            return

        self.logger.info(f"OBS 控制插件已初始化，连接参数：{obs_config}")
        self.logger.info(
            f"逐字显示功能：{'启用' if self.typewriter_enabled else '禁用'}，速度：{self.typewriter_speed}s/字符"
        )

    async def setup(self):
        """插件设置，注册消息处理器"""
        if not self.enabled:
            self.logger.warning("OBS 控制插件已禁用，因缺少必要配置")
            return

        # 尝试连接 OBS
        await self.connect_obs()

        # 注册服务供其他插件使用
        self.core.register_service("obs_control", self)

    async def connect_obs(self):
        """连接到 OBS WebSocket"""
        try:
            self.obs_connection = obs.ReqClient(host=self.host, port=self.port, password=self.password)
            self.logger.info(f"成功连接到 OBS WebSocket 服务器：{self.host}:{self.port}")

            # 发送测试消息确认连接
            await self.send_test_message()

        except Exception as e:
            self.logger.error(f"连接 OBS WebSocket 失败：{e}")
            self.logger.error("请检查：1. OBS 是否开启 WebSocket 服务；2. 密码是否正确；3. 网络是否通畅")
            self.enabled = False

    async def send_test_message(self):
        """发送测试消息"""
        if not self.obs_connection:
            return

        try:
            test_message = "OBS 控制插件已成功连接"
            # 使用逐字显示效果发送测试消息
            if self.typewriter_enabled:
                await self.send_typewriter_effect(test_message)
                self.logger.info(f"已使用逐字显示效果发送测试消息：{test_message}")
            else:
                self.obs_connection.set_input_settings(self.text_source_name, {"text": test_message}, True)
                self.logger.info(f"已向 OBS 文本源 '{self.text_source_name}' 发送测试消息：{test_message}")
        except Exception as e:
            self.logger.error(f"向 OBS 发送测试消息失败：{e}")
            self.logger.warning("请确认 OBS 中是否存在名为 '{}' 的文本源".format(self.text_source_name))

    def extract_text_from_message_segment(self, message_segment):
        """从消息段中提取文本内容"""
        if hasattr(message_segment, "type") and message_segment.type == "seglist":
            # 如果是 seglist 类型，遍历其数据
            if hasattr(message_segment, "data") and isinstance(message_segment.data, list):
                text_parts = []
                for seg in message_segment.data:
                    if hasattr(seg, "type") and seg.type == "text" and hasattr(seg, "data"):
                        text_parts.append(str(seg.data))
                return "".join(text_parts)
        elif hasattr(message_segment, "type") and message_segment.type == "text":
            # 如果直接是文本类型
            return str(getattr(message_segment, "data", ""))

        return ""

    async def handle_message(self, message: MessageBase):
        """处理消息并发送到 OBS"""
        if not self.enabled or not self.obs_connection:
            return

        try:
            # 尝试从不同来源获取文本内容
            message_content = ""

            # 方法1: 从 message_segment 提取
            if hasattr(message, "message_segment"):
                message_content = self.extract_text_from_message_segment(message.message_segment)

            # 方法2: 如果上面没获取到，尝试从 raw_message 获取
            if not message_content and hasattr(message, "raw_message"):
                message_content = str(getattr(message, "raw_message", ""))

            # 方法3: 如果还是没有，尝试直接访问 message（如果 message 本身是字符串）
            if not message_content and isinstance(message, str):
                message_content = message

            if not message_content:
                self.logger.debug("收到的消息不包含文本内容，已跳过")
                return

            # 根据配置决定是否使用逐字显示效果
            if self.typewriter_enabled:
                await self.send_typewriter_effect(message_content)
            else:
                # 直接发送完整消息
                self.obs_connection.set_input_settings(self.text_source_name, {"text": message_content}, True)
                self.logger.info(
                    f"已将消息发送至 OBS：{message_content[:50]}{'...' if len(message_content) > 50 else ''}"
                )

        except Exception as e:
            self.logger.error(f"向 OBS 发送消息时发生错误：{e}")
            self.logger.warning("请检查 OBS 是否正常运行，或文本源是否被删除")

    async def send_typewriter_effect(self, text: str):
        """发送逐字显示效果"""
        try:
            # 先清空文本
            self.obs_connection.set_input_settings(self.text_source_name, {"text": ""}, True)

            # 逐字显示
            current_text = ""
            for char in text:
                current_text += char
                self.obs_connection.set_input_settings(self.text_source_name, {"text": current_text}, True)
                await asyncio.sleep(self.typewriter_speed)

            # 显示完整文本后等待一段时间
            await asyncio.sleep(self.typewriter_delay)

            self.logger.info(f"已逐字显示消息：{text[:50]}{'...' if len(text) > 50 else ''}")

        except Exception as e:
            self.logger.error(f"逐字显示效果执行失败：{e}")
            # 如果逐字显示失败，直接显示完整文本
            self.obs_connection.set_input_settings(self.text_source_name, {"text": text}, True)

    async def send_to_obs(self, text: str, typewriter: bool = None):
        """直接发送文本到 OBS（供其他插件调用）"""
        if not self.obs_connection:
            self.logger.error("OBS 未连接，无法发送消息")
            return False

        try:
            # 使用参数指定的逐字显示设置，如果未指定则使用配置中的默认设置
            use_typewriter = self.typewriter_enabled if typewriter is None else typewriter

            if use_typewriter:
                await self.send_typewriter_effect(text)
            else:
                self.obs_connection.set_input_settings(self.text_source_name, {"text": text}, True)
                self.logger.info(f"已直接发送文本至 OBS：{text[:50]}{'...' if len(text) > 50 else ''}")

            return True
        except Exception as e:
            self.logger.error(f"直接发送文本到 OBS 失败：{e}")
            return False

    async def cleanup(self):
        """清理资源"""
        self.logger.info("正在关闭 OBS 控制插件...")

        # 断开 OBS 连接
        if self.obs_connection:
            try:
                self.obs_connection.disconnect()
                self.logger.info("已断开与 OBS WebSocket 的连接")
            except Exception as e:
                self.logger.error(f"断开 OBS 连接时出错：{e}")

        await super().cleanup()
        self.logger.info("OBS 控制插件已完全关闭")


plugin_entrypoint = ObsControlPlugin
