"""
MessageBuffer - 弹幕聚合缓冲

直播场景下弹幕高频突发，逐条调用 LLM 既慢又贵。MessageBuffer 在一个时间/条数
窗口内聚合多条 NormalizedMessage，由 AmaidesuDecider 的后台循环统一取出一批做决策。

设计要点：
- 记录首条消息的"到达时间"用于时间窗口判定（而非消息自带 timestamp，后者可能由上游设定）
- 标记本批是否包含"强制触发"消息（如醒目留言/上舰），用于跳过节奏采样立即响应
- 内置 should_flush() 判定逻辑，支持空窗补偿（idle compensation）：当窗口过期但消息
  数不足时，按空窗时间折算等效消息数，达到阈值则触发，避免慢直播流长时间无响应
"""

from typing import List, Optional, Tuple

from src.modules.types.base.normalized_message import NormalizedMessage
from src.modules.types.message_type import require_message_type


class MessageBuffer:
    """弹幕聚合缓冲。

    非线程安全，仅在 AmaidesuDecider 的单一 asyncio 事件循环内使用。

    可通过构造器注入聚合参数（batch_window_ms / batch_max_size / enable_idle_compensation），
    也可用 should_flush() 方法让调用者统一判定是否触发批次。
    """

    def __init__(
        self,
        *,
        batch_window_ms: int = 3000,
        batch_max_size: int = 20,
        enable_idle_compensation: bool = True,
    ) -> None:
        """初始化缓冲。

        Args:
            batch_window_ms: 聚合时间窗口（毫秒），窗口到期后按策略决定是否刷新
            batch_max_size: 单批最多聚合的消息条数，达到即触发刷新
            enable_idle_compensation: 是否启用空窗补偿（窗口过期时按空窗时间折算等效消息数）
        """
        self._messages: List[NormalizedMessage] = []
        self._force: bool = False
        self._first_arrival_ms: int = 0
        self._last_arrival_ms: int = 0

        self._batch_window_ms = batch_window_ms
        self._batch_max_size = batch_max_size
        self._enable_idle_compensation = enable_idle_compensation

    # ==================== 写入 ====================

    def add(self, message: NormalizedMessage, *, arrival_ms: int, forced: bool = False) -> None:
        """追加一条消息到缓冲。

        Args:
            message: 标准化消息
            arrival_ms: 消息到达时间（Unix 毫秒），用于时间窗口判定
            forced: 该消息是否触发强制响应（由 TimingGate 判定后传入）
        """
        if not self._messages:
            self._first_arrival_ms = arrival_ms
        self._last_arrival_ms = arrival_ms
        self._messages.append(message)
        if forced:
            self._force = True

    def drain(self) -> List[NormalizedMessage]:
        """取出并清空缓冲中的全部消息，同时重置强制标志与窗口起点。

        Returns:
            按到达顺序排列的消息列表
        """
        messages = self._messages
        self._messages = []
        self._force = False
        self._first_arrival_ms = 0
        self._last_arrival_ms = 0
        return messages

    # ==================== 只读属性 ====================

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

    @property
    def last_arrival_ms(self) -> int:
        """最后一条消息到达时间（Unix 毫秒），缓冲为空时为 0"""
        return self._last_arrival_ms

    @property
    def batch_window_ms(self) -> int:
        """聚合时间窗口（毫秒）"""
        return self._batch_window_ms

    @property
    def batch_max_size(self) -> int:
        """单批最多聚合的消息条数"""
        return self._batch_max_size

    @property
    def enable_idle_compensation(self) -> bool:
        """空窗补偿是否启用"""
        return self._enable_idle_compensation

    # ==================== 刷新判定 ====================

    def should_flush(
        self,
        now_ms: int,
        *,
        avg_interval_ms: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """判断是否应该触发批次刷新。

        触发条件（按优先级从高到低，任一满足即触发）::

            1. 本批包含强制触发消息                      → reason="forced"
            2. 缓冲消息数 >= batch_max_size              → reason="batch_full"
            3. 时间窗口未到期                            → 不触发
            4. 时间窗口到期 + 未启用空窗补偿              → reason="window_expired"
            5. 时间窗口到期 + 启用空窗补偿 + 无 avg 数据  → reason="window_expired"（降级）
            6. 时间窗口到期 + 空窗折算 >= batch_max_size   → reason="idle_compensation"
            7. 时间窗口到期 + 空窗折算 < batch_max_size    → 不触发（继续等待）

        空窗补偿折算（参考 MaiBot maisaka/turn_gates.py）::

            idle_equivalent = min(idle_ms / avg_interval_ms, batch_max_size - 1)
            equivalent_count = actual_size + idle_equivalent
            # 折算封顶 batch_max_size-1，确保至少需要 1 条真实消息才能触发

        Args:
            now_ms: 当前时间（Unix 毫秒）
            avg_interval_ms: 最近消息平均间隔（毫秒），用于空窗补偿折算。
                             传 None 时跳过补偿、降级为窗口到期即触发。

        Returns:
            (should_flush, reason) 元组
        """
        # 1. 强制触发
        if self._force:
            return True, "forced"

        # 2. 条数达标
        if self.size >= self._batch_max_size:
            return True, "batch_full"

        # 3. 窗口未到期
        window_expired = self._first_arrival_ms > 0 and (now_ms - self._first_arrival_ms) >= self._batch_window_ms
        if not window_expired:
            return False, "window_not_expired"

        # 4-5. 窗口到期，无补偿或无数据 → 直接触发
        if not self._enable_idle_compensation:
            return True, "window_expired"
        if avg_interval_ms is None or avg_interval_ms <= 0:
            return True, "window_expired"

        # 6-7. 空窗补偿折算
        idle_ms = max(0, now_ms - self._last_arrival_ms)
        threshold = self._batch_max_size
        idle_equivalent = min(
            idle_ms / avg_interval_ms,
            float(max(0, threshold - 1)),
        )
        equivalent_count = self.size + idle_equivalent
        if equivalent_count >= threshold:
            return True, "idle_compensation"

        return False, f"waiting_idle equivalent={equivalent_count:.1f}/{threshold}"

    # ==================== 渲染 ====================

    @staticmethod
    def render_batch_text(messages: List[NormalizedMessage]) -> str:
        """将一批消息渲染为带昵称/类型前缀的文本块，供 prompt 使用。

        Args:
            messages: 消息列表

        Returns:
            多行文本，每行形如 "[醒目留言] 昵称: 内容"
        """
        lines: List[str] = []
        for message in messages:
            spec = require_message_type(message.data_type)
            nickname = message.user_nickname or message.user_id or "观众"
            line = spec.prompt_template.format(text=message.text, nickname=nickname)
            lines.append(line)
        return "\n".join(lines)
