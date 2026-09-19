"""
事件历史记录器（订阅全部事件）

独立的 EventBus 订阅者，订阅 ``#``（全部事件）并统一记录到
EventHistoryService。与 Dashboard / EventBroadcaster 解耦——即使 WebUI
未启用也始终运行；动态族事件（``tool.result.*`` / ``tts.utterance.*`` 等）
无需逐一登记即可被覆盖。

记录规则：
- ``type``：``room.message.*`` 折叠为 ``room.message``，其余取精确事件名
- ``level``：按事件名推导（含 error → error；shutdown/disconnect → warn）
- ``source``：payload 携带 ``live_session_id`` 时优先（按场回看），否则 emit 来源
- ``data``：经开放载荷（``OpenPayload``）原样保留完整字段
"""

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from src.modules.events.event_history import EventRecord, EventHistoryService, infer_event_level
from src.modules.events.event_type_map import ROOM_MESSAGE_TYPE
from src.modules.events.payloads.base import BasePayload, OpenPayload
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.events.event_bus import EventBus

logger = get_logger("EventHistoryRecorder")

# 通配订阅：匹配一切事件名（AMQP topic 风格多层通配）
_SUBSCRIBE_ALL = "#"

# 摘要提取候选字段（按序取第一个非空值）
_SUMMARY_FIELDS = ("message", "content", "detail", "title", "reason", "summary")


def infer_record_type(event_name: str) -> str:
    """事件名 → 记录类型名。

    ``room.message.*`` 折叠为 ``room.message``（前端按此聚合）；其余直通。
    """
    if event_name.startswith("room.message."):
        return ROOM_MESSAGE_TYPE
    return event_name


class EventHistoryRecorder:
    """事件历史记录器 —— 订阅 EventBus 全部事件并记录。

    独立于 Dashboard 运行，确保即使用户不使用 WebUI 也能记录事件。
    由 `main.py` 的 `create_app_components` 创建。
    """

    def __init__(self, event_bus: "EventBus", event_history: EventHistoryService) -> None:
        self.event_bus = event_bus
        self.event_history = event_history

    async def start(self) -> None:
        """订阅全部事件，统一走通用记录通路。"""
        self.event_bus.on(_SUBSCRIBE_ALL, self._on_any_event, model_class=OpenPayload)
        logger.info("事件历史记录器已启动（订阅 # 全部事件）")

    async def stop(self) -> None:
        """取消订阅。"""
        self.event_bus.off(_SUBSCRIBE_ALL, self._on_any_event)

    async def _on_any_event(self, event_name: str, data: BasePayload, source: str) -> None:
        try:
            dict_data = data.model_dump() if isinstance(data, BaseModel) else {}
            self.event_history.record(
                EventRecord(
                    id=dict_data.get("id", ""),
                    type=infer_record_type(event_name),
                    event_name=event_name,
                    timestamp_ms=self._payload_timestamp_ms(dict_data),
                    level=infer_event_level(event_name),
                    source=str(dict_data.get("live_session_id", "") or source),
                    summary=self._build_summary(event_name, dict_data),
                    data=dict_data,
                )
            )
        except Exception as e:
            logger.warning(f"记录 {event_name} 事件失败: {e}")

    @staticmethod
    def _payload_timestamp_ms(data: Any) -> int:
        """从载荷提取毫秒时刻；载荷未携带时退回当前时刻（now_ms）。"""
        value = data.get("timestamp_ms") if isinstance(data, dict) else None
        return int(value) if isinstance(value, (int, float)) else now_ms()

    @staticmethod
    def _build_summary(event_name: str, dict_data: dict) -> str:
        """从载荷常见文本字段提取一行摘要；无候选时退回事件名。"""
        for field in _SUMMARY_FIELDS:
            value = dict_data.get(field)
            if isinstance(value, str) and value:
                prefix = f"[{dict_data['message_type']}] " if field == "content" and "message_type" in dict_data else ""
                return f"{prefix}{value}"[:200]
        return event_name
