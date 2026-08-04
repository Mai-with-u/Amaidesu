"""ProactiveTrigger 单元测试

对应 .omo/plans/proactive-speech.md 的 Task 2：
覆盖所有触发分支（``cold`` / ``schedule`` / ``external``）、触发优先级
（``external > schedule > cold``）、频率限制（``min_interval_ms`` 与
``max_per_hour``）、公共前置条件（``enabled`` / ``topic_required``）
以及 ``record_trigger`` 的状态维护。

时间敏感逻辑全部通过注入 ``now_ms`` 参数保证确定性，**禁止**
``time.sleep``。
"""

# 先导入 config.schemas 种子，规避 deciders 包 __init__ 的预存在循环导入：
#   deciders/__init__ → llm → llm_decider → schemas → decision_schemas → llm_decider(未完成)
# 经 schemas 入口先完成 llm_decider 的完整初始化，再进入 deciders 包即不再死锁。
import src.modules.config.schemas  # noqa: F401  # isort:skip
from typing import Optional
from unittest.mock import MagicMock

import pytest

from src.stages.decision.deciders.amaidesu.proactive_trigger import ProactiveTrigger


# ---------------------------------------------------------------------------
# 测试辅助：构造可控的 mock room_state
# ---------------------------------------------------------------------------


class _SnapshotStub:
    """最小可用的 RoomStateSnapshot duck-type：仅暴露 ``topic_summary``。

    ``room_state.get_snapshot(now_ms=...)`` 必须返回带 ``topic_summary`` 属性的对象；
    ProactiveTrigger 通过 ``getattr(snapshot, "topic_summary", "")`` 读取，因此
    无需引入真正的 RoomStateSnapshot dataclass。
    """

    def __init__(self, topic_summary: str = "") -> None:
        self.topic_summary = topic_summary


class _RoomStateMock:
    """ProactiveTrigger 所需的 room_state 接口的最小 mock。

    ProactiveTrigger 通过：
      - ``getattr(self, "last_speech_ms", None)`` 读发言时刻
      - ``self.is_cold(cold_timeout_ms, now_ms=...)`` 判冷场
      - ``self.get_snapshot(now_ms=...)`` 取快照
    与 room_state 交互，因此只需要这三个表面。
    """

    def __init__(
        self,
        *,
        is_cold: bool = False,
        topic_summary: str = "",
        last_speech_ms: Optional[int] = None,
    ) -> None:
        self.last_speech_ms = last_speech_ms
        self._is_cold = is_cold
        self._topic_summary = topic_summary

    def is_cold(self, cold_timeout_ms: int, *, now_ms: Optional[int] = None) -> bool:
        return self._is_cold

    def get_snapshot(self, *, now_ms: Optional[int] = None) -> _SnapshotStub:
        return _SnapshotStub(topic_summary=self._topic_summary)


def _make_trigger(**overrides) -> ProactiveTrigger:
    """构造一个 ProactiveTrigger，可选覆盖任意默认配置字段"""
    config = {
        "enabled": True,
        "cold_timeout_ms": 45_000,
        "min_interval_ms": 120_000,
        "schedule_interval_ms": 300_000,
        "schedule_only_cold": True,
        "max_per_hour": 6,
        "topic_required": True,
    }
    config.update(overrides)
    return ProactiveTrigger(config)


