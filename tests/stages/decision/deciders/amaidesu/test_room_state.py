"""RoomState 规则层单元测试

测试直播态势快照计算（弹幕速率热度 / 话题词频 / SC 队列）。
所有时间通过注入 now_ms 参数保证确定性，不依赖 time.sleep。
"""

# 先导入 config.schemas 种子，规避 deciders 包 __init__ 的预存在循环导入：
#   deciders/__init__ → llm → llm_decider → schemas → decision_schemas → llm_decider(未完成)
# 经 schemas 入口先完成 llm_decider 的完整初始化，再进入 deciders 包即不再死锁。
import src.modules.config.schemas  # noqa: F401  # isort:skip
from unittest.mock import MagicMock

import pytest

from src.stages.decision.deciders.amaidesu.room_state import (
    RoomState,
    RoomStateSnapshot,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_msg(text: str, data_type: str = "text") -> MagicMock:
    """构造带 text 属性的 mock 消息（兼容 NormalizedMessage 字段子集）"""
    m = MagicMock()
    m.text = text
    m.data_type = data_type
    return m


# ---------------------------------------------------------------------------
# RoomStateSnapshot 数据类
# ---------------------------------------------------------------------------


class TestRoomStateSnapshot:
    """RoomStateSnapshot dataclass 行为"""

    def test_default_topic_summary_empty(self):
        """新建 snapshot 时 topic_summary 默认为空字符串"""
        snap = RoomStateSnapshot(
            heat="low",
            topics=[],
            sc_queue=[],
            last_update_ms=1000,
        )
        assert snap.topic_summary == ""

    def test_fields_assigned(self):
        """字段正确赋值"""
        snap = RoomStateSnapshot(
            heat="high",
            topics=["游戏", "通关"],
            sc_queue=[{"message_id": "sc1"}],
            last_update_ms=9999,
            topic_summary="正在讨论游戏",
        )
        assert snap.heat == "high"
        assert snap.topics == ["游戏", "通关"]
        assert snap.sc_queue == [{"message_id": "sc1"}]
        assert snap.last_update_ms == 9999
        assert snap.topic_summary == "正在讨论游戏"


# ---------------------------------------------------------------------------
# Heat level 计算
# ---------------------------------------------------------------------------


class TestHeatLevel:
    """弹幕速率 → heat 等级"""

    def test_empty_state_is_low(self):
        """空状态下 heat = low"""
        rs = RoomState()
        snap = rs.get_snapshot(now_ms=1000000)
        assert snap.heat == "low"
        assert snap.topics == []
        assert snap.sc_queue == []

    def test_cold_room_low_heat(self):
        """稀疏弹幕（< 1 条 / 10 秒）→ low"""
        rs = RoomState()
        base = 1_000_000
        # 60 秒内只有 2 条 → 约 0.033 条/秒 → 远低于 0.1 条/秒
        rs.update(_make_msg("你好"), now_ms=base)
        rs.update(_make_msg("在吗"), now_ms=base + 30_000)
        snap = rs.get_snapshot(now_ms=base + 60_000)
        assert snap.heat == "low", f"稀疏弹幕应为 low，实际 {snap.heat}"

    def test_medium_density(self):
        """中等密度弹幕（1 条 / 10 秒 ~ 1 条 / 2 秒）→ medium

        阈值定义（见 room_state.py）：
        - < 0.1 条/秒 (即 < 1 条/10s) → low
        - < 0.5 条/秒 (即 < 1 条/2s) → medium
        - >= 0.5 条/秒 → high
        """
        rs = RoomState()
        base = 1_000_000
        # 60 秒内 10 条 → 约 0.167 条/秒 → medium
        for i in range(10):
            rs.update(_make_msg(f"消息{i}"), now_ms=base + i * 6_000)
        snap = rs.get_snapshot(now_ms=base + 60_000)
        assert snap.heat == "medium", f"中等密度应为 medium，实际 {snap.heat}"

    def test_high_density(self):
        """密集弹幕（>= 1 条 / 2 秒）→ high"""
        rs = RoomState()
        base = 1_000_000
        # 2 秒内 10 条 → 5 条/秒 → high
        for i in range(10):
            rs.update(_make_msg(f"弹幕{i}"), now_ms=base + i * 100)
        snap = rs.get_snapshot(now_ms=base + 2_000)
        assert snap.heat in ("medium", "high"), f"密集弹幕 heat={snap.heat}"
        assert snap.heat == "high", f"5 条/秒应为 high，实际 {snap.heat}"

    def test_heat_falls_when_messages_age_out(self):
        """消息滑出 60 秒窗口后 heat 下降"""
        rs = RoomState()
        base = 1_000_000
        # 短时间内大量弹幕 → high
        for i in range(20):
            rs.update(_make_msg(f"老弹幕{i}"), now_ms=base + i * 50)
        hot = rs.get_snapshot(now_ms=base + 2_000)
        assert hot.heat == "high"

        # 时间前进 120 秒，原消息全部出窗 → low
        cold = rs.get_snapshot(now_ms=base + 120_000)
        assert cold.heat == "low", f"出窗后应为 low，实际 {cold.heat}"

    def test_now_ms_defaults_to_real_clock(self):
        """不传 now_ms 时使用真实时钟（不抛错）"""
        rs = RoomState()
        rs.update(_make_msg("hello"), now_ms=None)
        # 仅验证不抛异常，且返回合法 snapshot
        snap = rs.get_snapshot()
        assert isinstance(snap, RoomStateSnapshot)
        assert snap.last_update_ms > 0


# ---------------------------------------------------------------------------
# 话题提取
# ---------------------------------------------------------------------------


class TestTopicExtraction:
    """词频话题提取（停用词过滤）"""

    def test_stopswords_filtered(self):
        """停用词不应出现在 topics"""
        rs = RoomState()
        base = 1_000_000
        for word in ["你", "我", "他", "的", "了", "吗"]:
            rs.update(_make_msg(word), now_ms=base)
        snap = rs.get_snapshot(now_ms=base + 1_000)
        for sw in ["你", "我", "他", "的", "了", "吗"]:
            assert sw not in snap.topics, f"停用词 {sw} 不应出现在 topics"

    def test_frequent_words_become_topics(self):
        """高频词被提取为 topics"""
        rs = RoomState()
        base = 1_000_000
        # "游戏" 出现 5 次，"通关" 出现 3 次，其余 1 次
        for i in range(5):
            rs.update(_make_msg("游戏真好玩"), now_ms=base + i * 100)
        for i in range(3):
            rs.update(_make_msg("通关了"), now_ms=base + 500 + i * 100)
        rs.update(_make_msg("不错"), now_ms=base + 1_000)
        snap = rs.get_snapshot(now_ms=base + 2_000)
        assert "游" in snap.topics or "戏" in snap.topics, "高频字应进 topics"
        # topics 按频次降序
        assert len(snap.topics) > 0

    def test_topics_sorted_by_frequency_desc(self):
        """topics 按词频降序"""
        rs = RoomState()
        base = 1_000_000
        # 构造 A 频次 > B 频次
        for _ in range(6):
            rs.update(_make_msg("啊啊啊"), now_ms=base)
        for _ in range(2):
            rs.update(_make_msg(" b"), now_ms=base + 1)
        snap = rs.get_snapshot(now_ms=base + 2_000)
        if len(snap.topics) >= 2:
            # 频次最高的应在前面
            assert snap.topics[0] == "啊"

    def test_punctuation_and_emoji_filtered(self):
        """标点 / emoji 等非汉字字符不应出现在 topics

        回归测试（P0）：实测日志中 "？。！✨" 等字符霸榜话题关键词，
        例如 "话题关键词: 播, 来, ？, 。, 主"。
        """
        rs = RoomState()
        base = 1_000_000
        # 标点 + emoji 高频出现，汉字低频
        for i in range(10):
            rs.update(_make_msg(f"？？？！！！✨😆 {i}"), now_ms=base + i * 100)
        rs.update(_make_msg("游戏好玩"), now_ms=base + 10_000)
        snap = rs.get_snapshot(now_ms=base + 11_000)
        for noise in ["？", "！", "✨", "😆", "0", "9"]:
            assert noise not in snap.topics, f"非汉字字符 {noise} 不应出现在 topics: {snap.topics}"
        # 汉字仍能进入 topics
        assert any(ch in snap.topics for ch in "游戏好玩"), f"汉字应进 topics: {snap.topics}"

    def test_pure_non_cjk_message_yields_no_topics(self):
        """纯标点/字母/数字消息不产生任何话题关键词"""
        rs = RoomState()
        base = 1_000_000
        for i in range(5):
            rs.update(_make_msg(f"!!!??? abc 123 {i}"), now_ms=base + i * 100)
        snap = rs.get_snapshot(now_ms=base + 1_000)
        assert snap.topics == [], f"纯非汉字消息 topics 应为空，实际: {snap.topics}"


# ---------------------------------------------------------------------------
# SC / 礼物 / 上舰 队列
# ---------------------------------------------------------------------------


class TestScQueue:
    """SC/礼物/上舰队列 push/drain"""

    def test_push_and_drain(self):
        rs = RoomState()
        rs.push_sc({"message_id": "sc1", "text": "打赏", "amount": 100})
        rs.push_sc({"message_id": "sc2", "text": "上舰", "amount": 198})
        drained = rs.drain_sc()
        assert len(drained) == 2
        assert drained[0]["message_id"] == "sc1"
        assert drained[1]["message_id"] == "sc2"

    def test_drain_clears_queue(self):
        """drain 后队列清空"""
        rs = RoomState()
        rs.push_sc({"message_id": "sc1"})
        rs.drain_sc()
        assert rs.drain_sc() == [], "drain 后队列应为空"

    def test_empty_drain_returns_empty_list(self):
        """空队列 drain 返回 []"""
        rs = RoomState()
        assert rs.drain_sc() == []

    def test_sc_visible_in_snapshot(self):
        """push 后 SC 在 snapshot.sc_queue 中可见，drain 后清空"""
        rs = RoomState()
        rs.push_sc({"message_id": "sc1"}, now_ms=1_000)
        snap = rs.get_snapshot(now_ms=2_000)
        assert len(snap.sc_queue) == 1
        assert snap.sc_queue[0]["message_id"] == "sc1"
        rs.drain_sc()
        snap2 = rs.get_snapshot(now_ms=3_000)
        assert snap2.sc_queue == []


# ---------------------------------------------------------------------------
# Snapshot 完整性
# ---------------------------------------------------------------------------


class TestSnapshot:
    """get_snapshot 返回值完整性"""

    def test_snapshot_last_update_reflects_now_ms(self):
        """snapshot.last_update_ms 反映传入的 now_ms"""
        rs = RoomState()
        snap = rs.get_snapshot(now_ms=1_234_567)
        assert snap.last_update_ms == 1_234_567

    def test_snapshot_is_snapshot_not_reference(self):
        """返回的 snapshot 是 SC 队列的副本，修改不影响内部状态"""
        rs = RoomState()
        rs.push_sc({"message_id": "sc1"})
        snap = rs.get_snapshot(now_ms=1_000)
        snap.sc_queue.append({"message_id": "mutated"})
        snap2 = rs.get_snapshot(now_ms=2_000)
        assert len(snap2.sc_queue) == 1, "外部修改 snapshot 不应影响内部状态"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
