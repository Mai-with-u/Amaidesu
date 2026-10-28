"""本文件测 MinecraftAgent 的长事件概述主题：超阈值长文经 summary 档压缩后转发主播，概述失败降级首句斩尾。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock


from src.modules.events.payloads.game import GamePayload
from src.modules.llm.payload import Response

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _resp,
    _wait_until,
    make_minecraft_agent,
)


def _attention_events(bus: MagicMock) -> List[GamePayload]:
    return [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "attention_required"
    ]


class _SummaryLlm:
    """summary 档替身：记下收到的档位与提示，按剧本回复或抛错。"""

    def __init__(self, behavior: Callable[[List[Dict[str, Any]]], Response]) -> None:
        self.behavior = behavior
        self.calls: List[Dict[str, Any]] = []

    async def generate(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Response:
        self.calls.append({"messages": messages, "profile": kwargs.get("profile")})
        return self.behavior(messages)

    def is_profile_visual(self, profile: Optional[str]) -> bool:
        """能力查询替身：summary 场景一律按非视觉。"""
        return False

    def get_profile_context_window(self, profile: Optional[str]) -> int:
        """窗口查询替身：summary 场景按未配置（0），预算走保守窗口。"""
        del profile
        return 0


async def test_long_body_event_is_summarized_before_reaching_the_streamer() -> None:
    """长文概述器：超过阈值的长文经 summary 档压缩后两栏发出，短文直通不调 LLM。"""
    server = FakeMaicraft()
    llm = _SummaryLlm(lambda messages: _resp("饿了：翻过箱子没有、合成缺干海带块、烧炼缺原料"))
    agent, _llm, bus = await make_minecraft_agent(server, llm=llm, live=False)

    short = "吃了一个苹果"
    await agent._on_body_event({"kind": "need_unhandled", "message": short})
    long_text = "饿了没吃上。" + "翻过箱子没有面包，合成台缺干海带块，烧炼炉缺原料，64 格内没有可猎动物。" * 20
    await agent._on_body_event({"kind": "need_unhandled", "message": long_text})
    await _wait_until(lambda: len(_attention_events(bus)) == 2)

    assert llm.calls and llm.calls[0]["profile"] == "summary", "概述走 summary 档"
    assert "只压缩不评价" in llm.calls[0]["messages"][0]["content"], "提示词只压缩不评价"
    first, summarized = _attention_events(bus)
    assert first.message == short and first.scene == "", "短文直通，不调概述"
    assert "干海带块" in summarized.message, "概述保信息"
    assert summarized.scene.startswith("原文留档：") and long_text in summarized.scene, "原文留档进载荷"
    await agent.stop()

    # 白话化只改说法不去内容：各不相同的排查分句重写后仍超阈值，压缩兜底照常触发
    # 互不重复的排查分句（去重逻辑压不短），白话化后仍超 300 字阈值
    heterogeneous = "，".join(f"挖了第{i}层都没有找到铁矿，背包里只剩{i}块圆石" for i in range(30))
    await agent._on_body_event({"kind": "need_unhandled", "message": heterogeneous})
    await _wait_until(lambda: len(_attention_events(bus)) == 3)
    assert llm.calls and llm.calls[0]["profile"] == "summary", "白话化仍超阈值的长文走保信息摘要"
    third = _attention_events(bus)[2]
    assert third.scene.startswith("原文留档："), "留档进 scene"
    await agent.stop()


async def test_summary_failure_falls_back_to_first_sentence() -> None:
    """压缩兜底仍有效：素材调度器直接收到超阈值长文时走 summary 档，概述失败降级首句斩尾。"""
    server = FakeMaicraft()

    def boom(messages: List[Dict[str, Any]]) -> Response:
        raise RuntimeError("summary 引擎不可用")

    llm = _SummaryLlm(boom)
    agent, _llm, bus = await make_minecraft_agent(server, llm=llm, live=False)

    long_text = "饿了没吃上。翻过箱子没有面包，合成台缺干海带块，烧炼炉缺原料。" + "细节" * 200
    # 直呼素材发布器（白话速览异常超长时的兜底路径）
    agent._attention_material.schedule(long_text)
    await _wait_until(lambda: len(_attention_events(bus)) == 1)

    emitted = _attention_events(bus)[0]
    assert emitted.message.startswith("饿了没吃上。"), "降级为首句斩尾"
    assert "细节" not in emitted.message, "斩尾不带后半长文"
    assert long_text in emitted.scene, "原文留档仍在"
    await agent.stop()
