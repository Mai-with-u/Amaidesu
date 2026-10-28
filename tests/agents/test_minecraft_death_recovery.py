"""本文件测 MinecraftAgent 的死亡恢复主题：死亡通知唤醒在跑任务、死亡恢复决策按任务状态唤醒并回答。"""

from __future__ import annotations

from typing import Any, Dict, List


from src.agents.minecraft.maicraft import goal_run_of
from src.modules.events.payloads.game import GamePayload
from src.modules.llm.payload import Response

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _called,
    _reports,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
)


async def test_character_death_tells_the_running_task_and_the_streamer() -> None:
    """角色死了：在跑的任务被唤醒，知道目标保留着、重生后接着做；同时上报主播。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False and len(llm.seen) == 2)

    server.body_event("character_died", "角色死了，等重生；主任务停在原地，重生后接着做")
    await _wait_until(lambda: len(llm.seen) == 3)

    woken = _texts(llm.seen[-1])
    assert "[身体] 角色死了" in woken and "目标保留着" in woken
    alerts = [
        call.args[1]
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "attention_required"
    ]
    # 上报主播的是 Mod 原话（死亡属短事件直通；长排查文由素材调度器压缩）
    assert any("角色死了" in alert.message and "主任务停在原地" in alert.message for alert in alerts)
    assert agent.tracked_goal_ids == [1], "死亡不结束目标"
    await agent.stop()


async def test_a_death_recovery_question_wakes_the_task_to_answer_it() -> None:
    """角色死了：死亡通知说明要回答死亡恢复决策，决策提问带着选项唤醒任务，模型按选项回答。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 下砍树目标 -> 等砍树 -> 角色死了被决策唤醒 -> 回答一次重生 -> 回去接着等砍树。
        # 答过就让出：决策提问一直留在历史里，若照着它一直答，替身模型与替身 Mod 都不让出事件循环，
        # 任务循环会空转到上下文超预算才停，测试等条件的轮询一直轮不上。
        if _called(messages, "maicraft_goal"):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
        if "Mod 挂出的决策" in _texts(messages):
            return _resp(calls=[_call("maicraft_goal", {"operation": "answer", "goal_id": -1, "answer": "respawn"})])
        if any(message.get("role") == "tool" and "running" in str(message.get("content")) for message in messages):
            return _resp(calls=[_call("minecraft_wait", {"reason": "等砍树"})])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍树"}})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去砍点木头", task_id="d-1")
    await _wait_until(lambda: agent.tracked_goal_ids == [1])
    await _wait_until(lambda: agent.batch_active is False and len(llm.seen) == 2)

    server.die()
    await _wait_until(lambda: server.calls_to("goal") and server.calls_to("goal")[-1].get("operation") == "answer")

    woken = _texts(llm.seen[-1])
    assert "死亡恢复决策等你回答" in woken and "goal_id=-1" in woken and "respawn" in woken
    assert server.calls_to("goal")[-1] == {"operation": "answer", "goal_id": -1, "answer": "respawn"}
    assert agent.tracked_goal_ids == [1], "决策不进跟踪名单，原来的目标还在跟"
    await agent.stop()


_ANSWER_RESPAWN = {"operation": "answer", "goal_id": -1, "answer": "respawn"}


async def test_a_death_decision_wakes_an_idle_agent_as_a_new_task() -> None:
    """任务交付后闲着时角色死了：死亡恢复决策照样叫醒模型，由它开一个新任务，角色不会一直停在死亡屏幕。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 看一眼周围交付 -> 闲着 -> 角色死了被决策叫醒（新任务）-> 回答重生 -> 交付。
        if _called(messages, "maicraft_goal"):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "复活了"})])
        if "Mod 挂出的决策" in _texts(messages):
            return _resp(calls=[_call("maicraft_goal", dict(_ANSWER_RESPAWN))])
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1 and agent.batch_active is False)
    assert agent.observation()["task_finished"]

    server.die()
    await _wait_until(lambda: len(_reports(bus)) == 2)
    woken = _texts(llm.seen[-1])
    assert "Mod 挂出的决策" in woken and "goal_id=-1" in woken
    assert "看一眼周围" not in woken, "交付过的任务不再续，由决策开新任务"
    assert "[开局资料]" in woken, "新任务照常附上开局资料"
    assert server.calls_to("goal")[-1] == _ANSWER_RESPAWN
    await agent.stop()


async def test_a_death_decision_wakes_a_task_suspended_for_the_streamer() -> None:
    """上报主播挂起后角色死了：决策叫醒模型接着原任务答，不当成新指令；答完可以接着等主播拿主意。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        # 缺燃料上报主播挂起 -> 角色死了被决策叫醒（接着原任务）-> 回答重生 -> 再上报一次接着等主播。
        if _called(messages, "maicraft_goal"):
            return _resp(calls=[_call("minecraft_report", {"kind": "escalation", "content": "复活了，还是缺燃料"})])
        if "Mod 挂出的决策" in _texts(messages):
            return _resp(calls=[_call("maicraft_goal", dict(_ANSWER_RESPAWN))])
        return _resp(calls=[_call("minecraft_report", {"kind": "escalation", "content": "缺燃料，要不要去砍树"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="烧点铁锭", task_id="d-1")
    await _wait_until(lambda: agent.observation()["task_suspended"] and agent.batch_active is False)

    server.die()
    await _wait_until(lambda: len(_reports(bus)) == 2)
    woken = _texts(llm.seen[-1])
    assert "烧点铁锭" in woken and "死亡恢复决策等你回答" in woken and "Mod 挂出的决策" in woken
    assert server.calls_to("goal")[-1] == _ANSWER_RESPAWN
    assert agent.observation()["task_instructions"] == ["烧点铁锭"], "决策不记成原始指令"
    assert agent.observation()["task_suspended"], "答完再上报就接着等主播"
    await agent.stop()


async def test_a_death_while_the_task_is_being_delivered_still_gets_answered() -> None:
    """模型交付的那一步角色死了：决策不管排在交付前还是交付后，都不能留在队列里没人读，交付后由它开新任务。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if _called(messages, "maicraft_goal"):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "复活了"})])
        if "Mod 挂出的决策" in _texts(messages):
            return _resp(calls=[_call("maicraft_goal", dict(_ANSWER_RESPAWN))])
        # 模型还在想这一步时角色死了（任务进行中），这一步随即交付；只死一次。
        if not any(goal_id < 0 for goal_id in server.goals):
            server.die()
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    # 目标跟踪第一次读事件流只建游标、不补发之前的事件：等它读过一次（发出第二次读取）再派活，
    # 不然第一步就死的事件会被当成接手前的旧事件跳过。
    await _wait_until(lambda: len(server.calls_to("events")) >= 2)
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 2)
    assert "Mod 挂出的决策" in _texts(llm.seen[-1])
    assert server.calls_to("goal")[-1] == _ANSWER_RESPAWN
    assert not agent.queued_messages
    await agent.stop()


async def test_a_death_decision_is_not_queued_while_off_air() -> None:
    """收工态不排决策叫醒：下一场开播时冒出上一场的死亡决策只会让模型去答过时的问题。"""
    agent, llm, _bus = await make_minecraft_agent(FakeMaicraft(), lambda messages: _resp("好"), live=False)
    decision = goal_run_of(
        {"goal_id": -1, "ability": "death_recovery", "state": "awaiting_answer", "question": {"text": "角色死了"}}
    )
    assert decision is not None
    await agent._on_decision_asked(decision)
    assert not agent.queued_messages and llm.seen == []
    await agent.stop()
