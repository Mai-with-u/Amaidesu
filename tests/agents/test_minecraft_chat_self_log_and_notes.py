"""测试 MinecraftAgent 的两项公屏链路行为：

- sequence 内嵌 chat 的本方公屏记账：已执行步骤进 game.self.chatted 与 [你回过]，
  未执行到被中断、命令与对生物说话不误记，折叠 /attempts 翻页读回，同一 goal_id 仅记一次；
- maicraft_execute 公屏消息超软上限的非阻断轻提醒：不拦截不报错，只在 notes 附温和提示，
  兼容直接 chat 与 sequence 内嵌 chat，跳过 dry_run、命令/私聊与对生物说话。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

import pytest

from src.agents.minecraft.agent import (
    PUBLIC_CHAT_SOFT_LENGTH_LIMIT,
    _append_observation_note,
    _extract_sequence_self_chats,
    _public_chat_length_note,
)
from src.agents.minecraft.maicraft import GoalRun
from src.modules.agents import AgentManager, LeadCoordinator, LeadHandoverContext
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameSelfChattedPayload
from src.modules.llm.payload import Response
from src.modules.tools.registry import ToolRegistry
from tests.agents.minecraft._wiring_helpers import StreamerPlaceholder, no_real_sleep, settle
from tests.agents.minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
)
from tests.helpers import FakeClock


def _seq_run(
    goal_id: int,
    result: Dict[str, Any],
    *,
    ability: str = "maicraft:sequence",
    state: str = "finished",
) -> GoalRun:
    """构造测试用 sequence 目标运行快照。"""
    return GoalRun(
        goal_id=goal_id,
        ability=ability,
        purpose="组合步骤",
        state=state,
        question=None,
        result=result,
        doing="",
        data={"goal_id": goal_id, "ability": ability, "state": state, "result": result},
    )


_MSG_PREFIX_RE = re.compile(r"^\[m\d+\]\s*")


def _tool_observations(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从模型看到的对话历史提取全部 tool 角色观察 JSON（剥离后台折叠的 [mNNNN] 编号前缀）。"""
    parsed: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        raw = msg.get("content")
        if isinstance(raw, str):
            body = _MSG_PREFIX_RE.sub("", raw, count=1).strip()
            try:
                obj = json.loads(body)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                parsed.append(obj)
    return parsed


# ======================================================================
# sequence 内嵌 chat 本方公屏提取与全链路记账
# ======================================================================


def test_extract_sequence_self_chats_reads_only_submitted_public_chats() -> None:
    """仅提取已实际提交的普通公屏 chat（tried='提交聊天'），忽略命令、未提交项与非 sequence 目标。"""
    run = _seq_run(
        10,
        {
            "status": "partial",
            "summary": "2 / 4 步做成",
            "attempts": [
                {"tried": "提交聊天", "result": "我先去看看矿洞"},
                {"tried": "提交命令", "result": "/home"},
                {"tried": "提交聊天", "result": "/msg PlayerA 悄悄话"},
                {"tried": "打开箱子", "result": "已打开"},
                {"tried": "提交聊天", "result": "找到了铁矿"},
            ],
            "remaining": ["第 4 步（maicraft:chat，回来报喜）：没有开始"],
        },
    )
    assert _extract_sequence_self_chats(run) == ["我先去看看矿洞", "找到了铁矿"]

    # 未结束的 sequence 不提前提取
    running = _seq_run(
        11,
        {"status": "done", "attempts": [{"tried": "提交聊天", "result": "不应提前记"}]},
        state="running",
    )
    assert _extract_sequence_self_chats(running) == []

    # 兼容带结构化 steps 形态：只认 submitted=True 且非对生物/非命令
    structured = _seq_run(
        12,
        {
            "status": "partial",
            "summary": "部分完成",
            "steps": [
                {"ability": "maicraft:chat", "details": {"message": "第一句公屏", "submitted": True}},
                {"ability": "maicraft:chat", "details": {"message": "打字被取消", "submitted": False}},
                {
                    "ability": "maicraft:chat",
                    "details": {"message": "对生物说话", "submitted": True, "to": {"id": "e3", "name": "女仆"}},
                },
                {"ability": "maicraft:chat", "details": {"message": "/home", "submitted": True}},
            ],
        },
    )
    assert _extract_sequence_self_chats(structured) == ["第一句公屏"]


