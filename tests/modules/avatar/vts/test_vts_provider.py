"""VTSProvider 单元测试（无需 VTS 外部环境）

覆盖：
- trigger_hotkey 按名解析优先 / hotkey_id 兜底 / 双空失败
- _hotkey_catalog_summary 与 list_tools 动态描述
- invoke 分发 vts_trigger_hotkey 的 name → find_by_name → trigger 链
- 工具 spec 的 provider 标识
"""

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.avatar.vts.vts_provider import VTSProvider, create_vts_provider
from src.modules.tools.models import ToolExecutionResult, ToolInvocation


def _build_provider(hotkey_list: Optional[List[Dict[str, Any]]] = None) -> VTSProvider:
    provider = create_vts_provider(config={"vts_host": "localhost", "vts_port": 8001})
    # 替换热键匹配器为受控 stub（不触网）
    matcher = MagicMock()
    matcher.hotkey_list = hotkey_list or []
    matcher.find_by_name = MagicMock(return_value=None)
    matcher.trigger_hotkey = AsyncMock(return_value=True)
    matcher.load_hotkeys = AsyncMock()
    provider.hotkey_matcher = matcher
    return provider


# =============================================================================
# trigger_hotkey：按名优先 / id 兜底
# =============================================================================


@pytest.mark.asyncio
async def test_trigger_hotkey_by_name_resolves_id():
    provider = _build_provider()
    provider.hotkey_matcher.find_by_name.return_value = "uuid-1"

    assert await provider.trigger_hotkey(name="Wave") is True

    provider.hotkey_matcher.find_by_name.assert_called_once_with("Wave")
    provider.hotkey_matcher.trigger_hotkey.assert_awaited_once_with("uuid-1")


@pytest.mark.asyncio
async def test_trigger_hotkey_falls_back_to_id_when_name_unmatched():
    provider = _build_provider()
    provider.hotkey_matcher.find_by_name.return_value = None

    assert await provider.trigger_hotkey(name="不存在", hotkey_id="uuid-2") is True

    provider.hotkey_matcher.trigger_hotkey.assert_awaited_once_with("uuid-2")


@pytest.mark.asyncio
async def test_trigger_hotkey_fails_when_name_unmatched_and_no_id():
    provider = _build_provider()
    provider.hotkey_matcher.find_by_name.return_value = None

    assert await provider.trigger_hotkey(name="不存在") is False
    provider.hotkey_matcher.trigger_hotkey.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_hotkey_fails_when_both_empty():
    provider = _build_provider()

    assert await provider.trigger_hotkey() is False
    provider.hotkey_matcher.find_by_name.assert_not_called()
    provider.hotkey_matcher.trigger_hotkey.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_hotkey_by_id_only():
    provider = _build_provider()

    assert await provider.trigger_hotkey(hotkey_id="uuid-3") is True
    provider.hotkey_matcher.find_by_name.assert_not_called()
    provider.hotkey_matcher.trigger_hotkey.assert_awaited_once_with("uuid-3")


# =============================================================================
# 动态描述：热键清单拼入工具描述
# =============================================================================


def test_hotkey_catalog_summary_empty_when_no_hotkeys():
    provider = _build_provider(hotkey_list=[])
    assert provider._hotkey_catalog_summary() == ""


def test_hotkey_catalog_summary_lists_names():
    provider = _build_provider(
        hotkey_list=[
            {"name": "Wave", "hotkeyID": "u1"},
            {"name": "Nod", "hotkeyID": "u2"},
        ]
    )
    summary = provider._hotkey_catalog_summary()
    assert "Wave" in summary
    assert "Nod" in summary


def test_list_tools_description_carries_hotkey_catalog():
    provider = _build_provider(hotkey_list=[{"name": "Wave", "hotkeyID": "u1"}])

    spec = next(s for s in provider.list_tools() if s.full_name == "vts_trigger_hotkey")
    assert "Wave" in spec.description
    # schema 不再强制 hotkey_id（按名优先）
    assert "hotkey_id" not in (spec.parameters_schema or {}).get("required", [])


def test_list_tools_provider_identifier():
    provider = _build_provider()
    for spec in provider.list_tools():
        assert spec.provider == "vts"


# =============================================================================
# invoke 分发：vts_trigger_hotkey 参数契约
# =============================================================================


@pytest.mark.asyncio
async def test_invoke_trigger_hotkey_with_name():
    provider = _build_provider()
    provider.hotkey_matcher.find_by_name.return_value = "uuid-9"

    result = await provider.invoke(
        ToolInvocation(tool_name="vts_trigger_hotkey", arguments={"name": "Wave"}, source="test")
    )

    assert result.success is True
    provider.hotkey_matcher.trigger_hotkey.assert_awaited_once_with("uuid-9")


@pytest.mark.asyncio
async def test_invoke_trigger_hotkey_without_args_fails_gracefully():
    provider = _build_provider()

    result = await provider.invoke(
        ToolInvocation(tool_name="vts_trigger_hotkey", arguments={}, source="test")
    )

    assert result.success is False
