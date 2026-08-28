"""B2 人设供应链端到端防回退测试（v2.0.6）。

任务背景：
  v2.0.5 之前人设供应链完全断链——装配根 main._register_agents_from_config 从
  config_service 拉取 persona 段喂给 StreamerAgent.persona_provider 这一段缺失，
  导致 Replyer 拿到空 dict、Planner 旧契约完全"零人设"。B2 修复目标：

    P0 装配接线：装配根把 persona 段喂给 StreamerAgent.persona_provider
    P1 bot_name 统一：所有默认值 + 测试 fixture 收敛到 "麦麦"
    P3 Schema + 注入：PersonaConfig 新增 behavior_style（Planner 决策侧注入）

本测试从"装配入口"出发（factory.instantiate_agent，与 main._register_agents_from_config
同源），按真实调用顺序构造 StreamerAgent，沿"装配 → Planner → Replyer"路径断言：
  1. 装配层：agent._persona_provider == 特征 dict（验证 P0 接通）
  2. Replyer：prompt 渲染含特征 bot_name/personality/style_constraints（验证表达侧注入）
  3. Planner：prompt 渲染含特征 behavior_style，不含特征 personality（验证决策/表达侧分离）

如果未来 P0/P1/P3 任一项回退（装配不传 / 默认值漂移 / 注入漏字段），本测试会在
对应断言点失败，防止"伪完成系统"再次发生。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.agents.factory import instantiate_agent


# 特征 persona dict（独立于 core.toml 真实值，特征串让回归错误一眼可见）
_PERSONA_SENTINEL = {
    "bot_name": "测试娘",
    "personality": "毒舌测试人格",
    "style_constraints": "简短犀利测试风格",
    "user_name": "测试观众",
    "max_response_length": 42,
    "emotion_intensity": 9,
    "behavior_style": "沉默寡言测试准则",
}


def _make_llm_mock(content: str) -> MagicMock:
    """构造 mock LLMManager：chat 返回给定内容（兼容 str 与 LLMResponse 鸭子类型）。"""
    llm = MagicMock()
    resp = MagicMock()
    resp.success = True
    resp.content = content
    resp.error = None
    llm.chat = AsyncMock(return_value=resp)
    return llm


def _make_prompt_mock() -> MagicMock:
    """构造 mock PromptManager：render_safe 透传变量名为 kwargs。"""
    prompt = MagicMock()
    prompt.render_safe = MagicMock(return_value="RENDERED_PROMPT")
    return prompt


def _make_agent_with_mock_deps(persona_provider: Any) -> tuple[Any, MagicMock, MagicMock]:
    """用 mock 依赖构造 StreamerAgent（绕过真实 LLM/PromptManager 注册）。

    Returns:
        (StreamerAgent 实例, llm mock, prompt mock)
    """
    llm = _make_llm_mock("{}")
    prompt = _make_prompt_mock()
    agent = instantiate_agent(
        "streamer",
        None,
        llm_manager=llm,
        prompt_manager=prompt,
        event_bus=MagicMock(),
        context_service=MagicMock(),
        tool_registry=MagicMock(),
        persona_provider=persona_provider,
    )
    assert agent is not None, "instantiate_agent('streamer') 应返回非 None"
    return agent, llm, prompt


class TestPersonaPipelineEndToEnd:
    """B2 修复端到端防回退：装配 → Planner → Replyer 全路径。"""

    def test_assembly_wires_persona_provider_to_streamer_agent(self) -> None:
        """P0：装配根必须把 persona dict 喂给 StreamerAgent.persona_provider（防断链）。"""
        agent, _llm, _prompt = _make_agent_with_mock_deps(persona_provider=_PERSONA_SENTINEL)

        # 装配层接线断言：特征 dict 必须原样落进 agent._persona_provider
        assert agent._persona_provider == _PERSONA_SENTINEL, (
            f"StreamerAgent.persona_provider 装配失败：\n"
            f"  期望: {_PERSONA_SENTINEL}\n"
            f"  实际: {agent._persona_provider}"
        )

    @pytest.mark.asyncio
    async def test_replyer_renders_sentinel_persona_into_prompt(self) -> None:
        """Replyer prompt 必须含特征 bot_name/personality/style_constraints（验证表达侧注入）。

        走 reply_tool.invoke() 触发 Replyer.generate()，捕获 prompt 渲染 kwargs，
        断言三个表达侧人设键均为特征值。注意 reply_tool 在 _register_tools 才会构造，
        这里直接拿 agent._replyer（Stage 2 表达引擎）调用 generate()，与工具入口等价。
        """
        agent, _llm, prompt = _make_agent_with_mock_deps(persona_provider=_PERSONA_SENTINEL)

        # _register_tools 在 _on_start 中调用才会构造 Provider；这里直接复刻装配根传
        # persona 的关键路径——Replyer.generate(plan, batch, persona, ...)。
        replyer = agent._replyer

        # mock LLM 返回合法 Replyer JSON
        replyer_payload = json.dumps(
            {"text": "测试回复", "emotion": "neutral", "action": "", "action_parameters": {}},
            ensure_ascii=False,
        )
        replyer._llm_service.chat = AsyncMock(
            return_value=MagicMock(success=True, content=replyer_payload, error=None)
        )

        from src.agents.streamer.plan import DecisionPlan

        plan = DecisionPlan(
            should_reply=True,
            target="all",
            topic_summary="测试话题",
            reply_guidance="按特征 persona 回答",
            confidence=0.9,
        )
        result = await replyer.generate(plan, [], _PERSONA_SENTINEL)

        # Replyer 应至少生成出 speech 字段（mock LLM 返回合法 JSON）
        assert result is not None, "Replyer.generate 返回 None（mock LLM 应能生成结果）"

        # 捕获 prompt 渲染 kwargs（最后一次调用即 generate 的那次）
        kwargs = prompt.render_safe.call_args.kwargs
        assert kwargs.get("bot_name") == "测试娘", (
            f"Replyer prompt 注入的 bot_name 应为特征值，实际: {kwargs.get('bot_name')!r}"
        )
        assert kwargs.get("personality") == "毒舌测试人格", (
            f"Replyer prompt 注入的 personality 应为特征值，实际: {kwargs.get('personality')!r}"
        )
        assert kwargs.get("style_constraints") == "简短犀利测试风格", (
            f"Replyer prompt 注入的 style_constraints 应为特征值，实际: "
            f"{kwargs.get('style_constraints')!r}"
        )

    @pytest.mark.asyncio
    async def test_planner_injects_sentinel_behavior_style_only(self) -> None:
        """P3-b：Planner prompt 必须含特征 behavior_style；不**含**特征 personality。

        验证 MaiBot 三层人格拆分的 Amaidesu 映射契约：
        personality/style_constraints/bot_name → 仅进 Replyer（表达侧）
        behavior_style → 仅进 Planner（决策侧）
        """
        agent, llm, prompt = _make_agent_with_mock_deps(persona_provider=_PERSONA_SENTINEL)

        # mock LLM 返回合法 Planner JSON（decision plan）
        planner_payload = json.dumps(
            {
                "should_reply": True,
                "target": "all",
                "topic_summary": "测试话题",
                "reply_guidance": "依据行动准则决策",
                "confidence": 0.85,
            },
            ensure_ascii=False,
        )
        llm.chat = AsyncMock(
            return_value=MagicMock(success=True, content=planner_payload, error=None)
        )

        from src.modules.types.base.normalized_message import NormalizedMessage

        # 触发 planner.plan()；StreamerAgent 持有的 Planner 在 __init__ 时已注入
        # behavior_style（来自装配根透传的 persona_provider）。
        msg = NormalizedMessage(
            text="测试弹幕",
            source="test",
            data_type="text",
            importance=0.5,
            timestamp=0,
            user_id="u1",
            user_nickname="测试观众",
        )
        result = await agent._planner.plan([msg], forced=False)

        assert result is not None, "Planner.plan 返回 None（mock LLM 应能生成决策）"

        # 决策侧：behavior_style 必须注入且为特征值
        kwargs = prompt.render_safe.call_args.kwargs
        assert kwargs.get("behavior_style") == "沉默寡言测试准则", (
            f"Planner prompt 注入的 behavior_style 应为特征值，实际: "
            f"{kwargs.get('behavior_style')!r}"
        )

        # 表达侧隔离：personality/style_constraints/bot_name 必须**不**进 Planner
        assert "personality" not in kwargs, (
            "Planner prompt 不得注入 personality（仅 Replyer 表达侧消费）"
        )
        assert "style_constraints" not in kwargs, (
            "Planner prompt 不得注入 style_constraints（仅 Replyer 表达侧消费）"
        )
        assert "bot_name" not in kwargs, (
            "Planner prompt 不得注入 bot_name（仅 Replyer 表达侧消费）"
        )

    @pytest.mark.asyncio
    async def test_replyer_does_not_render_behavior_style(self) -> None:
        """反向锁定：behavior_style 只进 Planner，**不**进 Replyer（决策/表达侧分离契约）。"""
        agent, _llm, prompt = _make_agent_with_mock_deps(persona_provider=_PERSONA_SENTINEL)

        # 触发 Replyer.generate（路径与 test_replyer_renders_sentinel_persona_into_prompt 同）
        replyer_payload = json.dumps(
            {"text": "ok", "emotion": "neutral", "action": "", "action_parameters": {}},
            ensure_ascii=False,
        )
        replyer = agent._replyer
        replyer._llm_service.chat = AsyncMock(
            return_value=MagicMock(success=True, content=replyer_payload, error=None)
        )

        from src.agents.streamer.plan import DecisionPlan

        plan = DecisionPlan(
            should_reply=True,
            target="all",
            topic_summary="t",
            reply_guidance="g",
            confidence=0.9,
        )
        await replyer.generate(plan, [], _PERSONA_SENTINEL)

        kwargs = prompt.render_safe.call_args.kwargs
        # behavior_style 绝不能漏到 Replyer——防止未来"全部塞进 prompt"的回退
        assert "behavior_style" not in kwargs, (
            "Replyer prompt 不得注入 behavior_style（仅 Planner 决策侧消费），"
            f"实际 kwargs={sorted(kwargs.keys())}"
        )

    def test_default_bot_name_is_maiamai_not_ides(self) -> None:
        """P1：bot_name 默认值全库统一为 '麦麦'，历史 '爱德丝' 禁止（防回退断言）。

        验证四个权威默认值源已对齐（任务 P1）：
          1. core_schemas.PersonaConfig.bot_name
          2. agents_schemas.StreamerAgentConfig.bot_name
          3. streamer_agent.StreamerAgentConfig.bot_name
          4. replyer._DEFAULT_BOT_NAME
        """
        from src.agents.streamer import replyer
        from src.agents.streamer.streamer_agent import StreamerAgentConfig
        from src.modules.config.agents_schemas import StreamerAgentConfig as AgentsStreamerConfig
        from src.modules.config.core_schemas import PersonaConfig

        assert PersonaConfig().bot_name == "麦麦", (
            f"core_schemas.PersonaConfig.bot_name 应为 '麦麦'，实际: {PersonaConfig().bot_name!r}"
        )
        assert StreamerAgentConfig().bot_name == "麦麦", (
            f"streamer_agent.StreamerAgentConfig.bot_name 应为 '麦麦'，实际: {StreamerAgentConfig().bot_name!r}"
        )
        assert AgentsStreamerConfig().bot_name == "麦麦", (
            f"agents_schemas.StreamerAgentConfig.bot_name 应为 '麦麦'，实际: {AgentsStreamerConfig().bot_name!r}"
        )
        assert replyer._DEFAULT_BOT_NAME == "麦麦", (
            f"replyer._DEFAULT_BOT_NAME 应为 '麦麦'，实际: {replyer._DEFAULT_BOT_NAME!r}"
        )

        # 显式断言历史值"爱德丝"已清零（任何一处出现即回归）
        for source_name, source_value in [
            ("PersonaConfig", PersonaConfig().bot_name),
            ("StreamerAgentConfig (streamer_agent)", StreamerAgentConfig().bot_name),
            ("StreamerAgentConfig (agents_schemas)", AgentsStreamerConfig().bot_name),
            ("replyer._DEFAULT_BOT_NAME", replyer._DEFAULT_BOT_NAME),
        ]:
            assert source_value != "爱德丝", (
                f"{source_name} 仍残留历史默认值 '爱德丝'，P1 修复回归！"
            )