async def test_sequence_embedded_chat_emits_self_chatted_and_records_dialogue_without_unexecuted_steps() -> None:
    """端到端：sequence 内嵌 2 步 chat，第 1 步执行完、第 2 步中断未执行；只对已执行的第 1 步记账并进 [你回过]。"""
    clock = FakeClock()
    server = FakeMaicraft()
    bus = EventBus(enable_stats=False)
    emitted: List[str] = []

    async def _capture(_event: str, payload: Any, _source: str) -> None:
        if isinstance(payload, GameSelfChattedPayload):
            emitted.append(payload.message)

    bus.on(CoreEvents.GAME_SELF_CHATTED, _capture, model_class=GameSelfChattedPayload)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    dispatched = [False]

    def script(_messages: List[Dict[str, Any]]) -> Response:
        if not dispatched[0]:
            dispatched[0] = True
            return _resp(
                "下达含两步 chat 的 sequence",
                calls=[
                    _call(
                        "maicraft_execute",
                        {
                            "goal": {
                                "ability": "sequence",
                                "purpose": "先打招呼再挖矿再收尾",
                                "steps": [
                                    {"ability": "chat", "parameters": {"message": "我这就去采点圆石"}},
                                    {"ability": "obtain", "parameters": {"item": "minecraft:cobblestone", "count": 4}},
                                    {"ability": "chat", "parameters": {"message": "挖好了回来咯"}},
                                ],
                            }
                        },
                        "c_seq_1",
                    )
                ],
            )
        return _resp("知道了，继续等待")

    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=2_000,
        chat_flush_wait=no_real_sleep,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="sequence 公屏记账"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: dispatched[0] and not agent.batch_active)
        assert len(server.goals) == 1
        seq_goal_id = next(iter(server.goals))

        # 模拟第 1 步 chat 已执行提交，第 2 步失败中断，第 3 步 chat 未开始执行
        server.goals[seq_goal_id]["state"] = "finished"
        server.goals[seq_goal_id]["result"] = {
            "status": "partial",
            "summary": "1 / 3 步做成",
            "changes": [],
            "remaining": [
                "第 2 步（maicraft:obtain）：附近没矿",
                "第 3 步（maicraft:chat）：没有开始",
            ],
            "attempts": [
                {"tried": "提交聊天", "result": "我这就去采点圆石"},
                {"tried": "找石头", "result": "找不到"},
            ],
        }
        server._append("finished", seq_goal_id, "1 / 3 步做成", status="partial")

        await _wait_until(lambda: len(emitted) == 1 and len(llm.seen) >= 2 and not agent.batch_active)
        await settle(bus)

        # 只有实际执行的第 1 步入账，未执行到的第 3 步绝不误记
        assert emitted == ["我这就去采点圆石"]
        hits = await agent.query_short_term_memory("我这就去采点圆石")
        assert any("我这就去采点圆石" in item["text"] for item in hits)
        assert await agent.query_short_term_memory("挖好了回来咯") == []
        # 双向对话窗 [你回过] 完整收录且不含未执行步骤
        state_text = _texts(llm.seen[-1])
        assert "[你回过" in state_text and "我这就去采点圆石" in state_text
        assert "挖好了回来咯" not in state_text
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_sequence_with_folded_attempts_reads_back_and_emits_once() -> None:
    """当 sequence 结束结果的 /attempts 因体积过大折进 result.folded 时，异步按路径读回并准确记账。"""
    clock = FakeClock()
    server = FakeMaicraft()
    bus = EventBus(enable_stats=False)
    emitted: List[str] = []

    async def _capture(_event: str, payload: Any, _source: str) -> None:
        if isinstance(payload, GameSelfChattedPayload):
            emitted.append(payload.message)

    bus.on(CoreEvents.GAME_SELF_CHATTED, _capture, model_class=GameSelfChattedPayload)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    dispatched = [False]

    def script(_messages: List[Dict[str, Any]]) -> Response:
        if not dispatched[0]:
            dispatched[0] = True
            return _resp(
                "执行大序列",
                calls=[
                    _call(
                        "maicraft_execute",
                        {
                            "goal": {
                                "ability": "sequence",
                                "steps": [
                                    {"ability": "chat", "parameters": {"message": "出发去修桥了"}},
                                    {"ability": "chat", "parameters": {"message": "桥修完啦"}},
                                ],
                            }
                        },
                        "c_seq_folded",
                    )
                ],
            )
        return _resp("收到")

    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=2_000,
        chat_flush_wait=no_real_sleep,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="折叠 attempts 测试"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: dispatched[0] and not agent.batch_active)
        seq_goal_id = next(iter(server.goals))

        # 模拟 Mod FoldedEvidence 把 attempts 折进 result.folded
        server.folded_attempts[seq_goal_id] = [
            {"tried": "提交聊天", "result": "出发去修桥了"},
            {"tried": "提交命令", "result": "/spawn"},
            {"tried": "提交聊天", "result": "桥修完啦"},
        ]
        server.goals[seq_goal_id]["state"] = "finished"
        server.goals[seq_goal_id]["result"] = {
            "status": "done",
            "summary": "按顺序做完了全部 2 步",
            "folded": [{"path": "/attempts", "type": "list", "items": 3, "chars": 5000}],
        }
        server._append("finished", seq_goal_id, "按顺序做完了全部 2 步", status="done")

        await _wait_until(lambda: len(emitted) == 2 and len(llm.seen) >= 2 and not agent.batch_active)
        await settle(bus)

        assert emitted == ["出发去修桥了", "桥修完啦"]
    finally:
        await agent.stop()
        await bus.cleanup()


