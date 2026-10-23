"""AvatarProvider 协议：各适配器满足结构契约，缺成员的对象被拒绝，包级与模块级是同一对象"""

from __future__ import annotations

import pytest

from src.modules.avatar import AvatarProvider
from src.modules.avatar.platform.expression_sheet.expression_sheet_provider import ExpressionSheetProvider
from src.modules.avatar.platform.vrchat.vrchat_provider import VRChatProvider
from src.modules.avatar.platform.vts.vts_provider import VTSProvider
from src.modules.avatar.platform.warudo.warudo_provider import WarudoProvider
from src.modules.avatar.protocol import AvatarProvider as AvatarProviderDirect


@pytest.mark.parametrize("provider_cls", [VTSProvider, WarudoProvider, VRChatProvider, ExpressionSheetProvider])
def test_providers_satisfy_protocol(provider_cls) -> None:
    assert isinstance(provider_cls(config={}), AvatarProvider)


def test_protocol_rejects_incomplete_objects_and_has_one_identity() -> None:
    class Partial:  # 缺契约成员（成员存在性检查）
        PROVIDER_NAME = "partial"
        name = "partial"

    assert not any(isinstance(obj, AvatarProvider) for obj in (Partial(), None, {}))
    assert AvatarProvider is AvatarProviderDirect
