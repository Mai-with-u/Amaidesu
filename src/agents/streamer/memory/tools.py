"""主播记忆的观测事件：备忘录变化通知（备忘录工具在阶段 3b 下线，这里只留清空通知）。

阶段 3b 起主播不再写备忘录，决策侧也不再注入它；仪留"下播清空"这一处通知，
让已订阅 \`\`streamer.notebook.changed\`\` 的观察面（WebUI 广播）不至于收不到终态。
工具本身（回忆三件套与备忘录）由 \`\`memory_recall\`\` 取代，见 \`\`recall.py\`\`。
"""

from __future__ import annotations

from typing import Any

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.notebook import StreamerNotebookChangedPayload
from src.modules.logging import get_logger

__all__ = ["emit_notebook_changed"]

logger = get_logger("StreamerMemoryTools")


async def emit_notebook_changed(event_bus: Any, notebook_text: str) -> None:
    """发一条备忘录状态变化通知（观测面用）；未注入总线时直接返回，失败只告警。"""
    if event_bus is None:
        return
    try:
        await event_bus.emit(
            CoreEvents.STREAMER_NOTEBOOK_CHANGED,
            StreamerNotebookChangedPayload(notebook=notebook_text),
            source="streamer_notebook",
        )
    except Exception as exc:  # noqa: BLE001 - 观测事件不阻断写入路径
        logger.warning(f"streamer.notebook.changed 发布失败（已忽略）: {exc}")
