"""
ResponseParser - 解析MessageBase为Intent

职责:
- 从MessageBase中提取文本内容
- 从MessageBase中解析表情和热键
- 构建Intent对象
"""

import re
from typing import Optional, TYPE_CHECKING, Dict

from maim_message import MessageBase
from .intent import Intent, IntentAction, ActionType, EmotionType

if TYPE_CHECKING:
    from .intent import Intent


class ResponseParser:
    """
    响应解析器(Layer 4: 表现理解层)

    职责:
    - 解析MessageBase的文本内容
    - 提取表情符号和热键
    - 构建Intent对象

    使用示例:
        ```python
        parser = ResponseParser()
        intent = parser.parse(message_base)
        ```
    """

    def __init__(self, emotion_keywords: Optional[Dict[str, EmotionType]] = None):
        """
        初始化响应解析器

        Args:
            emotion_keywords: 情感关键词字典(可选)
                             示例: {"开心": EmotionType.HAPPY, "难过": EmotionType.SAD}
        """
        self.emotion_keywords = emotion_keywords or {
            "开心": EmotionType.HAPPY,
            "高兴": EmotionType.HAPPY,
            "哈哈": EmotionType.HAPPY,
            "笑": EmotionType.HAPPY,
            "难过": EmotionType.SAD,
            "悲伤": EmotionType.SAD,
            "哭": EmotionType.SAD,
            "生气": EmotionType.ANGRY,
            "愤怒": EmotionType.ANGRY,
            "生气了": EmotionType.ANGRY,
            "惊讶": EmotionType.SURPRISED,
            "惊喜": EmotionType.SURPRISED,
            "意外": EmotionType.SURPRISED,
            "喜欢": EmotionType.LOVE,
            "爱": EmotionType.LOVE,
        }

    def parse(self, message: MessageBase) -> Intent:
        """
        解析MessageBase为Intent

        Args:
            message: MessageBase对象

        Returns:
            Intent对象
        """
        # 提取文本内容
        text = self._extract_text(message)
        if not text:
            text = ""

        # 分析情感
        emotion = self._analyze_emotion(text)

        # 提取动作(表情、热键等)
        actions = self._extract_actions(message)

        # 构建Intent
        return Intent(
            original_text=text,
            emotion=emotion,
            response_text=text,
            actions=actions,
            metadata=self._extract_metadata(message),
        )

    def _extract_text(self, message: MessageBase) -> str:
        """
        提取文本内容

        Args:
            message: MessageBase对象

        Returns:
            文本内容
        """
        if not message.message_segment:
            return ""

        # 尝试从message_segment.data获取文本
        if hasattr(message.message_segment, "data"):
            data = message.message_segment.data

            # 处理不同格式的数据
            if isinstance(data, str):
                return data
            elif isinstance(data, list):
                # 处理seglist
                text_parts = []
                for seg in data:
                    if hasattr(seg, "data") and isinstance(seg.data, str):
                        text_parts.append(seg.data)
                return " ".join(text_parts)

        return ""

    def _analyze_emotion(self, text: str) -> EmotionType:
        """
        分析文本情感

        Args:
            text: 文本内容

        Returns:
            情感类型
        """
        if not text:
            return EmotionType.NEUTRAL

        # 遍历情感关键词
        for keyword, emotion in self.emotion_keywords.items():
            if keyword in text:
                return emotion

        return EmotionType.NEUTRAL

    def _extract_actions(self, message: MessageBase) -> list[IntentAction]:
        """
        提取动作(表情、热键等)

        Args:
            message: MessageBase对象

        Returns:
            动作列表
        """
        actions = []

        if not message.message_info:
            return actions

        # 提取表情符号(使用正则表达式)
        emoji_pattern = r"[\U0001F600-\U0001F64F]|[\U0001F300-\U0001F5FF]|[\U0001F680-\U0001F6FF]|[\U0001F1A0-\U0001F1FF]|[\U0002600-\U00027BF]|[\U0001F200-\U0001F200]"
        text = self._extract_text(message)

        emojis = re.findall(emoji_pattern, text)
        for emoji in emojis:
            action = IntentAction(
                type=ActionType.EMOJI,
                params={"emoji": emoji},
                priority=50,  # 表情优先级较高
            )
            actions.append(action)

        # 提取热键命令(简单的模式)
        hotkey_patterns = {
            r"热键\s*[:：:]\s*(\w+)": ActionType.HOTKEY,
            r"按下\s*[:：:]\s*(\w+)": ActionType.HOTKEY,
        }

        for pattern, action_type in hotkey_patterns.items():
            match = re.search(pattern, text)
            if match:
                hotkey = match.group(1)
                action = IntentAction(
                    type=action_type,
                    params={"hotkey": hotkey},
                    priority=80,
                )
                actions.append(action)

        return actions

    def _extract_metadata(self, message: MessageBase) -> dict:
        """
        提取元数据

        Args:
            message: MessageBase对象

        Returns:
            元数据字典
        """
        metadata = {}

        if message.message_info:
            if message.message_info.sender:
                metadata["user_id"] = message.message_info.sender.user_id
                metadata["user_nickname"] = message.message_info.sender.nickname

            metadata["message_id"] = message.message_info.message_id
            metadata["platform"] = message.message_info.platform
            metadata["timestamp"] = message.message_info.timestamp

        if message.message_segment:
            metadata["segment_type"] = message.message_segment.type

        return metadata