# ---------------------------------------------------------------------------
# 默认配置 & 配置读取
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    """构造器配置读取与默认值"""

    def test_empty_dict_uses_defaults(self):
        """空 dict 时所有配置取内置默认值"""
        t = ProactiveTrigger({})
        assert t._enabled is True  # noqa: SLF001 - 测试内部状态
        assert t._cold_timeout_ms == 45_000  # noqa: SLF001
        assert t._min_interval_ms == 120_000  # noqa: SLF001
        assert t._schedule_interval_ms == 300_000  # noqa: SLF001
        assert t._schedule_only_cold is True  # noqa: SLF001
        assert t._max_per_hour == 6  # noqa: SLF001
        assert t._topic_required is True  # noqa: SLF001

    def test_pydantic_like_object_supported(self):
        """非 dict 对象（Pydantic 风格）通过 getattr 兜底读取"""
        cfg = MagicMock()
        cfg.enabled = False
        cfg.cold_timeout_ms = 10_000
        cfg.min_interval_ms = 5_000
        cfg.schedule_interval_ms = 0
        cfg.schedule_only_cold = False
        cfg.max_per_hour = 3
        cfg.topic_required = False
        t = ProactiveTrigger(cfg)
        assert t._enabled is False  # noqa: SLF001
        assert t._cold_timeout_ms == 10_000  # noqa: SLF001
        assert t._max_per_hour == 3  # noqa: SLF001
        assert t._schedule_interval_ms == 0  # noqa: SLF001

    def test_partial_overrides_only_touch_specified_keys(self):
        """部分覆盖时未指定字段保留默认值"""
        t = ProactiveTrigger({"max_per_hour": 10})
        assert t._max_per_hour == 10  # noqa: SLF001
        assert t._min_interval_ms == 120_000  # noqa: SLF001

    def test_initial_state_has_no_prior_trigger(self):
        """新建触发器没有历史触发记录"""
        t = _make_trigger()
        assert t._last_trigger_ms is None  # noqa: SLF001
        assert len(t._trigger_history) == 0  # noqa: SLF001


# ---------------------------------------------------------------------------
# 触发优先级（external > schedule > cold）
# ---------------------------------------------------------------------------


class TestTriggerReasons:
    """三种触发原因各自独立可触发"""

    def test_cold_trigger_when_is_cold(self):
        """冷场 → 返回 ``"cold"``（关闭 schedule 以隔离 cold 分支）"""
        t = _make_trigger(schedule_interval_ms=0)
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=None)
        assert t.should_trigger(rs, now_ms=1_000_000) == "cold"

    def test_schedule_trigger_when_interval_elapsed_and_cold(self):
        """定时间隔到点 + 房间冷场 → ``"schedule"``"""
        # 首次触发 _last_trigger_ms 为 None，schedule 条件天然满足
        t = _make_trigger(schedule_only_cold=True)
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=None)
        assert t.should_trigger(rs, now_ms=1_000_000) == "schedule"

    def test_external_trigger_independent_of_cold(self):
        """external_pending=True 即触发（与冷场/热度无关）"""
        t = _make_trigger()
        # 故意设为非冷场且有 topic；external 应当仍触发
        rs = _RoomStateMock(is_cold=False, topic_summary="聊游戏", last_speech_ms=None)
        assert t.should_trigger(rs, now_ms=1_000_000, external_pending=True) == "external"

    def test_external_overrides_schedule_and_cold(self):
        """external 优先级最高：schedule 与 cold 条件同时满足时仍返回 ``"external"``"""
        t = _make_trigger(schedule_only_cold=True)
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=None)
        # 三条件全满足时 external 优先
        assert t.should_trigger(rs, now_ms=1_000_000, external_pending=True) == "external"


# ---------------------------------------------------------------------------
# 公共前置条件
# ---------------------------------------------------------------------------


