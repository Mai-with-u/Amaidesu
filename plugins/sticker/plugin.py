# src/plugins/vtube_studio/plugin.py
import asyncio
from typing import Any, Dict
import time
from PIL import Image
import io
import base64

from maim_message.message_base import MessageBase

# 从 core 导入基类和核心类
from core.plugin_manager import BasePlugin
from core.amaidesu_core import AmaidesuCore


# --- Helper Function ---


# --- Plugin Class ---
class StickerPlugin(BasePlugin):
    """
    将麦麦发送的表情图片作为表情贴纸发送给VTS
    """

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.config = self.plugin_config
        # 添加表情贴纸配置
        self.sticker_size = self.config.get("sticker_size", 0.33)
        self.sticker_rotation = self.config.get("sticker_rotation", 90)
        self.sticker_position_x = self.config.get("sticker_position_x", 0)
        self.sticker_position_y = self.config.get("sticker_position_y", 0)
        # 添加图片处理配置
        self.image_width = self.config.get("image_width", 256)
        self.image_height = self.config.get("image_height", 256)
        # 添加冷却时间配置和上次触发时间记录
        self.cool_down_seconds = self.config.get("cool_down_seconds", 5)
        self.last_trigger_time: float = 0.0
        self.display_duration_seconds = self.config.get("display_duration_seconds", 3)

    def resize_image_base64(self, base64_str: str) -> str:
        """将base64图片调整为配置中指定的大小，保持原始比例"""
        try:
            # 解码base64
            image_data = base64.b64decode(base64_str)
            # 转换为PIL图像
            image = Image.open(io.BytesIO(image_data))
            original_width, original_height = image.size

            # 计算新的尺寸
            if self.image_width > 0 and self.image_height > 0:
                # 如果同时设置了宽高，强制调整为指定大小
                target_size = (self.image_width, self.image_height)
            elif self.image_width > 0:
                # 只设置了宽度，保持比例
                ratio = self.image_width / original_width
                new_height = int(original_height * ratio)
                target_size = (self.image_width, new_height)
            elif self.image_height > 0:
                # 只设置了高度，保持比例
                ratio = self.image_height / original_height
                new_width = int(original_width * ratio)
                target_size = (new_width, self.image_height)
            else:
                # 没有设置任何限制，返回原始图片
                return base64_str

            # 调整大小
            image = image.resize(target_size, Image.Resampling.LANCZOS)
            # 转换回base64
            buffered = io.BytesIO()
            image.save(buffered, format="PNG")
            return base64.b64encode(buffered.getvalue()).decode("utf-8")
        except Exception as e:
            self.logger.error(f"调整图片大小时出错: {e}")
            return base64_str  # 如果处理失败，返回原始base64

    async def setup(self):
        await super().setup()

        self.core.register_websocket_handler("emoji", self.handle_maicore_message)

        self.logger.info("表情贴纸插件设置完成")

    async def cleanup(self):
        self.logger.info("表情贴纸插件清理中...")
        # --- 新插件的清理逻辑 ---
        # 例如: 取消注册、关闭连接等
        # self.core.unregister_command(...)

        await super().cleanup()
        self.logger.info("表情贴纸插件清理完成")

    # --- 新插件的方法 ---
    # 例如: 处理消息、执行分析等
    # async def analyze_emotion(self, text: str): ...

    async def handle_maicore_message(self, message: MessageBase):
        """处理从 MaiCore 收到的消息，如果是文本类型，则进行处理，触发热键。"""
        if not message or not message.message_segment or message.message_segment.type != "emoji":
            self.logger.debug("收到非表情消息，跳过")
            return

        # --- 将冷却时间检查移到此处 ---
        current_time = time.monotonic()
        if current_time - self.last_trigger_time < self.cool_down_seconds:
            remaining_cooldown = self.cool_down_seconds - (current_time - self.last_trigger_time)
            self.logger.debug(f"表情贴纸冷却中，跳过消息处理。剩余 {remaining_cooldown:.1f} 秒")
            return
        # --- 冷却时间检查结束 ---

        image_base64 = message.message_segment.data
        # 调整图片大小
        resized_image_base64 = self.resize_image_base64(image_base64)

        vts_control_service = self.core.get_service("vts_control")
        if not vts_control_service:
            self.logger.warning("未找到 VTS 控制服务。无法发送表情贴纸。")
            return

        item_instance_id = await vts_control_service.load_item(
            custom_data_base64=resized_image_base64,
            position_x=self.sticker_position_x,
            position_y=self.sticker_position_y,
            size=self.sticker_size,
            rotation=self.sticker_rotation,
        )
        if not item_instance_id:
            self.logger.error("表情贴纸加载失败")
            return

        await asyncio.sleep(self.display_duration_seconds)

        success = await vts_control_service.unload_item(
            item_instance_id_list=[item_instance_id],
        )
        if not success:
            self.logger.error("表情贴纸卸载失败")


# --- Plugin Entry Point ---
plugin_entrypoint = StickerPlugin
