"""ProactiveTrigger - 主播主动发言的纯规则触发判定组件。

职责边界（重要）
----------------
本组件**只**回答"现在该不该主动开口"以及"为什么"，返回触发原因字符串
（``"external"`` / ``"schedule"`` / ``"cold"``）或 ``None``。

**不**负责生成发言内容 —— 话题文本由 ``Planner`` 在收到 ``proactive=True``
标志后从 ``room_state`` 快照的 ``topic_summary`` 字段自行决定；本组件不持有
prompt 模板、不调用 LLM、不预设话题 fallback 链。

设计要点（沿用 ``RoomStateLoop`` 的纯规则风格）
----------------------------------------------
- **无 I/O、无 LLM 调用、无 EventBus、无 asyncio**：所有时间通过参数注入
  （``now_ms``），便于确定性测试，禁止 ``time.sleep``。
- **触发优先级**：``external > schedule > cold``（同一 tick 多触发条件同时
  满足时只取优先级最高的一个）。
- **频率限制**：
    - ``min_interval_ms``（防接龙，对照 ``room_state.last_speech_ms``；
      ``None`` 视为 0/从未发言，恒通过）。
    - ``max_per_hour``（内部 ``collections.deque`` 维护过去 1 小时内的
      触发时间戳，``record_trigger`` 时追加、``should_trigger`` 时按需修剪）。
- **话题缺失优雅降级**：``topic_required=True`` 且 ``topic_summary`` 为空
  时返回 ``None``（**不**生成兜底话题，避免幻觉）。

使用方契约（AmaidesuDecider 接入）
---------------------------------
- 每个 flush tick 调用 :meth:`should_trigger` 询问；
- 实际发布 Intent 成功后再调用 :meth:`record_trigger` 更新内部状态
  （仅在确认要说话之后才调用，**不**做 try/finally 兜底）。
"""

from collections import deque
from typing import Any


__all__ = ["ProactiveTrigger"]


# ---------------------------------------------------------------------------
# 默认配置（与 .omo/plans/proactive-speech.md Task 6 的 AmaidesuConfig 默认值对齐）
# ---------------------------------------------------------------------------

_DEFAULT_ENABLED: bool = True
_DEFAULT_COLD_TIMEOUT_MS: int = 45_000
_DEFAULT_MIN_INTERVAL_MS: int = 120_000
_DEFAULT_SCHEDULE_INTERVAL_MS: int = 300_000  # 0 表示关闭定时触发
_DEFAULT_SCHEDULE_ONLY_COLD: bool = True
_DEFAULT_MAX_PER_HOUR: int = 6
_DEFAULT_TOPIC_REQUIRED: bool = True

# hourly 窗口宽度：3 600 秒 = 3 600 000 毫秒
_HOURLY_WINDOW_MS: int = 3_600_000


def _cfg(config: Any, key: str, default: Any) -> Any:
    """从配置对象读取字段值。

    对齐 :mod:`room_state_loop` 的同名辅助：dict 用 ``.get``，其他对象
    （如 Pydantic 模型）用 ``getattr`` 兜底。便于上层构造一个**精简 sub-dict**
    传给本组件（避免触发器读全量 AmaidesuConfig 的噪声字段）。
    """
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


