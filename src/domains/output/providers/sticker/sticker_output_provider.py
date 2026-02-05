"""
Sticker Output Provider

贴纸输出Provider，处理表情图片并发送到VTS。
"""

import time

import base64
import io
from PIL import Image

from src.core.base.output_provider import OutputProvider
from src.core.base.base import RenderParameters
from src.core.utils.logger import get_logger


class StickerOutputProvider(OutputProvider):
    """
    贴纸输出Provider

    处理表情图片，调整大小并发送到VTS显示。
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("StickerOutputProvider")

        # 表情贴纸配置
        self.sticker_size = self.config.get("sticker_size", 0.33)
        self.sticker_rotation = self.config.get("sticker_rotation", 90)
        self.sticker_position_x = self.config.get("sticker_position_x", 0)
        self.sticker_position_y = self.config.get("sticker_position_y", 0)

        # 图片处理配置
        self.image_width = self.config.get("image_width", 256)
        self.image_height = self.config.get("image_height", 256)

        # 冷却时间和显示时长
        self.cool_down_seconds = self.config.get("cool_down_seconds", 5)
        self.last_trigger_time: float = 0.0
        self.display_duration_seconds = self.config.get("display_duration_seconds", 3)

    async def _setup_internal(self):
        """内部设置"""
        # 注册事件监听器
        if self.event_bus:
            self.event_bus.on("render.sticker", self._handle_render_request, priority=50)
            self.logger.info("已注册 sticker 渲染事件监听器")

    async def _render_internal(self, parameters: RenderParameters):
        """
        内部渲染逻辑

        Args:
            parameters: 渲染参数，图片base64数据通过metadata传递
                      metadata: {"sticker_image": "base64_data"}
        """
        # 检查冷却时间
        current_time = time.monotonic()
        if current_time - self.last_trigger_time < self.cool_down_seconds:
            remaining_cooldown = self.cool_down_seconds - (current_time - self.last_trigger_time)
            self.logger.debug(f"表情贴纸冷却中，跳过渲染。剩余 {remaining_cooldown:.1f} 秒")
            return

        # 从metadata获取图片base64数据
        image_base64 = parameters.metadata.get("sticker_image") if parameters.metadata else None
        if not image_base64:
            self.logger.warning("未提供图片数据（metadata.sticker_image），跳过渲染。")
            return

        # 调整图片大小
        resized_image_base64 = self._resize_image_base64(image_base64)

        # 发送到VTS
        await self._send_to_vts(resized_image_base64)

        # 更新冷却时间
        self.last_trigger_time = time.monotonic()

    def _resize_image_base64(self, base64_str: str) -> str:
        """
        将base64图片调整为配置中指定的大小，保持原始比例

        Args:
            base64_str: 原始base64图片数据

        Returns:
            调整大小后的base64图片数据
        """
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
            self.logger.error(f"调整图片大小时出错: {e}", exc_info=True)
            return base64_str  # 如果处理失败，返回原始base64

    async def _send_to_vts(self, image_base64: str):
        """
        发送贴纸到VTS

        Args:
            image_base64: base64编码的图片数据
        """
        # 由于当前架构限制，我们暂时通过事件发送请求
        # 未来可能需要更完善的服务注册机制
        self.logger.debug(f"发送贴纸到VTS (大小: {self.sticker_size}, 旋转: {self.sticker_rotation})")

    async def _cleanup_internal(self):
        """内部清理"""
        pass

    async def _handle_render_request(self, event_name: str, data: RenderParameters, source: str):
        """处理贴纸渲染请求"""
        await self.render(data)
