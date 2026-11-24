"""成就描述 detail 采集兼容透传与流程单简报/备注/结束格式化单元测试。"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from src.agents.minecraft.chat_collector import MaicraftChatCollector, extract_chat_detail
from src.agents.minecraft.lead_mode import (
    clean_segment_notes,
    format_lead_handover_brief,
    format_rundown_update_notice,
)
from src.modules.agents.lead import LeadHandoverContext
from src.modules.events.payloads.game_chat import GameChatPayload
from tests.agents.minecraft_collector_fakes import FakeEventBus, FakeMcpClient, events_page, fake_client_factory


@pytest.mark.parametrize(
    ("raw_event", "expected"),
    [
        ({"detail": "使用盾牌格挡一次弹射物攻击"}, "使用盾牌格挡一次弹射物攻击"),
        ({"detail": "  收集全套下界合金装备  \n"}, "收集全套下界合金装备"),
        ({"detail": "   "}, ""),
        ({"detail": None}, ""),
        ({"detail": True}, ""),
        ({}, ""),
    ],
)
def test_extract_chat_detail_safely_normalizes_upstream_values(raw_event: Dict[str, Any], expected: str) -> None:
    """安全提取上游聊天事件的 detail 描述，缺失、空白或布尔值统一归为空串。"""
    assert extract_chat_detail(raw_event) == expected


async def test_chat_collector_forwards_system_advancement_with_detail() -> None:
    """聊天采集器收到带 detail 的成就事件时稳定转出并透传 detail 描述。"""
    bus = FakeEventBus()
    collector = MaicraftChatCollector(  # type: ignore[arg-type]
        config={},
        event_bus=bus,
        client_factory=fake_client_factory(),
    )
    assert await collector._ensure_ready() is True
    client = FakeMcpClient.instances[-1]
    client.pages.append(events_page([{"cursor": 1, "kind": "system", "text": "接手前"}], cursor=1))
    await collector._drain()

    advancement_event = {
        "cursor": 2,
        "kind": "system",
        "text": "Alex 达成了进度 [不是今天，谢谢]",
        "detail": "使用盾牌格挡一次弹射物攻击",
        "translation_key": "chat.type.advancement.task",
    }
    client.pages.append(events_page([advancement_event], cursor=2))
    await collector._drain()

    assert len(bus.events) == 1
    _event_name, payload = bus.events[0]
    assert isinstance(payload, GameChatPayload)
    assert payload.content == "Alex 达成了进度 [不是今天，谢谢]"
    assert payload.message_key == "chat.type.advancement.task"
    assert payload.detail == "使用盾牌格挡一次弹射物攻击"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("   ", ""),
        ("______", ""),
        ("___ / ___", ""),
        ("本场方向：______", ""),
        ("本场大方向：搭建动力磨粉线\n备注：______", "本场大方向：搭建动力磨粉线"),
        ("优先推进 create_v6 主线", "优先推进 create_v6 主线"),
        ("今晚目标：探访东边遗迹\n顺便测试传送带", "今晚目标：探访东边遗迹；顺便测试传送带"),
    ],
)
def test_clean_segment_notes_filters_placeholders_and_keeps_real_content(raw: str, expected: str) -> None:
    """纯下划线及未填模板槽位被滤除，真实备注与含单下划线标识符正常保留。"""
    assert clean_segment_notes(raw) == expected


def test_format_lead_handover_brief_supports_notes_and_rundown_finished() -> None:
    """交接简报在普通环节渲染有效备注、过滤占位符，并在流程单结束时清空旧环节目标切为自由游玩。"""
    ctx = LeadHandoverContext(
        segment_id="seg-play",
        segment_title="工业流水线",
        segment_goal="搭建自动化压板机",
        expected_ms=1_200_000,
        save_name="机械动力周目",
        memory_brief="之前约定把厂房建在西侧平地",
    )

    with_notes = format_lead_handover_brief(ctx, segment_notes="主线大方向：解锁黄铜时代")
    assert "- 当前环节：工业流水线（预计时长：约 20 分钟）" in with_notes
    assert "- 环节目标：搭建自动化压板机" in with_notes
    assert "- 本场备注/大方向：主线大方向：解锁黄铜时代" in with_notes

    with_placeholder = format_lead_handover_brief(ctx, segment_notes="方向：______")
    assert "本场备注/大方向" not in with_placeholder
    assert "______" not in with_placeholder

    finished_brief = format_lead_handover_brief(ctx, segment_notes="主线大方向：解锁黄铜时代", rundown_finished=True)
    assert finished_brief.startswith("[主控交接简报]")
    assert "本场流程单已结束" in finished_brief
    assert "自由游玩阶段" in finished_brief
    assert "无收尾总结或点名感谢要求" in finished_brief
    assert "工业流水线" not in finished_brief
    assert "搭建自动化压板机" not in finished_brief
    assert "解锁黄铜时代" not in finished_brief
    assert "- 当前存档：机械动力周目" in finished_brief
    assert "之前约定把厂房建在西侧平地" in finished_brief


def test_format_rundown_update_notice_covers_notes_and_finished() -> None:
    """切段通知渲染有效备注且滤掉占位符；finish 通知明确进入自由游玩阶段。"""
    normal = format_rundown_update_notice(
        segment_title="探矿阶段",
        segment_goal="凑齐下界合金碎片",
        segment_notes="本场主线：补齐装备与附魔",
        expected_ms=900_000,
    )
    assert "探矿阶段" in normal
    assert "本场备注/大方向：本场主线：补齐装备与附魔" in normal

    placeholder = format_rundown_update_notice(
        segment_title="探矿阶段",
        segment_goal="凑齐下界合金碎片",
        segment_notes="______",
    )
    assert "______" not in placeholder
    assert "本场备注/大方向" not in placeholder

    finished = format_rundown_update_notice(rundown_finished=True)
    assert finished.startswith("[流程单切换]")
    assert "本场流程单已结束" in finished
    assert "自由游玩阶段" in finished
    assert "旧环节目标已清空" in finished