class TestCommonPreconditions:
    """任一公共前置不满足 → 不触发"""

    def test_disabled_returns_none(self):
        """``enabled=False`` 时永不触发（无论其他条件如何）"""
        t = _make_trigger(enabled=False)
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=None)
        assert t.should_trigger(rs, now_ms=1_000_000) is None
        # 即便 external_pending=True 也不触发
        assert t.should_trigger(rs, now_ms=1_000_000, external_pending=True) is None

    def test_min_interval_blocks_recent_speech(self):
        """距 last_speech_ms < min_interval → 不触发"""
        t = _make_trigger(min_interval_ms=120_000)
        # 上次发言在 30s 前，远小于 120s 间隔
        now = 1_000_000
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=now - 30_000)
        assert t.should_trigger(rs, now_ms=now) is None
        # 即便 external_pending=True 也不触发（前置优先于优先级）
        assert t.should_trigger(rs, now_ms=now, external_pending=True) is None

    def test_min_interval_allows_when_interval_elapsed(self):
        """距 last_speech_ms ≥ min_interval → 通过前置（关闭 schedule 隔离 cold）"""
        t = _make_trigger(min_interval_ms=120_000, schedule_interval_ms=0)
        now = 1_000_000
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=now - 120_000)
        # 恰好等 120s 时通过（>= 比较）；期望返回 "cold"
        assert t.should_trigger(rs, now_ms=now) == "cold"

    def test_min_interval_treats_none_last_speech_as_zero(self):
        """``last_speech_ms is None``（从未发言）视为 0，恒通过（关闭 schedule 隔离 cold）"""
        t = _make_trigger(min_interval_ms=120_000, schedule_interval_ms=0)
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=None)
        assert t.should_trigger(rs, now_ms=1_000_000) == "cold"

    def test_topic_required_empty_topic_returns_none(self):
        """``topic_required=True`` 且 ``topic_summary`` 为空 → 不触发"""
        t = _make_trigger(topic_required=True)
        # 三种空值形式都要拦
        for empty in ("", "   ", "\t\n"):
            rs = _RoomStateMock(is_cold=True, topic_summary=empty, last_speech_ms=None)
            assert t.should_trigger(rs, now_ms=1_000_000) is None, f"空 topic '{empty!r}' 应被拒"
            # 即便 external_pending=True 也被拦（前置先于优先级）
            assert t.should_trigger(rs, now_ms=1_000_000, external_pending=True) is None

    def test_topic_required_false_allows_empty_topic(self):
        """``topic_required=False`` 且话题空时仍可触发（关闭 schedule 隔离 cold）"""
        t = _make_trigger(topic_required=False, schedule_interval_ms=0)
        rs = _RoomStateMock(is_cold=True, topic_summary="", last_speech_ms=None)
        assert t.should_trigger(rs, now_ms=1_000_000) == "cold"

    def test_topic_whitespace_only_strip_blocks(self):
        """仅空白字符的 topic_summary 被视为空（``str.strip()`` 判定）"""
        t = _make_trigger()
        rs = _RoomStateMock(is_cold=True, topic_summary="   ", last_speech_ms=None)
        assert t.should_trigger(rs, now_ms=1_000_000) is None


# ---------------------------------------------------------------------------
# 频率限制：max_per_hour
# ---------------------------------------------------------------------------


class TestMaxPerHour:
    """hourly 窗口内达到 ``max_per_hour`` 后第 7 次拒绝"""

    def test_seven_triggers_in_one_hour_seventh_rejected(self):
        """hourly 窗口内 6 次触发后第 7 次返回 None"""
        # min_interval=0 + schedule_interval_ms=0：仅验证 max_per_hour 单维限制
        t = _make_trigger(max_per_hour=6, min_interval_ms=0, schedule_interval_ms=0)
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=None)
        base = 2_000_000  # 起始时刻（远离零点便于计算）

        # 前 6 次全部通过
        for i in range(6):
            now = base + i * 10_000  # 间隔 10s
            reason = t.should_trigger(rs, now_ms=now)
            assert reason == "cold", f"第 {i + 1} 次应触发，实际 {reason!r}"
            t.record_trigger(reason, now_ms=now)
        # 此时触发器已有 6 条记录
        assert len(t._trigger_history) == 6  # noqa: SLF001

        # 第 7 次：now 推进 10s（仍在 1h 窗口内），应被拒
        reason = t.should_trigger(rs, now_ms=base + 70_000)
        assert reason is None

    def test_old_triggers_drop_out_of_hourly_window(self):
        """窗口外的旧触发记录在 should_trigger 时被丢弃，腾出名额"""
        # schedule_interval_ms=0 隔离，避免 cold 与 schedule 优先级混淆
        t = _make_trigger(max_per_hour=2, min_interval_ms=0, schedule_interval_ms=0)
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=None)

        # t=0 触发 2 次达到上限
        assert t.should_trigger(rs, now_ms=0) == "cold"
        t.record_trigger("cold", now_ms=0)
        assert t.should_trigger(rs, now_ms=1) == "cold"
        t.record_trigger("cold", now_ms=1)

        # t=2s 时窗口内 2 条记录已满，拒
        assert t.should_trigger(rs, now_ms=2) is None

        # t=2s + 3600s + 1ms 时窗口起点 = 2s + 1ms，两条旧记录全部过期被丢弃
        now_far = 2 + 3_600_000 + 1  # 跨过 1 小时 + 1ms
        assert t.should_trigger(rs, now_ms=now_far) == "cold"

    def test_max_per_hour_blocks_external_too(self):
        """max_per_hour 是公共前置：命中上限时 external 也被拦"""
        # schedule_interval_ms=0 隔离
        t = _make_trigger(max_per_hour=1, min_interval_ms=0, schedule_interval_ms=0)
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=None)

        assert t.should_trigger(rs, now_ms=0) == "cold"
        t.record_trigger("cold", now_ms=0)

        # 窗口内已满，external_pending=True 也被拦
        assert t.should_trigger(rs, now_ms=1, external_pending=True) is None


