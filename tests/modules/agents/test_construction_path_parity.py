"""Agent 两构造路径等价性：启动装配（main._register_agents_from_config）与 Dashboard 动态启用（AgentManager.enable_agent）

两者都必须经 factory.instantiate_agent 单一构造路径，且对同名 Agent 传入同一集合的构造关键字，
否则"启动时能跑、动态启用后行为漂移"（如 llm_profile / speech 管线两侧不一致）会静默发生。
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock, patch

import main as app_main
from src.agents.minecraft.agent import MINECRAFT_PROFILE
from src.modules.agents.base import BaseAgent
from src.modules.agents.factory import SUPPORTED_AGENTS, instantiate_agent
from src.modules.agents.manager import AgentManager
from src.modules.tools.registry import ToolRegistry

# 两路径共用的服务面（值本身不重要，等价性比较的是关键字集合）
SPEECH = {"enabled": False, "max_queue": 3, "render_timeout_ms": 60000}
SERVICES: dict[str, Any] = {
    "llm_manager": object(),
    "prompt_manager": object(),
    "event_bus": object(),
    "tool_registry": ToolRegistry(),
    "thinking_sink": object(),
    "speech_config": SPEECH,
    "tts_engine": object(),
    "subtitle_service": object(),
    "session_manager": object(),
    "rundown_repo": object(),
    "chat_repo": object(),
    "sessions_repo": object(),
    "topic_repo": object(),
    "viewer_repo": object(),
    "viewer_profile_repo": object(),
    "recap_repo": object(),
    "event_repo": object(),
    "context_assembler_config": None,
    "task_tracker": object(),
}


def _stub_agent(name: str) -> BaseAgent:
    """最小可启动 Agent（enable_agent 要求实例名 = 段名）。"""
    return type(
        "StubAgent", (BaseAgent,), {"name": name, "description": "parity stub", "list_tools": lambda self: []}
    )()


async def test_startup_and_enable_paths_pass_the_same_constructor_keywords() -> None:
    calls: list[dict[str, Any]] = []

    def fake(name, config, **kwargs):
        calls.append({"name": name, "config": config, **kwargs})
        return _stub_agent(name)

    # main 顶层 from-import 绑定了自己的命名空间引用，启动路径与 enable 路径须分别拦截
    with (
        patch("main.instantiate_agent", side_effect=fake),
        patch("src.modules.agents.factory.instantiate_agent", side_effect=fake),
    ):
        await app_main._register_agents_from_config(
            AgentManager(),
            {"enabled": list(SUPPORTED_AGENTS)},
            config_service=None,
            llm_service=SERVICES["llm_manager"],
            event_bus=SERVICES["event_bus"],
            tool_registry=SERVICES["tool_registry"],
            task_tracker=SERVICES["task_tracker"],
            tts_section=SPEECH,
            tts_engine=SERVICES["tts_engine"],
            subtitle_service=SERVICES["subtitle_service"],
            session_manager=SERVICES["session_manager"],
            rundown_repo=SERVICES["rundown_repo"],
            chat_repo=SERVICES["chat_repo"],
            sessions_repo=SERVICES["sessions_repo"],
            topic_repo=SERVICES["topic_repo"],
            viewer_repo=SERVICES["viewer_repo"],
            viewer_profile_repo=SERVICES["viewer_profile_repo"],
            recap_repo=SERVICES["recap_repo"],
            event_repo=SERVICES["event_repo"],
            thinking_sink=SERVICES["thinking_sink"],
        )
        startup = {c["name"]: set(c) - {"name", "config"} for c in calls}
        calls.clear()

        manager = AgentManager(tool_registry=SERVICES["tool_registry"])
        for name in SUPPORTED_AGENTS:
            await manager.enable_agent(name, {}, **SERVICES)
        enable = {c["name"]: set(c) - {"name", "config"} for c in calls}

    assert set(startup) == set(enable) == set(SUPPORTED_AGENTS)  # 两条路径都覆盖全部注册名
    for name in SUPPORTED_AGENTS:
        assert startup[name] == enable[name], (
            f"Agent '{name}' 构造 kwargs 不一致: 启动={sorted(startup[name])} 启用={sorted(enable[name])}"
        )


def test_decision_profile_is_decided_by_the_agent_class_not_the_factory() -> None:
    assert (
        "llm_profile" not in inspect.signature(instantiate_agent).parameters
    )  # 决策 profile 由 Agent 类默认值单点决定
    assert instantiate_agent("minecraft", {}, llm_manager=MagicMock(), prompt_manager=MagicMock()) is not None
    assert MINECRAFT_PROFILE == "minecraft"


def test_factory_forwards_streamer_infrastructure_and_repositories() -> None:
    session, tts, subtitle, sink = object(), object(), object(), object()
    repos = {name: object() for name in ("rundown_repo", "chat_repo", "sessions_repo", "topic_repo")}
    agent = instantiate_agent(
        "streamer",
        {},
        llm_manager=MagicMock(),
        prompt_manager=MagicMock(),
        speech_config={"enabled": False, "max_queue": 5, "render_timeout_ms": 1},
        tts_engine=tts,
        subtitle_service=subtitle,
        session_manager=session,
        thinking_sink=sink,
        **repos,
    )
    assert agent is not None
    assert agent._session_manager is session and agent._thinking_sink is sink
    assert agent._speech._tts_engine is tts and agent._speech._subtitle_service is subtitle
    assert agent._speech._speech_max_queue == 5
    # 仓储四件缺失时历史读取在入口守卫处整体短路，这里守住透传不回退
    assert agent._rundowns is repos["rundown_repo"] and agent._chat is repos["chat_repo"]
    assert (
        agent._background._sessions_repo is repos["sessions_repo"]
        and agent._background._topic_repo is repos["topic_repo"]
    )


async def test_enable_agent_falls_back_to_the_manager_repositories() -> None:
    calls: list[dict[str, Any]] = []

    def fake(name, config, **kwargs):
        calls.append(kwargs)
        return _stub_agent(name)

    manager = AgentManager(
        tool_registry=ToolRegistry(),
        rundown_repo=object(),
        chat_repo=object(),
        sessions_repo=object(),
        topic_repo=object(),
    )
    with patch("src.modules.agents.factory.instantiate_agent", side_effect=fake):
        assert await manager.enable_agent(
            "streamer", {}, llm_manager=object(), prompt_manager=object()
        )  # Dashboard 动态启用路径
    assert calls
    for key, member in (
        ("rundown_repo", "_rundown_repo"),
        ("chat_repo", "_chat_repo"),
        ("sessions_repo", "_sessions_repo"),
        ("topic_repo", "_topic_repo"),
    ):
        assert calls[0][key] is getattr(manager, member)
