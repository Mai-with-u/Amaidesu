"""
MessageBuffer - 弹幕聚合缓冲

直播场景下弹幕高频突发，逐条调用 LLM 既慢又贵。MessageBuffer 在一个时间/条数
窗口内聚合多条 NormalizedMessage，由 AmaidesuDecider 的后台循环统一取出一批做决策。

设计要点：
- 记录首条消息的"到达时间"用于时间窗口判定（而非消息自带 timestamp，后者可能由上游设定）
- 标记本批是否包含"强制触发"消息（如醒目留言/上舰），用于跳过节奏采样立即响应
"""

from typing import List

from src.modules.types.base.normalized_message import NormalizedMessage


class MessageBuffer:
    """弹幕聚合缓冲。

    非线程安全，仅在 AmaidesuDecider 的单一 asyncio 事件循环内使用。
    """

    def __init__(self) -> None:
        self._messages: List[NormalizedMessage] = []
        self._force: bool = False
        self._first_arrival_ms: int = 0

    def add(self, message: NormalizedMessage, *, arrival_ms: int, forced: bool = False) -> None:
        """追加一条消息到缓冲。

        Args:
            message: 标准化消息
            arrival_ms: 消息到达时间（Unix 毫秒），用于时间窗口判定
            forced: 该消息是否触发强制响应（由 TimingGate 判定后传入）
        """
        if not self._messages:
            self._first_arrival_ms = arrival_ms
        self._messages.append(message)
        if forced:
            self._force = True

    @property
    def is_empty(self) -> bool:
        """缓冲是否为空"""
        return not self._messages

    @property
    def size(self) -> int:
        """当前缓冲消息数"""
        return len(self._messages)

    @property
    def force(self) -> bool:
        """本批是否包含强制触发消息"""
        return self._force

    @property
    def first_arrival_ms(self) -> int:
        """首条消息到达时间（Unix 毫秒），缓冲为空时为 0"""
        return self._first_arrival_ms

    def drain(self) -> List[NormalizedMessage]:
        """取出并清空缓冲中的全部消息，同时重置强制标志与窗口起点。

        Returns:
            按到达顺序排列的消息列表
        """
        messages = self._messages
        self._messages = []
        self._force = False
        self._first_arrival_ms = 0
        return messages

    @staticmethod
    def render_batch_text(messages: List[NormalizedMessage]) -> str:
        """将一批消息渲染为带昵称/类型前缀的文本块，供 prompt 使用。

        Args:
            messages: 消息列表

        Returns:
            多行文本，每行形如 "[醒目留言] 昵称: 内容"
        """
        type_prefix = {
            "super_chat": "[醒目留言] ",
            "guard": "[上舰] ",
            "gift": "[礼物] ",
            "enter": "[入场] ",
        }
        lines: List[str] = []
        for message in messages:
            nickname = message.user_nickname or message.user_id or "观众"
            prefix = type_prefix.get(message.data_type, "")
            lines.append(f"{prefix}{nickname}: {message.text}")
        return "\n".join(lines)
