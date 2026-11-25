"""游戏公屏拆发/快捷字段提取/提示词边界与活跃 maicraft:timer 合法等待态回归测试。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from src.agents.minecraft.agent import (
    PUBLIC_CHAT_MAX_CHUNKS,
    PUBLIC_CHAT_SOFT_LENGTH_LIMIT,
    PUBLIC_CHAT_SPLIT_THRESHOLD,
    _extract_public_chat_messages_from_goal,
    _public_chat_length_note,
    split_public_chat_message,
)
from src.agents.minecraft.lead_mode import LEAD_MODE_PROMPT_SECTION
from src.agents.minecraft.maicraft import goal_run_of
from src.agents.minecraft.wait_progress import WaitProgress
from src.modules.agents.lead import LeadHandoverContext
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.game_chat import GameSelfChattedPayload
from src.modules.llm.payload import Response
from tests.agents.minecraft_v1_fakes import (
    FakeMaicraft,
    ManualClock,
    _call,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
)


# ======================================================================
# 快捷字段 goal.message 漏检修复与确定性游戏公屏短句拆分
# ======================================================================


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        (
            {"ability": "chat", "parameters": {"message": "我先去仓库拿木头"}},
            ["我先去仓库拿木头"],
        ),
        (
            {"ability": "maicraft:chat", "message": "快捷写法也要被识别出来"},
            ["快捷写法也要被识别出来"],
        ),
        (
            {"ability": "chat", "parameters": {}, "message": "parameters 空时回退读顶层 message"},
            ["parameters 空时回退读顶层 message"],
        ),
        (
            {"ability": "chat", "parameters": {"message": "优先参数正文"}, "message": "顶层备用"},
            ["优先参数正文"],
        ),
        (
            {"ability": "chat", "target": {"kind": "seen", "id": "e1"}, "message": "对女仆说话不算游戏公屏"},
            [],
        ),
        (
            {"ability": "chat", "message": "/msg PartnerA 悄悄说一句"},
            [],
        ),
        (
            {
                "ability": "sequence",
                "steps": [
                    {"ability": "travel", "target": {"kind": "landmark", "name": "家"}},
                    {"ability": "chat", "message": "到家了，我把矿放进箱子"},
                    {"ability": "chat", "parameters": {"message": "/home"}},
                    {"ability": "chat", "parameters": {"message": "顺便烧点铁锭"}},
                ],
            },
            ["到家了，我把矿放进箱子", "顺便烧点铁锭"],
        ),
    ],
)
def test_extract_public_chat_messages_supports_shortcut_and_sequence(goal: Dict[str, Any], expected: List[str]) -> None:
    """_extract_public_chat_messages_from_goal 兼容 goal.message 快捷字段并覆盖 sequence 内嵌 chat。"""
    assert _extract_public_chat_messages_from_goal(goal) == expected


@pytest.mark.parametrize(
    ("text", "expect_split"),
    [
        ("收到，我这就过去看看", False),
        ("/msg PartnerA " + "很长的一段私聊命令测试内容" * 6, False),
        (
            "我先把刚才在矿洞挖出来的铁矿全放到一楼熔炉那边烧着。顺便再去东边仓库补一点圆石和木板，等会儿一起把传送带和齿轮箱接起来！",
            True,
        ),
        (
            "这边动力压板机我刚调好齿轮方向，现在转速已经完全够用了，不过旁边储物箱里的铁锭快烧完了，我先去补一趟原料再回来接着搭完",
            True,
        ),
        (
            "没有标点的超长连续中文测试内容用来验证保底分段逻辑不会把整段文字塞进同一行聊天栏里挤爆屏幕显示空间导致同伴根本看不清",
            True,
        ),
    ],
)
def test_split_public_chat_message_boundaries(text: str, expect_split: bool) -> None:
    """确定性短句拆分遵守阈值、命令豁免、最多 3 段与不丢字边界。"""
    chunks = split_public_chat_message(text)
    if not expect_split:
        assert chunks == [text]
    else:
        assert len(text) > PUBLIC_CHAT_SPLIT_THRESHOLD
        assert 2 <= len(chunks) <= PUBLIC_CHAT_MAX_CHUNKS
        assert all(chunk.strip() for chunk in chunks)
        assert "".join(chunks) == text


def test_public_chat_length_note_detects_shortcut_message_between_soft_and_split_limit() -> None:
    """46–55 字的快捷写法 goal.message 也能触发温和长度提醒（不再漏检）。"""
    mid_msg = "我先把刚采回来的铁矿放进一楼自动熔炉里烧着，等烧完顺手把传送带和齿轮箱一起接上吧，我马上就回来"
    assert PUBLIC_CHAT_SOFT_LENGTH_LIMIT < len(mid_msg) <= PUBLIC_CHAT_SPLIT_THRESHOLD
    note = _public_chat_length_note({"goal": {"ability": "chat", "message": mid_msg}})
    assert str(len(mid_msg)) in note


def test_lead_mode_prompt_guides_natural_chat_and_plain_commentary_without_raw_readouts() -> None:
    """主控态提示词明确游戏公屏不用裸坐标报位置、framework_commentary 用处境白话并保留专有名称。"""
    assert "裸坐标" in LEAD_MODE_PROMPT_SECTION
    assert "处境白话" in LEAD_MODE_PROMPT_SECTION
    assert "专有名称保持原样" in LEAD_MODE_PROMPT_SECTION


async def test_long_public_chat_splits_into_cut_in_chats_with_zero_extra_llm_and_full_dedup() -> None:
    """超长普通游戏公屏（含快捷 goal.message）自动拆成多条独立 chat 顺序发送、不打断主目标且回声去重不漏。"""
    bus = EventBus(enable_stats=False)
    self_chatted: List[str] = []

    async def _on_self_chat(_name: str, payload: GameSelfChattedPayload, _src: str) -> None:
        self_chatted.append(payload.message)

    bus.on(CoreEvents.GAME_SELF_CHATTED, _on_self_chat, model_class=GameSelfChattedPayload)
    server = FakeMaicraft()
    long_msg = "我先把刚才在矿洞挖出来的铁矿全放到一楼熔炉那边烧着。顺便再去东边仓库补一点圆石和木板，等会儿一起把传送带和齿轮箱接起来！"
    assert len(long_msg) > PUBLIC_CHAT_SPLIT_THRESHOLD

    turn = 0

    def script(messages: List[Dict[str, Any]]) -> Response:
        nonlocal turn
        turn += 1
        if turn == 1:
            # 先起一个后台主目标，再同轮发一条快捷写法的超长游戏公屏（带 request_key 测试幂等键派生）
            return _resp(
                calls=[
                    _call("maicraft_execute", {"goal": {"ability": "gather", "parameters": {"item": "oak_log"}}}, "c1"),
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "chat", "message": long_msg}, "request_key": "chat-req-1"},
                        "c2",
                    ),
                ]
            )
        return _resp("继续等采集完成")

    agent, llm, _ = await make_minecraft_agent(server, script, bus=bus, live=True)
    try:
        assert (
            await agent.enter_lead(
                LeadHandoverContext(segment_id="seg-chat", segment_title="联机建厂", segment_goal="修传送带")
            )
            is True
        )
        await _wait_until(lambda: len(llm.seen) >= 2 and not agent.batch_active)
        await bus.wait_until_drained()

        # 拆发零额外 LLM 轮次：整批只走了“调工具 -> 读回执结束”正常两步
        assert len(llm.seen) == 2

        # 后台主目标仍在运行，没有被 sequence 替换打断
        assert len(agent.tracked_goal_ids) == 1

        # 发给 MaiCraft 的全是独立 chat（CUTS_IN），且每条都派生了不冲突的 request_key
        chat_execs = [
            args for args in server.calls_to("execute") if str((args.get("goal") or {}).get("ability") or "") == "chat"
        ]
        assert 2 <= len(chat_execs) <= PUBLIC_CHAT_MAX_CHUNKS
        sent_texts = [str(args["goal"]["parameters"]["message"]) for args in chat_execs]
        assert "".join(sent_texts) == long_msg
        assert all("message" not in args["goal"] for args in chat_execs)
        req_keys = [args.get("request_key") for args in chat_execs]
        assert len(set(req_keys)) == len(chat_execs)

        # 每条拆发出的短句都完整发出了 GAME_SELF_CHATTED，且已被 _own_emitted_chats 消化、不触发自唤醒
        assert self_chatted == sent_texts
        assert agent.queued_messages == []

        # 工具回执附带宿主自动拆发说明，且不重复拼接旧的超长提醒
        tool_msgs = [m for m in llm.seen[-1] if m.get("role") == "tool" and m.get("tool_call_id") == "c2"]
        assert len(tool_msgs) == 1
        raw_content = str(tool_msgs[0]["content"])
        json_payload = raw_content[raw_content.index("{") :]
        obs = json.loads(json_payload)
        notes = obs.get("notes") or []
        assert any("宿主已按句界拆成" in n for n in notes)
        assert not any("下次可以更简短一点或拆成几句说" in n for n in notes)
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_sequence_embedded_long_chat_expands_steps_in_place() -> None:
    """sequence 内嵌的超长普通游戏公屏在步骤内原地展开为连续短句步骤。"""
    server = FakeMaicraft()
    long_msg = "我刚把一楼仓库里堆着的矿石、木板和圆石全都清点完了。接下来先去地下室把动力磨粉机接上大水车，接好之后再回来整理箱子！"
    assert len(long_msg) > PUBLIC_CHAT_SPLIT_THRESHOLD
    turn = 0

    def script(_messages: List[Dict[str, Any]]) -> Response:
        nonlocal turn
        turn += 1
        if turn == 1:
            return _resp(
                calls=[
                    _call(
                        "maicraft_execute",
                        {
                            "goal": {
                                "ability": "sequence",
                                "steps": [
                                    {"ability": "travel", "target": {"kind": "landmark", "name": "仓库"}},
                                    {"ability": "chat", "message": long_msg},
                                ],
                            }
                        },
                        "c1",
                    )
                ]
            )
        return _resp("等序列跑完")

    agent, llm, bus = await make_minecraft_agent(server, script, live=True)
    try:
        assert (
            await agent.enter_lead(
                LeadHandoverContext(segment_id="seg-seq", segment_title="联机建厂", segment_goal="整理仓库")
            )
            is True
        )
        await _wait_until(lambda: len(llm.seen) >= 2 and not agent.batch_active)

        seq_execs = [
            args
            for args in server.calls_to("execute")
            if str((args.get("goal") or {}).get("ability") or "") == "sequence"
        ]
        assert len(seq_execs) == 1
        steps = seq_execs[0]["goal"]["steps"]
        assert steps[0]["ability"] == "travel"
        chat_steps = [step for step in steps[1:] if step["ability"] == "chat"]
        assert 2 <= len(chat_steps) <= PUBLIC_CHAT_MAX_CHUNKS
        assert "".join(step["parameters"]["message"] for step in chat_steps) == long_msg
    finally:
        await agent.stop()
        if isinstance(bus, EventBus):
            await bus.cleanup()


# ======================================================================
# 活跃 maicraft:timer 纳入合法等待态（假时钟、零真实 sleep）
# ======================================================================


def test_wait_progress_active_timers_tracks_set_due_cancel_and_expiry_with_fake_clock() -> None:
    """WaitProgress.active_timers 精确维护计时器登记、timer_due 核销、cancel 核销与到期自动过期。"""
    clock = ManualClock(1000.0)
    wp = WaitProgress(clock)

    # 经 details.pending 登记计时器
    set_run = goal_run_of(
        {
            "goal_id": 1,
            "ability": "maicraft:timer",
            "state": "finished",
            "result": {
                "status": "done",
                "details": {
                    "timer_id": 7,
                    "pending": [{"timer_id": 7, "remaining_seconds": 60, "note": "等熔炉烧好"}],
                },
            },
        }
    )
    assert set_run is not None
    wp.absorb_timer(set_run)
    timers = wp.active_timers()
    assert len(timers) == 1
    assert timers[0].timer_id == 7
    assert timers[0].note == "等熔炉烧好"
    assert timers[0].remaining_seconds(clock()) == pytest.approx(60.0)

    # 推进假时钟 30 秒：仍在有效期内
    clock.advance(30.0)
    assert len(wp.active_timers()) == 1
    assert wp.active_timers()[0].remaining_seconds(clock()) == pytest.approx(30.0)

    # 收到 timer_due：立即核销
    wp.timer_due({"kind": "timer_due", "timer_id": 7, "message": "计时 7 到点：等熔炉烧好"})
    assert wp.active_timers() == []

    # 老版本无 pending 时回退读取快捷参数，且假时钟超过到期时刻自动核销
    legacy_run = goal_run_of(
        {"goal_id": 2, "ability": "maicraft:timer", "state": "finished", "result": {"status": "done", "details": {}}}
    )
    assert legacy_run is not None
    wp.absorb_timer(legacy_run, {"goal": {"ability": "timer", "after_seconds": 45, "note": "等作物成熟"}})
    assert len(wp.active_timers()) == 1
    clock.advance(45.0)
    assert wp.active_timers() == []


async def test_lead_mode_active_timer_suppresses_auto_advance_nudge_and_idle_suspend() -> None:
    """主控态挂了未到期 maicraft:timer 且有未完成待办时，模型不调工具等待应静默让出，不误催也不触发防空转挂起。"""
    clock = ManualClock(2000.0)
    bus = EventBus(enable_stats=False)
    server = FakeMaicraft()

    turn = 0

    def script(messages: List[Dict[str, Any]]) -> Response:
        nonlocal turn
        turn += 1
        if turn == 1:
            # 写一条未完成待办并挂一个 120 秒计时器（无后台在跑主目标）
            return _resp(
                calls=[
                    _call(
                        "minecraft_todo",
                        {"action": "write", "todos": [{"content": "等铁锭烧完后去取", "status": "in_progress"}]},
                        "c1",
                    ),
                    _call(
                        "maicraft_execute",
                        {
                            "goal": {
                                "ability": "timer",
                                "parameters": {"operation": "set", "after_seconds": 120, "note": "回炉取铁锭"},
                            }
                        },
                        "c2",
                    ),
                ]
            )
        if turn == 2:
            # 模型明确选择不调工具等待计时器到点
            return _resp("计时器已经设好了，先等 120 秒到点通知再回去取。")
        if turn == 3:
            # 计时到点唤醒后收工标记待办完成
            return _resp(
                calls=[
                    _call(
                        "minecraft_todo",
                        {"action": "write", "todos": [{"content": "等铁锭烧完后去取", "status": "done"}]},
                        "c3",
                    )
                ]
            )
        # 待办写完后的自然纯文本收尾，避免循环继续调工具
        return _resp("待办已完成，铁锭收好了。")

    agent, llm, _ = await make_minecraft_agent(server, script, bus=bus, live=True, clock=clock)
    try:
        assert (
            await agent.enter_lead(
                LeadHandoverContext(segment_id="seg-timer", segment_title="熔炼等待", segment_goal="烧铁锭")
            )
            is True
        )
        await _wait_until(lambda: len(llm.seen) >= 2 and not agent.batch_active)

        # 关键断言：模型在第 2 步不调工具后直接合法让出，绝没有收到 [自主推进] 误催，也没有消耗空转计数或挂起
        assert len(llm.seen) == 2
        assert "[自主推进]" not in _texts(llm.seen[-1])
        obs = agent.observation()
        assert obs["lead_empty_turns"] == 0
        assert obs["lead_idle_waiting"] is False
        assert obs["active_timer_count"] == 1
        assert len(agent.active_timers) == 1
        timer_id = agent.active_timers[0].timer_id

        # 计时器到点通知抵达：自动核销活跃计时器，并唤醒模型处理待办（精准 4 轮收尾）
        clock.advance(120.0)
        server.body_event("timer_due", f"计时 {timer_id} 到点：回炉取铁锭", {"timer_id": timer_id})
        await _wait_until(lambda: len(llm.seen) >= 4 and not agent.batch_active)
        assert len(llm.seen) == 4
        assert agent.active_timers == []
        assert "[计时]" in _texts(llm.seen[2])
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_lead_mode_expired_timer_auto_clears_and_restores_auto_advance_guard() -> None:
    """若 timer_due 丢失但假时钟已跨过到期时刻，活跃计时器自动核销，后续不再永久静默。"""
    clock = ManualClock(3000.0)
    bus = EventBus(enable_stats=False)
    server = FakeMaicraft()

    turn = 0

    def script(_messages: List[Dict[str, Any]]) -> Response:
        nonlocal turn
        turn += 1
        if turn == 1:
            return _resp(
                calls=[
                    _call(
                        "minecraft_todo",
                        {"action": "write", "todos": [{"content": "收割小麦", "status": "in_progress"}]},
                        "c1",
                    ),
                    _call(
                        "maicraft_execute",
                        {
                            "goal": {
                                "ability": "timer",
                                "parameters": {"operation": "set", "after_seconds": 30, "note": "看小麦长好没"},
                            }
                        },
                        "c2",
                    ),
                ]
            )
        # 始终不调工具：未到期时静默让出，过期后被外部唤醒时恢复 [自主推进] 提醒
        return _resp("先看看情况")

    agent, llm, _ = await make_minecraft_agent(server, script, bus=bus, live=True, clock=clock)
    try:
        assert (
            await agent.enter_lead(
                LeadHandoverContext(segment_id="seg-exp", segment_title="农田等待", segment_goal="收小麦")
            )
            is True
        )
        await _wait_until(lambda: len(llm.seen) >= 2 and not agent.batch_active)
        assert len(llm.seen) == 2
        assert len(agent.active_timers) == 1

        # 推进假时钟超过 30 秒且不发送 timer_due：计时器自动核销
        clock.advance(35.0)
        assert agent.active_timers == []

        # 此时再唤醒一轮且模型仍不调工具：恢复正常 [自主推进] 兜底，不会因过期计时器永久跳过
        await agent.receive_suggestion("顺便看看麦田")
        await _wait_until(lambda: len(llm.seen) >= 4 and not agent.batch_active)
        assert "[自主推进]" in _texts(llm.seen[-1])
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_delegation_mode_active_timer_avoids_no_progress_nudge_and_undelivered() -> None:
    """委派态挂了未到期 maicraft:timer 且有未完成待办时，不调工具等待不触发催促也不误发 undelivered。"""
    clock = ManualClock(4000.0)
    bus = EventBus(enable_stats=False)
    reports: List[GamePayload] = []

    async def _on_report(_name: str, payload: GamePayload, _src: str) -> None:
        reports.append(payload)

    bus.on(CoreEvents.GAME_REPORT, _on_report, model_class=GamePayload)
    server = FakeMaicraft()

    turn = 0

    def script(_messages: List[Dict[str, Any]]) -> Response:
        nonlocal turn
        turn += 1
        if turn == 1:
            return _resp(
                calls=[
                    _call(
                        "minecraft_todo",
                        {"action": "write", "todos": [{"content": "等天亮再出门", "status": "in_progress"}]},
                        "c1",
                    ),
                    _call(
                        "maicraft_execute",
                        {
                            "goal": {
                                "ability": "timer",
                                "parameters": {"operation": "set", "after_seconds": 90, "note": "等天亮"},
                            }
                        },
                        "c2",
                    ),
                ]
            )
        return _resp("已挂好计时器，等待天亮。")

    agent, llm, _ = await make_minecraft_agent(server, script, bus=bus, live=True, clock=clock)
    try:
        assert agent.receive_delegation(instruction="等天亮后去门口收麦子", task_id="deleg-timer-1") is None
        await _wait_until(lambda: len(llm.seen) >= 2 and not agent.batch_active)
        await bus.wait_until_drained()

        assert len(llm.seen) == 2
        assert "[任务尚需行动]" not in _texts(llm.seen[-1])
        assert reports == []
        assert len(agent.active_timers) == 1
    finally:
        await agent.stop()
        await bus.cleanup()
