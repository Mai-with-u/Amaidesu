"""AvatarProvider 协议契约测试。

覆盖：

- 三个皮套 Provider（VTS / Warudo / VRChat）都能通过
  ``isinstance(p, AvatarProvider)`` 校验（结构契约的"形"满足）；
- 缺成员的类不满足契约（负例，证明 ``@runtime_checkable`` 生效）；
- 协议从 ``src.modules.avatar`` 与 ``src.modules.avatar.protocol``
  都能 import 到同一对象（包对外暴露路径一致性）。
"""

from __future__ import annotations

import pytest

from src.modules.avatar import AvatarProvider
from src.modules.avatar.protocol import AvatarProvider as AvatarProviderDirect
from src.modules.avatar.platform.vrchat.vrchat_provider import VRChatProvider
from src.modules.avatar.platform.vts.vts_provider import VTSProvider
from src.modules.avatar.platform.warudo.warudo_provider import WarudoProvider


class TestProvidersConform:
    def test_vts_satisfies_protocol(self):
        """``VTSProvider`` 在最小配置下满足 ``AvatarProvider`` 契约。"""
        provider = VTSProvider(config={})
        assert isinstance(provider, AvatarProvider)

    def test_warudo_satisfies_protocol(self):
        """``WarudoProvider`` 在最小配置下满足 ``AvatarProvider`` 契约。"""
        provider = WarudoProvider(config={})
        assert isinstance(provider, AvatarProvider)

    def test_vrchat_satisfies_protocol(self):
        """``VRChatProvider`` 在最小配置下满足 ``AvatarProvider`` 契约。"""
        provider = VRChatProvider(config={})
        assert isinstance(provider, AvatarProvider)


class TestProtocolNegative:
    def test_missing_contract_members_fail(self):
        """缺契约成员的类不满足协议（成员存在性检查）。"""

        class _Partial:
            PROVIDER_NAME = "partial"

            @property
            def name(self) -> str:
                return self.PROVIDER_NAME

        assert not isinstance(_Partial(), AvatarProvider)

    def test_none_and_dict_fail(self):
        """``None`` / ``dict`` 不满足契约。"""
        assert not isinstance(None, AvatarProvider)  # type: ignore[arg-type]
        assert not isinstance({}, AvatarProvider)  # type: ignore[arg-type]


class TestProtocolExportPath:
    def test_package_and_module_import_same_object(self):
        """包级与模块级 import 指向同一协议对象。"""
        assert AvatarProvider is AvatarProviderDirect


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
