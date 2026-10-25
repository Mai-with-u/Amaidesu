"""通知路由与素材发布器直测：不建 Agent、不连游戏。

路由表锁定每个已知种类的通道与附带处置、未知种类走 attention_event 兜底；
文案组装验证模板渲染与 need_unhandled 重复指回。发布器验证短文直通不调
LLM、长文走 summary 档两栏载荷、概述失败降级首句斩尾、LLM 经取值回调
每次现取（替换实例即生效）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from src.agents.minecraft.attention_material import AttentionMaterialPublisher
from src.agents.minecraft.notice_router import (
    BodyEventRoute,
    attention_notice,
    body_notice_content,
    route_body_event,
)


def test_route_table_channels_and_dispositions() -> None:
    """已知种类各走其道：一张表锁全部路由决策。"""
    expected = {
        "sky_changed": ("carry_sky", {}),
        "follow_status": ("inject_if_busy", {}),
        "timer_due": ("wake_even_idle", {}),
        "need_unhandled": ("inject_if_busy", {"dedup_content", "publish_material"}),
        "character_died": ("inject_if_busy", {"publish_material"}),
        "temporary_task_started": ("attention_event", {"execution_context_prefix"}),
        "temporary_task_finished": ("attention_event", {"urgent_marker", "already_resolved"}),
    }
    for kind, (channel, flags) in expected.items():
        route = route_body_event(kind)
        assert route.kind == kind and route.channel == channel
        assert route.dedup_content == ("dedup_content" in flags)
        assert route.publish_material == ("publish_material" in flags)
        assert route.urgent_marker == ("urgent_marker" in flags)
        assert route.execution_context_prefix == ("execution_context_prefix" in flags)
        assert route.already_resolved == ("already_resolved" in flags)
        if channel in ("carry_sky", "wake_even_idle", "inject_if_busy"):
            assert route.template and "{message}" in route.template, f"{kind} 的通道需要文案模板"


def test_unknown_kind_falls_back_to_attention_event() -> None:
    """未知种类不静默丢弃：走 attention_event 兜底，kind 原样保留。"""
    route = route_body_event("some_future_kind")
    assert isinstance(route, BodyEventRoute)
    assert route.kind == "some_future_kind" and route.channel == "attention_event"


def test_body_notice_content_formats_template() -> None:
    route = route_body_event("character_died")
    content = body_notice_content(route, "摔死了", history=[], queue=[])
    assert content.startswith("[身体] 摔死了。") and "复活后先 maicraft_observe(what=self)" in content


def test_body_notice_content_dedups_need_unhandled_repeat() -> None:
    """need_unhandled 与历史一字不差重复时只留指回；其余通知原样给。"""
    route = route_body_event("need_unhandled")
    full = route.template.format(message="饿了没吃上：缺面包")
    history: List[Dict[str, Any]] = [{"role": "user", "content": full}]
    assert body_notice_content(route, "饿了没吃上：缺面包", history=history, queue=[]) != full, "重复时留指回"
    assert body_notice_content(route, "又饿了一次", history=history, queue=[]) == route.template.format(
        message="又饿了一次"
    ), "不重复时完整展示"
    died = route_body_event("character_died")
    died_full = died.template.format(message="摔死了")
    assert (
        body_notice_content(died, "摔死了", history=[{"role": "user", "content": died_full}], queue=[]) == died_full
    ), "角色死亡每次都是新事实，原样给"


def test_attention_notice_prefixes_task_context_for_started() -> None:
    started = attention_notice(route_body_event("temporary_task_started"), " Mod 原话：狼来了")
    assert started.startswith("执行任务时")
    finished = attention_notice(route_body_event("temporary_task_finished"), "Mod 原话：跑掉了")
    assert not finished.startswith("执行任务时")


# ---------------------------------------------------------------------------
# 素材发布器
# ---------------------------------------------------------------------------


class _FakeLlm:
    def __init__(self, content: str = "", *, error: Optional[Exception] = None) -> None:
        self.content = content
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    async def generate(self, messages: List[Dict[str, Any]], *, profile: str = "", **_: Any) -> Any:
        self.calls.append({"messages": messages, "profile": profile})
        if self.error is not None:
            raise self.error

        class _Resp:
            success = True
            content = self.content
            tool_calls: List[Any] = []

        return _Resp()


class _Recorder:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    async def __call__(self, message: str, scene: Optional[str] = None) -> None:
        self.events.append({"message": message, "scene": scene})


def _publisher(llm: Optional[_FakeLlm]) -> tuple[AttentionMaterialPublisher, _Recorder]:
    box: Dict[str, Optional[_FakeLlm]] = {"llm": llm}
    recorder = _Recorder()
    publisher = AttentionMaterialPublisher(llm_getter=lambda: box["llm"], emit=recorder)
    return publisher, recorder


async def test_publisher_short_text_passes_through_without_llm() -> None:
    publisher, recorder = _publisher(None)  # 无 LLM 也能发短文
    publisher.schedule("吃了一个苹果")
    await asyncio.sleep(0)
    assert recorder.events == [{"message": "吃了一个苹果", "scene": None}]


async def test_publisher_long_text_summarizes_two_column() -> None:
    llm = _FakeLlm(content="饿了：缺面包、缺干海带块")
    publisher, recorder = _publisher(llm)
    long_text = "饿了没吃上。" + "翻遍箱子都没有吃的。" * 40
    publisher.schedule(long_text)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert llm.calls and llm.calls[0]["profile"] == "summary"
    assert "只压缩不评价" in llm.calls[0]["messages"][0]["content"]
    assert recorder.events == [{"message": "饿了：缺面包、缺干海带块", "scene": f"原文留档：{long_text}"}]


async def test_publisher_summary_failure_falls_back_to_first_sentence() -> None:
    llm = _FakeLlm(error=RuntimeError("引擎不可用"))
    publisher, recorder = _publisher(llm)
    publisher.schedule("饿了没吃上。翻过箱子没有面包。" + "细节" * 200)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    (event,) = recorder.events
    assert event["message"] == "饿了没吃上。", "降级为首句斩尾"
    assert "细节" not in event["message"] and (event["scene"] or "").startswith("原文留档：")


async def test_publisher_reads_llm_through_getter_each_time() -> None:
    """LLM 经取值回调现取：Agent 侧替换实例后，概述跟着走新实例。"""
    box_llm = _FakeLlm(content="旧实例的概述")
    box: Dict[str, _FakeLlm] = {"llm": box_llm}
    recorder = _Recorder()
    publisher = AttentionMaterialPublisher(llm_getter=lambda: box["llm"], emit=recorder)

    long_text = "饿了没吃上。" + "细节" * 200
    publisher.schedule(long_text)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert recorder.events[0]["message"] == "旧实例的概述"

    box["llm"] = _FakeLlm(content="新实例的概述")
    publisher.schedule(long_text)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert recorder.events[1]["message"] == "新实例的概述"
