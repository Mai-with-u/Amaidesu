"""VRChatProvider：默认动作表对应 SDK 默认 VRCEmote、动作写参数后延迟归零、聊天框随字幕服务显示（不连真实 VRChat）"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from src.modules.avatar.platform.vrchat.chatbox import CHATBOX_MAX_CHARS, VRChatChatboxBackend, fit_chatbox_text
from src.modules.avatar.platform.vrchat.vrchat_provider import VRChatProvider, register_vrchat_tools
from src.modules.subtitle.service import SubtitleService
from src.modules.tools.registry import ToolRegistry


def _connected(**config) -> VRChatProvider:
    provider = VRChatProvider(config={"emote_reset_ms": 50, "chatbox_min_interval_ms": 50, **config})
    provider.osc_client = MagicMock()
    provider._is_connected = True
    return provider


def _sent(provider: VRChatProvider) -> list:
    return [c.args for c in provider.osc_client.send_message.call_args_list]


def test_default_emotes_follow_the_sdk_default_vrcemote_menu() -> None:
    provider = VRChatProvider(config={})
    # SDK 默认动作菜单的 VRCEmote 取值；手势名（Peace、ThumbsUp 等）属于只读的 GestureLeft/Right，不在这里
    assert provider.emote_parameter == "VRCEmote"
    assert list(provider.emotes.items())[:6] == [
        ("Wave", 1),
        ("Clap", 2),
        ("Point", 3),
        ("Cheer", 4),
        ("Dance", 5),
        ("Backflip", 6),
    ]


async def test_emote_writes_the_parameter_then_resets_to_zero() -> None:
    provider = _connected(emote_parameter="MyEmote", emotes={"Dance": 5, "Wave": 1})
    assert (await provider.trigger_preset_action("Dance")).success is True
    await provider.trigger_preset_action("Wave")  # 新动作取消上一个归零，只归零一次
    await asyncio.sleep(0.12)
    assert _sent(provider) == [
        ("/avatar/parameters/MyEmote", 5),
        ("/avatar/parameters/MyEmote", 1),
        ("/avatar/parameters/MyEmote", 0),
    ]

    unknown = await provider.trigger_preset_action("Peace")
    assert unknown.success is False and unknown.structured_content["available_actions"] == ["Dance", "Wave"]


def test_chatbox_text_is_flattened_and_capped() -> None:
    assert fit_chatbox_text("第一行\n第二行") == "第一行 第二行"
    capped = fit_chatbox_text("长" * 200)
    assert len(capped) == CHATBOX_MAX_CHARS and capped.endswith("…")


async def test_chatbox_follows_subtitles_and_only_sends_the_latest_line_within_the_interval() -> None:
    subtitles = SubtitleService()
    provider = register_vrchat_tools(ToolRegistry(), {"chatbox_min_interval_ms": 50}, subtitle_service=subtitles)
    assert subtitles.backend_count == 1  # 开着聊天框时登记为字幕后端
    provider.osc_client, provider._is_connected = MagicMock(), True

    await subtitles.show("第一句")  # 距上一条已满间隔：立即发出
    await subtitles.show("第二句")
    await subtitles.clear()  # 间隔内的第二句被清空覆盖，到点只发最新一条
    await asyncio.sleep(0.15)
    assert _sent(provider) == [("/chatbox/input", ["第一句", True, False]), ("/chatbox/input", ["", True, False])]


async def test_chatbox_off_or_closed_sends_nothing() -> None:
    subtitles = SubtitleService()
    register_vrchat_tools(ToolRegistry(), {"chatbox_enabled": False}, subtitle_service=subtitles)
    assert subtitles.backend_count == 0  # 关掉聊天框不登记

    sent: list = []
    backend = VRChatChatboxBackend(sent.append, min_interval_ms=0)
    await backend.close()
    await backend.show("不会发出")
    await asyncio.sleep(0.01)
    assert sent == []


def test_connection_summary_reflects_connection_flag() -> None:
    """观测面契约：摘要连接态取自身标志，stats 透传 get_stats。"""
    provider = VRChatProvider(config={})
    provider._is_connected = True
    summary = provider.connection_summary()
    assert summary["is_connected"] is True
    assert summary["stats"]["is_connected"] is True
