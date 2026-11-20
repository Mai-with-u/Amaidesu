"""MinecraftAgent 折叠原话保留、短期语义回捞与被动线索卡接线测试（假时钟、零真实 sleep）。"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List

from src.agents.minecraft.agent import _extract_preserved_human_lines
from src.agents.minecraft.config import MinecraftContextConfig
from src.modules.agents import AgentManager, LeadCoordinator, LeadHandoverContext
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game_chat import GameChatPayload
from src.modules.llm.embedding import EmbeddingSetup
from src.modules.llm.payload import Response
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

        # 2) 等待嵌入补完后直接经工具面调用 minecraft_context_recall：字面“橘猫”0 命中 -> 自动返回 semantic_blocks 含 b1
        # （先确保后台嵌入完成，然后验证回捞结果）
        await settle(bus, turns=10)
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
        assert "仓库地下的红石机关拉杆绝对不能乱拉" in tail_text
    finally:
        slow_gate.set()
        await agent.stop()
        await bus.cleanup()
