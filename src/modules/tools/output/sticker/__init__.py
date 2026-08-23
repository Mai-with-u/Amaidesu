"""贴纸事件 helper（Wave 4）

旧 ``StickerHandler`` 是 EventBus 转发器：本身不是渲染器。新架构下：
- Agent 在需要展示贴纸时直接调用 ``StickerHelper.emit_sticker()``
- ``StickerHelper`` 直接 emit ``CoreEvents.OUTPUT_STICKER_COMMAND``
- ``VTSProvider`` 在 setup() 时订阅该事件并加载贴纸到 VTS

迁移策略（与 .omo/drafts/amaidesu-v2-migration.md A 段对齐）:
- ``OUTPUT_STICKER_COMMAND`` 事件**完整保留**（A 段定案：sticker→VTS 单向信号）
- ``StickerCommandPayload`` 不动
- 冷却机制 + sticker_id 生成 + 配置字段 verbatim
"""

from .sticker_helper import StickerHelper

__all__ = ["StickerHelper"]