# ======================================================================
# maicraft_execute 公屏超软上限非阻断轻提醒
# ======================================================================


@pytest.mark.parametrize(
    ("arguments", "expect_note"),
    [
        # 恰好 45 字边界：不提醒
        ({"goal": {"ability": "chat", "parameters": {"message": "字" * PUBLIC_CHAT_SOFT_LENGTH_LIMIT}}}, False),
        # 46 字超限：提醒
        ({"goal": {"ability": "chat", "parameters": {"message": "字" * (PUBLIC_CHAT_SOFT_LENGTH_LIMIT + 1)}}}, True),
        # 带命名空间 maicraft:chat 超限：提醒
        (
            {
                "goal": {
                    "ability": "maicraft:chat",
                    "parameters": {"message": "字" * (PUBLIC_CHAT_SOFT_LENGTH_LIMIT + 5)},
                }
            },
            True,
        ),
        # sequence 内嵌超长公屏 chat：提醒
        (
            {
                "goal": {
                    "ability": "sequence",
                    "steps": [
                        {"ability": "travel", "target": {"kind": "landmark", "name": "家"}},
                        {"ability": "chat", "parameters": {"message": "字" * (PUBLIC_CHAT_SOFT_LENGTH_LIMIT + 3)}},
                    ],
                }
            },
            True,
        ),
        # 私聊命令 /msg 超长：跳过不提醒
        (
            {
                "goal": {
                    "ability": "chat",
                    "parameters": {"message": "/msg PlayerA " + "字" * (PUBLIC_CHAT_SOFT_LENGTH_LIMIT + 10)},
                }
            },
            False,
        ),
        # 对生物说话（带 target）超长：跳过不提醒
        (
            {
                "goal": {
                    "ability": "chat",
                    "target": {"kind": "seen", "id": "e4"},
                    "parameters": {"message": "字" * (PUBLIC_CHAT_SOFT_LENGTH_LIMIT + 10)},
                }
            },
            False,
        ),
        # dry_run=True 估算模式：跳过不提醒
        (
            {
                "dry_run": True,
                "goal": {"ability": "chat", "parameters": {"message": "字" * (PUBLIC_CHAT_SOFT_LENGTH_LIMIT + 10)}},
            },
            False,
        ),
    ],
    ids=[
        "恰好45字",
        "46字直接chat",
        "带命名空间chat",
        "sequence内嵌超长chat",
        "私聊命令跳过",
        "对生物说话跳过",
        "dry_run跳过",
    ],
)
def test_public_chat_length_note_rules(arguments: Dict[str, Any], expect_note: bool) -> None:
    """验证普通公屏超 45 字提醒的精确触发边界与豁免规则。"""
    note = _public_chat_length_note(arguments)
    if expect_note:
        assert note != ""
        assert "游戏聊天栏较窄" in note
    else:
        assert note == ""


