"""ProactiveTrigger - 主播主动发言的纯规则触发判定组件

职责边界（重要）
----------------
本组件**只**回答"现在该不该主动开口"以及"为什么"，返回触发原因枚举
（:class:`TriggerKind` 的 ``EXTERNAL`` / ``RUNDOWN`` / ``GAME`` /
``SCHEDULE`` / ``COLD`` 等成员）或 ``None``。

**不**负责生成发言内容 —— 话题文本由 ``Planner`` 在收到 ``proactive=True``
标志后从 ``room_state`` 快照的 ``topic_summary`` 字段（或在流程单触发时从
当前环节任务描述）自行决定；本组件不持有 prompt 模板、不调用 LLM、
不预设话题 fallback 链。

设计要点（纯规则风格）
----------------------------------------------
- **无 I/O、无 LLM 调用、无 EventBus、无 asyncio**：所有时间通过参数注入
  （``now_ms``），便于确定性测试，禁止 ``time.sleep``。
- **触发优先级**：``reminder > rundown_pending > game_pending >
  external > rundown_overdue > rundown_ready > schedule > cold``（同一 tick
  多触发条件同时满足时只取优先级最高的一个；reminder/各 rundown/game 分支各自
  绕过部分公共前置，见下文）。
- **公共频率限制**（对 ``external`` / ``schedule`` / ``cold`` 生效）：
    - ``min_interval_ms``（防接龙，对照 ``room_state.last_speech_ms``；
      ``None`` 视为 0/从未发言，恒通过）。
    - ``max_per_hour``（内部 ``collections.deque`` 维护过去 1 小时内的
      触发时间戳，``record_trigger`` 时追加、``should_trigger`` 时按需修剪）。
- **话题缺失优雅降级**：``topic_required=True`` 且 ``topic_summary`` 为空
  时返回 ``None``（**不**生成兜底话题，避免幻觉）。**例外**：
  ``rundown`` 触发源不依赖 ``topic_summary``——流程单本身提供话题（由当前
  环节任务描述驱动），因此 rundown 分支绕过 ``topic_required`` 约束。
- **触发源独立性**：``external`` / ``schedule`` / ``cold`` 三分支受
  ``topic_required`` 约束；``rundown`` 分支不受其约束（见上）。

**流程单触发源（rundown）的限流独立性**
------------------------------------
流程单是**内容推进**而非**救场**——主播应按环节节奏持续发言，不应被为
"防接龙/冷场救场"设计的低频限流卡死。具体规则：

- ``rundown_pending=True``（环节切换即时信号）：仅受总开关约束，立即触发
  ``"rundown"``，新环节开场白必须马上说，不等任何间隔。
- ``rundown_overdue=True``（环节超时提醒信号）：环节停留超出预期后的
  唤醒闹钟——绕过公共三道前置，受独立双重冷却：``rundown_overdue_interval_ms``
  （默认 5 分钟，内部状态对照）+ ``rundown_speech_interval_ms`` 防抖（对照
  ``last_speech_ms``，刚发过言不立即提醒）。Agent 被唤醒后自行决定推进或
  继续——闹钟只提醒不执法。
- ``rundown_ready=True``（环节内持续产出信号）：环节供能期间持续开窗给
  主播开口机会——绕过公共三道前置（环节任务描述即话题），不对照
  ``last_speech_ms`` 防抖（发言节奏的防抖已移到发言放行侧执行，见
  ``DecisionRoundExecutor._pace_speech_release``）。这只供给机会：
  说不说、说什么由 Agent 按本窗新输入自定，发言质量由 ``is_novel`` 自检
  与台词重合度闸把关，闸打回即本轮沉默。
- **主动沉默冷却**：``rundown_ready`` 只给机会，她可能看完觉得没什么新鲜事、选择不说。
  沉默轮不刷新 ``last_speech_ms``，若只按开口时刻判定，下一个 tick 立刻又满足条件，
  沉默期间每个 tick 都会空跑一轮大模型。:meth:`ProactiveTrigger.record_silent_round`
  记下这一刻，此后 ``rundown_silent_cooldown_ms``（杂谈档用更短的
  ``rundown_silent_cooldown_talk_ms``）内 ``rundown_ready`` 不再触发。
- **给弹幕让路**：观众弹幕已进缓冲、还在攒批等放行时（``viewer_waiting_since_ms``
  非空），``rundown_ready`` 先让路，免得她在观众提问和回答之间插一句无关旁白；
  最多让 ``rundown_ready_yield_max_ms``，缓冲异常滞留也不会永久压住主动发言。
- 仅受总开关约束（``enabled=False`` 时不触发任何 rundown）。

**游戏待定夺触发源（game_pending）的限流独立性**
------------------------------------
游戏 Agent 主动上报（``game.report``，交付总结/升级决策）往往停在选项处
等主播定夺——不触发则剧情无限卡住。这与流程单同理：**内容推进**而非救场，
不应被"防接龙/冷场救场"的低频限流卡死。``game_pending=True`` 仅受总开关
约束，立即返回 ``TriggerKind.GAME``，绕过 ``min_interval_ms`` / ``max_per_hour`` /
``topic_required`` 三道前置。一次性信号由调用方（StreamerAgent）消费后复位。

**运营提醒催醒触发源（reminder_pending）的限流独立性**
------------------------------------
运营递话（后台留言给主播）是**必达**指令而非自主找话说——总开关、
每小时上限、话题要求三道闸是"自主找话说"的规矩，不辖运营递话；
唯一保留的约束是 ``min_interval_ms`` 防接龙（刚开口就插提醒没有意义，
等下一个 tick 自然送达）。``reminder_pending=True`` 仅查防接龙最小间隔，
通过即返回 ``"reminder"``。一次性信号由调用方（StreamerAgent）消费后复位。

使用方契约（StreamerAgent 接入）
---------------------------------
- 每个 flush tick 调用 :meth:`should_trigger` 询问；
- 实际发布发言成功后再调用 :meth:`record_trigger` 更新内部状态
  （仅在确认要说话之后才调用，**不**做 try/finally 兜底）。
"""

