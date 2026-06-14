"""MaiBot Action 建议消息格式定义"""

import time
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


class ActionSuggestionMessage(BaseModel):
    """Amaidesu → MaiBot 的 Action 建议消息"""

    message_type: Literal["action_suggestion"] = "action_suggestion"
    intent_id: str
    original_text: str
    response_text: str
    emotion: str
    suggested_actions: List[Dict[str, Any]]
    source: str = "amaidesu"
    timestamp: float = Field(default_factory=time.time)

    def to_message_base(self):
        """转换为 maim_message MessageBase 格式"""
        from maim_message import BaseMessageInfo, MessageBase, Seg, UserInfo

        user_info = UserInfo(user_id="amaidesu", user_nickname="Amaidesu")
        message_info = BaseMessageInfo(
            message_id=self.intent_id,
            platform="amaidesu",
            user_info=user_info,
            time=self.timestamp,
        )
        seg = Seg(type="action_suggestion", data=self.model_dump_json())
        return MessageBase(message_info=message_info, message_segment=seg)
