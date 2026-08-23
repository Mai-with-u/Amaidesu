"""
StickerHelper - 贴纸事件 helper（Wave 4）

迁移自 ``src.stages.output.handlers.sticker.StickerHandler``：

- **非渲染器，是 EventBus 转发器**：贴纸本身不进入 Tool 流程
- Agent 在需要展示贴纸时，**直接 emit** ``CoreEvents.OUTPUT_STICKER_COMMAND``
  到 EventBus；``VTSProvider`` 在 setup() 时订阅该事件，加载贴纸到 VTS
- 保留：payload 字段 + 冷却逻辑 verbatim
- 删除：@handler 装饰器 / handle(intent) 接口 / OutputHandlerManager 派发

迁移策略（与 .omo/drafts/amaidesu-v2-migration.md A 段对齐）:
- ``OUTPUT_STICKER_COMMAND`` 事件**完整保留**（A 段定案：sticker→VTS 单向信号）
- ``StickerCommandPayload`` 不动
- 冷却机制 + sticker_id 生成 + 配置字段 verbatim
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import StickerCommandPayload
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms


class StickerHelper:
    """贴纸事件 helper（Agent 直接调用）"""

    PROVIDER_NAME = "sticker"

    class ConfigSchema(BaseConfig):
        """贴纸配置（verbatim 自旧 StickerHandler.ConfigSchema）"""

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
        self.logger = get_logger(self.__class__.__name__)

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

    def emit_sticker(
        self,
        sticker_id: Optional[str] = None,
        target_handler: Optional[str] = None,
        image_base64: Optional[str] = None,
        size: Optional[float] = None,
        rotation: Optional[int] = None,
        position_x: Optional[float] = None,
        position_y: Optional[float] = None,
        display_duration_seconds: Optional[float] = None,
    ) -> bool:
        """直接 emit CoreEvents.OUTPUT_STICKER_COMMAND

        Returns:
            True 如果事件已发出，False 如果在冷却中或参数无效
        """
        try:
            current_time = time.monotonic()
            if current_time - self.last_trigger_time < self.cool_down_seconds:
                remaining = self.cool_down_seconds - (current_time - self.last_trigger_time)
                self.logger.debug(f"表情贴纸冷却中，跳过渲染。剩余 {remaining:.1f} 秒")
                return False

            self.last_trigger_time = current_time
            sid = sticker_id or f"sticker_{uuid.uuid4().hex[:8]}"

            payload = StickerCommandPayload(
                sticker_id=sid,
                target_handler=target_handler or self.target_handler,
                timestamp_ms=now_ms(),
                image_base64=image_base64,
                size=size if size is not None else self.sticker_size,
                rotation=rotation if rotation is not None else self.sticker_rotation,
                position_x=position_x if position_x is not None else self.sticker_position_x,
                position_y=position_y if position_y is not None else self.sticker_position_y,
                display_duration_seconds=display_duration_seconds
                if display_duration_seconds is not None
                else self.display_duration_seconds,
            )

            import asyncio

            try:
                loop = asyncio.get_running_loop()
                # 在运行中的 event loop 上：调度 fire-and-forget 任务
                loop.create_task(
                    self.event_bus.emit(
                        CoreEvents.OUTPUT_STICKER_COMMAND,
                        payload,
                        source="StickerHelper",
                    )
                )
            except RuntimeError:
                # 无运行中的 loop：跨线程 emit（事件总线内部会处理）
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        asyncio.run,
                        self.event_bus.emit(
                            CoreEvents.OUTPUT_STICKER_COMMAND,
                            payload,
                            source="StickerHelper",
                        ),
                    )
                    future.result(timeout=5.0)

            self.logger.debug(f"已发布贴纸事件: sticker_id={sid}")
            return True
        except Exception as e:
            self.logger.error(f"StickerHelper 渲染失败: {e}", exc_info=True)
            return False


__all__ = ["StickerHelper"]
