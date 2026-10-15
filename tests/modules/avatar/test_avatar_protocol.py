"""AvatarProvider 协议：三个适配器满足结构契约，缺成员的对象被拒绝，包级与模块级是同一对象；VRChat 手势表取值固定"""

from __future__ import annotations

import pytest

from src.modules.avatar import AvatarProvider
from src.modules.avatar.platform.vrchat.vrchat_provider import VRChatProvider
from src.modules.avatar.platform.vts.vts_provider import VTSProvider
from src.modules.avatar.platform.warudo.warudo_provider import WarudoProvider
from src.modules.avatar.protocol import AvatarProvider as AvatarProviderDirect


@pytest.mark.parametrize("provider_cls", [VTSProvider, WarudoProvider, VRChatProvider])
def test_providers_satisfy_protocol(provider_cls) -> None:
    assert isinstance(provider_cls(config={}), AvatarProvider)


def test_protocol_rejects_incomplete_objects_and_has_one_identity() -> None:
    class Partial:  # 缺契约成员（成员存在性检查）
        PROVIDER_NAME = "partial"
        name = "partial"

    assert not any(isinstance(obj, AvatarProvider) for obj in (Partial(), None, {}))
    assert AvatarProvider is AvatarProviderDirect


def test_vrchat_gesture_map_values_are_fixed_by_the_avatar_parameter_contract() -> None:
    # 值对应 VRChat Avatar 的 Gesture 参数枚举，改动会让所有已有模型的手势错位
    assert VRChatProvider.GESTURE_MAP == {
        "Neutral": 0,
        "Wave": 1,
        "Peace": 2,
        "ThumbsUp": 3,
        "RocknRoll": 4,
        "HandGun": 5,
        "Point": 6,
        "Victory": 7,
        "Cross": 8,
    }
