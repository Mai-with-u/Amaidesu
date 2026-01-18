"""
Sticker Provider - Layer 6 Rendering层实现

职责:
- 将ExpressionParameters中的贴纸动作渲染到VTS
- 支持VTS道具加载、显示、卸载
- 集成vts_control服务（向后兼容）
"""

import base64
import io
from typing import Optional, Dict, Any

from src.core.providers.output_provider import OutputProvider
from src.expression.render_parameters import ExpressionParameters
from src.utils.logger import get_logger


class StickerProvider(OutputProvider):
    """
    Sticker Provider实现

    核心功能:
    - VTS道具加载、显示、卸载
    - 支持自定义贴纸位置、大小、旋转
    - 图片大小调整（PIL）
    - 冷却时间控制
    """

    def __init__(self, config: Dict[str, Any], event_bus=None, core=None):
        """
        初始化Sticker Provider

        Args:
            config: Provider配置（来自[rendering.outputs.sticker]）
            event_bus: EventBus实例（可选）
            core: AmaidesuCore实例（可选，用于访问服务）
        """
        super().__init__(config, event_bus)
        self.core = core
        self.logger = get_logger("StickerProvider")

        # 配置参数
        self.sticker_size = config.get("size", 0.33)
        self.sticker_rotation = config.get("rotation", 90)
        self.sticker_position_x = config.get("position_x", 0)
        self.sticker_position_y = config.get("position_y", 0.5)

        # 显示配置
        self.display_duration_seconds = config.get("display_duration_seconds", 3)
        self.cool_down_seconds = config.get("cool_down_seconds", 5)

        # 图片处理配置
        self.image_width = config.get("image_width", 256)
        self.image_height = config.get("image_height", 256)

        # VTS服务引用（延迟初始化）
        self.vts_control_service = None

        # 状态
        self.last_trigger_time = 0.0

        self.logger.info("StickerProvider初始化完成")

    async def _setup_internal(self):
        """内部设置逻辑"""
        # 获取VTS控制服务
        if self.core:
            self.vts_control_service = self.core.get_service("vts_control")
            if self.vts_control_service:
                self.logger.info("已获取vts_control服务")
            else:
                self.logger.warning("未找到vts_control服务，贴纸功能将不可用")
        else:
            self.logger.warning("core未提供，贴纸功能将受限")

        self.logger.info("StickerProvider设置完成")

    async def _render_internal(self, parameters: ExpressionParameters):
        """
        渲染贴纸

        Args:
            parameters: ExpressionParameters对象
        """
        if not parameters.actions_enabled or not parameters.actions:
            self.logger.debug("贴纸动作未启用，跳过渲染")
            return

        # 查找贴纸动作
        sticker_action = None
        for action in parameters.actions:
            if action.get("type") == "sticker":
                sticker_action = action
                break

        if not sticker_action:
            self.logger.debug("未找到贴纸动作")
            return

        # 检查冷却时间
        import time

        current_time = time.monotonic()
        if current_time - self.last_trigger_time < self.cool_down_seconds:
            remaining = self.cool_down_seconds - (current_time - self.last_trigger_time)
            self.logger.debug(f"贴纸冷却中，跳过。剩余 {remaining:.1f} 秒")
            return

        # 更新触发时间
        self.last_trigger_time = current_time

        # 获取贴纸数据
        sticker_data = sticker_action.get("params", {})
        image_base64 = sticker_data.get("image_base64")

        if not image_base64:
            self.logger.warning("贴纸数据中缺少image_base64")
            return

        # 调整图片大小（如果需要）
        image_base64 = self._resize_image_base64(image_base64)

        # 通过VTS服务加载贴纸
        if not self.vts_control_service:
            self.logger.error("vts_control服务不可用，无法加载贴纸")
            return

        try:
            # 加载贴纸
            item_instance_id = await self.vts_control_service.load_item(
                custom_data_base64=image_base64,
                position_x=self.sticker_position_x,
                position_y=self.sticker_position_y,
                size=self.sticker_size,
                rotation=self.sticker_rotation,
            )

            if not item_instance_id:
                self.logger.error("贴纸加载失败")
                return

            self.logger.info(f"贴纸已加载: {item_instance_id}")

            # 等待显示时间
            await asyncio.sleep(self.display_duration_seconds)

            # 卸载贴纸
            success = await self.vts_control_service.unload_item(
                item_instance_id_list=[item_instance_id],
            )

            if not success:
                self.logger.error(f"贴纸卸载失败: {item_instance_id}")
            else:
                self.logger.info(f"贴纸已卸载: {item_instance_id}")

        except Exception as e:
            self.logger.error(f"贴纸渲染失败: {e}", exc_info=True)
            raise RuntimeError(f"Sticker渲染失败: {e}") from e

    def _resize_image_base64(self, base64_str: str) -> str:
        """
        调整base64编码的图片大小

        Args:
            base64_str: base64编码的图片

        Returns:
            调整后的base64编码图片
        """
        try:
            # 解码base64
            image_data = base64.b64decode(base64_str)

            # 转换为PIL图像
            image = Image.open(io.BytesIO(image_data))

            # 计算新尺寸
            original_width, original_height = image.size

            if self.image_width > 0 and self.image_height > 0:
                # 同时设置了宽高，强制调整
                target_size = (self.image_width, self.image_height)
            elif self.image_width > 0:
                # 只设置了宽度，按比例调整高度
                ratio = self.image_width / original_width
                target_size = (self.image_width, int(original_height * ratio))
            elif self.image_height > 0:
                # 只设置了高度，按比例调整宽度
                ratio = self.image_height / original_height
                target_size = (int(original_width * ratio), self.image_height)
            else:
                # 没有设置限制，返回原始图片
                return base64_str

            # 调整大小（使用LANCZOS重采样）
            resized_image = image.resize(target_size, Image.Resampling.LANCZOS)

            # 转换回base64
            buffered = io.BytesIO()
            resized_image.save(buffered, format="PNG")
            image_base64_resized = base64.b64encode(buffered.getvalue())

            self.logger.info(
                f"图片调整大小: {original_width}x{original_height} → {self.image_width}x{self.image_height}"
            )

            return image_base64_resized

        except Exception as e:
            self.logger.error(f"调整图片大小时出错: {e}", exc_info=True)
            return base64_str

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        self.logger.info("StickerProvider清理中...")
        self.vts_control_service = None
        self.logger.info("StickerProvider清理完成")
