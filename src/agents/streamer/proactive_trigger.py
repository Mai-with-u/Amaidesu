"""ProactiveTrigger - 主播主动发言的纯规则触发判定组件（Wave 6 verbatim 移植）

职责边界（重要）
----------------
本组件**只**回答"现在该不该主动开口"以及"为什么"，返回触发原因字符串
（``"external"`` / ``"agenda"`` / ``"schedule"`` / ``"cold"``）或 ``None``。

**不**负责生成发言内容 —— 话题文本由 ``Planner`` 在收到 ``proactive=True``
标志后从 ``room_state`` 快照的 ``topic_summary`` 字段（或在 Agenda 触发时从
当前 Agenda 环节任务描述）自行决定；本组件不持有 prompt 模板、不调用 LLM、
不预设话题 fallback 链。

设计要点（沿用 ``RoomState`` 的纯规则风格，verbatim 移植自
``stages/decision/deciders/amaidesu/proactive_trigger.py``）
----------------------------------------------
- **无 I/O、无 LLM 调用、无 EventBus、无 asyncio**：所有时间通过参数注入
  （``now_ms``），便于确定性测试，禁止 ``time.sleep``。
- **触发优先级**：``external > agenda > schedule > cold``（同一 tick 多
  触发条件同时满足时只取优先级最高的一个）。
- **公共频率限制**（对 ``external`` / ``schedule`` / ``cold`` 生效）：
    - ``min_interval_ms``（防接龙，对照 ``room_state.last_speech_ms``；
      ``None`` 视为 0/从未发言，恒通过）。
    - ``max_per_hour``（内部 ``collections.deque`` 维护过去 1 小时内的
      触发时间戳，``record_trigger`` 时追加、``should_trigger`` 时按需修剪）。
- **话题缺失优雅降级**：``topic_required=True`` 且 ``topic_summary`` 为空
  时返回 ``None``（**不**生成兜底话题，避免幻觉）。**例外**：
  ``agenda`` 触发源不依赖 ``topic_summary``——Agenda 本身提供话题（由当前
  环节任务描述驱动），因此 agenda 分支绕过 ``topic_required`` 约束。
- **触发源独立性**：``external`` / ``schedule`` / ``cold`` 三分支受
  ``topic_required`` 约束；``agenda`` 分支不受其约束（见上）。

**Agenda 触发源（agenda）的限流独立性**
------------------------------------
节目单是**内容推进**而非**救场**——主播应按环节节奏持续发言，不应被为
"防接龙/冷场救场"设计的低频限流卡死。具体规则：

- ``agenda_pending=True``（环节切换即时信号）：仅受总开关约束，立即触发
  ``"agenda"``，新环节开场白必须马上说，不等任何间隔。
- ``agenda_ready=True``（环节内持续发言信号）：绕过公共 ``min_interval_ms``
  / ``max_per_hour`` / ``topic_required`` 三道前置，使用独立的
  ``agenda_speech_interval_ms``（默认 3 秒防抖，对照 ``last_speech_ms``）
  控制发言节奏。观众弹幕回应仍会刷新 ``last_speech_ms``，自然推迟下一句
  节目单发言（刚回完观众就推话题很赶，这个联动保留）。
- 仅受总开关约束（``enabled=False`` 时不触发任何 agenda）。

使用方契约（StreamerAgent 接入）
---------------------------------
- 每个 flush tick 调用 :meth:`should_trigger` 询问；
- 实际发布发言成功后再调用 :meth:`record_trigger` 更新内部状态
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
# Agenda 环节内两次主动发言最小间隔（毫秒）。绕过通用 min_interval_ms（120s）
# 防接龙限流，使用防抖级别节奏（3 秒）保证大纲内容能持续推进；当前 LLM
# 生成耗时（~10s）远大于此值，实际开口节奏由 LLM 速度主导。
_DEFAULT_AGENDA_SPEECH_INTERVAL_MS: int = 3_000

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
        # Agenda 环节内持续发言间隔（毫秒）。仅 agenda 触发源使用，绕过通用
        # min_interval_ms / max_per_hour / topic_required 限流。
        self._agenda_speech_interval_ms: int = _cfg(
            config, "agenda_speech_interval_ms", _DEFAULT_AGENDA_SPEECH_INTERVAL_MS
        )

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
        agenda_pending: bool = False,
        agenda_ready: bool = False,
    ) -> str | None:
        """判定当前 tick 是否应触发主动发言。

        优先级顺序：``external > outline > schedule > cold``。

        公共前置条件（仅对 ``external`` / ``schedule`` / ``cold`` 生效）：
        - ``enabled=False`` → 永不触发（含 outline）
        - ``min_interval_ms`` 防接龙（对照 ``room_state.last_speech_ms``）
        - ``max_per_hour`` hourly 滑窗
        - ``topic_required`` 话题缺失则跳过

        任一公共前置条件不满足 → 返回 ``None``（不区分原因，由调用方自行记录）。

        触发源对 ``topic_required`` 的依赖差异：
        - ``external`` / ``schedule`` / ``cold`` 均受 ``topic_required``
          约束（外部 API 用户**或**定时/冷场发言需要话题支撑）。
        - ``outline`` **不**受 ``topic_required`` 约束（Agenda 本身提供话题，
          来自当前环节任务描述，不依赖 ``room_state.topic_summary``）。

        Agenda 触发源（outline）的限流独立性
        ---------------------------------
        节目单是**内容推进**而非**救场**——主播应按环节节奏持续发言，不应
        被为"防接龙/冷场救场"设计的低频限流卡死：

        - ``outline_pending=True``（环节切换即时信号）：仅受总开关约束，
          立即触发 ``"outline"``，新环节开场白必须马上说，不等任何间隔。
        - ``outline_ready=True``（环节内持续发言信号）：绕过公共
          ``min_interval_ms`` / ``max_per_hour`` / ``topic_required`` 三道
          前置，使用独立的 ``outline_speech_interval_ms``（默认 3 秒防抖，
          对照 ``last_speech_ms``）控制发言节奏。观众弹幕回应仍会刷新
          ``last_speech_ms``，自然推迟下一句大纲发言（刚回完观众就推话题
          很赶，这个联动保留）。

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
            agenda_pending: Agenda 环节切换即时信号（如 ``on_advance`` 回调
                触发）。``True`` 时仅受总开关约束，立即返回 ``"agenda"``，
                不等任何间隔（即使 ``last_speech_ms`` 刚刚刷新）。
            agenda_ready: Agenda 是否已激活且具有当前环节。``True`` 时绕过
                ``min_interval_ms`` / ``max_per_hour`` / ``topic_required``
                三道公共前置，使用 ``agenda_speech_interval_ms`` 控制节奏
                （对照 ``last_speech_ms``）。优先级低于 ``external``，高于
                ``schedule`` / ``cold``。

        Returns:
            ``"external"`` / ``"agenda"`` / ``"schedule"`` / ``"cold"`` ——
            表示应触发并给出原因；``None`` —— 表示不触发。
        """
        # 总开关（所有源适用，含 outline）
        if not self._enabled:
            return None

        # agenda_pending：环节切换即时信号——只受总开关约束，立即触发
        # （新环节开场白必须马上说，不等任何间隔）
        if agenda_pending:
            return "agenda"

        last_speech_ms = getattr(room_state, "last_speech_ms", None)

        # external：最高优先级（受 min_interval/max_per_hour/topic_required，既有行为）。
        # ——把原公共前置②③的检查移入 external 分支内（行为等价）
        if external_pending:
            if last_speech_ms is not None and (now_ms - last_speech_ms) < self._min_interval_ms:
                return None
            self._prune_history(now_ms)
            if len(self._trigger_history) >= self._max_per_hour:
                return None
            if not self._topic_available(room_state, now_ms):
                return None
            return "external"

        # agenda：环节内持续发言——内容推进不是救场，绕过
        # min_interval/max_per_hour/topic_required，用自己的发言节奏
        # agenda_speech_interval_ms（对照 last_speech_ms）
        if agenda_ready:
            if last_speech_ms is not None and (now_ms - last_speech_ms) < self._agenda_speech_interval_ms:
                return None
            return "agenda"

        # 公共前置（schedule / cold 专用，行为不变）
        if last_speech_ms is not None and (now_ms - last_speech_ms) < self._min_interval_ms:
            return None
        self._prune_history(now_ms)
        if len(self._trigger_history) >= self._max_per_hour:
            return None
        if not self._topic_available(room_state, now_ms):
            return None

        # schedule：定时间隔到点 + （always 或 当前冷场）
        if self._schedule_interval_ms > 0 and (
            self._last_trigger_ms is None or (now_ms - self._last_trigger_ms) >= self._schedule_interval_ms
        ):
            if not self._schedule_only_cold or room_state.is_cold(self._cold_timeout_ms, now_ms=now_ms):
                return "schedule"

        # cold：房间处于冷场
        if room_state.is_cold(self._cold_timeout_ms, now_ms=now_ms):
            return "cold"

        return None

    def record_trigger(self, reason: str, now_ms: int) -> None:
        """记录一次主动触发（由 StreamerAgent 在发布发言成功后调用）。

        更新 ``_last_trigger_ms`` 并在 hourly 窗口中追加 ``now_ms``。
        ``reason`` 仅由调用方用于日志/统计；本组件内部行为对所有 reason 一致。

        Args:
            reason: 触发原因字符串（``"external"`` / ``"outline"`` /
                ``"schedule"`` / ``"cold"``）。
            now_ms: 触发时刻（Unix 毫秒）。
        """
        del reason  # 接口完整性保留；当前实现不分原因处理
        self._last_trigger_ms = now_ms
        self._trigger_history.append(now_ms)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _topic_available(self, room_state: Any, now_ms: int) -> bool:
        """判定当前话题是否满足 ``topic_required`` 约束。

        - ``topic_required=False``：恒返回 ``True``（不约束）。
        - ``topic_required=True``：从 ``room_state.get_snapshot(now_ms=...)``
          读取 ``topic_summary``，strip 后非空 → ``True``，否则 ``False``。

        提取为辅助方法以避免 :meth:`should_trigger` 中重复内联实现。
        """
        if not self._topic_required:
            return True
        snapshot = room_state.get_snapshot(now_ms=now_ms)
        topic = getattr(snapshot, "topic_summary", "") or ""
        return bool(topic.strip())

    def _prune_history(self, now_ms: int) -> None:
        """丢弃 hourly 窗口外的旧触发记录（``now_ms - _HOURLY_WINDOW_MS`` 之前）。

        仅在 :meth:`should_trigger` 中调用，O(k) k 为过期条目数（通常很小）。
        """
        cutoff = now_ms - _HOURLY_WINDOW_MS
        while self._trigger_history and self._trigger_history[0] < cutoff:
            self._trigger_history.popleft()
