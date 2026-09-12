"""人设供应链端到端防回退测试（配置→提示词）。

人设唯一来源是 agents.toml ``[agents.streamer.persona]``（StreamerPersonaConfig）：
  1. 表达侧：StreamerAgent 构造期把 bot_name/personality/style_constraints/
     audience_salutation 四字段注入 Replyer，prompt 渲染 kwargs 必须等于配置值
     （非 _DEFAULT_* 硬编码兜底）。
  2. 决策侧：behavior_style 仅注入 Planner；Planner 渲染 kwargs 不得含
     personality/style_constraints/bot_name（决策/表达分离契约的反向断言）。
  3. bot_name 默认值全库统一为 "麦麦"，历史 "爱德丝" 禁止（默认值防漂移）。

测试入口：factory.instantiate_agent（与 manager 动态启用路径同源）。
任一项回退（配置断达 / 默认值漂移 / 分离契约被破坏）都会在对应断言点失败。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.agents.factory import instantiate_agent

# 特征 persona（非默认值，特征串让"落回硬编码默认"的回归一眼可见）
_PERSONA_CONFIG = {
    "bot_name": "测试娘",
    "personality": "毒舌测试人格",
    "style_constraints": "简短犀利测试风格",
    "behavior_style": "沉默寡言测试准则",
    "audience_salutation": "各位测试观众",
}


def _make_llm_mock() -> MagicMock:
    llm = MagicMock()
    llm.call_tools = AsyncMock()
    return llm


def _make_prompt_mock() -> MagicMock:
    """构造 mock PromptManager：render 透传变量名为 kwargs。"""
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="RENDERED_PROMPT")
    return prompt


def _make_agent_with_persona_config() -> tuple[Any, MagicMock, MagicMock]:
    """经 factory.instantiate_agent 构造带特征 persona 配置的 StreamerAgent。

    入口与 manager 动态启用路径同源（配置 dict → StreamerConfig → StreamerAgent），
    保证测的是真实装配链而非绕过生产来源的手工注入。
    """
    llm = _make_llm_mock()
    prompt = _make_prompt_mock()
    agent = instantiate_agent(
        "streamer",
        {"persona": _PERSONA_CONFIG},
        llm_manager=llm,
        prompt_manager=prompt,
        event_bus=MagicMock(),
        tool_registry=MagicMock(),
    )
    assert agent is not None, "instantiate_agent('streamer') 应返回非 None"
    return agent, llm, prompt


def _replyer_llm_response(speech: str = "测试回复") -> object:
    # 用例内导入（测试可 mock 性）：按用例独立构造桩响应，模块导入期不绑定 LLM 类型
    from src.modules.llm.manager import LLMResponse

    payload = json.dumps({"speech": speech, "emotion": "neutral"}, ensure_ascii=False)
    return LLMResponse(success=True, content="", tool_calls=[{"name": "reply", "arguments": payload}])


class TestPersonaConfigToPromptEndToEnd:
    """配置→提示词端到端：表达侧四字段必须来自配置而非硬编码默认。"""

    @pytest.mark.asyncio
    async def test_replyer_renders_config_persona_into_prompt(self) -> None:
        """Replyer 渲染 kwargs 的四字段必须全部等于配置值（非 _DEFAULT_*）。"""
        agent, llm, prompt = _make_agent_with_persona_config()

        llm.call_tools = AsyncMock(return_value=_replyer_llm_response())

        # 用例内导入（测试可 mock 性）：决策计划在本用例内独立构造，不与模块导入期耦合
        from src.agents.streamer.plan import DecisionPlan

        plan = DecisionPlan(
            should_reply=True,
            target="all",
            topic_summary="测试话题",
            reply_guidance="按配置 persona 回答",
            confidence=0.9,
        )
        result = await agent._replyer.generate(plan, [])
        assert result is not None, "Replyer.generate 返回 None（mock LLM 应能生成结果）"

        kwargs = prompt.render.call_args.kwargs
        for field in ("bot_name", "personality", "style_constraints", "audience_salutation"):
            expected = _PERSONA_CONFIG[field]
            assert kwargs.get(field) == expected, (
                f"Replyer prompt 注入的 {field} 应为配置值 {expected!r}，实际: {kwargs.get(field)!r}"
            )

    @pytest.mark.asyncio
    async def test_replyer_does_not_render_behavior_style(self) -> None:
        """反向锁定：behavior_style 只进 Planner，不进 Replyer（决策/表达侧分离契约）。"""
        agent, llm, prompt = _make_agent_with_persona_config()

        llm.call_tools = AsyncMock(return_value=_replyer_llm_response(speech="ok"))

        # 用例内导入（测试可 mock 性）：决策计划在本用例内独立构造，不与模块导入期耦合
        from src.agents.streamer.plan import DecisionPlan

        plan = DecisionPlan(
            should_reply=True,
            target="all",
            topic_summary="t",
            reply_guidance="g",
            confidence=0.9,
        )
        await agent._replyer.generate(plan, [])

        kwargs = prompt.render.call_args.kwargs
        assert "behavior_style" not in kwargs, (
            f"Replyer prompt 不得注入 behavior_style（仅 Planner 决策侧消费），实际 kwargs={sorted(kwargs.keys())}"
        )


class TestDecisionExpressionSeparation:
    """决策/表达分离反向断言：Planner 侧只见 behavior_style。"""

    @pytest.mark.asyncio
    async def test_planner_injects_behavior_style_only(self) -> None:
        """Planner 渲染 kwargs 必须含配置 behavior_style；不得含表达侧三字段。"""
        agent, llm, prompt = _make_agent_with_persona_config()

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
        # 用例内导入（测试可 mock 性）：按用例独立构造桩响应，模块导入期不绑定 LLM 类型
        from src.modules.llm.manager import LLMResponse

        llm.call_tools = AsyncMock(
            return_value=LLMResponse(
                success=True, content="", tool_calls=[{"name": "produce_plan", "arguments": planner_payload}]
            )
        )

        from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser

        # 触发 planner.plan()；StreamerAgent 持有的 Planner 在 __init__ 时已注入
        # behavior_style（来自装配根透传的 persona_provider）。
        msg = RoomMessagePayload(
            message_type="danmaku",
            user=RoomMessageUser(id="u1", name="测试观众"),
            content="测试弹幕",
            timestamp_ms=0,
        )
        result = await agent._planner.plan([msg], forced=False)
        assert result is not None, "Planner.plan 返回 None（mock LLM 应能生成决策）"

        kwargs = prompt.render.call_args.kwargs
        # 决策侧：behavior_style 必须注入且为配置值
        assert kwargs.get("behavior_style") == _PERSONA_CONFIG["behavior_style"], (
            f"Planner prompt 注入的 behavior_style 应为配置值，实际: {kwargs.get('behavior_style')!r}"
        )
        # 表达侧隔离：bot_name/personality/style_constraints 不得进 Planner
        assert "personality" not in kwargs, "Planner prompt 不得注入 personality（仅 Replyer 表达侧消费）"
        assert "style_constraints" not in kwargs, "Planner prompt 不得注入 style_constraints（仅 Replyer 表达侧消费）"
        assert "bot_name" not in kwargs, "Planner prompt 不得注入 bot_name（仅 Replyer 表达侧消费）"


class TestPersonaDefaults:
    """默认值防漂移：全库统一 '麦麦'，历史 '爱德丝' 禁止。"""

    def test_default_bot_name_is_maiamai_not_ides(self) -> None:
        from src.agents.streamer import replyer
        from src.agents.streamer.config import StreamerConfig, StreamerPersonaConfig
        from src.modules.config.agents_schemas import AgentsConfig

        assert StreamerPersonaConfig().bot_name == "麦麦", (
            f"StreamerPersonaConfig.bot_name 应为 '麦麦'，实际: {StreamerPersonaConfig().bot_name!r}"
        )
        assert StreamerConfig().persona.bot_name == "麦麦", (
            f"StreamerConfig.persona.bot_name 应为 '麦麦'，实际: {StreamerConfig().persona.bot_name!r}"
        )
        assert AgentsConfig().streamer.persona.bot_name == "麦麦", (
            f"AgentsConfig.streamer.persona.bot_name 应为 '麦麦'，实际: {AgentsConfig().streamer.persona.bot_name!r}"
        )
        assert replyer._DEFAULT_BOT_NAME == "麦麦", (
            f"replyer._DEFAULT_BOT_NAME 应为 '麦麦'，实际: {replyer._DEFAULT_BOT_NAME!r}"
        )

        # 显式断言历史值"爱德丝"已清零（任何一处出现即回归）
        for source_name, source_value in [
            ("StreamerPersonaConfig", StreamerPersonaConfig().bot_name),
            ("StreamerConfig.persona", StreamerConfig().persona.bot_name),
            ("AgentsConfig.streamer.persona", AgentsConfig().streamer.persona.bot_name),
            ("replyer._DEFAULT_BOT_NAME", replyer._DEFAULT_BOT_NAME),
        ]:
            assert source_value != "爱德丝", f"{source_name} 仍残留历史默认值 '爱德丝'，P1 修复回归！"

    def test_agents_config_default_subtrees_are_package_authoritative(self) -> None:
        """包内单一权威：AgentsConfig 默认实例的 minecraft/text_adv 子配置
        由各自包内权威 Schema 实例化，确保漂移写回路径不丢默认子树。
        """
        from src.agents.minecraft.config import MinecraftConfig
        from src.agents.streamer.config import StreamerConfig
        from src.agents.text_adv.config import TextAdvConfig
        from src.modules.config.agents_schemas import AgentsConfig

        cfg = AgentsConfig()

        assert isinstance(cfg.streamer, StreamerConfig)
        assert cfg.streamer.persona.bot_name == "麦麦"
        assert isinstance(cfg.minecraft, MinecraftConfig)
        assert cfg.minecraft.max_steps == 50
        assert isinstance(cfg.text_adv, TextAdvConfig)
        assert cfg.text_adv.engine_kind == "text_adv"
        assert cfg.text_adv.decision_strategy == "first_option"
        assert cfg.text_adv.enable_event_emission is True
