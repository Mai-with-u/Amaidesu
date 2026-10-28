"""本文件测 MinecraftAgent 的请求发不出去时的补救主题：夹带超长 base64 的回执换写、对话重开、身体持续故障的如实上报。"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import MagicMock


from src.modules.events.payloads.game import GamePayload
from src.modules.llm.engine import _guard_inline_payload, _normalize_generate_input
from src.modules.llm.errors import UnsendableRequestError
from src.modules.llm.payload import Response

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _reports,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
)


# ---------------- 请求发不出去时的补救 ----------------


def _errors(bus: MagicMock) -> List[str]:
    return [
        call.args[1].message
        for call in bus.emit.await_args_list
        if isinstance(call.args[1], GamePayload) and call.args[1].event_type == "error"
    ]


async def test_binary_in_a_receipt_is_swapped_for_a_note_and_the_task_goes_on() -> None:
    """周围的读数里夹着一长串 base64，整条请求被装配守卫拒发：换成一句说明重试一次，其余内容照旧，任务接着做完。"""
    server = FakeMaicraft()
    server.scene_view["snapshot"] = "Qk" * 110_000

    def script(messages: List[Dict[str, Any]]) -> Response:
        _guard_inline_payload(_normalize_generate_input(messages, system=None, tools=None, temperature=None))
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看过周围了"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="看一眼周围", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    sent = _texts(llm.seen[-1]) + json.dumps(llm.seen[-1], ensure_ascii=False)
    assert "Qk" * 1000 not in sent and "220000 字连续 base64" in sent
    assert "minecraft:cow" in sent, "回执其余内容原样留着"
    assert _reports(bus)[0].report_kind == "delivery" and not agent.observation()["body_fault"]
    await agent.stop()


async def test_history_that_still_cannot_be_sent_is_restarted_from_the_task_facts() -> None:
    """换不掉的内容让请求一直发不出去：整段对话丢掉，按原指令与待办重开，补读现场后接着做。"""
    server = FakeMaicraft()
    server.scene_view["note"] = "坏掉的回执"

    def script(messages: List[Dict[str, Any]]) -> Response:
        if any(message.get("role") == "tool" and "坏掉的回执" in str(message.get("content")) for message in messages):
            raise UnsendableRequestError("装配守卫：拒绝发送")
        if "整段丢掉" in _texts(messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "重新看过，做完了"})])
        return _resp(calls=[_call("maicraft_observe", {"what": "scene"})])

    agent, llm, bus = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="看看周围有什么", task_id="d-1")
    await _wait_until(lambda: len(_reports(bus)) == 1)

    restarted = llm.seen[-1]
    assert restarted[1]["content"].startswith("[原任务事实与已有证据]") and "看看周围有什么" in restarted[1]["content"]
    assert "[开局资料]" in _texts(restarted), "重开后补读现场"
    assert not any(message.get("role") in {"assistant", "tool"} for message in restarted)
    assert _errors(bus) == [] and not agent.observation()["body_fault"]
    await agent.stop()


async def test_a_body_that_cannot_send_anything_says_it_is_broken_instead_of_waiting_for_the_streamer() -> None:
    """换回执、重开对话都救不回来：任务停下，报错写明重新派活也不会好，主播看一眼读到身体出故障了；
    再派一次活也只补救一轮就停，不会无限重发。暂时性失败则说再派一次会重新试，成功一次就恢复正常。"""
    server = FakeMaicraft()
    attempts: List[int] = []

    def broken(messages: List[Dict[str, Any]]) -> Response:
        attempts.append(1)
        raise UnsendableRequestError("装配守卫：拒绝发送")

    agent, llm, bus = await make_minecraft_agent(server, broken)
    agent.receive_delegation(instruction="去砍树", task_id="d-1")
    await _wait_until(lambda: len(_errors(bus)) == 1 and agent.batch_active is False)

    assert len(attempts) == 2, "原对话一次、重开后一次"
    assert "重新派活也不会好" in _errors(bus)[0]
    assert agent.observation()["task_suspended"] and agent._glance_work()["state"].startswith("身体出故障了，动不了")
    agent.receive_delegation(instruction="再试试砍树", task_id="d-2")
    await _wait_until(lambda: len(_errors(bus)) == 2 and agent.batch_active is False)
    assert len(attempts) == 4

    llm.script = lambda messages: Response(success=False, error="网络断了")
    agent.receive_delegation(instruction="还是去砍树", task_id="d-3")
    await _wait_until(lambda: len(_errors(bus)) == 3 and agent.batch_active is False)
    assert agent._glance_work()["state"] == "身体出故障了，暂时动不了；再派一次活它会重新试"

    llm.script = lambda messages: _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "砍完了"})])
    agent.receive_delegation(instruction="最后去砍一次树", task_id="d-4")
    await _wait_until(lambda: len(_reports(bus)) == 1)
    assert not agent.observation()["body_fault"] and agent._glance_work()["state"] != "卡住了，正等我拿主意"
    await agent.stop()
