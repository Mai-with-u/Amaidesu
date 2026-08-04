"""RoomState 主动发言跟踪能力单元测试

对应 .omo/plans/proactive-speech.md 的 Task 1：
``record_speech()`` 与 ``last_speech_ms`` / ``speech_count`` 行为。
同时验证 ``is_cold()`` / ``update()`` / ``get_snapshot()`` 既有语义保持不变
（主动发言**不**影响冷场判定，冷场仍只看弹幕到达时刻）。

所有时间通过注入 ``now_ms`` 参数保证确定性，不依赖 ``time.sleep``。
"""

# 先导入 config.schemas 种子，规避 deciders 包 __init__ 的预存在循环导入：
#   deciders/__init__ → llm → llm_decider → schemas → decision_schemas → llm_decider(未完成)
# 经 schemas 入口先完成 llm_decider 的完整初始化，再进入 deciders 包即不再死锁。
import src.modules.config.schemas  # noqa: F401  # isort:skip
from unittest.mock import MagicMock

import pytest

from src.stages.decision.deciders.amaidesu.room_state import RoomState


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_msg(text: str) -> MagicMock:
    """构造带 text 属性的 mock 消息（兼容 NormalizedMessage 字段子集）"""
    m = MagicMock()
    m.text = text
    return m


# ---------------------------------------------------------------------------
# 主动发言跟踪：record_speech / last_speech_ms / speech_count
# ---------------------------------------------------------------------------


class TestSpeechTracking:
    """RoomState 主动发言时刻跟踪能力"""

    def test_initial_state_no_speech(self):
        """新建 RoomState 时 last_speech_ms 为 None，speech_count 为 0"""
        rs = RoomState()
        assert rs.last_speech_ms is None
        assert rs.speech_count == 0

    def test_record_speech_updates_last_speech_ms(self):
        """record_speech 将 last_speech_ms 更新到注入时刻"""
        rs = RoomState()
        rs.record_speech(now_ms=1_000)
        assert rs.last_speech_ms == 1_000
        assert rs.speech_count == 1

    def test_record_speech_increments_count(self):
        """每次 record_speech 调用 speech_count 自增 1"""
        rs = RoomState()
        for i in range(5):
            rs.record_speech(now_ms=10_000 + i * 100)
        assert rs.speech_count == 5

    def test_record_speech_overwrites_to_latest(self):
        """连续多次 record_speech 时 last_speech_ms 取最新一次"""
        rs = RoomState()
        rs.record_speech(now_ms=1_000)
        rs.record_speech(now_ms=2_000)
        rs.record_speech(now_ms=3_500)
        assert rs.last_speech_ms == 3_500
        assert rs.speech_count == 3

    def test_record_speech_does_not_touch_last_message_ms(self):
        """主动发言不影响 _last_message_ms（弹幕时刻独立）"""
        rs = RoomState()
        # 先来一条弹幕
        rs.update(_make_msg("弹幕1"), now_ms=500)
        assert rs._last_message_ms == 500  # noqa: SLF001 - 测试内部状态
        # 主动发言后弹幕时刻不变
        rs.record_speech(now_ms=2_000)
        assert rs._last_message_ms == 500  # noqa: SLF001
        assert rs.last_speech_ms == 2_000


# ---------------------------------------------------------------------------
# 既有 is_cold() / update() / get_snapshot() 语义保持不变（回归保护）
# ---------------------------------------------------------------------------


class TestExistingSemanticsPreserved:
    """主动发言新增能力不得影响既有 is_cold / update / get_snapshot 行为"""

    def test_is_cold_unaffected_by_record_speech(self):
        """is_cold 仍基于 _last_message_ms（弹幕），主动发言不重置冷场计时器"""
        rs = RoomState()
        base = 1_000_000
        # 来一条弹幕后立即主动发言
        rs.update(_make_msg("hi"), now_ms=base)
        rs.record_speech(now_ms=base + 10)
        # 60 秒后无新弹幕，应判定冷场（即便中间有过主动发言）
        assert rs.is_cold(cold_timeout_ms=30_000, now_ms=base + 60_000) is True

    def test_is_cold_returns_true_before_any_message(self):
        """无任何弹幕时 is_cold 必为 True（既有行为）"""
        rs = RoomState()
        # 即便有过主动发言，无弹幕仍判定冷场
        rs.record_speech(now_ms=1_000)
        assert rs.is_cold(cold_timeout_ms=30_000, now_ms=2_000) is True

    def test_get_snapshot_does_not_expose_last_speech_ms(self):
        """RoomStateSnapshot 不暴露 last_speech_ms（ProactiveTrigger 直接访问实例）"""
        rs = RoomState()
        rs.record_speech(now_ms=5_000)
        snap = rs.get_snapshot(now_ms=6_000)
        # RoomStateSnapshot 是 dataclass，其字段集合不含 last_speech_ms / speech_count
        from src.stages.decision.deciders.amaidesu.room_state import RoomStateSnapshot

        snap_fields = {f.name for f in RoomStateSnapshot.__dataclass_fields__.values()}
        assert "last_speech_ms" not in snap_fields
        assert "speech_count" not in snap_fields
        # 确保快照实例上也没意外暴露
        assert not hasattr(snap, "last_speech_ms")
        assert not hasattr(snap, "speech_count")

    def test_update_still_increments_heat_window(self):
        """update() 既有热度窗口维护行为不受 record_speech 影响"""
        rs = RoomState()
        base = 1_000_000
        # 主动发言后插入弹幕，rate 仍按弹幕窗口计算
        rs.record_speech(now_ms=base - 100)
        for i in range(10):
            rs.update(_make_msg(f"弹幕{i}"), now_ms=base + i * 6_000)
        snap = rs.get_snapshot(now_ms=base + 60_000)
        assert snap.heat == "medium", f"中等密度应为 medium，实际 {snap.heat}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])