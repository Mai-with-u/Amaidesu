"""主播 Agent 接线测试共用构造器。"""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.llm.payload import Response


def streamer_config(sections: Optional[Dict[str, Any]] = None) -> StreamerConfig:
    """测试缺省：关闭主动发言与词过滤，攒批窗口缩短；sections 按子段浅合并覆盖。"""
    merged: Dict[str, Any] = {
        "proactive": {"enabled": False},
        "word_filter": {"enabled": False},
        "batch": {"batch_window_ms": 100, "tick_interval_ms": 50},
    }
    for key, value in (sections or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return StreamerConfig.from_dict(merged)


def stub_llm() -> MagicMock:
    """不应被真正调用的 LLM：调用即得到失败响应，而不是悬挂。"""
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=False, error="not used"))
    return llm


def stub_prompt() -> MagicMock:
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    return prompt


def make_streamer_agent(
    sections: Optional[Dict[str, Any]] = None,
    *,
    llm: Any = None,
    prompt: Any = None,
    **kwargs: Any,
) -> StreamerAgent:
    """最小 StreamerAgent；其余构造参数（event_bus / tts_engine / chat_repo 等）原样透传。"""
    return StreamerAgent(
        config=streamer_config(sections),
        llm_manager=llm or stub_llm(),
        prompt_manager=prompt or stub_prompt(),
        **kwargs,
    )
