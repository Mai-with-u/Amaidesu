"""
Sticker Output Provider

贴纸输出Provider，处理表情图片并发送到VTS。
"""

import asyncio
import base64
import io
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from PIL import Image
from pydantic import Field

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.types.base.output_provider import OutputProvider

if TYPE_CHECKING:
    from src.modules.di.context import ProviderContext
    from src.modules.types import Intent


class StickerOutputProvider(OutputProvider):
    """
    贴纸输出Provider

    处理表情图片，调整大小并发送到VTS显示。
    """

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """获取 Provider 注册信息"""
        return {"layer": "output", "name": "sticker", "class": cls, "source": "builtin:sticker"}

    class ConfigSchema(BaseProviderConfig):
        """贴纸输出Provider配置"""

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

    def __init__(self, config: dict, context: "ProviderContext"):
        super().__init__(config, context)
        self.logger = get_logger("StickerOutputProvider")

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

        # VTS Provider 引用（延迟初始化）
        self._vts_provider: Optional[Any] = None

        # 当前加载的贴纸实例ID（用于卸载）
        self._current_sticker_id: Optional[str] = None
        self._unload_task: Optional[Any] = None

    async def init(self):
        """初始化 Provider"""
        # 查找 VTS Provider（通过 ProviderManager）
        self._vts_provider = await self._find_vts_provider()
        if self._vts_provider:
            self.logger.info("已找到 VTS Provider，贴纸功能已启用")
        else:
            self.logger.warning("未找到 VTS Provider，贴纸功能将被禁用")

    async def execute(self, intent: "Intent"):
        """
        执行意图

        Args:
            intent: 意图对象，从 intent.actions 中获取 ActionType.STICKER 类型的动作
        """
        from src.modules.types import ActionType

        # 检查 VTS Provider 是否可用
        if not self._vts_provider:
            self.logger.warning("VTS Provider 不可用，无法显示贴纸")
            return

        # 检查冷却时间
        current_time = time.monotonic()
        if current_time - self.last_trigger_time < self.cool_down_seconds:
            remaining_cooldown = self.cool_down_seconds - (current_time - self.last_trigger_time)
            self.logger.debug(f"表情贴纸冷却中，跳过渲染。剩余 {remaining_cooldown:.1f} 秒")
            return

        # 从 intent.actions 中获取 STICKER 类型的动作
        sticker_action = None
        for action in intent.actions:
            if action.type == ActionType.STICKER:
                sticker_action = action
                break

        if not sticker_action:
            self.logger.debug("Intent 中没有 STICKER 动作，跳过渲染")
            return

        # 从 action.params 获取图片base64数据
        image_base64 = sticker_action.params.get("sticker_image")
        if not image_base64:
            self.logger.warning("未提供图片数据（action.params.sticker_image），跳过渲染")
            return

        try:
            # 调整图片大小
            resized_image_base64 = self._resize_image_base64(image_base64)

            # 发送到VTS
            await self._send_to_vts(resized_image_base64)

            # 更新冷却时间
            self.last_trigger_time = time.monotonic()
        except Exception as e:
            self.logger.error(f"渲染贴纸失败: {e}", exc_info=True)

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

            # 使用 VTS Provider 的 load_item 方法加载贴纸
            if hasattr(self._vts_provider, "load_item"):
                instance_id = await self._vts_provider.load_item(
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
                self.logger.error("VTS Provider 不支持 load_item 方法")

        except Exception as e:
            self.logger.error(f"发送贴纸到VTS失败: {e}", exc_info=True)

    async def _unload_current_sticker(self):
        """卸载当前贴纸"""
        if self._current_sticker_id and hasattr(self._vts_provider, "unload_item"):
            try:
                success = await self._vts_provider.unload_item(item_instance_id_list=[self._current_sticker_id])
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

    async def _find_vts_provider(self) -> Optional[Any]:
        """
        查找 VTS Provider

        Returns:
            VTS Provider 实例或 None
        """
        # TODO: 通过 ProviderManager 或其他方式获取 VTS Provider
        # STATUS: PENDING - 依赖注入方案待确定
        # 当前返回 None，需要集成实际的 Provider 查找逻辑
        return None

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
