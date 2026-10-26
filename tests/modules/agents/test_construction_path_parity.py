"""Agent 两构造路径等价性：启动装配（main._register_agents_from_config）与 Dashboard 动态启用（AgentManager.enable_agent）

两者都必须经 factory.instantiate_agent 单一构造路径，且对同名 Agent 传入同一份依赖值
（AgentDeps），否则"启动时能跑、动态启用后行为漂移"（如 llm_profile / speech 管线两侧
不一致）会静默发生。
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock, patch

import main as app_main
from src.agents.minecraft.agent import MINECRAFT_PROFILE
from src.modules.agents.base import BaseAgent
from src.modules.agents.deps import AgentDeps
from src.modules.agents.factory import SUPPORTED_AGENTS, instantiate_agent
from src.modules.agents.manager import AgentManager
from src.modules.tools.registry import ToolRegistry

# 两路径共用的服务面（值本身不重要，等价性比较的是依赖对象内容）
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

    def fake(name, config, *, deps):
        calls.append({"name": name, "config": config, "deps": deps})
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
            deps=AgentDeps(**SERVICES),
        )
        startup = {c["name"]: c["deps"] for c in calls}
        calls.clear()

        manager = AgentManager(tool_registry=SERVICES["tool_registry"])
        for name in SUPPORTED_AGENTS:
            await manager.enable_agent(name, {}, deps=AgentDeps(**SERVICES))
        enable = {c["name"]: c["deps"] for c in calls}

    assert set(startup) == set(enable) == set(SUPPORTED_AGENTS)  # 两条路径都覆盖全部注册名
    for name in SUPPORTED_AGENTS:
        # 启动路径原样透传调用方 deps；enable 路径经成员回退合成——本测试的
        # manager 未注入额外成员默认（tool_registry 与 SERVICES 同源），两侧应等值
        assert startup[name] == enable[name], f"Agent '{name}' 构造依赖不一致"


def test_decision_profile_is_decided_by_the_agent_class_not_the_factory() -> None:
    assert (
        "llm_profile" not in inspect.signature(instantiate_agent).parameters
    )  # 决策 profile 由 Agent 类默认值单点决定
    assert (
        instantiate_agent("minecraft", {}, deps=AgentDeps(llm_manager=MagicMock(), prompt_manager=MagicMock()))
        is not None
    )
    assert MINECRAFT_PROFILE == "minecraft"


def test_factory_forwards_streamer_infrastructure_and_repositories() -> None:
    session, tts, subtitle, sink = object(), object(), object(), object()
    repos = {name: object() for name in ("rundown_repo", "chat_repo", "sessions_repo", "topic_repo")}
    agent = instantiate_agent(
        "streamer",
        {},
        deps=AgentDeps(
            llm_manager=MagicMock(),
            prompt_manager=MagicMock(),
            speech_config={"enabled": False, "max_queue": 5, "render_timeout_ms": 1},
            tts_engine=tts,
            subtitle_service=subtitle,
            session_manager=session,
            thinking_sink=sink,
            **repos,
        ),
    )
    assert agent is not None
    assert agent._session_manager is session and agent._thinking_sink is sink
    assert agent._speech._tts_engine is tts and agent._speech._subtitle_service is subtitle
    assert agent._speech._speech_max_queue == 5
    # 仓储四件缺失时历史读取在入口守卫处整体短路，这里守住透传不回退
    assert agent._rundown_coord._rundowns is repos["rundown_repo"] and agent._scope._chat is repos["chat_repo"]
    assert (
        agent._background._sessions_repo is repos["sessions_repo"]
        and agent._background._topic_repo is repos["topic_repo"]
    )


async def test_enable_agent_falls_back_to_the_manager_repositories() -> None:
    calls: list[dict[str, Any]] = []

    def fake(name, config, *, deps):
        calls.append({"deps": deps})
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
            "streamer", {}, deps=AgentDeps(llm_manager=object(), prompt_manager=object())
        )  # Dashboard 动态启用路径
    assert calls
    resolved = calls[0]["deps"]
    for field, member in (
        ("rundown_repo", "_rundown_repo"),
        ("chat_repo", "_chat_repo"),
        ("sessions_repo", "_sessions_repo"),
        ("topic_repo", "_topic_repo"),
        ("tool_registry", "_tool_registry"),
    ):
        assert getattr(resolved, field) is getattr(manager, member)
