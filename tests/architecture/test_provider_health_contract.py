"""
工具 Provider 探活契约架构测试。

约束：所有注册到 ``ToolRegistry`` 的 Provider 必须继承 ``BaseToolProvider``
（见 ``src/modules/tools/provider.py``），并按需覆写 ``health_check``。

外部连接的 Provider（MCP server）必须**真正**覆写 ``health_check``——仅继承
基类而未覆写的话，运行时会拿到默认实现（返回 True），熔断器无法识别连接
异常，等价于"无探活"。本测试用 ``cls.health_check is not BaseToolProvider.health_check``
判定**实际覆写**，避免默认实现被误算成有效探活。

待接入：VTSProvider / WarudoProvider / VRChatProvider / OBSProvider —— 它们维护
外部连接但当前沿用基类默认实现（返回 True），实际探活能力待后续任务补齐。
后续接入只需：1) 改类为重写 health_check；2) 把类加进下方的 EXPECTED_HEALTH_CHECK_PROVIDERS。
"""

from __future__ import annotations

from src.modules.mcp.provider import McpToolProvider
from src.modules.tools.provider import BaseToolProvider


# 维护外部连接且必须重写 health_check 的 Provider 清单（curated）。
# 待接入（VTS / Warudo / VRChat / OBS）—— 后续补齐真实探活后并入此清单。
EXPECTED_HEALTH_CHECK_PROVIDERS: list[type] = [
    McpToolProvider,
]


class TestProviderHealthContract:
    """Provider 探活契约：保证维护外部连接的 Provider 都已重写 health_check。"""

    def test_curated_providers_inherit_base_tool_provider(self) -> None:
        """清单内每个 Provider 必须继承 ``BaseToolProvider``。"""
        missing_base = [cls for cls in EXPECTED_HEALTH_CHECK_PROVIDERS if not issubclass(cls, BaseToolProvider)]
        assert not missing_base, (
            f"以下 Provider 未继承 BaseToolProvider：{missing_base}。请先完成 BaseToolProvider 迁移再接入探活契约。"
        )

    def test_curated_providers_override_health_check(self) -> None:
        """清单内每个 Provider 必须真正覆写 ``health_check``（非基类默认实现）。

        默认实现是 ``async def health_check(self) -> bool: return True``——语义为
        "无可检查之物"。外部连接型 Provider 重写后必须做真实连接检查，否则熔断器
        无法识别连接异常，等价于无探活。
        """
        not_overridden = [
            cls for cls in EXPECTED_HEALTH_CHECK_PROVIDERS if cls.health_check is BaseToolProvider.health_check
        ]
        assert not not_overridden, (
            f"以下 Provider 未重写 health_check（仍使用 BaseToolProvider 默认实现）："
            f"{not_overridden}。维护外部连接的 Provider 必须覆写 health_check 做真实"
            f"连接检查；仅继承基类无法识别连接异常。"
        )

    def test_curated_providers_importable(self) -> None:
        """清单内类能正常导入——防止 import 路径漂移导致清单静默失效。"""
        for cls in EXPECTED_HEALTH_CHECK_PROVIDERS:
            assert cls.__module__, f"{cls.__name__} 缺少 __module__ 属性"
            assert hasattr(cls, "health_check"), f"{cls.__name__} 缺少 health_check 方法"