# ---------------------------------------------------------------------------
# schedule 分支
# ---------------------------------------------------------------------------


class TestScheduleBranch:
    """schedule 间隔到点 + (always 或 冷场) 才触发"""

    def test_schedule_only_cold_true_blocks_warm_room(self):
        """``schedule_only_cold=True`` 且非冷场时 schedule 被拒"""
        t = _make_trigger(schedule_only_cold=True, min_interval_ms=0)
        rs = _RoomStateMock(is_cold=False, topic_summary="聊游戏", last_speech_ms=None)
        # 即便 _last_trigger_ms 为 None（首次到点），非冷场时被拒
        assert t.should_trigger(rs, now_ms=1_000_000) is None

    def test_schedule_only_cold_false_allows_warm_room(self):
        """``schedule_only_cold=False`` 且非冷场时 schedule 仍可触发"""
        t = _make_trigger(schedule_only_cold=False, min_interval_ms=0)
        rs = _RoomStateMock(is_cold=False, topic_summary="聊游戏", last_speech_ms=None)
        assert t.should_trigger(rs, now_ms=1_000_000) == "schedule"

    def test_schedule_disabled_when_interval_zero(self):
        """``schedule_interval_ms=0`` 时 schedule 分支完全不启用"""
        t = _make_trigger(schedule_interval_ms=0, min_interval_ms=0)
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=None)
        # schedule 关闭 → fall through 到 cold
        assert t.should_trigger(rs, now_ms=1_000_000) == "cold"

    def test_schedule_blocked_when_interval_not_elapsed(self):
        """距 _last_trigger_ms < schedule_interval → schedule 不触发"""
        t = _make_trigger(schedule_interval_ms=300_000, schedule_only_cold=False, min_interval_ms=0)
        rs = _RoomStateMock(is_cold=False, topic_summary="聊游戏", last_speech_ms=None)

        # 首次到点：触发 schedule
        assert t.should_trigger(rs, now_ms=1_000_000) == "schedule"
        t.record_trigger("schedule", now_ms=1_000_000)

        # 仅过 100s（< 300s 间隔）：schedule 被拦 → fall through → 非冷场 → None
        assert t.should_trigger(rs, now_ms=1_100_000) is None

        # 刚好 300s（>= 间隔）：schedule 重新触发
        assert t.should_trigger(rs, now_ms=1_300_000) == "schedule"

    def test_schedule_outranks_cold(self):
        """冷场 + schedule 间隔到点 → 返回 ``"schedule"``（不是 ``"cold"``）"""
        t = _make_trigger(schedule_interval_ms=300_000, schedule_only_cold=True, min_interval_ms=0)
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=None)
        assert t.should_trigger(rs, now_ms=1_000_000) == "schedule"


# ---------------------------------------------------------------------------
# record_trigger 行为
# ---------------------------------------------------------------------------


class TestRecordTrigger:
    """``record_trigger`` 维护内部状态"""

    def test_record_trigger_updates_last_trigger_ms(self):
        """``record_trigger`` 后 ``_last_trigger_ms`` 等于传入的 ``now_ms``"""
        t = _make_trigger()
        t.record_trigger("external", now_ms=1_000_000)
        assert t._last_trigger_ms == 1_000_000  # noqa: SLF001

    def test_record_trigger_appends_to_history(self):
        """``record_trigger`` 在 deque 末尾追加 ``now_ms``"""
        t = _make_trigger()
        t.record_trigger("cold", now_ms=100)
        t.record_trigger("cold", now_ms=200)
        t.record_trigger("cold", now_ms=300)
        assert list(t._trigger_history) == [100, 200, 300]  # noqa: SLF001

    def test_record_trigger_reason_does_not_affect_state(self):
        """reason 字符串不影响内部状态（接口完整性保留）"""
        t1 = _make_trigger()
        t2 = _make_trigger()
        for reason in ("external", "schedule", "cold", "any-other-string"):
            t1.record_trigger(reason, now_ms=1_000)
            t2.record_trigger(reason, now_ms=1_000)
        assert t1._last_trigger_ms == t2._last_trigger_ms  # noqa: SLF001
        assert list(t1._trigger_history) == list(t2._trigger_history)  # noqa: SLF001

    def test_record_trigger_enables_next_schedule_check(self):
        """``record_trigger`` 更新 ``_last_trigger_ms``，影响下一次 schedule 判定"""
        t = _make_trigger(schedule_interval_ms=300_000, schedule_only_cold=False, min_interval_ms=0)
        rs = _RoomStateMock(is_cold=False, topic_summary="聊游戏", last_speech_ms=None)

        # 首次 schedule 触发
        assert t.should_trigger(rs, now_ms=1_000_000) == "schedule"
        t.record_trigger("schedule", now_ms=1_000_000)

        # 紧接着同 tick 再问一次：距 _last_trigger_ms = 0 < 300s → schedule 被拦 → None
        assert t.should_trigger(rs, now_ms=1_000_000) is None


