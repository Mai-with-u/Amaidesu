"""Minecraft 公屏门控单测：事实标签、2 秒合并窗、优先级截留、刷屏冷却、远处旁听与近期原话滚动窗。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

from src.agents.minecraft.chat_gate import MinecraftChatGate
from src.agents.minecraft.short_term_memory import SessionShortTermMemory
from src.modules.events.payloads.game_chat import GameChatMention, GameChatPayload
from tests.helpers import FakeClock


def _chat(
    sender: str,
    content: str,
    *,
    private: bool = False,
    self_mention: bool = False,
    note: str = "",
    distance: Optional[float] = None,
    suppressed: int = 0,
) -> GameChatPayload:
    """构造测试用玩家公屏载荷，直接透传正式的 ``sender_distance`` 可选字段。"""
    mentions = [GameChatMention(name="麦麦", type="self", id="")] if self_mention else []
    return GameChatPayload(
        game="minecraft",
        kind="player",
        sender=sender,
        sender_distance=distance,
        content=content,
        private=private,
        note=note,
        mentions=mentions,
        suppressed=suppressed,
    )


def test_fact_tags_format_private_mention_distance_nearby_fallback_far_and_note() -> None:
    """事实标签按私聊/点名/身边距离/降级身边/远处准确格式化，并完整保留未核验 note 说明。"""
    clock = FakeClock(10_000)
    gate = MinecraftChatGate(clock=clock.now_ms)

    r_private = gate.ingest(_chat("同伴甲", "箱子密码在屋后", private=True, note="来源未核验"))
    r_mention = gate.ingest(_chat("同伴乙", "麦麦来帮忙看下熔炉", self_mention=True))
    r_dist = gate.ingest(_chat("同伴丙", "这边缺圆石", distance=11.6))
    # 无 sender_distance 属性时优雅降级参考 nearby_players
    r_nearby_fallback = gate.ingest(_chat("同伴丁", "我刚放到你脚边了"), nearby_players={"同伴丁"})
    r_far = gate.ingest(_chat("远方路人", "主城今天真热闹", distance=80.0))

    assert r_private.formatted_line == "[公屏·私聊] 同伴甲（来源未核验）：箱子密码在屋后"
    assert r_mention.formatted_line == "[公屏·点名你] 同伴乙：麦麦来帮忙看下熔炉"
    assert r_dist.formatted_line == "[公屏·身边12格] 同伴丙：这边缺圆石"
    assert r_nearby_fallback.formatted_line == "[公屏·身边] 同伴丁：我刚放到你脚边了"
    assert r_far.formatted_line == "[公屏·远处] 远方路人：主城今天真热闹"


def test_far_unmentioned_chat_goes_to_eavesdrop_and_never_wakes_while_partner_wakes_within_2min() -> None:
    """远处非点名公屏仅进旁听滑窗绝不唤醒；近 2 分钟互动同伴可唤醒，超过 2 分钟后退回旁听。"""
    clock = FakeClock(50_000)
    gate = MinecraftChatGate(clock=clock.now_ms)

    res = gate.ingest(_chat("路人甲", "谁有附魔书", distance=100.0))
    assert res.accepted is True and res.routed_to == "eavesdrop" and res.should_schedule_flush is False
    assert gate.next_flush_delay_ms() is None

    clock.tick(10_000)
    assert gate.flush_due() is None, "纯远处非点名公屏绝不触发主动唤醒"

    eavesdrop = gate.drain_eavesdrop()
    assert eavesdrop is not None
    assert eavesdrop.should_wake is False
    assert "[公屏旁听]" in eavesdrop.text
    assert "[公屏·远处] 路人甲：谁有附魔书" in eavesdrop.text
    assert gate.drain_eavesdrop() is None

    # 登记近期互动同伴：2 分钟内即便距离超过 24 格仍走唤醒合并窗
    gate.note_partner_interaction("同伴甲")
    clock.tick(60_000)
    partner_res = gate.ingest(_chat("同伴甲", "我把铁锭放进公共箱了", distance=40.0))
    assert partner_res.routed_to == "wake_window" and partner_res.should_schedule_flush is True
    assert gate.flush_due() is None
    clock.tick(2_000)
    batch = gate.flush_due()
    assert batch is not None and batch.should_wake is True
    assert "[公屏合批]" in batch.text and "同伴甲：我把铁锭放进公共箱了" in batch.text

    # 跨过 2 分钟互动窗后，同一人远处非点名公屏退回旁听滑窗
    clock.tick(130_000)
    expired_res = gate.ingest(_chat("同伴甲", "远处闲聊一句", distance=60.0))
    assert expired_res.routed_to == "eavesdrop"
    assert gate.flush_due(force=True) is None


def test_two_second_batch_window_caps_at_8_by_priority_and_collapses_repeats_and_bursts() -> None:
    """2 秒短合并窗单窗上限 8 条按优先级截留，同时折叠同人同内容与单人高频连发。"""
    clock = FakeClock(100_000)
    gate = MinecraftChatGate(clock=clock.now_ms, batch_window_ms=2_000, max_batch_items=8)

    # 先发一条身边消息打开 2 秒合并窗
    first = gate.ingest(_chat("身边1", "身边消息1", distance=8.0))
    assert first.should_schedule_flush is True and first.due_in_ms == 2_000

    # 同人同内容连发 3 次应折叠成 1 条带 ×3 计数
    gate.ingest(_chat("身边1", "身边消息1", distance=8.0))
    gate.ingest(_chat("身边1", "身边消息1", distance=8.0))

    # 再灌入不同优先级的 10 条消息：2 条私聊、2 条点名、3 条身边、3 条远处抽样
    gate.ingest(_chat("远处1", "远处闲聊1", distance=90.0))
    gate.ingest(_chat("远处2", "远处闲聊2", distance=95.0))
    gate.ingest(_chat("远处3", "远处闲聊3", distance=99.0))
    gate.ingest(_chat("身边2", "身边消息2", distance=10.0))
    gate.ingest(_chat("身边3", "身边消息3", distance=12.0))
    gate.ingest(_chat("身边4", "身边消息4", distance=14.0))
    gate.ingest(_chat("点名1", "麦麦看这边1", self_mention=True))
    gate.ingest(_chat("点名2", "麦麦看这边2", self_mention=True))
    gate.ingest(_chat("私聊1", "悄悄话1", private=True))
    gate.ingest(_chat("私聊2", "悄悄话2", private=True))

    # 未满 2 秒不到窗
    clock.tick(1_999)
    assert gate.flush_due() is None
    assert gate.next_flush_delay_ms() == 1

    # 满 2 秒到窗：总计 11 条去重后候选截留至 8 条，保留 2 私聊 + 2 点名 + 4 身边，3 条远处被省略
    clock.tick(1)
    batch = gate.flush_due()
    assert batch is not None and batch.should_wake is True
    assert batch.count == 8 and batch.dropped_count == 3
    assert batch.has_private is True and batch.has_self_mention is True
    assert any("连发 ×3" in line for line in batch.lines)
    assert all("远处闲聊" not in line for line in batch.lines)
    assert "另有 3 条低优先级消息已省略" in batch.text

    # 单人单窗高频发 5 条不同内容时，只占 3 条名额并折叠另发 2 条计数
    clock.tick(20_000)
    for idx in range(5):
        gate.ingest(_chat("刷屏侠", f"第 {idx} 句不同刷屏", self_mention=True))
    clock.tick(2_000)
    burst_batch = gate.flush_due()
    assert burst_batch is not None and burst_batch.count == 3
    assert any("同人另发 2 条已折叠" in line for line in burst_batch.lines)


def test_sustained_spam_enables_6s_wake_cooldown_and_recovers_after_quiet_period() -> None:
    """持续刷屏时启用 6000ms 最小唤醒冷却，静默超过两倍冷却期后恢复 2000ms 正常短窗。"""
    clock = FakeClock(200_000)
    gate = MinecraftChatGate(clock=clock.now_ms, batch_window_ms=2_000, wake_cooldown_ms=6_000, spam_burst_threshold=3)

    # 连续触发 3 次常规 2 秒唤醒进入持续刷屏保护态
    for round_idx in range(3):
        res = gate.ingest(_chat("同伴甲", f"连续喊话 {round_idx}", self_mention=True))
        assert res.due_in_ms == 2_000
        clock.tick(2_000)
        assert gate.flush_due() is not None
        clock.tick(500)

    assert gate.in_spam_cooldown is True
    # 冷却期内立刻再来新喊话：距上次唤醒已过 500ms，还需再等 5500ms 才满 6000ms 冷却
    throttled = gate.ingest(_chat("同伴甲", "刷屏期间又喊了一句", self_mention=True))
    assert throttled.due_in_ms == 5_500

    clock.tick(2_000)
    assert gate.flush_due() is None, "刷屏冷却期内不到 6000ms 不得提前唤醒"

    clock.tick(3_500)
    cooled_batch = gate.flush_due()
    assert cooled_batch is not None and cooled_batch.should_wake is True

    # 静默超过两倍冷却期（12000ms）后，恢复正常 2000ms 短窗
    clock.tick(13_000)
    assert gate.in_spam_cooldown is False
    recovered = gate.ingest(_chat("同伴甲", "平静后的正常喊话", self_mention=True))
    assert recovered.due_in_ms == 2_000


def test_recent_human_lines_rolling_window_keeps_8_chats_and_4_suggestions() -> None:
    """近期人类原话滚动窗最多保留最近 8 条公屏与 4 条主播建议，并可格式化与导出纯文本用于去重。"""
    clock = FakeClock(300_000)
    gate = MinecraftChatGate(clock=clock.now_ms)

    for idx in range(10):
        gate.ingest(_chat("同伴甲", f"公屏第 {idx} 句", distance=5.0))
    for idx in range(6):
        gate.record_streamer_suggestion(f"主播建议第 {idx} 条")
    # 相邻重复建议不重复占位
    gate.record_streamer_suggestion("主播建议第 5 条")

    assert len(gate.recent_player_chats()) == 8
    assert all("公屏第 0 句" not in line and "公屏第 1 句" not in line for line in gate.recent_player_chats())
    assert gate.recent_streamer_suggestions() == (
        "主播建议第 2 条",
        "主播建议第 3 条",
        "主播建议第 4 条",
        "主播建议第 5 条",
    )

    rendered = gate.format_recent_human_lines()
    assert "[近期人类原话]" in rendered
    assert "近期主播建议：" in rendered and "近期玩家公屏：" in rendered
    assert "主播建议第 5 条" in rendered and "公屏第 9 句" in rendered

    dedup_texts = gate.recent_human_texts()
    assert "公屏第 9 句" in dedup_texts and "主播建议第 5 条" in dedup_texts


def test_legacy_payload_without_sender_distance_attribute_falls_back_to_nearby_players() -> None:
    """兼容旧载荷对象（完全没有 sender_distance 属性）：优雅降级参考 nearby_players。"""
    clock = FakeClock(310_000)
    gate = MinecraftChatGate(clock=clock.now_ms)
    legacy_payload: Any = SimpleNamespace(
        kind="player",
        sender="旧版同伴",
        content="我就在你身后",
        private=False,
        note="",
        mentions=[],
        suppressed=0,
    )

    res = gate.ingest(legacy_payload, nearby_players={"旧版同伴"})
    assert res.accepted is True and res.routed_to == "wake_window"
    assert res.formatted_line == "[公屏·身边] 旧版同伴：我就在你身后"


def test_clear_pending_windows_drops_stale_batches_and_eavesdrop_while_keeping_recent_lines() -> None:
    """切出/重进主控态调 clear_pending_windows：清空待发窗、旁听窗与冷却，但保留近期人话与互动同伴。"""
    clock = FakeClock(320_000)
    gate = MinecraftChatGate(clock=clock.now_ms, spam_burst_threshold=1, wake_cooldown_ms=6_000)
    gate.note_partner_interaction("同伴甲")
    gate.record_streamer_suggestion("先修桥再过河")
    gate.ingest(_chat("路人远", "远处闲聊", distance=90.0))
    gate.ingest(_chat("同伴甲", "立刻看这边", self_mention=True))
    clock.tick(2_000)
    assert gate.flush_due() is not None
    assert gate.in_spam_cooldown is True

    # 再塞一条待发唤醒消息与一条旁听消息，随后切主控清空待发窗
    gate.ingest(_chat("同伴甲", "滞留的旧消息", self_mention=True))
    assert gate.next_flush_delay_ms() is not None
    gate.clear_pending_windows()

    assert gate.next_flush_delay_ms() is None
    assert gate.flush_due(force=True) is None
    assert gate.drain_eavesdrop() is None
    assert gate.in_spam_cooldown is False
    # 近期人话滚动窗与近期互动同伴保留完好
    assert gate.is_recent_partner("同伴甲") is True
    assert gate.recent_streamer_suggestions() == ("先修桥再过河",)
    assert any("滞留的旧消息" in line for line in gate.recent_player_chats())
    # 清窗后再来远处非点名消息走旁听滑窗，不会被旧过期窗误唤醒
    after_far = gate.ingest(_chat("路人甲", "新环节的远处闲聊", distance=80.0))
    assert after_far.routed_to == "eavesdrop"


def test_high_priority_private_or_mention_is_never_dropped_or_mislabeled_after_3_public_bursts() -> None:
    """单人 2 秒内连发 3 条公开消息后再发私聊或点名：保留高优原文、绝不误改公开条目标签、仅淘汰低优公开条。"""
    clock = FakeClock(350_000)
    gate = MinecraftChatGate(clock=clock.now_ms)

    # 先发 3 条普通身边公屏
    for idx in range(3):
        gate.ingest(_chat("同伴甲", f"公开消息{idx}", distance=6.0))
    # 第 4 条发高优私聊：不得丢弃原文，也不得把"公开消息2"误改标为 [公屏·私聊]
    gate.ingest(_chat("同伴甲", "私下告诉你密室在树下", private=True))
    clock.tick(2_000)
    batch = gate.flush_due()
    assert batch is not None and batch.has_private is True
    assert batch.count == 3
    assert any("[公屏·私聊] 同伴甲：私下告诉你密室在树下" in line for line in batch.lines)
    assert all(not ("[公屏·私聊]" in line and "公开消息" in line) for line in batch.lines)
    assert any("同人另发 1 条已折叠" in line for line in batch.lines)

    # 同人同内容先公开后私聊：绝不混并成一条把公开话改成私聊
    clock.tick(20_000)
    gate.ingest(_chat("同伴乙", "去东边", distance=5.0))
    gate.ingest(_chat("同伴乙", "去东边", private=True))
    clock.tick(2_000)
    mixed = gate.flush_due()
    assert mixed is not None and mixed.count == 2
    assert any("[公屏·身边5格] 同伴乙：去东边" == line for line in mixed.lines)
    assert any("[公屏·私聊] 同伴乙：去东边" == line for line in mixed.lines)


async def test_recent_human_texts_dedups_short_chat_with_unverified_note_in_short_term_memory() -> None:
    """带 note 且短正文（<6 字）的近期公屏在 recent_human_texts 中带发言人原形，能精准与短期记忆去重。"""
    clock = FakeClock(400_000)
    gate = MinecraftChatGate(clock=clock.now_ms)
    memory = SessionShortTermMemory(clock=clock.now_ms)

    payload = _chat("同伴甲", "别碰", note="来源未核验", distance=5.0)
    gate.ingest(payload)
    memory.record_player_chat("同伴甲", "别碰")

    assert "同伴甲：别碰" in gate.recent_human_texts()
    hits = await memory.search_entries("别碰", exclude_texts=set(gate.recent_human_texts()), wait_vector_ms=0)
    assert hits == []
