"""本文件测 MinecraftAgent 收到别的玩家冲角色做的肢体动作（``player_body_language``）时怎么叫醒模型。

覆盖：
- 交付后闲着：照样叫醒，但不开新任务、不兜底交付；回的动作做完也不再叫醒
- 闲着时这一批里上报会被拒，不调工具也安静收尾
- 上报后等主播定夺：叫醒回应，等定夺的状态和委派账原样留着，不注入"继续原游戏任务"
- 任务进行中：插进原任务的历史，原任务照常交付
- 收工态不叫醒；肢体动作不记进随上报带给主播的身体事件
- 按人节流：同一个人 20 秒内第二条不叫醒、到点合成一条再叫醒一次；两个人各计各的；
  正在干活的批次跑着时照常跟着下一步读，只回应互动的批次里再来的照样攒着；没有 player 字段的共用一个桶
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from src.modules.llm.payload import Response

from .minecraft_v1_fakes import (
    FakeMaicraft,
    ScriptLlm,
    _call,
    _called,
    _reports,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
)

_SQUAT = "玩家 Alex（e4）看着你蹲起 4 下；他在你前方 3 格"
_BODY_LANGUAGE = {"goal": {"ability": "body_language", "parameters": {"operation": "crouch", "count": 3}}}


def _tool_said(messages: List[Dict[str, Any]], text: str) -> bool:
    """最后一条工具回执里有没有这句话。"""
    return bool(messages) and messages[-1].get("role") == "tool" and text in str(messages[-1].get("content"))


def _answer_squat(messages: List[Dict[str, Any]]) -> Response:
    """被 [互动] 叫醒就蹲回去，蹲起在跑就让出；别的时候交付"看过周围了"。"""
    if "[互动]" in _texts(messages):
        if _tool_said(messages, "running"):
            return _resp("等目标事件通知")
        if not _called(messages, "maicraft_execute"):
            return _resp(calls=[_call("maicraft_execute", _BODY_LANGUAGE)])
    return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])


async def test_an_idle_agent_answers_a_squat_without_a_new_task_or_a_delivery() -> None:
    """交付后有人冲她蹲起：叫醒模型回一个蹲起，不开新任务、不再交付；蹲起做完的通知只排队，不再叫醒。"""
    server = FakeMaicraft()
    agent, llm, bus = await make_minecraft_agent(server, _answer_squat)
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1 and agent.batch_active is False)

    server.body_event("player_body_language", _SQUAT)
    await _wait_until(lambda: agent.tracked_goal_ids == [1] and agent.batch_active is False and len(llm.seen) == 3)

    woken = _texts(llm.seen[1])
    assert f"[互动] {_SQUAT}。" in woken and "看一眼周围" in woken, "接着上个任务的历史读，没有清空重开"
    assert len(_reports(bus)) == 1, "回一个动作不交付，主播不会每蹲一下念一次"
    observed = agent.observation()
    assert observed["task_finished"] is True and observed["task_instructions"] == ["看一眼周围"]
    assert observed["batch_body_events"] == [], "肢体动作不挤进随上报带给主播的身体事件"

    server.finish(1, summary="蹲起 3 下")
    await _wait_until(lambda: any("目标 1" in str(content) for _, content in agent.queued_messages))
    await asyncio.sleep(0.2)
    assert len(llm.seen) == 3, "闲着时回的动作做完不再叫醒模型"
    await agent.stop()


async def test_reporting_is_refused_and_a_silent_turn_ends_quietly_while_only_answering() -> None:
    """只回应互动的一批：上报被拒（不是一件要交付的活）；不调工具就安静收尾，没有兜底交付。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 交付 -> 被 [互动] 叫醒先试着上报 -> 被拒后不调工具结束这一轮
        if "[互动]" in _texts(messages):
            if _tool_said(messages, "不是一件要交付的活"):
                return _resp("回个招呼就好")
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "跟 Alex 打了招呼"})])
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1 and agent.batch_active is False)

    server.body_event("player_body_language", _SQUAT)
    await _wait_until(lambda: len(llm.seen) == 3 and agent.batch_active is False)

    assert [report.message for report in _reports(bus)] == ["看过周围了"], "被拒的上报和兜底交付都没有发出去"
    assert agent.observation()["task_finished"] is True
    await agent.stop()


