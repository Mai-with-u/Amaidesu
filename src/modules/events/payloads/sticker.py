"""v2 Sticker Payload（§1.46.1 保留 Sticker→VTS 单向信号）

Wave 6 设计：OUTPUT_STICKER_COMMAND 事件保留（Sticker → VTS 单向信号），
Agent 直接 emit StickerCommandPayload；VTSProvider 订阅。
"""

from typing import Optional

from pydantic import Field

from src.modules.events.payloads.base import BasePayload
from src.modules.events.registry import register_event


@register_event("output.sticker.command")
class StickerCommandPayload(BasePayload):
    """贴纸事件 Payload（StickerHelper → VTS Provider 单向信号）。

    Attributes:
        sticker_id: 贴纸唯一 ID（用于 VTS 加载贴纸）
        category: 贴纸分类（vts 动画 / 用户头像等）
        cooldown_ms: 冷却时长（毫秒，0 = 不冷却）
    """

    sticker_id: str = Field(..., description="贴纸唯一 ID")
    category: str = Field(default="vts", description="贴纸分类（vts / avatar 等）")
    cooldown_ms: int = Field(default=0, ge=0, description="冷却时长（毫秒）")
    metadata: Optional[dict] = Field(default=None, description="额外元数据")


__all__ = ["StickerCommandPayload"]
