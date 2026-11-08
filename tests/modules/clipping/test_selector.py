"""AI 选段解析与收敛：JSON 抠取、墙钟换算、时长约束；_ask 的重试语义。"""

from __future__ import annotations

import pytest

from src.modules.clipping.config_schema import ClippingConfigSchema
from src.modules.clipping.selector import (
    ClipSelectionError,
    ClipSelector,
    _extract_json_array,
    _suggestions_of,
)
from src.modules.storage.models.sessions import LiveSession
from tests.modules.llm.fakes import FakeLLMManager
from src.modules.llm.payload import Response

_SESSION = LiveSession(
    id=1,
    started_at_ms=1_000_000,
    ended_at_ms=1_000_000 + 3600_000,
    updated_at_ms=1_000_000 + 3600_000,
)


def test_extract_json_array_tolerates_fences_and_chatter() -> None:
    assert _extract_json_array('```json\n[{"a": 1}]\n```') == '[{"a": 1}]'
    assert _extract_json_array("好的，如下：[1, 2] 请查收") == "[1, 2]"
    with pytest.raises(ClipSelectionError, match="JSON 数组"):
        _extract_json_array("没有数组")


def test_suggestions_convert_and_clamp() -> None:
    payload = (
        '[{"start_s": 1.5, "end_s": 61.5, "title": "开场", "reason": "互动"},'
        ' {"start_s": 100, "end_s": 999999, "title": "超长", "reason": ""},'
        ' {"start_s": 200, "end_s": 210, "title": "太短", "reason": ""},'
        ' {"start_s": 4000, "end_s": 4100, "title": "越界", "reason": ""}]'
    )
    suggestions = _suggestions_of(payload, session=_SESSION, min_ms=30_000, max_ms=300_000)
    titles = [s.title for s in suggestions]
    # 越界段起点已超场次终点被收敛到终点后不足最短时长 → 丢弃
    assert titles == ["开场", "超长"]
    first, second = suggestions
    assert (first.start_ms, first.end_ms) == (1_000_000 + 1500, 1_000_000 + 61_500)
    # 超长段截到 max
    assert second.end_ms - second.start_ms == 300_000


def test_suggestions_rejects_malformed() -> None:
    with pytest.raises(ClipSelectionError, match="不是合法 JSON"):
        _suggestions_of("[{start_s: 1}]", session=_SESSION, min_ms=1000, max_ms=60_000)
    with pytest.raises(ClipSelectionError, match="找不到 JSON 数组"):
        _suggestions_of('{"a": 1}', session=_SESSION, min_ms=1000, max_ms=60_000)
    with pytest.raises(ClipSelectionError, match="缺 start_s"):
        _suggestions_of('[{"title": "x"}]', session=_SESSION, min_ms=1000, max_ms=60_000)
    with pytest.raises(ClipSelectionError, match="任何满足时长约束"):
        _suggestions_of("[]", session=_SESSION, min_ms=1000, max_ms=60_000)


def _selector(llm: FakeLLMManager) -> ClipSelector:
    """仓储只被 select() 用到；本文件只测解析与 _ask，传占位即可。"""
    return ClipSelector(
        llm,  # type: ignore[arg-type]
        chat_repo=None,  # type: ignore[arg-type]
        event_repo=None,  # type: ignore[arg-type]
        session_repo=None,  # type: ignore[arg-type]
        topic_repo=None,  # type: ignore[arg-type]
        config=ClippingConfigSchema(),
    )


async def test_ask_retries_once_then_succeeds() -> None:
    good = '[{"start_s": 1, "end_s": 61, "title": "t", "reason": "r"}]'
    llm = FakeLLMManager(responses=[Response(success=True, content="瞎写的"), Response(success=True, content=good)])
    result = await _selector(llm)._ask("素材", system="s", profile="settle")
    assert result == good
    assert len(llm.calls) == 2


async def test_ask_gives_up_after_two_failures() -> None:
    llm = FakeLLMManager(responses=[Response(success=True, content="x"), Response(success=True, content="y")])
    with pytest.raises(ClipSelectionError, match="两次都无法解析"):
        await _selector(llm)._ask("素材", system="s", profile="settle")
    assert len(llm.calls) == 2
