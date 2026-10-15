"""工具 Provider 探活契约：维护外部连接的 Provider 必须继承 BaseToolProvider 并真正覆写 health_check

仅继承基类默认实现（返回 True，语义"无可检查之物"）等价于无探活：熔断器识别不了连接异常。
待接入：VTSProvider / WarudoProvider / VRChatProvider / OBSProvider 当前沿用默认实现，补齐真实探活后并入下面的清单。
"""

from __future__ import annotations

import pytest

from src.modules.mcp.provider import McpToolProvider
from src.modules.tools.provider import BaseToolProvider

EXPECTED_HEALTH_CHECK_PROVIDERS: list[type] = [McpToolProvider]


@pytest.mark.parametrize("cls", EXPECTED_HEALTH_CHECK_PROVIDERS, ids=lambda c: c.__name__)
def test_external_connection_providers_override_health_check(cls: type) -> None:
    assert issubclass(cls, BaseToolProvider), f"{cls.__name__} 未继承 BaseToolProvider，请先完成迁移再接入探活契约"
    assert cls.health_check is not BaseToolProvider.health_check, (
        f"{cls.__name__} 仍使用基类默认探活，熔断器无法识别连接异常"
    )
