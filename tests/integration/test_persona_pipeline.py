"""人设供应链端到端防回退（配置→提示词）。

人设唯一来源是 agents.toml ``[agents.streamer.persona]``：
  1. 表达侧：StreamerAgent 构造期把 bot_name/personality/style_constraints/audience_salutation 注入 Replyer，
     prompt 渲染 kwargs 必须等于配置值（非 _DEFAULT_* 硬编码兜底）。
  2. 决策侧：behavior_style 仅注入 Planner；Planner 渲染 kwargs 不得含 personality/style_constraints/bot_name
     （决策/表达分离契约的反向断言）。
  3. bot_name 默认值全库统一为 "麦麦"，历史 "爱德丝" 禁止。

入口是 factory.instantiate_agent（与 manager 动态启用路径同源），任一项回退都会在对应断言点失败。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.minecraft.config import MinecraftConfig
from src.agents.streamer import replyer
from src.agents.streamer.config import StreamerConfig, StreamerPersonaConfig
from src.agents.streamer.plan import DecisionPlan
from src.agents.text_adv.config import TextAdvConfig
from src.modules.agents.factory import instantiate_agent
from src.modules.config.agents_schemas import AgentsConfig
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.llm.payload import Response, ToolCall

# 特征 persona（非默认值，特征串让"落回硬编码默认"的回归一眼可见）
_PERSONA = {
    "bot_name": "测试娘",
    "personality": "毒舌测试人格",
    "style_constraints": "简短犀利测试风格",
    "behavior_style": "沉默寡言测试准则",
    "audience_salutation": "各位测试观众",
}


@pytest.fixture
def streamer() -> tuple[Any, MagicMock, MagicMock]:
    """经 factory 装配带特征 persona 的 StreamerAgent，保证测的是真实装配链而非手工注入。"""
    llm = MagicMock()
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="RENDERED_PROMPT")
    agent = instantiate_agent(
        "streamer",
        {"persona": _PERSONA},
        llm_manager=llm,
        prompt_manager=prompt,
        event_bus=MagicMock(),
        tool_registry=MagicMock(),
    )
    assert agent is not None
    return agent, llm, prompt


async def test_replyer_renders_config_persona_and_never_the_planner_only_behavior_style(streamer) -> None:
    """system 稳定段承载全程不变的人设（bot_name/personality/audience_salutation），style_constraints 属本轮表达风格，
    进本轮输入模板；behavior_style 只进 Planner。"""
    agent, llm, prompt = streamer
    llm.generate = AsyncMock(return_value=Response(success=True, content="测试回复"))  # 纯文本契约：正文即台词
    plan = DecisionPlan(
        should_reply=True, target="all", topic_summary="测试话题", reply_guidance="按配置 persona 回答"
    )

    assert await agent._replyer.generate(plan, []) is not None

    by_template = {call.args[0]: call.kwargs for call in prompt.render.call_args_list}
    system_kwargs = by_template["amaidesu_replyer_system"]
    assert {f: system_kwargs.get(f) for f in ("bot_name", "personality", "audience_salutation")} == {
        f: _PERSONA[f] for f in ("bot_name", "personality", "audience_salutation")
    }
    assert by_template["amaidesu_replyer"].get("style_constraints") == _PERSONA["style_constraints"]
    assert all("behavior_style" not in kwargs for kwargs in by_template.values())  # 决策/表达侧分离契约


async def test_planner_gets_behavior_style_only_and_none_of_the_expression_side_persona(streamer) -> None:
    agent, llm, prompt = streamer
    plan_args = {
        "should_reply": True,
        "target": "all",
        "topic_summary": "测试话题",
        "reply_guidance": "依据行动准则决策",
    }
    llm.generate = AsyncMock(
        return_value=Response(
            success=True, content="", tool_calls=[ToolCall(id="call_plan", name="produce_plan", arguments=plan_args)]
        )
    )
    message = RoomMessagePayload(
        message_type="danmaku", user=RoomMessageUser(id="u1", name="测试观众"), content="测试弹幕", timestamp_ms=0
    )

    assert await agent._planner.plan([message], forced=False) is not None

    kwargs = prompt.render.call_args.kwargs
    assert kwargs.get("behavior_style") == _PERSONA["behavior_style"]
    assert not {"personality", "style_constraints", "bot_name"} & kwargs.keys()  # 仅 Replyer 表达侧消费


def test_default_bot_name_is_maimai_everywhere_and_the_legacy_name_is_gone() -> None:
    defaults = {
        "StreamerPersonaConfig": StreamerPersonaConfig().bot_name,
        "StreamerConfig.persona": StreamerConfig().persona.bot_name,
        "AgentsConfig.streamer.persona": AgentsConfig().streamer.persona.bot_name,
        "replyer._DEFAULT_BOT_NAME": replyer._DEFAULT_BOT_NAME,
    }

    assert defaults == dict.fromkeys(defaults, "麦麦")  # 任何一处出现历史默认值 "爱德丝" 即回归


def test_agents_config_default_subtrees_come_from_each_package_schema() -> None:
    """包内单一权威：默认实例的子配置由各自包内 Schema 实例化，漂移写回路径不丢默认子树。"""
    config = AgentsConfig()

    assert isinstance(config.streamer, StreamerConfig) and isinstance(config.minecraft, MinecraftConfig)
    assert isinstance(config.text_adv, TextAdvConfig)
    assert "max_steps" not in config.minecraft.model_dump()  # 累计推理步数不再是可配置的停止条件