class ProactiveTrigger:
    """主播主动发言的纯规则触发判定器。

    本类**不**订阅事件、不持有 asyncio 任务、不输出日志（由调用方决定是否
    记录触发决策）。状态仅有两项：上次主动触发时刻 + hourly 窗口触发历史，
    均为进程内内存态，重启即重置。
    """

    def __init__(self, config: Any) -> None:
        # 配置读取
        self._enabled: bool = _cfg(config, "enabled", _DEFAULT_ENABLED)
        self._cold_timeout_ms: int = _cfg(config, "cold_timeout_ms", _DEFAULT_COLD_TIMEOUT_MS)
        self._min_interval_ms: int = _cfg(config, "min_interval_ms", _DEFAULT_MIN_INTERVAL_MS)
        self._schedule_interval_ms: int = _cfg(config, "schedule_interval_ms", _DEFAULT_SCHEDULE_INTERVAL_MS)
        self._schedule_only_cold: bool = _cfg(config, "schedule_only_cold", _DEFAULT_SCHEDULE_ONLY_COLD)
        self._max_per_hour: int = _cfg(config, "max_per_hour", _DEFAULT_MAX_PER_HOUR)
        self._topic_required: bool = _cfg(config, "topic_required", _DEFAULT_TOPIC_REQUIRED)

        # 运行期状态
        # 上次主动触发时刻（None = 尚未触发过）。仅 schedule 判定读取；
        # 不参与公共前置条件（公共前置用 room_state.last_speech_ms 防接龙）。
        self._last_trigger_ms: int | None = None
        # hourly 窗口内触发时间戳（升序追加）。修剪仅在 should_trigger() 中
        # 进行，避免 record_trigger 与 should_trigger 重复清理。
        self._trigger_history: deque[int] = deque()

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def should_trigger(
        self,
        room_state: Any,
        now_ms: int,
        external_pending: bool = False,
    ) -> str | None:
        """判定当前 tick 是否应触发主动发言。

        优先级顺序：``external > schedule > cold``。
        任一公共前置条件不满足 → 返回 ``None``（不区分原因，由调用方自行记录）。

        Args:
            room_state: 直播间态势对象（duck-typed；需具备 ``last_speech_ms``
                属性 / ``is_cold(cold_timeout_ms, now_ms=...)`` 方法 /
                ``get_snapshot(now_ms=...)`` 方法）。
            now_ms: 当前时刻（Unix 毫秒）。``room_state.is_cold`` /
                ``room_state.get_snapshot`` 等调用会透传此参数，便于测试
                注入确定性时间戳。
            external_pending: 是否有外部指令（如 WebUI API）待消费。
                **一次性**：调用方消费后应置 ``False``（通常下一 tick 即可，
                本组件不维护该标志）。

        Returns:
            ``"external"`` / ``"schedule"`` / ``"cold"`` —— 表示应触发并给出原因；
            ``None`` —— 表示不触发。
        """
        # 公共前置①：总开关
        if not self._enabled:
            return None

        # 公共前置②：min_interval（防接龙）
        # last_speech_ms 为 None 视为 0/从未发言 → 恒通过（首次可触发）。
        last_speech_ms = getattr(room_state, "last_speech_ms", None)
        if last_speech_ms is not None and (now_ms - last_speech_ms) < self._min_interval_ms:
            return None

        # 公共前置③：max_per_hour（修剪过期后看 deque 长度）
        self._prune_history(now_ms)
        if len(self._trigger_history) >= self._max_per_hour:
            return None

        # 公共前置④：topic_required（话题缺失 → no-op）
        if self._topic_required:
            snapshot = room_state.get_snapshot(now_ms=now_ms)
            topic = getattr(snapshot, "topic_summary", "") or ""
            if not topic.strip():
                return None

        # ① external：最高优先级，无其他附加条件
        if external_pending:
            return "external"

        # ② schedule：定时间隔到点 + （always 或 当前冷场）
        if self._schedule_interval_ms > 0 and (
            self._last_trigger_ms is None or (now_ms - self._last_trigger_ms) >= self._schedule_interval_ms
        ):
            if not self._schedule_only_cold or room_state.is_cold(self._cold_timeout_ms, now_ms=now_ms):
                return "schedule"

        # ③ cold：房间处于冷场
        if room_state.is_cold(self._cold_timeout_ms, now_ms=now_ms):
            return "cold"

        return None

    def record_trigger(self, reason: str, now_ms: int) -> None:
        """记录一次主动触发（由 AmaidesuDecider 在发布 Intent 成功后调用）。

        更新 ``_last_trigger_ms`` 并在 hourly 窗口中追加 ``now_ms``。
        ``reason`` 仅由调用方用于日志/统计；本组件内部行为对所有 reason 一致。

        Args:
            reason: 触发原因字符串（``"external"`` / ``"schedule"`` / ``"cold"``）。
            now_ms: 触发时刻（Unix 毫秒）。
        """
        del reason  # 接口完整性保留；当前实现不分原因处理
        self._last_trigger_ms = now_ms
        self._trigger_history.append(now_ms)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _prune_history(self, now_ms: int) -> None:
        """丢弃 hourly 窗口外的旧触发记录（``now_ms - _HOURLY_WINDOW_MS`` 之前）。

        仅在 :meth:`should_trigger` 中调用，O(k) k 为过期条目数（通常很小）。
        """
        cutoff = now_ms - _HOURLY_WINDOW_MS
        while self._trigger_history and self._trigger_history[0] < cutoff:
            self._trigger_history.popleft()