from collections import deque
from typing import Any, Optional

from .config import StreamerProactiveConfig
from .trigger_cause import TriggerKind


__all__ = ["ProactiveTrigger"]


# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------

# hourly 窗口宽度：3 600 秒 = 3 600 000 毫秒
_HOURLY_WINDOW_MS: int = 3_600_000

# 内容推进类触发源：在 should_trigger 里本来就绕过 max_per_hour，记录时不占
# hourly 额度——自己绕过上限的来源不该占别人的额度（否则话痨期几十个流程单
# 窗把账记满，冷场/定时触发在此后一小时内全被上限扣死）。
_UNCAPPED_KINDS = frozenset(
    {TriggerKind.RUNDOWN, TriggerKind.GAME, TriggerKind.REMINDER, TriggerKind.GAME_LEAD_IDLE_FALLBACK}
)


class ProactiveTrigger:
    """主播主动发言的纯规则触发判定器。

    本类**不**订阅事件、不持有 asyncio 任务、不输出日志（由调用方决定是否
    记录触发决策）。状态仅有两项：上次主动触发时刻 + hourly 窗口触发历史，
    均为进程内内存态，重启即重置。
    """

    def __init__(
        self,
        config: StreamerProactiveConfig,
        *,
        game_lead_idle_fallback_ms: int = 50_000,
    ) -> None:
        """注入 proactive 子配置（字段默认值即"未配置降级"，在 Schema 一处表达）。"""
        self._enabled: bool = config.enabled
        self._cold_timeout_ms: int = config.cold_timeout_ms
        self._min_interval_ms: int = config.min_interval_ms
        self._schedule_interval_ms: int = config.schedule_interval_ms
        self._schedule_only_cold: bool = config.schedule_only_cold
        self._max_per_hour: int = config.max_per_hour
        self._topic_required: bool = config.topic_required
        # 环节内防抖间隔（毫秒）。仅 rundown_overdue 触发源使用。
        self._rundown_speech_interval_ms: int = config.rundown_speech_interval_ms
        # 环节超时提醒冷却（毫秒）。仅 rundown_overdue 触发源使用。
        self._rundown_overdue_interval_ms: int = config.rundown_overdue_interval_ms
        # 发言放行节奏间隔（毫秒）：决策执行器在发言放行时刻经 speech_release_wait_ms 用它限速，
        # 开窗侧配合前瞻思考窗口决定何时提前开窗，既保留播音期间提前思考又避免刚出口就空转。
        self._rundown_ready_interval_ms: int = config.rundown_ready_interval_ms
        # 环节内持续产出类主动窗的前瞻开窗窗口（毫秒）：距离下次允许发言的剩余等待时间
        # 超过该窗口时不提前开窗，进入窗口后再提前思考以衔接播放与生成耗时。
        self._rundown_think_lead_ms: int = config.rundown_think_lead_ms
        # 主动沉默冷却（毫秒）：play 档与 talk 档各一档。仅 rundown_ready 触发源使用。
        self._silent_cooldown_ms: int = config.rundown_silent_cooldown_ms
        self._silent_cooldown_talk_ms: int = config.rundown_silent_cooldown_talk_ms
        # 观众弹幕攒批期间 rundown_ready 最多让路多久（毫秒）
        self._ready_yield_max_ms: int = config.rundown_ready_yield_max_ms
        # 游戏主控态下全场连续无声超过该时长才由主播轻从侧暖一句闲话（毫秒）
        self._game_lead_idle_fallback_ms: int = max(0, int(game_lead_idle_fallback_ms))

        # 运行期状态
        # 上次主动触发时刻（None = 尚未触发过）。仅 schedule 判定读取；
        # 不参与公共前置条件（公共前置用 room_state.last_speech_ms 防接龙）。
        self._last_trigger_ms: int | None = None
        # hourly 窗口内触发时间戳（升序追加）。修剪仅在 should_trigger() 中
        # 进行，避免 record_trigger 与 should_trigger 重复清理。
        self._trigger_history: deque[int] = deque()
        # 上次环节超时提醒时刻（None = 尚未提醒过）。仅 overdue 分支读写；
        # pending 触发不更新它。
        self._last_overdue_ms: int | None = None
        # 上次主动轮走完却没开口的时刻（None = 没有沉默过，或之后已经开过口）
        self._last_silent_ms: int | None = None
        # 最近一次 RUNDOWN 触发是否来自即时信号（环节切换 / 超时提醒）：
        # True = 即时信号（发言放行不限速），False = 环节内持续产出（限速）。
        # 默认 True：未走 should_trigger 判定直接开 rundown 窗的调用方按即时信号对待。
        self._last_rundown_immediate: bool = True

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """总开关当前状态（Dashboard 动态开关展示用）。"""
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """运行时切换总开关（立即生效；持久化由调用方负责写回配置）。"""
        self._enabled = bool(enabled)

    def should_trigger(
        self,
        room_state: Any,
        now_ms: int,
        external_pending: bool = False,
        rundown_pending: bool = False,
        rundown_overdue: bool = False,
        rundown_ready: bool = False,
        game_pending: bool = False,
        reminder_pending: bool = False,
        talk_mode: bool = False,
        viewer_waiting_since_ms: int | None = None,
        in_game_lead: bool = False,
        lead_since_ms: int | None = None,
    ) -> TriggerKind | None:
        """判定当前 tick 是否应触发主动发言。

        优先级顺序：``reminder > rundown_pending > game_pending >
        external > rundown_overdue > rundown_ready > schedule > cold``。

        公共前置条件（仅对 ``external`` / ``schedule`` / ``cold`` 生效）：
        - ``enabled=False`` → 永不触发（含 rundown；reminder 豁免——必达）
        - ``min_interval_ms`` 防接龙（对照 ``room_state.last_speech_ms``）
        - ``max_per_hour`` hourly 滑窗
        - ``topic_required`` 话题缺失则跳过

        任一公共前置条件不满足 → 返回 ``None``（不区分原因，由调用方自行记录）。

        触发源对 ``topic_required`` 的依赖差异：
        - ``external`` / ``schedule`` / ``cold`` 均受 ``topic_required``
          约束（外部 API 用户**或**定时/冷场发言需要话题支撑）。
        - ``rundown`` **不**受 ``topic_required`` 约束（流程单本身提供话题，
          来自当前环节任务描述，不依赖 ``room_state.topic_summary``）。

        流程单触发源的限流独立性
        ---------------------------------
        流程单是**内容推进**而非**救场**——主播应按环节节奏持续发言，不应
        被为"防接龙/冷场救场"设计的低频限流卡死：

        - ``rundown_pending=True``（环节切换即时信号）：仅受总开关约束，
          立即触发 ``"rundown"``，新环节开场白必须马上说，不等任何间隔。
        - ``rundown_overdue=True``（环节超时提醒信号）：绕过公共三道前置，
          双重冷却（``rundown_overdue_interval_ms`` 内部状态 +
          ``rundown_speech_interval_ms`` 防抖对照 ``last_speech_ms``），
          Agent 被唤醒后自行决定推进或继续。
        - ``rundown_ready=True``（环节内持续产出信号）：绕过公共三道前置，
          不对照 ``last_speech_ms`` 防抖——上一轮出口即具备开下一窗的资格
          （发言节奏的防抖在发言放行侧执行），供给开口机会，不保证发言。

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
            rundown_pending: 流程单环节切换即时信号（如环节切换回调节点）。
                ``True`` 时仅受总开关约束，立即返回 ``TriggerKind.RUNDOWN``，
                不等任何间隔（即使 ``last_speech_ms`` 刚刚刷新）。
            rundown_overdue: 当前环节停留已超预期（超时闹钟信号）。绕过
                公共三道前置，双重冷却后返回 ``"rundown"``——Agent 醒来
                自行决定推进或继续。
        game_pending: 游戏 Agent 是否有待定夺上报（``game.report``）。
            ``True`` 时仅受总开关约束，立即返回 ``TriggerKind.GAME``，绕过
            ``min_interval_ms`` / ``max_per_hour`` / ``topic_required``
            三道前置（内容推进而非救场，剧情不能无限卡在选项处）。
        reminder_pending: 运营是否有待送达的递话留言。``True`` 时仅查
            ``min_interval_ms`` 防接龙一条约束（总开关/每小时上限/话题
            要求三道闸豁免——必达语义不辖"自主找话说"的规矩），通过即
            返回 ``TriggerKind.REMINDER``。
        talk_mode: 当前是否处于杂谈档（流程单环节说话模式为 talk）；仅决定
            ``rundown_ready`` 用哪一档主动沉默冷却。
        viewer_waiting_since_ms: 观众弹幕正在攒批等放行时，最早一条的到达时刻；
            非空且未超过让路上限时 ``rundown_ready`` 让路。

        Returns:
            ``TriggerKind`` 成员（``EXTERNAL`` / ``REMINDER`` / ``RUNDOWN`` /
            ``GAME`` / ``SCHEDULE`` / ``COLD``）—— 表示应触发并给出原因；
            ``None`` —— 表示不触发。
        """
        # reminder_pending：运营递话催醒——必达语义，仅受防接龙最小间隔一条
        # 约束（总开关/每小时上限/话题要求三道闸豁免：那是"自主找话说"的
        # 规矩，不辖运营递话；刚开口就插提醒没有意义，等下一 tick 自然送达）
        if reminder_pending:
            reminder_last_speech = getattr(room_state, "last_speech_ms", None)
            if reminder_last_speech is None or (now_ms - reminder_last_speech) >= self._min_interval_ms:
                return TriggerKind.REMINDER

        # 总开关（reminder 之外的源适用，含 rundown）
        if not self._enabled:
            return None

        # rundown_pending：环节切换即时信号——只受总开关约束，立即触发
        # （新环节开场白必须马上说，不等任何间隔）
        if rundown_pending:
            self._last_rundown_immediate = True
            return TriggerKind.RUNDOWN

        # game_pending：游戏待定夺即时信号——同 rundown_pending，只受总开关
        # 约束，立即触发（游戏停在选项处等主播定夺，不等任何间隔）
        if game_pending:
            return TriggerKind.GAME

        last_speech_ms = getattr(room_state, "last_speech_ms", None)

        # external：最高优先级（受 min_interval/max_per_hour/topic_required）
        if external_pending:
            if last_speech_ms is not None and (now_ms - last_speech_ms) < self._min_interval_ms:
                return None
            self._prune_history(now_ms)
            if len(self._trigger_history) >= self._max_per_hour:
                return None
            if not self._topic_available(room_state, now_ms):
                return None
            return TriggerKind.EXTERNAL

        # rundown_overdue：环节超时提醒——内容推进信号，绕过公共三道前置；
        # 双重冷却：overdue_interval_ms（内部状态）+ speech_interval_ms 防抖
        # （对照 last_speech_ms，刚发过言不立即提醒）。触发即记冷却时刻。
        if rundown_overdue:
            overdue_cooled = (
                self._last_overdue_ms is None or (now_ms - self._last_overdue_ms) >= self._rundown_overdue_interval_ms
            )
            speech_settled = last_speech_ms is None or (now_ms - last_speech_ms) >= self._rundown_speech_interval_ms
            if overdue_cooled and speech_settled:
                self._last_overdue_ms = now_ms
                self._last_rundown_immediate = True
                return TriggerKind.RUNDOWN

        # 游戏主控态下：停用高频 rundown_ready / schedule / 常规 cold，改为低频冷场兜底。
        # 只有全场连续无声（解说、快嘴、观众回复均推进 last_speech_ms）达到阈值时才允许主播暖一句闲话。
        if in_game_lead:
            if self._game_lead_idle_fallback_allowed(
                last_speech_ms,
                now_ms,
                viewer_waiting_since_ms=viewer_waiting_since_ms,
                lead_since_ms=lead_since_ms,
            ):
                return TriggerKind.GAME_LEAD_IDLE_FALLBACK
            return None

        # rundown_ready：环节内持续产出机会——内容推进信号，绕过公共三道前置
        # （流程单环节任务描述即话题，不依赖弹幕消化出的话题摘要）；开窗受前瞻窗口约束，
        # 仅在进入下次可发言前的前瞻窗口后才提前思考，避免刚说完话就高频空转连开窗。
        if rundown_ready and self._ready_allowed(last_speech_ms, now_ms, talk_mode, viewer_waiting_since_ms):
            self._last_rundown_immediate = False
            return TriggerKind.RUNDOWN

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
                return TriggerKind.SCHEDULE

        # cold：房间处于冷场
        if room_state.is_cold(self._cold_timeout_ms, now_ms=now_ms):
            return TriggerKind.COLD

        return None

    def record_trigger(self, kind: TriggerKind, now_ms: int) -> None:
        """记录一次主动触发（由 StreamerAgent 在发布发言成功后调用）。

        更新 ``_last_trigger_ms``；受限触发源（``external`` / ``schedule`` /
        ``cold``）还会在 hourly 窗口中追加 ``now_ms``。内容推进类触发源
        （``rundown`` / ``game`` / ``reminder``）只在 should_trigger 里绕过
        ``max_per_hour``——它们照常记账的话，高频推进窗会把 hourly 账本记满，
        受限源此后一小时内全被 6 次上限扣死；自己绕过上限的来源不该占别人的
        额度，因此只更新时刻、不追加历史。

        Args:
            kind: 触发原因枚举成员。
            now_ms: 触发时刻（Unix 毫秒）。
        """
        self._last_trigger_ms = now_ms
        # 开过口就不再处于"刚沉默过"的冷却里，下一轮按开口时刻重新计节奏
        self._last_silent_ms = None
        if kind not in _UNCAPPED_KINDS:
            self._trigger_history.append(now_ms)

    def record_silent_round(self, now_ms: int) -> None:
        """记录一轮主动决策走完却没开口（由决策执行器在主动轮沉默收尾时调用）。

        沉默轮不刷新开口时刻，不记的话下一个 tick ``rundown_ready`` 立刻又满足
        条件，沉默期间每个 tick 都空跑一轮大模型；记下后按沉默冷却等一等。
        """
        self._last_silent_ms = now_ms

    @property
    def has_silent_cooldown(self) -> bool:
        """最近一轮主动决策是否以沉默收尾（尚未开口清除）；供决策侧判断是否跳过 45 秒冷场死等。"""
        return self._last_silent_ms is not None

    def should_pace_speech(self, kind: TriggerKind) -> bool:
        """本轮触发源的发言放行是否限速（ADR-080 发言侧节拍的适用范围）。

        自主找话说的源限速：``rundown_ready``（环节内持续产出）、``schedule``、
        ``cold``。即时信号不限速——环节切换开场白 / 超时提醒 / 运营递话 /
        游戏定夺 / 弹幕回应 / 付费触发 / 道别 / 续播开口必须及时，等满间隔
        会误事。RUNDOWN 按最近一次判定的来源细分（即时信号 vs 持续产出）。
        """
        if kind is TriggerKind.RUNDOWN:
            return not self._last_rundown_immediate
        return kind in (TriggerKind.SCHEDULE, TriggerKind.COLD)

    def speech_release_wait_ms(self, last_speech_ms: Optional[int], now_ms: int) -> int:
        """发言放行侧的节拍：距上次开口还差多少毫秒满环节节奏间隔；0 = 已可放行。

        ADR-080：防抖从开窗侧移除后，``rundown_ready_interval_ms`` 的防抖职责
        保留原值移到发言放行时刻执行——只管"这句几时能说"，不再管"几时开始
        想"。纯函数判定（无 I/O、无 asyncio），等待由调用方执行。
        """
        if last_speech_ms is None or self._rundown_ready_interval_ms <= 0:
            return 0
        return max(0, last_speech_ms + self._rundown_ready_interval_ms - now_ms)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _ready_allowed(
        self,
        last_speech_ms: Optional[int],
        now_ms: int,
        talk_mode: bool,
        viewer_waiting_since_ms: int | None,
    ) -> bool:
        """``rundown_ready`` 的三道节奏闸：前瞻思考开窗、主动沉默冷却、给攒批弹幕让路。

        前瞻开窗：当上次开口时刻存在且距离 ``last_speech_ms + rundown_ready_interval_ms``
        的剩余等待时长仍大于 ``rundown_think_lead_ms`` 时暂不开窗；进入前瞻窗口后再提前
        触发思考，保留播放期间思考不断线的能力，同时消灭刚说完话即零间隔空转的问题。
        """
        # 距离下次允许放行发言还超过前瞻思考窗口：暂不开窗，等进入前瞻窗或被即时事件唤醒
        remaining_ms = self.speech_release_wait_ms(last_speech_ms, now_ms)
        if remaining_ms > self._rundown_think_lead_ms:
            return False
        # 上一轮主动决策刚选择了沉默：冷却期内不再催（杂谈档冷却更短，要靠她自己撑场）
        cooldown = self._silent_cooldown_talk_ms if talk_mode else self._silent_cooldown_ms
        if self._last_silent_ms is not None and (now_ms - self._last_silent_ms) < cooldown:
            return False
        # 观众弹幕正在攒批：先让弹幕批放行再说，最多让到上限，防缓冲滞留饿死主动发言
        if viewer_waiting_since_ms is not None and (now_ms - viewer_waiting_since_ms) < self._ready_yield_max_ms:
            return False
        return True

    def _game_lead_idle_fallback_allowed(
        self,
        last_speech_ms: Optional[int],
        now_ms: int,
        *,
        viewer_waiting_since_ms: int | None,
        lead_since_ms: int | None,
    ) -> bool:
        """游戏主控态低频冷场兜底判定：连续无声满阈值、不在沉默冷却内、且不与攒批观众弹幕抢窗。"""
        if self._game_lead_idle_fallback_ms <= 0:
            return False
        # 观众弹幕正在攒批：先让弹幕批放行，最多让到上限
        if viewer_waiting_since_ms is not None and (now_ms - viewer_waiting_since_ms) < self._ready_yield_max_ms:
            return False
        # 上一轮冷场兜底选择沉默：等待满一个冷场兜底周期再重判，防止每 tick 空跑决策
        if self._last_silent_ms is not None and (now_ms - self._last_silent_ms) < self._game_lead_idle_fallback_ms:
            return False
        # 全场连续无声时长以最近一次任意出声为基线；若尚未开口则以进入游戏主控时刻为基线
        reference_ms = last_speech_ms if last_speech_ms is not None else lead_since_ms
        if reference_ms is None:
            return True
        return (now_ms - reference_ms) >= self._game_lead_idle_fallback_ms

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
