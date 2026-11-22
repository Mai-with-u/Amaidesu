"""MinecraftAgent 折叠原话保留、短期语义回捞与被动线索卡接线测试（假时钟、零真实 sleep）。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List

from src.agents.minecraft.agent import _extract_preserved_human_lines
from src.agents.minecraft.config import MinecraftContextConfig
from src.modules.agents import AgentManager, LeadCoordinator, LeadHandoverContext
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload, GameSelfChattedPayload
from src.modules.llm.embedding import EmbeddingSetup
from src.modules.llm.payload import Response
from src.modules.storage.database import SQLiteDatabase
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from tests.agents.minecraft._wiring_helpers import StreamerPlaceholder, settle
from tests.agents.minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _resp,
    _texts,
    _wait_until,
    make_minecraft_agent,
    offline_config,
)
from tests.helpers import FakeClock
from tests.modules.llm.fakes import FakeEmbeddingServer


def test_extract_preserved_human_lines_strips_ref_enforces_limits() -> None:
    """机械提取人类原话：剥 [mNNNN] 与只读尾注，单条限 120 字、单块最多 6 条且总长 ≤500 字。"""
    long_line = "超长公屏内容" + "字" * 150
    originals: List[Dict[str, Any]] = [
        {"role": "user", "content": "[m0001] [系统] 这是宿主系统提示，不应保留"},
        {"role": "user", "content": "[m0002] [开局资料] 宿主持有的开局资料，不应保留"},
        {
            "role": "user",
            "content": (
                "[m0003] [公屏合批] 收到 2 条玩家消息\n"
                "- [公屏·点名你] 同伴甲：麦麦别拆红石钟\n"
                f"- [公屏·身边5格] 同伴乙：{long_line}\n\n"
                "[脑中闪过]（只读联想线索）\n- 这段闪过不能当原话"
            ),
        },
        {
            "role": "user",
            "content": (
                "[m0004] [直播间建议]（来源：streamer）\n"
                "建议内容：观众建议先补火把\n"
                "来源原话：\n"
                "- 水友A：矿洞太黑快插火把"
            ),
        },
        {"role": "user", "content": "[m0005] [主播补充]\n记得留一组煤炭\n[交付提醒] 若已完成请交付"},
    ]
    for idx in range(6, 12):
        originals.append(
            {"role": "user", "content": f"[m00{idx:02d}] [公屏·身边3格] 同伴{idx}：第 {idx} 句叮嘱 " + "啊" * 70}
        )

    preserved = _extract_preserved_human_lines(originals)
    assert 1 <= len(preserved) <= 6
    assert sum(len(line) for line in preserved) <= 500
    assert all(len(line) <= 120 for line in preserved)
    assert all(not line.startswith("[m") for line in preserved)
    assert all("宿主系统提示" not in line and "这段闪过不能当原话" not in line for line in preserved)

    # 验证单条 120 字截断与 [直播间建议]/[主播补充] 提取
    small_originals = originals[:5]
    small = _extract_preserved_human_lines(small_originals)
    assert any("麦麦别拆红石钟" in line for line in small)
    assert any(line.endswith("…") and len(line) == 120 for line in small)
    assert any("[直播间建议] 观众建议先补火把" in line for line in small)
    assert any("记得留一组煤炭" in line and "交付提醒" not in line for line in small)

    # 独立 [公屏旁听] 绝不抽入原话保留（避免挤掉真实建议与身边公屏）；非主控多行 [公屏] 保留全部行
    multiline_and_eavesdrop: List[Dict[str, Any]] = [
        {
            "role": "user",
            "content": "[m0020] [公屏] 玩家 同伴甲：第一行提醒\n第二行记得关闸门\n\n[脑中闪过]\n- 附尾闪过不保留",
        },
        {
            "role": "user",
            "content": "[m0021] [公屏旁听]（以下为远处公屏闲聊）\n- [公屏·远处] 路人A：主城卖甘蔗啦\n- [公屏·远处] 路人B：收木头",
        },
    ]
    extracted = _extract_preserved_human_lines(multiline_and_eavesdrop)
    assert len(extracted) == 1
    assert "第一行提醒 第二行记得关闸门" in extracted[0]
    assert "附尾闪过不保留" not in extracted[0]
    assert all("主城卖甘蔗啦" not in line and "收木头" not in line for line in extracted)

    # 严格白名单：拦截 [装备]、[流程单超时提醒] 及任意未知宿主通知，绝不误收进人类原话保留区
    host_notifications: List[Dict[str, Any]] = [
        {"role": "user", "content": "[m0030] [装备] 你的石镐用坏了。这是观察不是命令"},
        {"role": "user", "content": "[m0031] [流程单超时提醒] 当前环节已超时 5 分钟"},
        {"role": "user", "content": "[m0032] [未知系统事件] 区块重新加载完成"},
        {"role": "user", "content": "[m0033] [运营原话]\n今晚记得修完水车"},
    ]
    whitelist_only = _extract_preserved_human_lines(host_notifications)
    assert whitelist_only == ["[运营原话] 今晚记得修完水车"]


async def test_fold_preserves_human_lines_and_context_recall_falls_back_to_semantic_blocks(
    chars_as_tokens: None,
) -> None:
    """后台折叠落地保留原话并收录短期记忆；minecraft_context_recall 字面 0 命中时自动按语义回捞折叠块与块号。"""
    del chars_as_tokens
    clock = FakeClock()
    emb_server = FakeEmbeddingServer()
    emb_client = emb_server.client(clock=clock.now_ms)
    server = FakeMaicraft()
    server.scene_view["summary"] = "前方有大片草地与小溪" + "草" * 2000

    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    step = {"n": 0}
    recalled_semantic: List[Dict[str, Any]] = []

    def script(messages: List[Dict[str, Any]]) -> Response:
        last_text = str(messages[-1].get("content") or "")
        if last_text.startswith("[宿主整理请求]"):
            ranges = re.findall(r"^- (\S+)–(\S+)：", last_text, re.M)
            start, end = ranges[0]
            return _resp(
                calls=[
                    _call(
                        "minecraft_context_fold",
                        {"folds": [{"start": start, "end": end, "summary": "先前把小猫安置在河边并巡视了营地"}]},
                        "f_fold_1",
                    )
                ]
            )
        step["n"] += 1
        joined = _texts(messages)
        if "记得给那只猫留条鱼干" not in joined:
            # 等公屏先入历史再开始连续观察触发折叠，保证公屏落在 b1 被折区间内
            return _resp("先等一下同伴开口")
        if "[已折叠 b1" in joined and not recalled_semantic:
            recalled_semantic.append({"triggered": True})
            return _resp(
                "按同义词回捞刚才折叠的小猫段落",
                calls=[_call("minecraft_context_recall", {"query": "那只橘猫在哪"}, "r_sem_1")],
            )
        if any(msg.get("tool_call_id") == "r_sem_1" for msg in messages) or step["n"] > 20:
            return _resp("先休息一下")
        return _resp(calls=[_call("maicraft_observe", {"what": "scene"}, f"obs_{step['n']}")])

    cfg = offline_config(events_wait_ms=1000).model_copy(
        update={"context": MinecraftContextConfig(max_tokens=40_000, fold_keep_recent=1)}
    )
    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        config=cfg,
        embedding_setup=EmbeddingSetup(client=emb_client, note=""),
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=0,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="营地日常"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) >= 1 and not agent.batch_active)
        # 在折叠发生前送入一条含“小猫”的人类原话公屏，确保它进入被折区间
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="同伴甲",
                sender_distance=6.0,
                content="记得给那只猫留条鱼干",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: bool(recalled_semantic) and not agent.batch_active)

        # 1) 折叠替身包含 [本段人类原话保留] 且原样保留了公屏人话
        all_seen = "\n".join(_texts(seen) for seen in llm.seen)
        assert "[已折叠 b1" in all_seen
        assert "[本段人类原话保留]" in all_seen
        assert "记得给那只猫留条鱼干" in all_seen

        # 2) 等待后台嵌入任务真实处理完含折叠摘要的请求后，经工具面调用 minecraft_context_recall：
        #    字面“橘猫”0 命中 -> 自动返回 semantic_blocks 含 b1
        await _wait_until(lambda: any("河边" in text for batch in emb_server.requests for text in batch))
        await settle(bus, turns=5)
        res = await registry.invoke(
            ToolInvocation(
                tool_name="minecraft_context_recall",
                arguments={"query": "那只橘猫在哪"},
                source="minecraft-react",
                invoking_agent="minecraft",
            )
        )
        assert res.success is True
        body = res.structured_content
        assert body["ok"] is True
        assert body["hits"] == []
        assert body["semantic_blocks"]
        assert body["semantic_blocks"][0]["block"] == "b1"
        assert any("留条鱼干" in line for line in body["semantic_blocks"][0]["preserved_lines"])
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_landmark_arrival_attaches_passive_flash_at_tail_without_rewriting_fixed_prefix() -> None:
    """主控态刚走近记住地标时，经 LeadCoordinator.fetch_passive_cues 拉取被动线索并作为 [脑中闪过] 附在尾部。"""
    clock = FakeClock()
    server = FakeMaicraft()
    server.enter_world("save_landmark", "北境二周目")
    server.world_memory_view = {
        "places": [
            {
                "name": "北岸水车房",
                "position": {"dimension": "minecraft:overworld", "x": 100.0, "y": 64.0, "z": 0.0},
            }
        ],
        "regions": [],
        "records": [],
    }
    server.self_view["position"] = {"x": 0.0, "y": 64.0, "z": 0.0, "dimension": "minecraft:overworld"}

    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    cue_calls: List[Dict[str, Any]] = []

    async def _passive_provider(
        *,
        game: str,
        texts: Any = (),
        players: Any = (),
        situation: Any = None,
        arrived_places: Any = (),
        limit: int = 2,
    ) -> List[str]:
        cue_calls.append(
            {
                "game": game,
                "texts": texts,
                "players": players,
                "situation": situation,
                "arrived_places": arrived_places,
                "limit": limit,
            }
        )
        if "北岸水车房" in arrived_places:
            return ["跨场线索：北岸水车房的主齿轮拆了会停机"]
        return []

    coord.set_passive_cue_provider(_passive_provider)

    turn = {"n": 0}

    def script(messages: List[Dict[str, Any]]) -> Response:
        turn["n"] += 1
        if turn["n"] == 1:
            # 第 1 步在远处先观测一次，之后移动到地标跟前再观测一次
            server.self_view["position"] = {"x": 92.0, "y": 64.0, "z": 0.0, "dimension": "minecraft:overworld"}
            return _resp("走近北岸水车房看看", calls=[_call("maicraft_observe", {"what": "self"}, "obs_near")])
        return _resp("已经记着主齿轮别乱拆啦")

    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        # 先经主播只读速览预热 remembered_places 缓存（距离 100 格，远离状态）
        glance_res = await registry.invoke(
            ToolInvocation(tool_name="minecraft_glance", arguments={}, source="streamer", invoking_agent="streamer")
        )
        assert glance_res.success is True

        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="巡检水车"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) >= 2 and not agent.batch_active)

        assert cue_calls
        assert cue_calls[-1]["arrived_places"] == ("北岸水车房",)
        # 第 2 步尾部出现 [脑中闪过]，且头部系统提示词与主控交接简报完全未被改写
        assert llm.seen[0][0] == llm.seen[1][0]
        assert llm.seen[0][1] == llm.seen[1][1]
        tail_content = str(llm.seen[1][-1].get("content") or "")
        assert "[脑中闪过]" in tail_content
        assert "北岸水车房的主齿轮拆了会停机" in tail_content
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_private_chat_never_sent_to_passive_cues_or_embedding_and_marks_folded_block_private(
    chars_as_tokens: None,
) -> None:
    """私聊正文绝不发往跨场被动检索与外部嵌入服务（保留 sender_id 走玩家卡），且含私聊的折叠块标 private=True。"""
    del chars_as_tokens
    clock = FakeClock()
    emb_server = FakeEmbeddingServer()
    emb_client = emb_server.client(clock=clock.now_ms)
    server = FakeMaicraft()
    server.scene_view["summary"] = "前方有大片草地与小溪" + "草" * 2000

    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    cue_calls: List[Dict[str, Any]] = []

    async def _passive_provider(
        *,
        game: str,
        texts: Any = (),
        players: Any = (),
        situation: Any = None,
        arrived_places: Any = (),
        limit: int = 2,
    ) -> List[str]:
        cue_calls.append({"game": game, "texts": texts, "players": players, "arrived_places": arrived_places})
        return []

    coord.set_passive_cue_provider(_passive_provider)

    step = {"n": 0}
    folded = {"done": False}

    def script(messages: List[Dict[str, Any]]) -> Response:
        last_text = str(messages[-1].get("content") or "")
        if last_text.startswith("[宿主整理请求]"):
            ranges = re.findall(r"^- (\S+)–(\S+)：", last_text, re.M)
            start, end = ranges[0]
            return _resp(
                calls=[
                    _call(
                        "minecraft_context_fold",
                        {"folds": [{"start": start, "end": end, "summary": "刚才收到一条私下提醒并巡视了周边"}]},
                        "f_priv_fold",
                    )
                ]
            )
        step["n"] += 1
        joined = _texts(messages)
        if "藏宝库密码在床下" not in joined:
            return _resp("先等一下同伴消息")
        if "[已折叠 b1" in joined or step["n"] > 18:
            folded["done"] = "[已折叠 b1" in joined
            return _resp("好了，暂停观察")
        return _resp(calls=[_call("maicraft_observe", {"what": "scene"}, f"obs_priv_{step['n']}")])

    cfg = offline_config(events_wait_ms=1000).model_copy(
        update={"context": MinecraftContextConfig(max_tokens=40_000, fold_keep_recent=1)}
    )
    agent, _, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        config=cfg,
        embedding_setup=EmbeddingSetup(client=emb_client, note=""),
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=0,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="私聊防泄测试"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: not agent.batch_active)

        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="密友甲",
                sender_id="uuid-secret-friend",
                sender_online=True,
                private=True,
                content="藏宝库密码在床下",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: folded["done"] and not agent.batch_active)
        await settle(bus, turns=10)

        # 1) 被动线索回调收到玩家身份 (minecraft, uuid-secret-friend) 供玩家往来卡使用，但绝不携带私聊正文
        assert cue_calls
        assert any(("minecraft", "uuid-secret-friend") in call["players"] for call in cue_calls)
        assert all("藏宝库密码" not in " ".join(str(t) for t in call["texts"]) for call in cue_calls)

        # 2) 外部嵌入服务绝未收到私聊正文或含私聊的折叠块文本
        all_embedded = " ".join(text for batch in emb_server.requests for text in batch)
        assert "藏宝库密码" not in all_embedded
        assert "刚才收到一条私下提醒" not in all_embedded

        # 3) query_short_term_memory 返回的折叠块与公屏命中均带 private=True
        fold_hits = await agent.query_short_term_memory("私下提醒 藏宝库密码", scope="this_stream", limit=6)
        assert fold_hits
        assert any(item["kind"] == "fold_block" and item["private"] is True for item in fold_hits)
        assert all(
            item["private"] is True for item in fold_hits if "藏宝库密码" in item["text"] or "私下提醒" in item["text"]
        )
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_passive_flash_returns_keyword_hit_under_slow_embedding() -> None:
    """慢嵌入（远超 passive_cue_timeout_ms）下，[脑中闪过] 走 wait_vector_ms=0 仍即时返回本场规矩关键词命中。"""
    clock = FakeClock()
    slow_gate = asyncio.Event()

    class _SlowEmbeddingClient:
        backing_off = False

        async def embed(self, texts: List[str]) -> List[List[float]]:
            # 模拟慢外部嵌入 API：挂起等待门闸，测试绝不放行也不会阻断关键词命中
            await slow_gate.wait()
            return [[1.0, 0.0] for _ in texts]

    server = FakeMaicraft()
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    added_rule = {"done": False}

    def script(messages: List[Dict[str, Any]]) -> Response:
        if not added_rule["done"]:
            added_rule["done"] = True
            return _resp(
                "记下本场红石机关规矩",
                calls=[
                    _call(
                        "minecraft_rules",
                        {"action": "add", "rule": "仓库地下的红石机关拉杆绝对不能乱拉", "durable": False},
                        "c_rule_slow",
                    )
                ],
            )
        return _resp("明白，没动拉杆")

    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        embedding_setup=EmbeddingSetup(client=_SlowEmbeddingClient(), note=""),
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=0,
        passive_cue_timeout_ms=80,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="慢嵌入耐受测试"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: added_rule["done"] and not agent.batch_active)

        turns_before = len(llm.seen)
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="同伴乙",
                sender_distance=5.0,
                content="我去看看红石机关拉杆怎么回事",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) > turns_before and not agent.batch_active)

        tail_text = str(llm.seen[-1][-1].get("content") or "")
        assert "[脑中闪过]" in tail_text
        assert "[刚刚] [本场规矩] 仓库地下的红石机关拉杆绝对不能乱拉" in tail_text
    finally:
        slow_gate.set()
        await agent.stop()
        await bus.cleanup()


async def test_self_chat_synced_to_gate_and_short_term_memory_without_self_wakeup_and_stale_flash_blocked() -> None:
    """己方 maicraft:chat 确认后同步记入 chat_gate 与短期记忆且不自唤醒；97 分钟前旧话绝不闪回。"""
    clock = FakeClock(2_000_000)
    server = FakeMaicraft()
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    replied = {"done": False}

    def script(messages: List[Dict[str, Any]]) -> Response:
        joined = _texts(messages)
        if "给你羊肉" in joined and not replied["done"]:
            replied["done"] = True
            return _resp(
                "回同伴收下羊肉",
                calls=[
                    _call(
                        "maicraft_execute",
                        {"goal": {"ability": "chat", "parameters": {"message": "羊肉收下啦"}}},
                        "c_self_chat",
                    )
                ],
            )
        return _resp("继续观察")

    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=0,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="公屏记忆测试"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) == 1 and not agent.batch_active)

        # 1) 97 分钟前玩家发过一条短闲聊“你要猪排吗”
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="PlayerA",
                sender_distance=5.0,
                content="你要猪排吗",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) == 2 and not agent.batch_active)

        # 2) 推进 97 分钟后，玩家说“给你羊肉”，模型用 chat 回复“羊肉收下啦”
        clock.tick(97 * 60_000)
        turns_before_mutton = len(llm.seen)
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="PlayerA",
                sender_distance=5.0,
                content="给你羊肉",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: replied["done"] and not agent.batch_active)
        turns_after_reply = len(llm.seen)

        # 排空总线后验证：己方发出的 GAME_SELF_CHATTED 被 _own_emitted_chats 过滤，绝不再次自唤醒
        await settle(bus)
        assert len(llm.seen) == turns_after_reply
        # 97 分钟前的“猪排”未在“给你羊肉”这轮尾部 [脑中闪过] 里闪回，也不再挂在任务状态 recent_human_lines 里
        mutton_tail = str(llm.seen[turns_before_mutton][-1].get("content") or "")
        assert "你要猪排吗" not in mutton_tail
        state_msgs = [
            str(m.get("content") or "")
            for m in llm.seen[turns_before_mutton]
            if str(m.get("content") or "").startswith("[继续原游戏任务]")
        ]
        assert all("你要猪排吗" not in msg for msg in state_msgs)

        # 3) 短期记忆已收录己方回复“你回过：羊肉收下啦”
        stm_hits = await agent.query_short_term_memory("羊肉收下啦")
        assert any("你回过：羊肉收下啦" in item["text"] for item in stm_hits)

        # 4) 推进 2 分钟后再发一条关于猪排的公屏：97+2 分钟前的旧猪排依然绝不闪回；
        #    而如果切出续做，近期双向对话包含 [你回过·2分钟前] 羊肉收下啦
        clock.tick(2 * 60_000)
        turns_before_pork = len(llm.seen)
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="PlayerA",
                sender_distance=5.0,
                content="猪排还有吗",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) > turns_before_pork and not agent.batch_active)
        pork_tail = str(llm.seen[-1][-1].get("content") or "")
        assert "你要猪排吗" not in pork_tail
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_clock_domain_isolation_unix_payloads_decay_and_skill_wall_clock(db_path: Path) -> None:
    """单调时钟域与 wall-clock 严格分离：Unix epoch 事件不破坏短期衰减与近期公屏窗，技能入库写 epoch 毫秒。"""
    mono_clock = FakeClock(1_000_000)
    wall_clock = FakeClock(1_795_000_000_000)
    db = SQLiteDatabase(db_path)
    await db.initialize()
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=mono_clock.now_ms)
    server = FakeMaicraft()
    server.enter_world("save-clock", "时钟域测试档")

    try:
        agent, llm, _ = await make_minecraft_agent(
            server,
            lambda _msgs: _resp("收到"),
            registry=registry,
            bus=bus,
            live=True,
            work_docs_repo=db.game_work_docs,
            lead_coordinator=coord,
            lead_idle_wakeup_s=0.0,
            chat_batch_window_ms=0,
            clock=lambda: mono_clock.now_ms() / 1000.0,
            wall_clock_ms=wall_clock.now_ms,
        )
        manager.register(agent)
        try:
            assert (
                await coord.request_game_lead(LeadHandoverContext(segment_title="时钟测试"), target="minecraft")
                == "minecraft"
            )
            await _wait_until(lambda: len(llm.seen) == 1 and not agent.batch_active)

            # 1) T=0 收到带真实 Unix epoch timestamp_ms 的玩家旧闲聊 + 外部替说公屏事件
            await bus.emit(
                CoreEvents.GAME_CHAT_RECEIVED,
                GameChatPayload(
                    game="minecraft",
                    kind="player",
                    sender="PlayerA",
                    sender_distance=5.0,
                    content="你要猪排吗",
                    timestamp_ms=wall_clock.now_ms(),
                ),
                source="test",
            )
            await bus.emit(
                CoreEvents.GAME_SELF_CHATTED,
                GameSelfChattedPayload(
                    game="minecraft",
                    message="先去整理仓库啦",
                    timestamp_ms=wall_clock.now_ms(),
                ),
                source="external_streamer",
            )
            await bus.wait_until_drained()
            await _wait_until(lambda: len(llm.seen) >= 2 and not agent.batch_active)

            # 2) 外部 GAME_SELF_CHATTED 不得用 Unix epoch 毫秒把单调时钟下的近期公屏窗整窗洗空
            mono_clock.tick(2 * 60_000)
            wall_clock.tick(2 * 60_000)
            stm_recent = await agent.query_short_term_memory("猪排")
            assert any("你要猪排吗" in item["text"] for item in stm_recent)
            # 切出后通过只读公开查询确认短期记忆随单调时钟正常推进，不是恒为 [刚刚]
            mono_clock.tick(10 * 60_000)
            wall_clock.tick(10 * 60_000)
            await bus.emit(
                CoreEvents.GAME_CHAT_RECEIVED,
                GameChatPayload(
                    game="minecraft",
                    kind="player",
                    sender="PlayerA",
                    sender_distance=5.0,
                    content="仓库整理好了吗",
                    timestamp_ms=wall_clock.now_ms(),
                ),
                source="test",
            )
            await bus.wait_until_drained()
            await _wait_until(lambda: len(llm.seen) >= 3 and not agent.batch_active)
            flash_12m_tail = str(llm.seen[-1][-1].get("content") or "")
            assert "[12分钟前]" in flash_12m_tail
            assert "先去整理仓库啦" in flash_12m_tail

            # 3) 推进至 97 分钟后，旧闲聊“你要猪排吗”被单调时钟衰减彻底淘汰，不再闪回
            mono_clock.tick(85 * 60_000)
            wall_clock.tick(85 * 60_000)
            stm_after_97m = await agent.query_short_term_memory("PlayerA 猪排")
            assert all("你要猪排吗" not in item["text"] for item in stm_after_97m)

            # 4) 自建技能入库写的是 wall_clock_ms（Unix epoch 毫秒），而非单调时钟毫秒
            skill_res = await registry.invoke(
                ToolInvocation(
                    tool_name="minecraft_skill",
                    arguments={
                        "action": "upsert",
                        "name": "clock_verified_skill",
                        "scope": "world",
                        "trigger": "校验落库时间戳",
                        "content": "测试步骤",
                    },
                    source="minecraft-react",
                    invoking_agent="minecraft",
                )
            )
            assert skill_res.success is True
            persisted = await db.learned_skills.get_skill(
                game="minecraft",
                scope="world",
                save_id="save-clock",
                name="clock_verified_skill",
            )
            assert persisted is not None
            assert persisted.updated_at_ms == wall_clock.now_ms()
            assert persisted.updated_at_ms >= 1_795_000_000_000
        finally:
            await agent.stop()
    finally:
        await bus.cleanup()
        await db.close()


async def test_streamer_chat_send_does_not_double_record_when_player_chat_interleaves() -> None:
    """活跃委派任务期间主播直发 minecraft_chat_send 且中途穿插玩家公屏时，GAME_SELF_CHATTED 不得二次写入重复 [你回过]。"""
    clock = FakeClock(5_000_000)
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)
    server = FakeMaicraft()

    interleaved = {"fired": False}

    async def _interleave_player_chat(_event: str, _payload: GameSelfChattedPayload, _source: str) -> None:
        if interleaved["fired"]:
            return
        interleaved["fired"] = True
        # 在 _on_self_chatted 之前先写入一条玩家公屏，打破单纯“相邻去重”的假设
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="PlayerB",
                sender_distance=4.0,
                content="晚上好",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )

    # 先于 agent.start() 订阅，确保穿插玩家公屏发生在 _send_self_chat 之后、_on_self_chatted 之前
    bus.on(CoreEvents.GAME_SELF_CHATTED, _interleave_player_chat, GameSelfChattedPayload)

    started_goal = {"sent": False}

    def script(_msgs: List[Dict[str, Any]]) -> Response:
        if not started_goal["sent"]:
            started_goal["sent"] = True
            return _resp(
                "先去砍木头",
                calls=[_call("maicraft_execute", {"goal": {"ability": "gather", "purpose": "砍木头"}}, "c_g1")],
            )
        return _resp("继续等后台砍木头")

    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=0,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        # 委派一个后台长任务（非主控态且任务活跃：_task_finished=False），主播可通过 minecraft_chat_send 代发公屏
        agent.receive_delegation(instruction="去营地西边砍一点木头", task_id="d-chat-send")
        await _wait_until(lambda: len(agent.tracked_goal_ids) == 1 and not agent.batch_active)

        send_res = await registry.invoke(
            ToolInvocation(
                tool_name="minecraft_chat_send",
                arguments={"text": "大家晚上好呀"},
                source="streamer",
                invoking_agent="streamer",
            )
        )
        assert send_res.success is True
        await bus.wait_until_drained()
        await _wait_until(lambda: not agent.batch_active)
        assert interleaved["fired"] is True

        # 短期记忆中只存在 1 条己方回复记录（未被 _on_self_chatted 二次写入）
        hits = await agent.query_short_term_memory("大家晚上好呀", limit=5)
        self_hits = [item for item in hits if "你回过：大家晚上好呀" in item["text"]]
        assert len(self_hits) == 1

        # 随后切入游戏主控态，核对 [继续原游戏任务] / 近期双向对话窗口中仅出现 1 次 [你回过]
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="复核双向公屏"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: not agent.batch_active and any("复核双向公屏" in _texts(seen) for seen in llm.seen))
        lead_msgs = [str(m.get("content") or "") for m in llm.seen[-1] if "[你回过" in str(m.get("content") or "")]
        assert sum(msg.count("大家晚上好呀") for msg in lead_msgs) <= 1
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_commitment_ledger_injected_at_tail_only_omitted_when_empty_and_keeps_msg0_prefix() -> None:
    """权威承诺账本仅挂在尾部任务状态，空时省略键，绝不修改 msg[0] 前缀，且透传视野与近期公屏在线玩家 UUID。"""
    clock = FakeClock(6_000_000)
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    server = FakeMaicraft()
    server.enter_world("save_ledger_1", "联机承诺测试档")
    server.scene_view["entities"] = [
        {
            "id": "e1",
            "type": "minecraft:player",
            "name": "PlayerA",
            "uuid": "uuid-player-a",
            "distance": 4.0,
        }
    ]

    ledger_calls: List[Dict[str, Any]] = []
    ledger_state: Dict[str, Any] = {"value": {}}

    async def _ledger_provider(
        *,
        game: str,
        save_id: str = "",
        player_uuids: Any = (),
        limit: int = 8,
    ) -> Dict[str, Any]:
        ledger_calls.append(
            {
                "game": game,
                "save_id": save_id,
                "player_uuids": tuple(player_uuids),
                "limit": limit,
            }
        )
        return dict(ledger_state["value"])

    coord.set_commitment_ledger_provider(_ledger_provider)

    agent, llm, _ = await make_minecraft_agent(
        server,
        lambda _messages: _resp("明白，记着当前任务与承诺"),
        registry=registry,
        bus=bus,
        live=True,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=0,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)
    try:
        # 1) 账本为空时开局：尾部完全不出现 commitment_ledger，零多余 token
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="账本测试"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) >= 1 and not agent.batch_active)

        first_seen = llm.seen[0]
        msg0_before = dict(first_seen[0])
        assert "commitment_ledger" not in _texts(first_seen)
        assert "约#43" not in str(msg0_before.get("content") or "")
        assert ledger_calls
        assert ledger_calls[-1]["game"] == "minecraft"
        assert ledger_calls[-1]["save_id"] == "save_ledger_1"
        assert "uuid-player-a" in ledger_calls[-1]["player_uuids"]

        # 2) 账本变为非空且 PlayerB 在公屏发言唤醒：尾部任务状态出现 commitment_ledger，同时携带视野 PlayerA 与公屏 PlayerB UUID，msg[0] 纹丝不动
        ledger_state["value"] = {
            "open": [{"id": "约#43", "target": "PlayerA", "what": "补还 3 颗钻石"}],
            "closed_this_session": [],
        }
        turns_before = len(llm.seen)
        await bus.emit(
            CoreEvents.GAME_CHAT_RECEIVED,
            GameChatPayload(
                game="minecraft",
                kind="player",
                sender="PlayerB",
                sender_id="uuid-player-b",
                sender_online=True,
                sender_distance=5.0,
                content="钻石挖够了吗",
                timestamp_ms=clock.now_ms(),
            ),
            source="test",
        )
        await bus.wait_until_drained()
        await _wait_until(lambda: len(llm.seen) > turns_before and not agent.batch_active)

        second_seen = llm.seen[-1]
        assert second_seen[0] == msg0_before, "权威承诺账本绝不能改写 msg[0] 系统提示词前缀"
        assert "约#43" not in str(second_seen[0].get("content") or "")
        joined = _texts(second_seen)
        assert "commitment_ledger" in joined
        assert "约#43" in joined
        assert "补还 3 颗钻石" in joined
        assert "closed_this_session" not in joined
        assert "uuid-player-a" in ledger_calls[-1]["player_uuids"]
        assert "uuid-player-b" in ledger_calls[-1]["player_uuids"]
    finally:
        await agent.stop()
        await bus.cleanup()


async def test_game_lead_sees_and_invokes_streamer_memory_correct_and_receives_items_tossed_receipt() -> None:
    """游戏主控态可见并可调用 streamer_memory_correct，收到 items_tossed 时上下文给简明 [交付回执] 并刷新尾部账本。"""
    from src.modules.tools.models import ToolExecutionResult, ToolSpec
    from src.modules.tools.provider import BaseToolProvider

    clock = FakeClock(7_000_000)
    bus = EventBus(enable_stats=False)
    registry = ToolRegistry(event_bus=bus)
    manager = AgentManager(tool_registry=registry)
    manager.register(StreamerPlaceholder())
    coord = LeadCoordinator(manager, event_bus=bus, clock=clock.now_ms)

    invocations: List[Dict[str, Any]] = []
    ledger_state: Dict[str, Any] = {
        "open": [{"id": "约#12", "target": "PlayerA", "what": "还 2 颗钻石"}],
        "closed_this_session": [],
    }

    class _FakeMemoryCorrectProvider(BaseToolProvider):
        @property
        def name(self) -> str:
            return "streamer"

        def list_tools(self) -> List[ToolSpec]:
            return [
                ToolSpec(
                    name="memory_correct",
                    provider="streamer",
                    description="更新本场记忆或承诺账本",
                    parameters_schema={"type": "object", "properties": {}},
                    visible_to=["streamer", "minecraft"],
                )
            ]

        async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
            invocations.append(dict(invocation.arguments))
            ledger_state["open"] = []
            ledger_state["closed_this_session"] = [
                {"id": "约#12", "what": "还 2 颗钻石", "summary": "已当面扔还 2 颗钻石"}
            ]
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=True,
                structured_content={"ok": True, "effect": "约#12 已了结"},
            )

    registry.register_provider(_FakeMemoryCorrectProvider())

    async def _ledger_provider(
        *,
        game: str,
        save_id: str = "",
        player_uuids: Any = (),
        limit: int = 8,
    ) -> Dict[str, Any]:
        del game, save_id, player_uuids, limit
        return dict(ledger_state)

    coord.set_commitment_ledger_provider(_ledger_provider)

    server = FakeMaicraft()
    server.enter_world("save_toss_1", "交付回执测试档")

    corrected = {"done": False}
    seen_tools: List[List[str]] = []

    def script(messages: List[Dict[str, Any]]) -> Response:
        joined = _texts(messages)
        if "[交付回执]" in joined and not corrected["done"]:
            corrected["done"] = True
            match = re.search(r"\[交付回执\]\s*([^。]+)。", joined)
            assert match is not None
            receipt_quote = match.group(1)
            return _resp(
                "已当面扔出钻石，调用 streamer_memory_correct 结案",
                calls=[
                    _call(
                        "streamer_memory_correct",
                        {
                            "target": "约#12",
                            "verdict": "done",
                            "summary": "已当面扔还 2 颗钻石",
                            "evidence_kind": "game_receipt",
                            "evidence_quote": receipt_quote,
                        },
                        "c_mc_correct_1",
                    )
                ],
            )
        return _resp("收到，继续推进")

    agent, llm, _ = await make_minecraft_agent(
        server,
        script,
        registry=registry,
        bus=bus,
        live=True,
        lead_coordinator=coord,
        lead_idle_wakeup_s=0.0,
        chat_batch_window_ms=0,
        clock=lambda: clock.now_ms() / 1000.0,
    )
    manager.register(agent)

    # 包装 generate 以核验传给游戏侧模型的工具定义列表包含 streamer_memory_correct
    orig_generate = llm.generate

    async def _spy_generate(messages: Any, **kwargs: Any) -> Response:
        tools = kwargs.get("tools") or []
        seen_tools.append([str(t.get("name") or "") for t in tools if isinstance(t, dict)])
        return await orig_generate(messages, **kwargs)

    llm.generate = _spy_generate  # type: ignore[method-assign]
    try:
        assert (
            await coord.request_game_lead(LeadHandoverContext(segment_title="交付验证"), target="minecraft")
            == "minecraft"
        )
        await _wait_until(lambda: len(llm.seen) >= 1 and not agent.batch_active)
        assert any("streamer_memory_correct" in names for names in seen_tools)

        turns_before = len(llm.seen)
        # 模拟 Mod 发来带观察编号与括号坐标的真实 items_tossed 事实事件 -> GoalWatch -> MinecraftAgent._on_body_event
        server.body_event(
            "items_tossed",
            "朝 PlayerA（e4）扔出了 2 个 minecraft:diamond（12, 64, -8）",
            {
                "items_tossed": {
                    "to": "PlayerA",
                    "to_uuid": "uuid-player-a",
                    "at": {"x": 12, "y": 64, "z": -8, "dimension": "minecraft:overworld"},
                    "items": [{"id": "minecraft:diamond", "count": 2}],
                }
            },
        )
        await _wait_until(lambda: corrected["done"] and len(llm.seen) >= turns_before + 2 and not agent.batch_active)

        assert len(invocations) == 1
        assert invocations[0]["verdict"] == "done"
        # 游戏侧 [交付回执] 已按同源口径剥除观察编号与括号坐标，模型直接照抄即与落库消息一致
        assert invocations[0]["evidence_quote"] == "朝 PlayerA 扔出了 2 个 minecraft:diamond"
        # 调完 streamer_memory_correct 后，紧邻下一步尾部 [当前任务状态] 立即反映本场已了结
        latest = _texts(llm.seen[-1])
        assert "[交付回执] 朝 PlayerA 扔出了 2 个 minecraft:diamond。" in latest
        assert "（e4）" not in latest and "（12, 64, -8）" not in latest
        assert "closed_this_session" in latest
        assert "已当面扔还 2 颗钻石" in latest

        # 顺带验证 others_things_touched：stored 剥除括号坐标与落库同源，非 stored（如 took）保留原话不动
        turns_before_stored = len(llm.seen)
        server.body_event(
            "others_things_touched",
            "往 PlayerA 放的木桶（10, 64, 5）存进了 1 个 minecraft:diamond",
            {
                "others_things": {
                    "action": "stored",
                    "owner": "PlayerA",
                    "owner_uuid": "uuid-player-a",
                    "block": "minecraft:barrel",
                    "at": {"x": 10, "y": 64, "z": 5},
                    "items": [{"item": "minecraft:diamond", "count": 1}],
                }
            },
        )
        await _wait_until(lambda: len(llm.seen) > turns_before_stored and not agent.batch_active)
        stored_notice = str(llm.seen[-1][-1].get("content") or "")
        assert "[身体] 往 PlayerA 放的木桶存进了 1 个 minecraft:diamond。" in stored_notice
        assert "（10, 64, 5）" not in stored_notice

        turns_before_took = len(llm.seen)
        server.body_event(
            "others_things_touched",
            "从 PlayerA 放的箱子（20, 64, 8）拿了 1 个 minecraft:oak_planks",
            {
                "others_things": {
                    "action": "took",
                    "owner": "PlayerA",
                    "owner_uuid": "uuid-player-a",
                    "block": "minecraft:chest",
                    "at": {"x": 20, "y": 64, "z": 8},
                    "items": [{"item": "minecraft:oak_planks", "count": 1}],
                }
            },
        )
        await _wait_until(lambda: len(llm.seen) > turns_before_took and not agent.batch_active)
        took_notice = str(llm.seen[-1][-1].get("content") or "")
        assert "[身体] 从 PlayerA 放的箱子（20, 64, 8）拿了 1 个 minecraft:oak_planks。" in took_notice
    finally:
        await agent.stop()
        await bus.cleanup()