# ---------------------------------------------------------------------------
# 集成：典型 5s 循环内的行为轨迹
# ---------------------------------------------------------------------------


class TestTickTrace:
    """模拟一连串 tick，验证完整生命周期行为正确"""

    def test_cold_room_triggers_then_quiet_until_min_interval(self):
        """冷场房间：首次触发 → min_interval 期间静默 → 再触发"""
        # schedule_interval_ms=0 隔离：仅验证 cold + min_interval 链路
        t = _make_trigger(
            min_interval_ms=120_000, max_per_hour=10, schedule_interval_ms=0
        )
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=None)

        # t=0：首次 cold 触发
        assert t.should_trigger(rs, now_ms=0) == "cold"
        t.record_trigger("cold", now_ms=0)

        # 模拟 AmaidesuDecider 调用 room_state.record_speech 更新 last_speech_ms
        rs.last_speech_ms = 0

        # t=10s：min_interval 未到 → None
        assert t.should_trigger(rs, now_ms=10_000) is None

        # t=120s：刚好达到 min_interval → 重新触发 cold
        assert t.should_trigger(rs, now_ms=120_000) == "cold"

    def test_warm_room_no_trigger_no_topic_quiet(self):
        """非冷场 + 无 schedule → 静默"""
        t = _make_trigger(schedule_interval_ms=0, min_interval_ms=0)
        rs = _RoomStateMock(is_cold=False, topic_summary="聊游戏", last_speech_ms=None)
        for now in (0, 1_000, 60_000, 600_000):
            assert t.should_trigger(rs, now_ms=now) is None


# ---------------------------------------------------------------------------
# 纯规则保证（边界检查）
# ---------------------------------------------------------------------------


class TestPureRuleContract:
    """验证组件是纯函数：多次调用无副作用、状态明确"""

    def test_should_trigger_does_not_mutate_history(self):
        """``should_trigger`` 不修改触发历史（仅读取并按需修剪过期项）"""
        # schedule_interval_ms=0 隔离
        t = _make_trigger(max_per_hour=10, min_interval_ms=0, schedule_interval_ms=0)
        rs = _RoomStateMock(is_cold=True, topic_summary="聊游戏", last_speech_ms=None)

        # 记录 3 次
        for now in (0, 10, 20):
            assert t.should_trigger(rs, now_ms=now) == "cold"
            t.record_trigger("cold", now_ms=now)

        before_history = list(t._trigger_history)  # noqa: SLF001
        before_last = t._last_trigger_ms  # noqa: SLF001

        # 多次调用 should_trigger（带 external_pending=False）不应改写状态
        for now in (30, 40, 50):
            t.should_trigger(rs, now_ms=now)

        assert list(t._trigger_history) == before_history  # noqa: SLF001
        assert t._last_trigger_ms == before_last  # noqa: SLF001

    def test_no_sleep_no_io(self):
        """组件不持有任何 I/O 资源（构造后对象图有限）"""
        t = _make_trigger()
        # 仅有 dict（config）的拷贝（_cfg 返回值）和 deque；
        # 没有 threading/asyncio/网络/文件系统对象
        attrs = vars(t)
        # 排除 deque（合法）和基本 int/bool/None（合法）
        for name, val in attrs.items():
            assert not hasattr(val, "read"), f"{name} 不应持有文件句柄"
            assert not hasattr(val, "fileno"), f"{name} 不应持有 fd"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
