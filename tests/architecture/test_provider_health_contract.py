"""工具 Provider 探活契约：维护外部连接的 Provider 必须继承 BaseToolProvider 并真正覆写 health_check

仅继承基类默认实现（返回 True，语义"无可检查之物"）等价于无探活：熔断器识别不了连接异常。
本文件同时守护清单的完备性：扫描 src/ 下全部 BaseToolProvider 具名子类，凡维护外部连接
且已实现真实探活的进 EXPECTED 清单；尚未接入的按待接入豁免并注明原因——新增子类既不在
清单也不在豁免里时本测试红，防止新 Provider 悄悄绕过探活约定。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.modules.avatar.platform.vts.vts_provider import VTSProvider
from src.modules.mcp.provider import McpToolProvider
from src.modules.tools.provider import BaseToolProvider

EXPECTED_HEALTH_CHECK_PROVIDERS: list[type] = [McpToolProvider, VTSProvider]

# 待接入 / 无连接语义的具名子类豁免清单：子类名 → 豁免理由（逐项注明）
PENDING_HEALTH_CHECK_PROVIDERS: dict[str, str] = {
    "WarudoProvider": "维护外部 WebSocket 连接，真实探活未实现，待接入后并入 EXPECTED 清单",
    "OBSProvider": "维护外部 WebSocket 连接，真实探活未实现，待接入后并入 EXPECTED 清单",
    "VRChatProvider": "维护外部连接，真实探活未实现，待接入后并入 EXPECTED 清单",
    "WebSearchProvider": "无状态、无长连接：invoke 永不抛异常、失败统一转结果由熔断器计数，基类默认探活语义成立",
    "LookAtScreenProvider": "屏幕采集 / 文本读取后端经构造器注入，Provider 本体无连接生命周期，基类默认探活语义成立",
    "ExpressionSheetProvider": "静态差分图资源，无外部连接，基类默认探活语义成立",
    "ReplyToolProvider": "主播 Agent 域内部调用面，不维护外部连接，基类默认探活语义成立",
    "GameToolProvider": "游戏工具的 Agent 域内部调用面，不维护外部连接，基类默认探活语义成立",
    "AgentControlProvider": "Agent 控制面内部调用面，不维护外部连接，基类默认探活语义成立",
    "AvatarActProvider": "委托面捕合成器执行身体动作，不维护外部连接，基类默认探活语义成立",
}

SRC = Path(__file__).resolve().parents[2] / "src"
# 子类判定认可的基类名（含连接态中间层 ConnectionToolProvider 的传递子类）
_BASE_CLASS_NAMES = {"BaseToolProvider", "ConnectionToolProvider"}


def _named_provider_subclasses() -> list[tuple[str, str]]:
    """AST 扫描 src/ 下 BaseToolProvider 的具名子类（类名 → 相对文件路径）"""
    found: list[tuple[str, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name.startswith("_") or node.name in _BASE_CLASS_NAMES:
                continue
            base_names = {base.id for base in node.bases if isinstance(base, ast.Name)}
            if base_names & _BASE_CLASS_NAMES:
                found.append((node.name, str(path.relative_to(SRC.parent))))
    return found


@pytest.mark.parametrize("cls", EXPECTED_HEALTH_CHECK_PROVIDERS, ids=lambda c: c.__name__)
def test_external_connection_providers_override_health_check(cls: type) -> None:
    assert issubclass(cls, BaseToolProvider), f"{cls.__name__} 未继承 BaseToolProvider，请先完成迁移再接入探活契约"
    assert cls.health_check is not BaseToolProvider.health_check, (
        f"{cls.__name__} 仍使用基类默认探活，熔断器无法识别连接异常"
    )


def test_expected_list_entries_are_real_named_subclasses() -> None:
    """EXPECTED 清单里的类必须真实存在于扫描结果中（防止 import 漂移让清单空转）"""
    scanned_names = {name for name, _ in _named_provider_subclasses()}
    for cls in EXPECTED_HEALTH_CHECK_PROVIDERS:
        assert cls.__name__ in scanned_names, f"{cls.__name__} 不再是 src/ 下可扫描到的 BaseToolProvider 具名子类"


def test_every_named_provider_subclass_is_accounted_for() -> None:
    """全部具名子类要么在 EXPECTED 清单、要么在豁免清单，新子类漏登记即红"""
    expected_names = {cls.__name__ for cls in EXPECTED_HEALTH_CHECK_PROVIDERS}
    unknown = [
        f"{name} ({path})"
        for name, path in _named_provider_subclasses()
        if name not in expected_names and name not in PENDING_HEALTH_CHECK_PROVIDERS
    ]
    assert not unknown, (
        "发现未接入探活契约对账的 BaseToolProvider 具名子类："
        "维护外部连接的请实现真实 health_check 后并入 EXPECTED 清单，"
        "无连接语义或待接入的请加入 PENDING_HEALTH_CHECK_PROVIDERS 并注明原因：\n"
        + "\n".join(f"  - {item}" for item in unknown)
    )
