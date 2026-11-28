"""通知路由与素材发布器直测：不建 Agent、不连游戏。

路由表锁定每个已知种类的通道与附带处置、未知种类走 attention_event 兜底；
文案组装验证模板渲染与 need_unhandled 重复指回。发布器验证短文直通不调
LLM、长文走 summary 档两栏载荷、概述失败降级首句斩尾、LLM 经取值回调
每次现取（替换实例即生效）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock

from src.agents.minecraft.agent import MinecraftAgent
from src.agents.minecraft.attention_material import AttentionMaterialPublisher
from src.agents.minecraft.notice_router import (
    BodyEventRoute,
    attention_notice,
    body_notice_content,
    route_body_event,
)
from src.agents.minecraft.repeated_notice import notice_present, repeat_reference
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.payload import Response

from .minecraft_v1_fakes import FakeMaicraft, _resp, _wait_until, make_minecraft_agent


def test_route_table_channels_and_dispositions() -> None:
    """已知种类各走其道：一张表锁全部路由决策。"""
    expected = {
        "sky_changed": ("carry_sky", {}),
        "follow_status": ("inject_if_busy", {}),
        "timer_due": ("wake_even_idle", {}),
        "need_unhandled": ("inject_if_busy", {"dedup_content", "publish_material"}),
        "character_died": ("inject_if_busy", {"publish_material"}),
        "gear_broken": ("inject_if_busy", {"publish_material"}),
        "others_food_taken": ("inject_if_busy", {}),
        "player_body_language": ("wake_for_interaction", {}),
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
        if channel in ("carry_sky", "wake_even_idle", "wake_for_interaction", "inject_if_busy"):
            assert route.template and "{message}" in route.template, f"{kind} 的通道需要文案模板"


def test_chosen_death_routes_as_going_home_without_material() -> None:
    """自己寻死时的死讯：通知写明是回家的办法、照样选重生，不再把死亡播报发成主播素材。"""
    route = route_body_event("character_died", chosen_death=True)
    assert route.channel == "inject_if_busy" and route.publish_material is False
    content = body_notice_content(route, "Steve被杀死了", history=[], queue=[])
    assert content.startswith("[身体] Steve被杀死了。") and "回重生点的办法，不是出事" in content
    assert route_body_event("need_unhandled", chosen_death=True).kind == "need_unhandled", "别的种类不受影响"


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


def test_player_body_language_notice_says_it_is_social_and_how_to_answer() -> None:
    """别的玩家冲角色做的动作：通知点明是在互动、可以用 body_language 回一个动作、插一下不打断手上的活、闲着时回完就收尾。"""
    route = route_body_event("player_body_language")
    content = body_notice_content(route, "玩家 Alex（e4）看着你蹲起 4 下；他在你前方 3 格", history=[], queue=[])
    assert content.startswith("[互动] 玩家 Alex（e4）看着你蹲起 4 下；他在你前方 3 格。")
    for phrase in (
        "不是打你",
        "body_language",
        "不打断手上的活",
        "player-body-language",
        "回过一次就够了",
        "不用交付也不用上报",
    ):
        assert phrase in content


# ---------------------------------------------------------------------------
# 重复身体通知判定与指回（原 test_minecraft_repeated_notice.py）
# ---------------------------------------------------------------------------


def test_present_in_history_or_queue_only_when_the_exact_text_is_there() -> None:
    content = "[身体] 饿了但没吃上：箱子没有"
    history = [{"role": "system", "content": "说明"}, {"role": "user", "content": content}]

    assert notice_present(content, history, [])
    assert notice_present(content, [], [("", content)])
    assert not notice_present(content, [{"role": "user", "content": content + "。"}], [("", "别的")])
    # 助手自己说的话里带着同样的字也不算：只认交给模型的通知
    assert not notice_present(content, [{"role": "assistant", "content": content}], [])


def test_reference_names_the_notice_by_its_label() -> None:
    text = repeat_reference("饿了但没吃上：还要 8 个能吃的，问过各来源都拿不到；箱子：没有")

    assert "「饿了但没吃上」" in text and "没有新变化" in text
    assert "箱子" not in text, "不再抄正文"


def test_reference_falls_back_to_a_generic_label_when_the_head_is_not_a_label() -> None:
    long_head = "这是一整句没有冒号分隔的很长很长的说明文字用来模拟没有标签的情况" + "：后面还有"
    assert "「身体处理不了的事」" in repeat_reference(long_head)
    assert "「身体处理不了的事」" in repeat_reference("没有冒号的一句话")


# ---------------------------------------------------------------------------
# 装备损坏与长事件概述的 Agent 接线（原 test_minecraft_gear_broken.py / test_minecraft_long_event_summary.py）
# ---------------------------------------------------------------------------


def _attention_payloads(bus: MagicMock) -> List[GamePayload]:
    return [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "attention_required"
    ]


def _capture_injections(agent: MinecraftAgent) -> Callable[[], List[str]]:
    captured: List[str] = []
    agent._task_finished = False

    def collect() -> List[str]:
        while agent.queued_messages:
            _, content = agent._wake_queue.pop()
            captured.append(content)
        return captured

    return collect


async def test_gear_broken_is_injected_while_a_task_is_running() -> None:
    """任务进行中装备用坏：注入模型，让模型知道工具没了、可当场换工具或换办法。"""
    agent, _llm, _bus = await make_minecraft_agent(FakeMaicraft(), lambda messages: _resp("好"), live=False)
    collect = _capture_injections(agent)

    await agent._on_body_event({"kind": "gear_broken", "message": "装备用坏了，铁镐坏了", "cursor": 1})
    captured = collect()
    assert len(captured) == 1
    assert captured[0].startswith("[装备] 装备用坏了，铁镐坏了。")
    assert "换一件好的或换种办法" in captured[0]
    await agent.stop()


async def test_gear_broken_reaches_the_streamer_between_batches_instead_of_being_dropped() -> None:
    """批次间隙装备用坏：素材发布器收到排程、发出原话，不注入唤醒队列。"""
    agent, _llm, bus = await make_minecraft_agent(FakeMaicraft(), lambda messages: _resp("好"), live=False)

    await agent._on_body_event({"kind": "gear_broken", "message": "装备用坏了，铁镐坏了", "cursor": 1})
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert [p.message for p in _attention_payloads(bus)] == ["装备用坏了，铁镐坏了"], "Mod 原话直通主播素材"
    assert agent.queued_messages == [], "批次间隙不注入唤醒队列"
    await agent.stop()


class _SummaryLlm:
    """summary 档替身：记下收到的档位与提示，按剧本回复或抛错。"""

    def __init__(self, behavior: Callable[[List[Dict[str, Any]]], Response]) -> None:
        self.behavior = behavior
        self.calls: List[Dict[str, Any]] = []

    async def generate(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Response:
        self.calls.append({"messages": messages, "profile": kwargs.get("profile")})
        return self.behavior(messages)

    def is_profile_visual(self, profile: Optional[str]) -> bool:
        del profile
        return False

    def get_profile_context_window(self, profile: Optional[str]) -> int:
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
    await _wait_until(lambda: len(_attention_payloads(bus)) == 2)

    assert llm.calls and llm.calls[0]["profile"] == "summary", "概述走 summary 档"
    first, summarized = _attention_payloads(bus)
    assert first.message == short and first.scene == "", "短文直通，不调概述"
    assert "干海带块" in summarized.message, "概述保信息"
    assert summarized.scene.startswith("原文留档：") and long_text in summarized.scene, "原文留档进载荷"

    # 互不重复的排查分句白话化后仍超阈值，压缩兜底照常触发
    heterogeneous = "，".join(f"挖了第{i}层都没有找到铁矿，背包里只剩{i}块圆石" for i in range(30))
    await agent._on_body_event({"kind": "need_unhandled", "message": heterogeneous})
    await _wait_until(lambda: len(_attention_payloads(bus)) == 3)
    assert _attention_payloads(bus)[2].scene.startswith("原文留档："), "留档进 scene"
    await agent.stop()


async def test_summary_failure_falls_back_to_first_sentence() -> None:
    """压缩兜底仍有效：素材调度器直接收到超阈值长文时走 summary 档，概述失败降级首句斩尾。"""
    server = FakeMaicraft()

    def boom(messages: List[Dict[str, Any]]) -> Response:
        raise RuntimeError("summary 引擎不可用")

    llm = _SummaryLlm(boom)
    agent, _llm, bus = await make_minecraft_agent(server, llm=llm, live=False)

    long_text = "饿了没吃上。翻过箱子没有面包，合成台缺干海带块，烧炼炉缺原料。" + "细节" * 200
    agent._attention_material.schedule(long_text)
    await _wait_until(lambda: len(_attention_payloads(bus)) == 1)

    emitted = _attention_payloads(bus)[0]
    assert emitted.message.startswith("饿了没吃上。"), "降级为首句斩尾"
    assert "细节" not in emitted.message, "斩尾不带后半长文"
    assert long_text in emitted.scene, "原文留档仍在"
    await agent.stop()
