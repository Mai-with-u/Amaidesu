"""
Sticker Provider - Layer 7 渲染呈现层实现

职责:
- 将ExpressionParameters中的贴纸动作渲染到VTS
- 支持VTS道具加载、显示、卸载
- 支持自定义贴纸位置、大小、旋转
- 图片大小调整（PIL）
- 冷却时间控制
"""

from typing import Dict, Any

try:
    from PIL import Image
except ImportError:
    Image = None

from src.core.base.output_provider import OutputProvider
from src.layers.expression.render_parameters import ExpressionParameters
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

    def __init__(self, config: Dict[str, Any]):
        """
        初始化Sticker Provider

        Args:
            config: Provider配置（来自[rendering.outputs.sticker]）
        """
        super().__init__(config)
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

        # 状态
        self.last_trigger_time = 0.0

        self.logger.info("StickerProvider初始化完成")

    async def _setup_internal(self):
        """内部设置逻辑"""
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

        self.logger.warning("贴纸功能需要VTS控制服务，当前已禁用")

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        self.logger.info("StickerProvider清理中...")
        self.logger.info("StickerProvider清理完成")
