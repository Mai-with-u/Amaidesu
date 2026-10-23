"""VRChatProvider：默认动作表对应 SDK 默认 VRCEmote、动作写参数后延迟归零（不连真实 VRChat）"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from src.modules.avatar.platform.vrchat.vrchat_provider import VRChatProvider


def _connected(**config) -> VRChatProvider:
    provider = VRChatProvider(config={"emote_reset_ms": 50, **config})
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
