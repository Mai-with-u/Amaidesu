"""场次盖章拦截器：给业务事件统一注入当前场次主键。

发布方（采集器/模拟器/Agent）不感知"当前是哪一场"——场次归属是存储与
观察侧的关心点。本拦截器挂在 EventBus 分发链上，凡携带 ``live_session_id``
字段且值为 0（未归属）的 payload，统一盖章为 ``LiveSessionManager.resolve_pk()``
的解析结果：显式场次进行中取其主键，否则保持 0（``resolve_pk()`` 返回 ``None``）。

事件经过拦截器后，下游所有消费者（StorageLedger 落库 / EventHistoryRecorder
记录 / Dashboard WS 广播 / traces）看到同一份已归属的场次 ID——单点注入，
全链一致。无显式场次期间消息仅在事件总线/WebUI/Agent 链路流转，落库路径
依据 0 值由 StorageLedger 跳过。
"""

from typing import Any, Dict, Optional, Tuple

from src.modules.events.interceptors.base import EventInterceptor
from src.modules.logging import get_logger

logger = get_logger("SessionStampInterceptor")

# 需要场次归属的事件域前缀：观众行为流、主播发言、决策轮、阶段状态、工具结果
_STAMPED_PREFIXES: Tuple[str, ...] = (
    "room.message.",
    "streamer.",
    "planner.decision",
    "tool.result.",
)


class SessionStampInterceptor(EventInterceptor):
    """给未归属（live_session_id=0）的业务事件盖章当前场次主键。

    设计约束：
    - 只补缺不覆盖：发布方已显式填写非 0 值时尊重之（如 live.* 生命周期
      事件自带主键——其事件域前缀本就不在作用域内）；
    - ``resolve_pk()`` 返回 ``None``（无显式进行中场次）时 payload 原样放行，
      由落库路径依据 0 值跳过写入；
    - ``resolve_pk()`` 抛异常时同样放行原 payload（归属缺 0 优于阻断直播流）。
    """

    scope_prefixes: Tuple[str, ...] = _STAMPED_PREFIXES

    def __init__(self, session_manager) -> None:  # type: ignore[no-untyped-def]
        self._session_manager = session_manager

    @property
    def name(self) -> str:
        return "session_stamp"

    async def intercept(
        self,
        event_name: str,
        payload: Dict[str, Any],
        source: str,
    ) -> Optional[Dict[str, Any]]:
        # 只处理声明了 live_session_id 字段的 payload（域前缀 + 字段双重过滤，
        # 避免 checkpoint 等无该字段的同域事件被误注入未知键）
        if "live_session_id" not in payload:
            return payload
        if payload["live_session_id"]:
            return payload
        try:
            resolved = await self._session_manager.resolve_pk()
        except Exception:
            # 归属解析失败不阻断事件流；payload 保持 0（未归属），由日志暴露
            return payload
        if resolved is None:
            logger.debug(f"未开场次，不盖章（event={event_name}）；payload 保持 live_session_id=0，落库路径据此跳过")
            return payload
        payload["live_session_id"] = resolved
        return payload


__all__ = ["SessionStampInterceptor"]