class _RecordingLedger:
    """框架任务账替身：记下每次写入，查条目一律查不到。"""

    def __init__(self) -> None:
        self.updates: List[Dict[str, Any]] = []

    def update(self, task_id: str, status: str, **kwargs: Any) -> bool:
        self.updates.append({"task_id": task_id, "status": status})
        return True

    def get(self, task_id: str) -> None:
        return None


async def test_a_squat_while_waiting_for_the_streamer_keeps_the_task_waiting() -> None:
    """上报后等主播定夺时有人冲她蹲起：回一个动作，任务仍在等定夺，委派账不被改回进行中，也不续原任务。"""
    server = FakeMaicraft()
    ledger = _RecordingLedger()
    tracker = SimpleNamespace(ledger=ledger, track=lambda **kwargs: None)

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "[互动]" in _texts(messages):
            return _answer_squat(messages)
        return _resp(calls=[_call("minecraft_report", {"kind": "escalation", "content": "缺燃料，要不要去砍树"})])

    agent, llm, bus = await make_minecraft_agent(server, script, tracker=tracker)
    agent.receive_delegation(instruction="烧点铁锭", task_id="d-1")
    await _wait_until(lambda: agent.observation()["task_suspended"] and agent.batch_active is False)
    waiting = [update["status"] for update in ledger.updates if update["task_id"] == "d-1"]
    assert waiting[-1] == "waiting_for_decision"

    server.body_event("player_body_language", _SQUAT)
    await _wait_until(lambda: agent.tracked_goal_ids == [1] and agent.batch_active is False and len(llm.seen) == 3)

    assert "[继续原游戏任务]" not in _texts(llm.seen[1]), "只回应互动，不把原任务再摊开一遍"
    assert [update["status"] for update in ledger.updates if update["task_id"] == "d-1"] == waiting
    observed = agent.observation()
    assert observed["task_suspended"] is True and observed["task_instructions"] == ["烧点铁锭"]
    assert [report.report_kind for report in _reports(bus)] == ["escalation"], "没有多出交付或上报"
    await agent.stop()