async def test_maicraft_execute_long_public_chat_attaches_non_blocking_note_for_chat_and_sequence() -> None:
    """端到端 ReAct：直接 chat（含异步 running）与 sequence 内嵌超 45 字公屏均不阻断，工具 ok=True 且 notes 附温和提醒。"""
    clock = FakeClock()
    server = FakeMaicraft()
    # 开启逐字打字形态（execute 返回 state="running"），验证异步受理时同样即时附轻提醒
    server.chat_instant = False
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    long_direct = (
        "我把熔炉那边的原料都整理好了，顺便也把箱子里的圆石和木炭分了一下类，等会儿烧完铁锭我就直接拿过去给你。"
    )
    long_seq = "我先沿着北边小路过去看看仓库里的红石够不够用，如果不够的话我再顺路下矿洞补一批回来一起交给你们。"
    short_msg = "好，我这就去看看"
    assert len(long_direct) > PUBLIC_CHAT_SOFT_LENGTH_LIMIT
    assert len(long_seq) > PUBLIC_CHAT_SOFT_LENGTH_LIMIT
    assert len(short_msg) <= PUBLIC_CHAT_SOFT_LENGTH_LIMIT

    step_idx = [0]

    def script(_messages: List[Dict[str, Any]]) -> Response:
        step_idx[0] += 1
        if step_idx[0] == 1:
            return _resp(
                "连发三条测试消息",
                calls=[
                    _call(
                        "maicraft_execute", {"goal": {"ability": "chat", "parameters": {"message": short_msg}}}, "c1"
                    ),
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "chat", "parameters": {"message": long_direct}}},
                        "c2",
                    ),
                    _call(
                        "maicraft_execute",
                        {
                            "goal": {
                                "ability": "sequence",
                                "steps": [{"ability": "chat", "parameters": {"message": long_seq}}],
                            }
                        },
                        "c3",
                    ),
                ],
            )
        return _resp("收到提醒，下次说短点")

    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=2_000,
        chat_flush_wait=no_real_sleep,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="公屏轻提醒测试"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) >= 2 and not agent.batch_active)

        observations = _tool_observations(llm.seen[1])
        assert len(observations) == 3
        obs_short, obs_long_direct, obs_long_seq = observations

        # 全部放行不拦截、不报错
        assert obs_short["ok"] is True
        assert obs_long_direct["ok"] is True
        assert obs_long_seq["ok"] is True

        # 短消息不附长度提醒
        assert not obs_short.get("notes")
        # 超 45 字的直接 chat（哪怕异步 running）与 sequence 内嵌 chat 均在 notes 附温和轻提醒
        assert any("游戏聊天栏较窄" in str(n) for n in obs_long_direct.get("notes") or [])
        assert any(f"{len(long_direct)} 字" in str(n) for n in obs_long_direct.get("notes") or [])
        assert any("游戏聊天栏较窄" in str(n) for n in obs_long_seq.get("notes") or [])
        assert any(f"{len(long_seq)} 字" in str(n) for n in obs_long_seq.get("notes") or [])
    finally:
        await agent.stop()
        await bus.cleanup()


def test_append_observation_note_preserves_existing_notes_and_deduplicates() -> None:
    """原返回已有 notes 时保留原条目并追加轻提醒，重复追加同一提醒不重复。"""
    obs: Dict[str, Any] = {"ok": True, "notes": ["原有 Mod 说明"]}
    _append_observation_note(obs, "这条公屏消息有 50 字，游戏聊天栏较窄，下次可以更简短一点或拆成几句说。")
    _append_observation_note(obs, "这条公屏消息有 50 字，游戏聊天栏较窄，下次可以更简短一点或拆成几句说。")
    assert obs["notes"] == [
        "原有 Mod 说明",
        "这条公屏消息有 50 字，游戏聊天栏较窄，下次可以更简短一点或拆成几句说。",
    ]


async def test_goal_get_reading_finished_sequence_records_self_chat_once_without_duplicate() -> None:
    """模型先于事件流通过 maicraft_goal(operation=get) 读到已结束 sequence，同样触发记账且不因后续事件重复。"""
    clock = FakeClock()
    server = FakeMaicraft()
    bus = EventBus(enable_stats=False)
    emitted: List[str] = []

    async def _capture(_event: str, payload: Any, _source: str) -> None:
        if isinstance(payload, GameSelfChattedPayload):
            emitted.append(payload.message)

    bus.on(CoreEvents.GAME_SELF_CHATTED, _capture, model_class=GameSelfChattedPayload)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    turn = [0]

    def script(_messages: List[Dict[str, Any]]) -> Response:
        turn[0] += 1
        if turn[0] == 1:
            return _resp(
                "下达 sequence",
                calls=[
                    _call(
                        "maicraft_execute",
                        {
                            "goal": {
                                "ability": "sequence",
                                "steps": [{"ability": "chat", "parameters": {"message": "我先去看看熔炉"}}],
                            }
                        },
                        "c_seq_exec",
                    )
                ],
            )
        if turn[0] == 2:
            # 在事件流通知前先把服务端目标置为 finished，并由模型直接 goal(get) 读回
            gid = next(iter(server.goals))
            server.goals[gid]["state"] = "finished"
            server.goals[gid]["result"] = {
                "status": "done",
                "summary": "按顺序做完了全部 1 步",
                "attempts": [{"tried": "提交聊天", "result": "我先去看看熔炉"}],
            }
            return _resp(
                "主动查一次目标结果",
                calls=[_call("maicraft_goal", {"operation": "get", "goal_id": gid}, "c_seq_get")],
            )
        return _resp("已确认完成")

    agent, _llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=2_000,
        chat_flush_wait=no_real_sleep,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="goal get 先读终态"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: turn[0] >= 3 and not agent.batch_active)
        await settle(bus)
        # 再补推一条迟到的 finished 事件，验证同一 goal_id 绝不重复记账
        gid = next(iter(server.goals))
        server._append("finished", gid, "按顺序做完了全部 1 步", status="done")
        await settle(bus)

        assert emitted == ["我先去看看熔炉"]
    finally:
        await agent.stop()
        await bus.cleanup()
