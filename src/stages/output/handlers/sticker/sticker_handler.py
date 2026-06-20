"""
Sticker Handler

贴纸输出Handler，处理表情图片并发送到VTS。
"""

import asyncio
import base64
import io
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from PIL import Image
from pydantic import Field

from src.stages.output.registry import handler
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.types import Intent


@handler("sticker")
class StickerHandler:
    """
    贴纸输出Handler

    处理表情图片，调整大小并发送到VTS显示。
    """

    class ConfigSchema(BaseConfig):
        """贴纸输出Handler配置"""

        type: str = "sticker"

        # 表情贴纸配置
        sticker_size: float = Field(default=0.33, ge=0.0, le=1.0, description="贴纸大小")
        sticker_rotation: int = Field(default=90, ge=0, le=360, description="贴纸旋转角度")
        sticker_position_x: float = Field(default=0.0, ge=-1.0, le=1.0, description="贴纸X位置")
        sticker_position_y: float = Field(default=0.0, ge=-1.0, le=1.0, description="贴纸Y位置")

        # 图片处理配置
        image_width: int = Field(default=256, ge=0, le=4096, description="图片宽度")
        image_height: int = Field(default=256, ge=0, le=4096, description="图片高度")

        # 冷却时间和显示时长
        cool_down_seconds: float = Field(default=5.0, ge=0.0, le=300.0, description="冷却时间（秒）")
        display_duration_seconds: float = Field(default=3.0, ge=0.0, le=300.0, description="显示时长（秒）")

    def __init__(self, config: Dict[str, Any], event_bus: EventBus, handler_manager: Any = None):
        """
        初始化贴纸Handler

        Args:
            config: Handler配置字典
            event_bus: EventBus实例
            handler_manager: OutputHandlerManager 实例，用于查找 VTS Handler
        """
        self.config = config
        self.event_bus = event_bus
        self.handler_manager = handler_manager
        self.logger = get_logger("StickerHandler")

        # 使用 ConfigSchema 验证配置，获得类型安全的配置对象
        self.typed_config = self.ConfigSchema(**config)

        # 表情贴纸配置
        self.sticker_size = self.typed_config.sticker_size
        self.sticker_rotation = self.typed_config.sticker_rotation
        self.sticker_position_x = self.typed_config.sticker_position_x
        self.sticker_position_y = self.typed_config.sticker_position_y

        # 图片处理配置
        self.image_width = self.typed_config.image_width
        self.image_height = self.typed_config.image_height

        # 冷却时间和显示时长
        self.cool_down_seconds = self.typed_config.cool_down_seconds
        self.last_trigger_time: float = 0.0
        self.display_duration_seconds = self.typed_config.display_duration_seconds

        # VTS Handler 引用（延迟初始化）
        self._vts_handler: Optional[Any] = None

        # 当前加载的贴纸实例ID（用于卸载）
        self._current_sticker_id: Optional[str] = None
        self._unload_task: Optional[Any] = None

    async def init(self):
        """初始化 Handler"""
        # 查找 VTS Handler（通过 HandlerManager）
        self._vts_handler = await self._find_vts_handler()
        if self._vts_handler:
            self.logger.info("已找到 VTS Handler，贴纸功能已启用")
        else:
            self.logger.warning("未找到 VTS Handler，贴纸功能将被禁用")

    async def handle(self, intent: "Intent"):
        """
        执行意图

        Args:
            intent: 意图对象，检查 intent.action 是否为 sticker 类型
        """
        # 检查 VTS Handler 是否可用
        if not self._vts_handler:
            self.logger.warning("VTS Handler 不可用，无法显示贴纸")
            return

        # 检查是否为 sticker 动作
        if not intent.action or "sticker" not in intent.action.lower():
            self.logger.debug("Intent 中没有 sticker 动作，跳过渲染")
            return

        # 检查冷却时间
        current_time = time.monotonic()
        if current_time - self.last_trigger_time < self.cool_down_seconds:
            remaining_cooldown = self.cool_down_seconds - (current_time - self.last_trigger_time)
            self.logger.debug(f"表情贴纸冷却中，跳过渲染。剩余 {remaining_cooldown:.1f} 秒")
            return

        # 新 Intent 结构中使用自然语言 action，暂不支持图片数据传递
        self.logger.debug(f"检测到 sticker 动作: {intent.action}，但暂不支持图片数据传递")
        return

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
        try:
            # 取消之前的卸载任务（如果存在）
            if self._unload_task and not self._unload_task.done():
                self._unload_task.cancel()
                self.logger.debug("取消之前的卸载任务")

            # 如果已有贴纸，先卸载
            if self._current_sticker_id:
                await self._unload_current_sticker()

            # 使用 VTS Handler 的 load_item 方法加载贴纸
            if hasattr(self._vts_handler, "load_item"):
                instance_id = await self._vts_handler.load_item(
                    file_name="sticker.png",  # 使用固定文件名，因为数据在 custom_data_base64
                    position_x=self.sticker_position_x,
                    position_y=self.sticker_position_y,
                    size=self.sticker_size,
                    rotation=self.sticker_rotation,
                    fade_time=0.5,  # 淡入时间
                    order=10,  # 较高的层级，确保在模型上方
                    custom_data_base64=image_base64,
                )

                if instance_id:
                    self._current_sticker_id = instance_id
                    self.logger.debug(f"贴纸已加载到VTS: {instance_id}")

                    # 设置自动卸载任务
                    self._unload_task = asyncio.create_task(self._delayed_unload())
                else:
                    self.logger.error("贴纸加载失败")
            else:
                self.logger.error("VTS Handler 不支持 load_item 方法")

        except Exception as e:
            self.logger.error(f"发送贴纸到VTS失败: {e}", exc_info=True)

    async def _unload_current_sticker(self):
        """卸载当前贴纸"""
        if self._current_sticker_id and hasattr(self._vts_handler, "unload_item"):
            try:
                success = await self._vts_handler.unload_item(item_instance_id_list=[self._current_sticker_id])
                if success:
                    self.logger.debug(f"贴纸已卸载: {self._current_sticker_id}")
                self._current_sticker_id = None
            except Exception as e:
                self.logger.error(f"卸载贴纸失败: {e}")

    async def _delayed_unload(self):
        """延迟卸载贴纸"""
        try:
            await asyncio.sleep(self.display_duration_seconds)
            await self._unload_current_sticker()
        except asyncio.CancelledError:
            self.logger.debug("延迟卸载任务被取消")
        except Exception as e:
            self.logger.error(f"延迟卸载失败: {e}")

    async def _find_vts_handler(self) -> Optional[Any]:
        """
        查找 VTS Handler

        通过 handler_manager 获取名为 "vts" 的 Handler 实例。

        Returns:
            VTS Handler 实例或 None（未找到或 handler_manager 不可用）
        """
        if not self.handler_manager:
            self.logger.debug("handler_manager 未注入，无法查找 VTS Handler")
            return None

        if not hasattr(self.handler_manager, "get_handler_by_name"):
            self.logger.warning("handler_manager 没有 get_handler_by_name 方法")
            return None

        vts_handler = self.handler_manager.get_handler_by_name("vts")
        if vts_handler:
            self.logger.info("已找到 VTS Handler")
        else:
            self.logger.warning("未找到名为 'vts' 的 Handler")

        return vts_handler

    async def cleanup(self):
        """清理资源"""
        # 取消卸载任务
        if self._unload_task and not self._unload_task.done():
            self._unload_task.cancel()
            try:
                await self._unload_task
            except Exception as e:
                self.logger.warning(f"取消卸载任务失败: {e}")

        # 卸载当前贴纸
        await self._unload_current_sticker()
