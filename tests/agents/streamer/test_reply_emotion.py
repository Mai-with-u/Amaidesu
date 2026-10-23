"""reply 工具的情绪值收敛：名单外的近义词就近映射，不为一个情绪词打回整轮。

实测 gemini 常填 cheerful / playful / amused 这类不在枚举里的词，一场 22 句台词被拒 18 次，
每次都让 Planner 多跑一步。
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from src.agents.streamer.tools.reply_tool import ReplyToolProvider
from src.modules.tools.models import ToolExecutionResult, ToolInvocation


class _Replyer:
    """表达引擎桩：固定返回一句台词。"""

    async def generate(self, **kwargs: Any) -> dict[str, Any]:
        return {"speech": "掉河里了，行吧", "llm_request_id": None}


async def _reply(emotion: Any, intensity: float = 0.6) -> ToolExecutionResult:
    provider = ReplyToolProvider(replyer=_Replyer())
    arguments = {"topic_summary": "主播掉进河里", "is_novel": True, "emotion": emotion, "intensity": intensity}
    return await provider.invoke(
        ToolInvocation(tool_name="streamer_reply", arguments=arguments, source="planner-react")
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("happy", "happy"),  # 合法值原样通过
        ("Cheerful", "happy"),  # 大小写不敏感
        ("playful", "smug"),
        ("lively", "excited"),
        ("amused", "happy"),
        ("pleased", "happy"),
        ("一脸懵", "neutral"),  # 映射不到的一律按 neutral
        (None, "neutral"),
    ],
)
async def test_out_of_vocabulary_emotion_is_mapped_not_rejected(raw: Optional[str], expected: str) -> None:
    result = await _reply(raw)
    assert result.success is True
    assert result.structured_content["emotion"] == {"name": expected, "intensity": 0.6}


async def test_invalid_intensity_still_rejected() -> None:
    """强度越界仍然打回：它是数值约束，不是措辞近义词。"""
    result = await _reply("happy", intensity=1.5)
    assert result.success is False and "情绪强度无效" in (result.error_message or "")
