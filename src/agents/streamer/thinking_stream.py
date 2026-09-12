"""思考流旁路通道契约与单轮组装器）。

思考流是 best-effort 观测通道：delta 不经过 EventBus、不落库，仅由
dashboard 侧 hub 合帧后经 WebSocket 直推控制台。与事件通道（可靠 +
游标回填）的心智模型不同——断线丢尾部、不回填、不持久化是预期行为。

依赖方向：Protocol 定义在消费侧（Agent），dashboard 的 hub 提供同形
实现（结构化鸭子匹配，无需 import），装配根注入。
"""

from typing import Callable, Protocol

OnDelta = Callable[[str, str], None]
"""LLM 层增量回调形态：(kind, text_delta)，kind ∈ {"reasoning", "content"}。"""


class ThinkingStreamSink(Protocol):
    """思考流旁路出口。

    契约：
    - 同步调用，实现方内部仅缓冲（禁止回调内慢操作）
    - best-effort：不保证送达、断线丢尾部、不持久化、不回填
    - seq 在 (round_id, phase, step) 内单调递增
    """

    def on_thinking_delta(self, *, round_id: str, phase: str, step: int, seq: int, text_delta: str) -> None: ...


class ThinkingStreamContext:
    """单决策轮的思考流组装器。

    持有轮级 seq 计数，把 LLM 层形态的增量回调（kind, text_delta）组装为
    sink 契约形态（round_id, phase, step, seq, text_delta）。只转发
    reasoning 增量，content 增量丢弃（播出面不消费正文流）。

    重试语义：LLM 调用失败重试时各次尝试的思考会连续追加（seq 持续递增），
    真实反映"失败 → 再想"的过程，不做去重。
    """

    def __init__(self, sink: ThinkingStreamSink, round_id: str) -> None:
        self._sink = sink
        self._round_id = round_id
        self._seq = 0

    def callback_for(self, phase: str, step: int) -> OnDelta:
        """构造指定阶段/步骤的 LLM 层增量回调。"""

        def _on_delta(kind: str, text_delta: str) -> None:
            if kind != "reasoning" or not text_delta:
                return
            self._seq += 1
            self._sink.on_thinking_delta(
                round_id=self._round_id,
                phase=phase,
                step=step,
                seq=self._seq,
                text_delta=text_delta,
            )

        return _on_delta


__all__ = [
    "OnDelta",
    "ThinkingStreamSink",
    "ThinkingStreamContext",
]
