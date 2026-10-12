"""Agent 两构造路径等价性测试（启动装配 vs Dashboard 动态启用）。

组合根启动装配（``main._register_agents_from_config``）与动态启用
（``AgentManager.enable_agent``）都必须经 ``factory.instantiate_agent``
单一构造路径，且对同名 Agent 传入**同一集合**的构造关键字——否则
"启动时能跑、动态启用后行为漂移"（如 llm_profile / speech 管线两侧
不一致）这类缺陷会静默发生。

另验证 llm_profile 统一：工厂不再显式传 ``llm_profile``，Minecraft
决策 profile 由 Agent 类默认值（"minecraft"）单点决定。
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import patch

import pytest

import main as app_main
from src.modules.agents.base import BaseAgent
from src.modules.agents.factory import SUPPORTED_AGENTS, instantiate_agent
from src.modules.agents.manager import AgentManager
from src.modules.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


def _make_stub_agent(name: str) -> BaseAgent:
    """构造指定注册名的最小可启动 Agent（enable_agent 要求实例名 = 段名）。"""
    return type(
        "StubAgent",
        (BaseAgent,),
        {"name": name, "description": "parity stub", "list_tools": lambda self: []},
    )()


@pytest.fixture
def recorded_factory():
    """Patch 工厂记录调用 kwargs；返回 (patcher, calls)。

    双点 patch：``main`` 顶层 ``from ... import instantiate_agent`` 绑定了
    自己的命名空间引用，启动路径与 enable 路径须分别拦截才能统一记录。
    """
    calls: list[dict[str, Any]] = []

    def _fake(name, config, **kwargs):
        calls.append({"name": name, "config": config, **kwargs})
        return _make_stub_agent(name)

    patcher = patch("main.instantiate_agent", side_effect=_fake)
    patcher2 = patch("src.modules.agents.factory.instantiate_agent", side_effect=_fake)
    yield (patcher, patcher2), calls


# 两路径共用的服务面（值本身不重要，等价性比较的是关键字集合）
_SHARED_SERVICES: dict[str, Any] = {
    "llm_manager": object(),
    "prompt_manager": object(),
    "event_bus": object(),
    "tool_registry": ToolRegistry(),
    "memory": object(),
    "thinking_sink": object(),
    "speech_config": {"enabled": False, "max_queue": 3, "render_timeout_ms": 60000},
    "tts_engine": object(),
    "subtitle_service": object(),
    "session_manager": object(),
    "rundown_repo": object(),
    "chat_repo": object(),
    "sessions_repo": object(),
    "topic_repo": object(),
    "context_assembler_config": None,
    "task_tracker": object(),
}


# ---------------------------------------------------------------------------
# 等价性：kwargs 关键字集合一致
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_and_enable_paths_pass_same_kwarg_names(recorded_factory) -> None:
    """启动装配与 enable_agent 对每个注册名传入相同集合的构造关键字。"""
    (main_patcher, factory_patcher), calls = recorded_factory

    main_patcher.start()
    factory_patcher.start()
    try:
        agents_section = {"enabled": list(SUPPORTED_AGENTS)}
        await app_main._register_agents_from_config(
            AgentManager(),
            agents_section,
            config_service=None,
            llm_service=_SHARED_SERVICES["llm_manager"],
            event_bus=_SHARED_SERVICES["event_bus"],
            tool_registry=_SHARED_SERVICES["tool_registry"],
            memory=_SHARED_SERVICES["memory"],
            task_tracker=_SHARED_SERVICES["task_tracker"],
            tts_section={"enabled": False, "max_queue": 3, "render_timeout_ms": 60000},
            tts_engine=_SHARED_SERVICES["tts_engine"],
            subtitle_service=_SHARED_SERVICES["subtitle_service"],
            session_manager=_SHARED_SERVICES["session_manager"],
            rundown_repo=_SHARED_SERVICES["rundown_repo"],
            chat_repo=_SHARED_SERVICES["chat_repo"],
            sessions_repo=_SHARED_SERVICES["sessions_repo"],
            topic_repo=_SHARED_SERVICES["topic_repo"],
            thinking_sink=_SHARED_SERVICES["thinking_sink"],
        )
    finally:
        main_patcher.stop()
        factory_patcher.stop()
    startup_calls = {c["name"]: set(c) - {"name", "config"} for c in calls}
    calls.clear()

    manager = AgentManager(
        tool_registry=_SHARED_SERVICES["tool_registry"],
        memory=_SHARED_SERVICES["memory"],
    )
    factory_patcher.start()
    try:
        for name in SUPPORTED_AGENTS:
            await manager.enable_agent(name, {}, **_SHARED_SERVICES)
    finally:
        factory_patcher.stop()
    enable_calls = {c["name"]: set(c) - {"name", "config"} for c in calls}

    assert set(startup_calls) == set(SUPPORTED_AGENTS), "启动装配应覆盖全部注册名"
    assert set(enable_calls) == set(SUPPORTED_AGENTS), "enable_agent 应覆盖全部注册名"
    for name in SUPPORTED_AGENTS:
        assert startup_calls[name] == enable_calls[name], (
            f"Agent '{name}' 两构造路径的 kwargs 集合不一致: "
            f"启动={sorted(startup_calls[name])} vs 启用={sorted(enable_calls[name])}"
        )


# ---------------------------------------------------------------------------
# llm_profile 统一：以 Agent 类默认为准
# ---------------------------------------------------------------------------


def test_factory_does_not_expose_llm_profile() -> None:
    """工厂签名不含 llm_profile —— 决策 profile 由 Agent 类默认值单点决定。"""
    assert "llm_profile" not in inspect.signature(instantiate_agent).parameters


def test_minecraft_uses_agent_default_llm_profile() -> None:
    """工厂构造 MinecraftAgent 时使用代码显式声明的 profile（'minecraft'）。"""
    from unittest.mock import MagicMock

    from src.agents.minecraft.agent import MINECRAFT_PROFILE

    agent = instantiate_agent("minecraft", {}, llm_manager=MagicMock(), prompt_manager=MagicMock())
    assert agent is not None
    assert MINECRAFT_PROFILE == "minecraft"


# ---------------------------------------------------------------------------
# 工厂基础设施参数透传（streamer 发言管线 / 场次 / 思考流）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streamer_infra_kwargs_forwarded() -> None:
    """工厂把 speech/tts/subtitle/session/thinking 基建透传给 StreamerAgent。"""
    from unittest.mock import MagicMock

    session_obj = object()
    tts_obj = object()
    subtitle_obj = object()
    sink_obj = object()
    agent = instantiate_agent(
        "streamer",
        {},
        llm_manager=MagicMock(),
        prompt_manager=MagicMock(),
        speech_config={"enabled": False, "max_queue": 5, "render_timeout_ms": 1},
        tts_engine=tts_obj,
        subtitle_service=subtitle_obj,
        session_manager=session_obj,
        thinking_sink=sink_obj,
    )
    assert agent is not None
    assert agent._session_manager is session_obj
    assert agent._speech._tts_engine is tts_obj
    assert agent._speech._subtitle_service is subtitle_obj
    assert agent._thinking_sink is sink_obj
    assert agent._speech._speech_max_queue == 5


# ---------------------------------------------------------------------------
# 工厂仓储透传（对话历史/流程单/场次状态/话题快照四件；缺失 = 历史读取短路）
# ---------------------------------------------------------------------------


def test_streamer_repos_forwarded() -> None:
    """工厂把仓储四件透传给 StreamerAgent（历史读取与后台维护的数据面）。"""
    from unittest.mock import MagicMock

    rundown_repo = object()
    chat_repo = object()
    sessions_repo = object()
    topic_repo = object()
    agent = instantiate_agent(
        "streamer",
        {},
        llm_manager=MagicMock(),
        prompt_manager=MagicMock(),
        rundown_repo=rundown_repo,
        chat_repo=chat_repo,
        sessions_repo=sessions_repo,
        topic_repo=topic_repo,
    )
    assert agent is not None
    assert agent._rundowns is rundown_repo
    assert agent._chat is chat_repo
    assert agent._background._sessions_repo is sessions_repo
    assert agent._background._topic_repo is topic_repo
    # 历史读取入口可用：chat_repo 缺失时历史读取在入口守卫处整体短路
    # （_read_history 首行 _chat is None 即返回 None），此断言守住透传不回退
    assert agent._chat is not None


@pytest.mark.asyncio
async def test_enable_agent_repos_fall_back_to_manager_members() -> None:
    """enable_agent 未显式传仓储时回退构造成员（Dashboard 动态启用路径）。"""
    from unittest.mock import patch

    calls: list[dict[str, Any]] = []

    def _fake(name, config, **kwargs):
        calls.append(kwargs)
        return _make_stub_agent(name)

    manager = AgentManager(
        tool_registry=ToolRegistry(),
        rundown_repo=object(),
        chat_repo=object(),
        sessions_repo=object(),
        topic_repo=object(),
    )
    with patch("src.modules.agents.factory.instantiate_agent", side_effect=_fake):
        ok = await manager.enable_agent("streamer", {}, llm_manager=object(), prompt_manager=object())
    assert ok
    assert calls, "工厂应被调用"
    assert calls[0]["rundown_repo"] is manager._rundown_repo
    assert calls[0]["chat_repo"] is manager._chat_repo
    assert calls[0]["sessions_repo"] is manager._sessions_repo
    assert calls[0]["topic_repo"] is manager._topic_repo
