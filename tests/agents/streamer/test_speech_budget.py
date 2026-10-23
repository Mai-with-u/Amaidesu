"""台词长度预算：超出先让模型重说成一句，仍超就截到预算内最后一个完整句子。

提示词里的"一两句"是软要求：本机实测换了两种模型，台词字数中位都在 90 上下、最长 155，
还常分好几段——所以在表达引擎出口再卡一道。
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.replyer import Replyer, _fit_to_budget
from src.modules.llm.payload import Response
from tests.agents.streamer.helpers import make_streamer_agent

_LONG = (
    "呼，总算爬上来了，整个人湿得跟落汤鸡似的……不过你们看，我手里这块安山岩！"
    "其实刚才下水都是战术动作。\n\n诶不过这水底下怎么还有美西螈在游啊？"
)


@pytest.mark.parametrize(
    ("speech", "budget", "expected"),
    [
        ("行吧，顺便洗个澡。", 40, "行吧，顺便洗个澡。"),  # 预算内原样
        ("掉水里了。\n\n行吧。", 40, "掉水里了。行吧。"),  # 分段换行去掉
        ("第一句话说完了。第二句话也说完了。第三句会超出去的。", 18, "第一句话说完了。第二句话也说完了。"),
        ("……诶等一下。我床呢？", 6, "……诶等一下。"),  # 开头的省略号不丢
        ("…………！！", 2, "…………！！"),  # 全是标点切不出句子：原样返回，不截成空串
        (
            "这一整句话本身就已经超出了预算但是没有任何句末标点",
            10,
            "这一整句话本身就已经超出了预算但是没有任何句末标点",
        ),
    ],
)
def test_fit_to_budget_keeps_whole_sentences(speech: str, budget: int, expected: str) -> None:
    """只在完整句子之后下刀；第一句本身超预算时整句保留，不截成半截。"""
    assert _fit_to_budget(speech, budget) == expected


def _replyer(*responses: Response, speech_max_chars: Optional[int] = 40) -> tuple[Replyer, MagicMock]:
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=list(responses))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    config: dict[str, Any] = {} if speech_max_chars is None else {"speech_max_chars": speech_max_chars}
    return Replyer(config=config, llm_service=llm, prompt_service=prompt), llm


def _plan() -> DecisionPlan:
    return DecisionPlan(should_reply=True, topic_summary="主播从水里爬上岸")


async def test_long_speech_is_retold_as_one_line() -> None:
    """超预算：只带人设和原话让模型重说一句，用重说的结果和它的请求号。"""
    replyer, llm = _replyer(
        Response(success=True, content=_LONG, request_id="req_long"),
        Response(success=True, content="上岸了……水底下居然有美西螈。", request_id="req_short"),
    )
    result = await replyer.generate(_plan(), [])
    assert result == {"speech": "上岸了……水底下居然有美西螈。", "llm_request_id": "req_short"}
    retell = llm.generate.await_args_list[1]
    instruction = retell.args[0][0]["content"]
    assert _LONG in instruction and "不超过 40 字" in instruction and "不要加原话里没有的事" in instruction
    assert retell.kwargs["system"] == "PROMPT" and retell.kwargs["profile"] == "replyer"


async def test_retell_failure_falls_back_to_cutting_whole_sentences() -> None:
    """重说失败：直接按完整句子截短原台词，照样发声。"""
    replyer, _ = _replyer(
        Response(success=True, content=_LONG, request_id="req_long"),
        Response(success=False, error="timeout"),
    )
    result = await replyer.generate(_plan(), [])
    assert result == {
        "speech": "呼，总算爬上来了，整个人湿得跟落汤鸡似的……不过你们看，我手里这块安山岩！",
        "llm_request_id": "req_long",
    }


async def test_budget_zero_leaves_speech_untouched() -> None:
    """预算为 0 = 不限制：只调一次模型，台词原样。"""
    replyer, llm = _replyer(Response(success=True, content=_LONG, request_id="req_long"), speech_max_chars=0)
    result = await replyer.generate(_plan(), [])
    assert result is not None and result["speech"] == _LONG and llm.generate.await_count == 1


async def test_turn_prompt_states_the_budget() -> None:
    """预算先写进本轮要求，多数台词一次就落在预算内。"""
    replyer, _ = _replyer(Response(success=True, content="好耶", request_id="r"))
    await replyer.generate(_plan(), [])
    turn_kwargs = {call.args[0]: call.kwargs for call in replyer._prompt_service.render.call_args_list}[
        "amaidesu_replyer"
    ]
    assert turn_kwargs["length_rule"] == "整句不超过 40 字。"


def test_streamer_wires_speech_budget_into_replyer() -> None:
    """装配期把 speech_max_chars 交给表达引擎。"""
    agent = make_streamer_agent({"speech_max_chars": 30})
    assert agent._replyer._speech_max_chars == 30