async def test_a_squat_during_a_running_task_cuts_into_its_history() -> None:
    """任务进行中有人冲她蹲起：在原任务里叫醒模型，回完接着等原目标，原目标结束照常交付。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 下砍树目标 -> 让出 -> 被 [互动] 叫醒蹲回去 -> 让出 -> 砍树结束交付
        if "砍了 5 块原木" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "砍到 5 块原木了"})])
        if _tool_said(messages, "running"):
            return _resp("等目标事件通知")
        if "[互动]" in _texts(messages) and _texts(messages).count("maicraft:body_language") == 0:
            return _resp(calls=[_call("maicraft_execute", _BODY_LANGUAGE)])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1] and agent.batch_active is False and len(llm.seen) == 2)

    server.body_event("player_body_language", _SQUAT)
    await _wait_until(lambda: agent.tracked_goal_ids == [1, 2] and agent.batch_active is False and len(llm.seen) == 4)
    woken = _texts(llm.seen[2])
    assert f"[互动] {_SQUAT}" in woken and "去砍点木头" in woken, "插进原任务的历史"
    assert agent.observation()["task_instructions"] == ["去砍点木头"], "互动不记成原始指令"

    server.finish(2, summary="蹲起 3 下")
    server.finish(1, summary="砍了 5 块原木")
    await _wait_until(lambda: len(_reports(bus)) == 1)
    assert _reports(bus)[0].message == "砍到 5 块原木了"
    await agent.stop()


async def test_a_squat_is_dropped_while_off_air() -> None:
    """收工态不叫醒：下播后有人冲她蹲起没人看，开播时冒出来只是过时的事。"""
    agent, llm, _bus = await make_minecraft_agent(FakeMaicraft(), lambda messages: _resp("好"), live=False)
    await agent._on_body_event({"kind": "player_body_language", "message": _SQUAT, "cursor": 1})
    assert not agent.queued_messages and llm.seen == []
    await agent.stop()


# 按人节流

_ALEX = {"name": "Alex", "uuid": "00000000-0000-0000-0000-00000000a1ec", "id": "e4"}
_STEVE = {"name": "Steve", "uuid": "00000000-0000-0000-0000-0000000005e7", "id": "e2"}


def _reply_hi(messages: List[Dict[str, Any]]) -> Response:
    """被 [互动] 叫醒就不调工具收尾（只回应互动的一批）；别的时候交付"看过周围了"。"""
    if "[互动]" in _texts(messages):
        return _resp("回个招呼就好")
    return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])


async def _idle_agent(
    script: Any = _reply_hi, llm: Optional[Any] = None
) -> tuple[Any, Any, Any, FakeMaicraft, List[float]]:
    """接上注入时钟、已交付闲着的游戏 Agent：返回 (agent, llm, bus, 替身服务器, 时钟)。"""
    server = FakeMaicraft()
    now = [1000.0]
    if llm is None:
        agent, llm, bus = await make_minecraft_agent(server, script, clock=lambda: now[0])
    else:
        agent, llm, bus = await make_minecraft_agent(server, llm=llm, clock=lambda: now[0])
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1 and agent.batch_active is False)
    return agent, llm, bus, server, now


async def test_the_same_player_wakes_once_and_the_held_squats_come_together_when_due() -> None:
    """同一个人 20 秒内的第二、三条不叫醒，先攒着；到点合成一条，只再叫醒一次。"""
    agent, llm, _bus, server, now = await _idle_agent()
    server.body_event("player_body_language", "玩家 Alex（e4）看着你蹲起 4 下；他在你前方 3 格", {"player": _ALEX})
    await _wait_until(lambda: len(llm.seen) == 2 and agent.batch_active is False)

    now[0] += 5
    server.body_event("player_body_language", "玩家 Alex（e4）看着你蹲起 3 下；他在你前方 3 格", {"player": _ALEX})
    now[0] += 3
    server.body_event("player_body_language", "玩家 Alex（e4）拿出铁剑；他在你前方 2 格", {"player": _ALEX})
    await _wait_until(lambda: agent.observation()["held_interactions"] == 2)
    assert len(llm.seen) == 2, "20 秒内同一个人不再叫醒"
    assert not agent.queued_messages, "攒着的不进队列"

    now[0] += 13
    await _wait_until(lambda: len(llm.seen) == 3 and agent.batch_active is False)
    woken = _texts(llm.seen[-1])
    assert "这一阵攒下 2 段动作" in woken
    assert "1. 玩家 Alex（e4）看着你蹲起 3 下" in woken and "2. 玩家 Alex（e4）拿出铁剑" in woken
    assert agent.observation()["held_interactions"] == 0
    await agent.stop()


async def test_two_players_wake_her_independently() -> None:
    """Alex 刚叫醒过，Steve 5 秒后冲她挥手照样叫醒：各计各的 20 秒。"""
    agent, llm, _bus, server, now = await _idle_agent()
    server.body_event("player_body_language", "玩家 Alex（e4）看着你蹲起 4 下；他在你前方 3 格", {"player": _ALEX})
    await _wait_until(lambda: len(llm.seen) == 2 and agent.batch_active is False)

    now[0] += 5
    server.body_event("player_body_language", "玩家 Steve（e2）对着你挥了 2 下；他在你左侧 4 格", {"player": _STEVE})
    await _wait_until(lambda: len(llm.seen) == 3 and agent.batch_active is False)
    assert "玩家 Steve（e2）对着你挥了 2 下" in _texts(llm.seen[-1])
    assert agent.observation()["held_interactions"] == 0
    await agent.stop()


async def test_squats_without_a_player_field_share_one_bucket() -> None:
    """事件上没有 player 字段（认不出是谁）：所有人共用一个桶，20 秒内第二条照样攒着。"""
    agent, llm, _bus, server, now = await _idle_agent()
    server.body_event("player_body_language", "有玩家看着你蹲起 4 下；他在你前方 3 格")
    await _wait_until(lambda: len(llm.seen) == 2 and agent.batch_active is False)

    now[0] += 5
    server.body_event("player_body_language", "另一个玩家对着你挥了 2 下；他在你左侧 4 格")
    await _wait_until(lambda: agent.observation()["held_interactions"] == 1)
    assert len(llm.seen) == 2
    await agent.stop()


class _GatedLlm(ScriptLlm):
    """推理时能拦住的模型替身：门关着时停在"想"这一步，批次一直算在跑。"""

    def __init__(self, script: Any) -> None:
        super().__init__(script)
        self.gate = asyncio.Event()
        self.gate.set()

    async def generate(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Response:
        self.seen.append([dict(message) for message in messages])
        await self.gate.wait()
        return self.script(messages)


async def test_squats_arriving_while_a_working_batch_runs_are_read_with_its_next_step() -> None:
    """正在干活的批次跑着时同一个人连着蹲：不攒，照常跟着下一步一起读（本来就不多调模型）。"""

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 下砍树目标（想着的时候 Alex 连蹲两段）-> 下一步读到这两段，砍树还在跑就让出
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp("等目标事件通知")
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    server = FakeMaicraft()
    now = [1000.0]
    llm = _GatedLlm(script)
    agent, _llm, _bus = await make_minecraft_agent(server, llm=llm, clock=lambda: now[0])
    llm.gate.clear()
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: len(llm.seen) == 1 and agent.batch_active is True)

    server.body_event("player_body_language", "玩家 Alex（e4）看着你蹲起 4 下；他在你前方 3 格", {"player": _ALEX})
    now[0] += 3
    server.body_event("player_body_language", "玩家 Alex（e4）原地跳了 2 下；他在你前方 3 格", {"player": _ALEX})
    await _wait_until(lambda: len(agent.queued_messages) == 2)
    assert agent.observation()["held_interactions"] == 0, "正在干活的批次跑着时不攒"

    llm.gate.set()
    await _wait_until(lambda: len(llm.seen) == 2 and agent.batch_active is False)
    next_step = _texts(llm.seen[-1])
    assert "玩家 Alex（e4）看着你蹲起 4 下" in next_step and "玩家 Alex（e4）原地跳了 2 下" in next_step
    await agent.stop()


async def test_squats_during_an_answer_only_batch_are_held_until_due() -> None:
    """闲着时被 Alex 叫醒，只回应互动的那一批还在跑时他又蹲：这一条照样攒着；
    那一批让出后正常结束，不再开第二批；20 秒到了才合成一条再叫醒一次。"""

    def script(messages: List[Dict[str, Any]]) -> Response:
        if "[互动]" in _texts(messages):
            return _resp("等目标事件通知")
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])

    llm = _GatedLlm(script)
    agent, _llm, _bus, server, now = await _idle_agent(llm=llm)
    llm.gate.clear()
    server.body_event("player_body_language", "玩家 Alex（e4）看着你蹲起 4 下；他在你前方 3 格", {"player": _ALEX})
    await _wait_until(lambda: len(llm.seen) == 2 and agent.batch_active is True)

    now[0] += 6
    server.body_event("player_body_language", "玩家 Alex（e4）看着你蹲起 3 下；他在你前方 3 格", {"player": _ALEX})
    await _wait_until(lambda: agent.observation()["held_interactions"] == 1)
    assert not agent.queued_messages, "只回应互动的批次里再来的不跟着读"

    llm.gate.set()
    await _wait_until(lambda: agent.batch_active is False)
    await asyncio.sleep(0.2)
    assert len(llm.seen) == 2, "让出后不再开第二批"
    assert agent.observation()["held_interactions"] == 1

    now[0] += 14
    await _wait_until(lambda: len(llm.seen) == 3 and agent.batch_active is False)
    assert "玩家 Alex（e4）看着你蹲起 3 下" in _texts(llm.seen[-1])
    assert agent.observation()["held_interactions"] == 0
    await agent.stop()
