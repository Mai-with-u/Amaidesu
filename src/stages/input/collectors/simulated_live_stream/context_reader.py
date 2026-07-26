"""主播上下文读取器

提供两种读取主播上下文的方式（Strategy 模式）：
- ContextServiceReader: 主路径（查询 ContextService 的 ASSISTANT 消息）
- EventHistoryReader: 降级路径（maibot 模式，查询 EventHistory 的 output 事件）

两者实现相同的接口（duck typing），Collector 不感知差异。
"""

# pyright: reportDeprecated=false

import time
from typing import Any, Dict, List, Optional

from src.modules.context.models import ConversationMessage, MessageRole
from src.modules.context.service import ContextService
from src.modules.events.event_history import EventHistoryService
from src.modules.logging import get_logger
from src.stages.input.collectors.simulated_live_stream.config_schema import (
    SimulatorConfigSchema,
)
from src.stages.input.collectors.simulated_live_stream.types import (
    StreamerContextSnapshot,
)


class ContextServiceReader:
    """ContextService pull-mode 读取器（主路径）

    通过 ContextService.get_history() 读取主播发言，
    过滤出 ASSISTANT 角色的消息（由 LLMDecider/AmaidesuDecider 写入）。

    架构合规：只读 pull 模式，不订阅事件，不写入 ContextService。
    """

    def __init__(
        self,
        context_service: ContextService,
        config: SimulatorConfigSchema,
    ):
        self._ctx: ContextService = context_service
        self._config: SimulatorConfigSchema = config
        self._logger: Any = get_logger("ContextServiceReader")
        self._last_seen_message_count: int = 0

    async def get_streamer_context(
        self,
        session_id: str,
        window_size: int = 5,
    ) -> StreamerContextSnapshot:
        """读取指定会话最近的主播上下文快照。"""
        try:
            history = await self._ctx.get_history(session_id, limit=window_size)
            if not history:
                return StreamerContextSnapshot()

            assistant_msgs: List[ConversationMessage] = [m for m in history if m.role == MessageRole.ASSISTANT]
            if not assistant_msgs:
                return StreamerContextSnapshot()

            latest = assistant_msgs[-1]
            recent_emotion: Optional[str] = getattr(latest, "emotion", None)
            last_activity_ts_sec = latest.timestamp
            current_count = len(history)
            has_new = current_count > self._last_seen_message_count
            self._last_seen_message_count = current_count

            return StreamerContextSnapshot(
                recent_messages=[m.content for m in assistant_msgs],
                recent_emotion=recent_emotion,
                last_activity_at_ms=int(last_activity_ts_sec * 1000),
                is_online=(time.time() - last_activity_ts_sec) < self._config.idle_threshold_s,
                has_new_activity_since_last_check=has_new,
            )
        except Exception as error:
            self._logger.error(f"读取主播上下文失败: {error}", exc_info=True)
            return StreamerContextSnapshot()


class EventHistoryReader:
    """EventHistoryService 读取器（maibot 降级路径）

    当 deciders.active == "maibot" 时，ContextService 没有被 LLMDecider
    写入 ASSISTANT 消息（MaiBot 是外部引擎）。改用 EventHistoryService
    读取 output.intent.finished / output.render 事件的 summary 字段。

    接口与 ContextServiceReader 兼容（duck typing）：
    - get_streamer_context 无 session_id 参数（全局读取）
    - 不返回 emotion（EventHistory 不存储 emotion）
    """

    def __init__(
        self,
        event_history_service: EventHistoryService,
        config: SimulatorConfigSchema,
    ):
        self._ehs = event_history_service
        self._config = config
        self._logger = get_logger("EventHistoryReader")
        self._last_seen_event_count: int = 0

    async def get_streamer_context(
        self,
        window_size: int = 5,
    ) -> StreamerContextSnapshot:
        """从 EventHistory 读取主播上下文快照。"""
        try:
            events = self._ehs.query(
                types=["output.intent.finished", "output.render"],
                limit=window_size,
            )
            if not events:
                return StreamerContextSnapshot()

            recent_messages: List[str] = []
            last_ts_sec: float = 0.0
            for ev in events:
                text = _extract_speech_text(ev)
                if text:
                    recent_messages.append(text)
                if ev.timestamp > last_ts_sec:
                    last_ts_sec = ev.timestamp

            if not recent_messages:
                return StreamerContextSnapshot()

            current_count = len(events)
            has_new = current_count > self._last_seen_event_count
            self._last_seen_event_count = current_count

            return StreamerContextSnapshot(
                recent_messages=recent_messages,
                recent_emotion=None,
                last_activity_at_ms=int(last_ts_sec * 1000),
                is_online=(time.time() - last_ts_sec) < self._config.idle_threshold_s,
                has_new_activity_since_last_check=has_new,
            )
        except Exception as e:
            self._logger.error(f"从 EventHistory 读取上下文失败: {e}", exc_info=True)
            return StreamerContextSnapshot()


def _extract_speech_text(record: Any) -> Optional[str]:
    """从 EventRecord 中提取可读的主播发言文本。

    优先用 summary 字段，否则从 data 字典中提取 speech/text。
    """
    if hasattr(record, "summary") and record.summary:
        return record.summary
    if hasattr(record, "data") and isinstance(record.data, dict):
        data: Dict[str, Any] = record.data
        for key in ("speech", "text", "summary"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None
