"""
Sticker Handler

贴纸输出Handler，通过 OUTPUT_STICKER_COMMAND 事件通知其他 Handler（如 VTSHandler）。
"""

import time
import uuid
from typing import TYPE_CHECKING, Any, Dict

from pydantic import Field

from src.stages.output.handlers.completion_mixin import CompletionEmitterMixin
from src.stages.output.registry import handler
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import IntentPayload, StickerCommandPayload
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.types import Intent


@handler("sticker")
class StickerHandler(CompletionEmitterMixin):
    """
    贴纸输出Handler

    处理表情图片，通过 OUTPUT_STICKER_COMMAND 事件广播给订阅者（典型为 VTSHandler）。
    """

    class ConfigSchema(BaseConfig):
        """贴纸输出Handler配置"""

        type: str = "sticker"

        sticker_size: float = Field(default=0.33, ge=0.0, le=1.0, description="贴纸大小")
        sticker_rotation: int = Field(default=90, ge=0, le=360, description="贴纸旋转角度")
        sticker_position_x: float = Field(default=0.0, ge=-1.0, le=1.0, description="贴纸X位置")
        sticker_position_y: float = Field(default=0.0, ge=-1.0, le=1.0, description="贴纸Y位置")

        image_width: int = Field(default=256, ge=0, le=4096, description="图片宽度")
        image_height: int = Field(default=256, ge=0, le=4096, description="图片高度")

        cool_down_seconds: float = Field(default=5.0, ge=0.0, le=300.0, description="冷却时间（秒）")
        display_duration_seconds: float = Field(default=3.0, ge=0.0, le=300.0, description="显示时长（秒）")

        target_handler: str = Field(default="vts", description="默认目标 Handler 名")

    def __init__(self, config: Dict[str, Any], event_bus: EventBus):
        self.config = config
        self.event_bus = event_bus
        self.logger = get_logger("StickerHandler")

        self.typed_config = self.ConfigSchema.from_dict(config)

        self.sticker_size = self.typed_config.sticker_size
        self.sticker_rotation = self.typed_config.sticker_rotation
        self.sticker_position_x = self.typed_config.sticker_position_x
        self.sticker_position_y = self.typed_config.sticker_position_y
        self.image_width = self.typed_config.image_width
        self.image_height = self.typed_config.image_height
        self.cool_down_seconds = self.typed_config.cool_down_seconds
        self.display_duration_seconds = self.typed_config.display_duration_seconds
        self.target_handler = self.typed_config.target_handler

        self.last_trigger_time: float = 0.0

        self._dispatch_subscribed = False

    async def init(self):
        """初始化 Handler"""
        if self.event_bus and not getattr(self, "_dispatch_subscribed", False):
            self.event_bus.on(
                CoreEvents.OUTPUT_INTENT_DISPATCHED,
                self._handle_intent_dispatched,
                model_class=IntentPayload,
            )
            self._dispatch_subscribed = True

    async def handle(self, intent: "Intent"):
        success = True
        try:
            if not intent.action or "sticker" not in intent.action.name.lower():
                self.logger.debug("Intent 中没有 sticker 动作，跳过渲染")
                return

            current_time = time.monotonic()
            if current_time - self.last_trigger_time < self.cool_down_seconds:
                remaining = self.cool_down_seconds - (current_time - self.last_trigger_time)
                self.logger.debug(f"表情贴纸冷却中，跳过渲染。剩余 {remaining:.1f} 秒")
                return

            self.last_trigger_time = current_time
            sticker_id = f"sticker_{uuid.uuid4().hex[:8]}"

            await self.event_bus.emit(
                CoreEvents.OUTPUT_STICKER_COMMAND,
                StickerCommandPayload(
                    sticker_id=sticker_id,
                    target_handler=self.target_handler,
                    timestamp_ms=now_ms(),
                    size=self.sticker_size,
                    rotation=self.sticker_rotation,
                    position_x=self.sticker_position_x,
                    position_y=self.sticker_position_y,
                    display_duration_seconds=self.display_duration_seconds,
                ),
                source="StickerHandler",
            )
            self.logger.debug(f"已发布贴纸事件: sticker_id={sticker_id}")
        except Exception as e:
            success = False
            self.logger.error(f"StickerHandler 渲染失败: {e}", exc_info=True)
        finally:
            await self._emit_completed(intent, success=success)

    async def _handle_intent_dispatched(self, event_name: str, payload: IntentPayload, source: str):
        intent = payload.to_intent()
        await self.handle(intent)

    async def cleanup(self):
        if self.event_bus and getattr(self, "_dispatch_subscribed", False):
            self.event_bus.off(CoreEvents.OUTPUT_INTENT_DISPATCHED, self._handle_intent_dispatched)
            self._dispatch_subscribed = False
