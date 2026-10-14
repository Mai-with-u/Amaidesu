"""存储域测试辅助：构造事件载荷的便利工厂。"""

from __future__ import annotations

from typing import Any, Optional

from src.modules.llm.bootstrap import _ResolvedModel, _ResolvedProfile
from src.modules.llm.engine import LLMManager
from src.modules.events.payloads.room import (
    GiftInfo,
    GuardInfo,
    RoomMessagePayload,
    RoomMessageUser,
    SuperChatInfo,
)
from src.modules.time_utils import now_ms

# 测试默认平台标识（B 站链路）
DEFAULT_PLATFORM = "bilibili"


def make_room_message(
    *,
    message_type: str = "danmaku",
    live_session_id: int = 0,
    message_id: str = "",
    platform: str = DEFAULT_PLATFORM,
    user: Optional[RoomMessageUser] = None,
    content: str = "",
    gift_name: str = "小星星",
    gift_count: int = 1,
    unit_price: int = 1000,
    sc_amount: float = 50.0,
    guard_level: int = 3,
    simulated: bool = False,
    timestamp_ms: Optional[int] = None,
) -> RoomMessagePayload:
    """构造 ``RoomMessagePayload``：避免测试内重复样板。

    付费消息默认带金额载荷（金瓜子口径：``unit_price`` 标价单价；
    SC 金额 ``sc_amount`` 按元入参、×1000 转金瓜子，与采集器口径一致）。
    ``live_session_id`` 默认 0（未归属），由场次盖章拦截器或测试内显式指定。
    """
    if user is None:
        user = RoomMessageUser(id="tester", name="测试观众")

    common: dict = {
        "live_session_id": live_session_id,
        "message_id": message_id,
        "message_type": message_type,  # type: ignore[arg-type]
        "platform": platform,
        "user": user,
        "content": content,
        "simulated": simulated,
    }
    if timestamp_ms is not None:
        common["timestamp_ms"] = timestamp_ms
    else:
        common["timestamp_ms"] = now_ms()

    if message_type == "gift":
        common["gift"] = GiftInfo(
            name=gift_name,
            count=gift_count,
            unit_price=unit_price,
            total_price=unit_price * gift_count,
            paid_price=unit_price * gift_count,
            currency="bilibili_gold_coin",
        )
    elif message_type == "super_chat":
        common["sc"] = SuperChatInfo(
            total_price=int(sc_amount * 1000),
            currency="bilibili_gold_coin",
        )
    elif message_type == "guard":
        common["guard"] = GuardInfo(
            guard_level=guard_level,
            guard_num=1,
            guard_unit="月",
            total_price=138_000,
            currency="bilibili_gold_coin",
        )

    return RoomMessagePayload(**common)


def make_llm_manager(client: Any, *, llm_repo: Any = None, **kwargs: Any) -> LLMManager:
    """绕过 setup() 的真实 provider 装配：planner 档位单模型 glm-4.7，走注入的客户端替身。"""
    manager = LLMManager(llm_repo=llm_repo, **kwargs)
    manager._provider_clients["fake"] = client
    manager._providers["fake"] = ({"name": "fake", "client_type": "openai"}, client)
    manager._models["glm-4.7"] = ({"name": "glm-4.7", "model_identifier": "glm-4.7", "api_provider": "fake"}, "fake")
    manager._profiles["planner"] = _ResolvedProfile(
        profile_name="planner",
        slow_threshold_ms=15_000,
        selection_strategy="sequential",
        seed=0,
        temperature=0.3,
        models=[_ResolvedModel(model_name="glm-4.7", model_identifier="glm-4.7", provider_name="fake")],
    )
    manager._model_call_counts["planner"] = {}
    return manager


__all__ = ["DEFAULT_PLATFORM", "make_llm_manager", "make_room_message"]
