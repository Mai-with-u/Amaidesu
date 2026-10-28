"""主播 Agent 接线测试共用构造器与替身。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from src.agents.streamer.config import StreamerConfig
from src.agents.streamer.streamer_agent import StreamerAgent
from tests.modules.llm.fakes import FakeLLMManager

__all__ = ["FakeRoundExecutor", "make_streamer_agent", "streamer_config", "stub_llm", "stub_prompt"]


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


def stub_llm() -> FakeLLMManager:
    """不应被真正调用的 LLM：调用即得到失败响应，而不是悬挂（统一假件 fail 态）。"""
    return FakeLLMManager(fail=True, fail_error="not used")


def stub_prompt() -> MagicMock:
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    return prompt


class FakeRoundExecutor:
    """决策轮执行器替身：``execute`` 记录调用并返回固定结果，供测试替换决策执行半。

    缺省不消费运营递话提醒队列（真实执行器在任何决策窗入口取空队列）；
    ``drain_reminders = True`` 后经 ``reminders_provider`` 取空，模拟正常送达。
    """

    def __init__(self, result: Optional[Dict[str, Any]] = None) -> None:
        self.result: Dict[str, Any] = result if result is not None else {}
        self.calls: List[Dict[str, Any]] = []
        self.drain_reminders: bool = False
        self.reminders_provider: Any = None

    async def execute(
        self,
        batch: List[Any],
        *,
        forced: bool,
        trigger_cause: Any,
        proactive: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self.calls.append(
            {"batch": list(batch), "forced": forced, "trigger_cause": trigger_cause, "proactive": proactive, **kwargs}
        )
        if self.drain_reminders and self.reminders_provider is not None:
            self.reminders_provider()
        return dict(self.result)


def make_streamer_agent(
    sections: Optional[Dict[str, Any]] = None,
    *,
    llm: Any = None,
    prompt: Any = None,
    planner: Any = None,
    round_executor: Any = None,
    reply_provider: Any = None,
    session_scope: Any = None,
    mouth: Any = None,
    **kwargs: Any,
) -> StreamerAgent:
    """最小 StreamerAgent；其余构造参数（event_bus / tts_engine / chat_repo 等）原样透传。

    ``planner`` / ``round_executor`` / ``reply_provider`` / ``session_scope`` / ``mouth``
    对应 StreamerAgent 的构造期替身注入面：传替身时 Agent 直接使用，不内建真件，
    测试不必穿透私有组装链替换 LLM/规划器/决策执行半。
    """
    return StreamerAgent(
        config=streamer_config(sections),
        llm_manager=llm or stub_llm(),
        prompt_manager=prompt or stub_prompt(),
        planner=planner,
        round_executor=round_executor,
        reply_provider=reply_provider,
        session_scope=session_scope,
        mouth=mouth,
        **kwargs,
    )
