"""Minecraft 本场短期动态记忆单测：三类收录、有界淘汰、语义/关键词回捞、去重与异常降级。"""

from __future__ import annotations

import asyncio
from typing import Any, List, Sequence

from src.agents.minecraft.short_term_memory import (
    KIND_FOLD_BLOCK,
    KIND_PLAYER_CHAT,
    KIND_WORK_DOC,
    SessionShortTermMemory,
)
from tests.helpers import FakeClock
from tests.modules.llm.fakes import FakeEmbeddingServer


async def test_records_three_high_signal_kinds_with_bounds_and_rejects_raw_json_noise() -> None:
    """三类高信号短文本正确收录且受有界上限约束，空白与原始 JSON 噪声一律拒收。"""
    clock = FakeClock(1_000)
    memory = SessionShortTermMemory(clock=clock.now_ms, max_player_chats=3)

    assert memory.record_player_chat("同伴甲", "   ") is None
    assert memory.record_player_chat("同伴甲", '{"blocks": [{"x": 1, "y": 64, "z": 2}]}') is None

    for idx in range(4):
        clock.tick(1_000)
        assert memory.record_player_chat("同伴甲", f"第 {idx} 句坐标提醒", private=(idx == 3)) is not None

    # 上限 3 条：第 0 句被淘汰，保留第 1/2/3 句
    assert memory.entry_counts()[KIND_PLAYER_CHAT] == 3
    hits = await memory.search_entries("坐标提醒", kinds={KIND_PLAYER_CHAT}, limit=6)
    texts = [hit.text for hit in hits]
    assert len(hits) == 3
    assert all("第 0 句" not in text for text in texts)
    assert any(hit.private and "[私聊]" in hit.text for hit in hits)

    assert memory.record_fold_block("b1", "m0001–m0004", "清理了北边矿道", ["同伴甲：熔炉别拆"]) == "fold:b1"
    changed = memory.sync_work_docs(
        "## 基地\n- 主箱区在东边高台",
        ["不要拆公共高炉"],
        ["本场先把铁轨铺完"],
    )
    assert changed == 3
    assert memory.entry_counts() == {KIND_PLAYER_CHAT: 3, KIND_FOLD_BLOCK: 1, KIND_WORK_DOC: 3}

    # 内容完全未变时再次 sync_work_docs 返回 0，不做无谓重建
    assert (
        memory.sync_work_docs(
            "## 基地\n- 主箱区在东边高台",
            ["不要拆公共高炉"],
            ["本场先把铁轨铺完"],
        )
        == 0
    )


async def test_keyword_search_filters_by_kind_and_excludes_recent_verbatim_texts() -> None:
    """未配嵌入时走中文两字切分关键词召回，支持按类型过滤并排除已在近端展示的原话。"""
    clock = FakeClock(10_000)
    memory = SessionShortTermMemory(clock=clock.now_ms)
    memory.record_player_chat("同伴甲", "北侧红石仓库的开关别乱碰")
    memory.record_player_chat("同伴乙", "红石仓库地下还有一条密道")
    memory.record_fold_block("b2", "m0005–m0009", "检查过红石仓库电路", ["同伴甲：北侧开关通向刷铁机"])
    memory.sync_work_docs("## 仓库\n- 红石仓库门口放了两桶水", ["进红石仓库前关好木门"], [])

    # 排除近端已经展示过的同伴甲原话后，不再重复返回该条
    filtered = await memory.search_entries(
        "红石仓库",
        exclude_texts={"北侧红石仓库的开关别乱碰"},
        limit=6,
    )
    assert all("开关别乱碰" not in hit.text for hit in filtered)
    assert any(hit.kind == KIND_PLAYER_CHAT and "密道" in hit.text for hit in filtered)
    assert any(hit.kind == KIND_FOLD_BLOCK and hit.block_id == "b2" for hit in filtered)
    assert any(hit.kind == KIND_WORK_DOC for hit in filtered)

    only_work = await memory.search_entries("红石仓库", kinds={KIND_WORK_DOC}, limit=6)
    assert len(only_work) == 2 and all(hit.kind == KIND_WORK_DOC for hit in only_work)


async def test_embedding_enables_synonym_fold_block_recall_and_respects_min_cosine_and_dedup() -> None:
    """注入嵌入客户端后：后台算向量支持同义折叠块回捞、余弦底线防低分噪声、重复 sync 不重复请求嵌入。"""
    clock = FakeClock(20_000)
    server = FakeEmbeddingServer()
    memory = SessionShortTermMemory(
        embedding_client=server.client(clock=clock.now_ms), clock=clock.now_ms, min_cosine=0.5
    )

    # FakeEmbeddingServer 按"猫"与"水"落在正交轴上，其余落第三轴
    memory.record_fold_block("b1", "m0001–m0003", "找回了走丢的黑猫", ["同伴甲：芝麻一直在炉子旁"])
    memory.record_fold_block("b2", "m0004–m0006", "北边挖了一条水渠")
    memory.sync_work_docs("## 农场\n- 灌溉水渠连着麦田", ["别破坏水渠堤坝"], [])
    await memory.flush_embeddings()

    embed_calls_after_first = len(server.requests)
    assert embed_calls_after_first >= 3

    # 内容未变时再次 sync 不触发额外嵌入请求；仅新增一条规矩时只算新增那一条
    assert memory.sync_work_docs("## 农场\n- 灌溉水渠连着麦田", ["别破坏水渠堤坝"], []) == 0
    await memory.flush_embeddings()
    assert len(server.requests) == embed_calls_after_first

    assert memory.sync_work_docs("## 农场\n- 灌溉水渠连着麦田", ["别破坏水渠堤坝"], ["小猫窝放室内"]) == 1
    await memory.flush_embeddings()
    assert len(server.requests) == embed_calls_after_first + 1

    # 字面无共同词，但同含"猫"语义轴：search_fold_blocks 语义回捞命中 b1，且不把正交的 b2 捞出来
    recalled_blocks = await memory.search_fold_blocks("橘猫", limit=3)
    assert [item["block"] for item in recalled_blocks] == ["b1"]
    assert recalled_blocks[0]["range"] == "m0001–m0003"
    assert recalled_blocks[0]["preserved_lines"] == ["同伴甲：芝麻一直在炉子旁"]
    assert recalled_blocks[0]["vector_score"] is not None and recalled_blocks[0]["vector_score"] >= 0.99


async def test_embedding_failure_degrades_to_keywords_and_clear_session_wipes_all(loguru_capture: Any) -> None:
    """嵌入服务失败记 warning 且无缝退化关键词；clear_session 彻底清空本场记忆不跨场残留。"""
    clock = FakeClock(30_000)
    server = FakeEmbeddingServer()
    server.fail = True
    memory = SessionShortTermMemory(embedding_client=server.client(clock=clock.now_ms), clock=clock.now_ms)

    memory.record_player_chat("同伴丙", "铁砧放在地下二层熔炉区")
    memory.record_fold_block("b3", "m0010–m0012", "地下二层铁砧已补放")
    await memory.flush_embeddings()

    hits = await memory.search_entries("铁砧", limit=5)
    assert len(hits) == 2
    assert all(hit.vector_score is None for hit in hits)
    assert any(rec["level"] == "WARNING" and "降级关键词" in rec["message"] for rec in loguru_capture.records)

    memory.clear_session()
    assert memory.entry_counts() == {KIND_PLAYER_CHAT: 0, KIND_FOLD_BLOCK: 0, KIND_WORK_DOC: 0}
    assert await memory.search_entries("铁砧", limit=5) == []
    assert await memory.search_fold_blocks("铁砧", limit=3) == []


async def test_private_chat_and_private_fold_block_never_sent_to_external_embedding() -> None:
    """私聊与含私聊折叠块透传 private=True，且绝不发往外部嵌入服务，仅本地关键词可检索。"""
    clock = FakeClock(40_000)
    server = FakeEmbeddingServer()
    memory = SessionShortTermMemory(embedding_client=server.client(clock=clock.now_ms), clock=clock.now_ms)

    memory.record_player_chat("同伴甲", "箱子暗格密码是九五二七", private=True)
    memory.record_player_chat("同伴乙", "公开提醒北边有煤矿", private=False)
    memory.record_fold_block("b10", "m0001–m0004", "记录了一段私密对话", ["同伴甲：暗号别说出去"], private=True)
    # 即使调用方漏传 private=True，保留原话带 [公屏·私聊] 也自动兜底标为私聊
    memory.record_fold_block("b11", "m0005–m0008", "整理了仓库安排", ["[公屏·私聊] 同伴甲：暗门钥匙在床下"])
    await memory.flush_embeddings()

    sent_texts = [text for batch in server.requests for text in batch]
    assert any("公开提醒北边有煤矿" in text for text in sent_texts)
    assert all("九五二七" not in text for text in sent_texts), "私聊公屏绝不可外发外部嵌入 API"
    assert all("暗号别说出去" not in text for text in sent_texts), "显式私聊折叠块绝不可外发外部嵌入 API"
    assert all("暗门钥匙在床下" not in text for text in sent_texts), "含私聊标记的折叠块绝不可外发外部嵌入 API"

    chat_hits = await memory.search_entries("九五二七", kinds={KIND_PLAYER_CHAT}, wait_vector_ms=0)
    assert len(chat_hits) == 1 and chat_hits[0].private is True and chat_hits[0].vector_score is None

    fold_hits = await memory.search_entries("暗号", kinds={KIND_FOLD_BLOCK}, wait_vector_ms=0)
    assert len(fold_hits) == 1 and fold_hits[0].block_id == "b10" and fold_hits[0].private is True

    marker_hits = await memory.search_entries("暗门钥匙", kinds={KIND_FOLD_BLOCK}, wait_vector_ms=0)
    assert len(marker_hits) == 1 and marker_hits[0].block_id == "b11" and marker_hits[0].private is True


async def test_wait_vector_zero_returns_keyword_hits_immediately_under_slow_embedding_and_caches_for_next() -> None:
    """慢嵌入阻塞时 wait_vector_ms=0 立即返回关键词命中且后台预取查询向量，算好后下次直接融合余弦。"""
    gate = asyncio.Event()
    started = asyncio.Event()
    embed_calls: List[List[str]] = []

    class _GatedEmbeddingClient:
        backing_off = False

        async def embed(self, texts: Sequence[str]) -> List[List[float]]:
            embed_calls.append(list(texts))
            if any("小猫" in t for t in texts):
                started.set()
                await gate.wait()
            return [[1.0, 0.0] if ("猫" in t or "芝麻" in t) else [0.0, 1.0] for t in texts]

    clock = FakeClock(50_000)
    memory = SessionShortTermMemory(embedding_client=_GatedEmbeddingClient(), clock=clock.now_ms, min_cosine=0.5)  # type: ignore[arg-type]
    # 先把两条公开条目算好向量：一条字面含"小猫"（工作规矩），一条仅语义同轴"芝麻"（折叠块）
    memory.sync_work_docs("", ["小猫不能带进岩浆矿洞"], [])
    memory.record_fold_block("b20", "m0010–m0012", "黑芝麻一直趴在熔炉边取暖")
    # 放开门闸先让条目向量算完，再合上门闸模拟查询时嵌入服务变慢
    gate.set()
    await memory.flush_embeddings()
    gate.clear()
    started.clear()

    # 慢嵌入门闸紧闭，但 wait_vector_ms=0 不阻塞等待，直接返回关键词命中的规矩并后台预取查询向量
    fast_hits = await asyncio.wait_for(memory.search_entries("小猫", wait_vector_ms=0), timeout=0.5)
    assert len(fast_hits) == 1
    assert "小猫不能带进岩浆矿洞" in fast_hits[0].text
    assert fast_hits[0].vector_score is None
    await asyncio.wait_for(started.wait(), timeout=0.5)

    # 放开慢嵌入并等后台预取入缓存：下一次同样传 wait_vector_ms=0 即时命中查询向量缓存，同义捞出"黑芝麻"
    gate.set()
    await memory.flush_embeddings()
    cached_hits = await memory.search_entries("小猫", wait_vector_ms=0)
    assert any(
        hit.block_id == "b20" and hit.vector_score is not None and hit.vector_score >= 0.99 for hit in cached_hits
    )
    assert sum(1 for batch in embed_calls if batch == ["小猫"]) == 1, "查询向量只算一次并复用缓存"


async def test_self_chat_indexed_relative_age_prefix_and_time_decay_blocks_stale_flashback() -> None:
    """己方公屏正常收录检索、闪回带相对时间前缀、20 分钟半衰期与 45 分钟降权彻底阻止 97 分钟陈旧闪回。"""
    clock = FakeClock(1_000_000)
    server = FakeEmbeddingServer()
    memory = SessionShortTermMemory(embedding_client=server.client(clock=clock.now_ms), clock=clock.now_ms)

    # T=0：97 分钟前的旧闲聊（模拟同伴 97 分钟前问过猪排）与一条长期规矩
    memory.record_player_chat("PlayerA", "你要猪排吗", timestamp_ms=clock.now_ms())
    memory.sync_work_docs("", ["营地公共高炉不要乱拆"], [], timestamp_ms=clock.now_ms())

    # T=+85 分钟（距查询 12 分钟前）：玩家送羊肉 + 己方回话
    clock.tick(85 * 60_000)
    memory.record_player_chat("PlayerA", "给你羊肉放脚边了", timestamp_ms=clock.now_ms())
    self_id = memory.record_self_chat("羊肉收下啦，谢谢", timestamp_ms=clock.now_ms())
    assert self_id is not None

    # T=+97 分钟：执行查询
    clock.tick(12 * 60_000)
    await memory.flush_embeddings()

    # 1) 己方公屏可正常命中、标记 is_self=True，且闪回渲染强制带 [12分钟前] 前缀
    mutton_hits = await memory.search_entries("羊肉", limit=5)
    assert any(hit.is_self and "你回过：羊肉收下啦" in hit.text for hit in mutton_hits)
    rendered_self = memory.format_hit_for_flash(next(h for h in mutton_hits if h.is_self))
    assert rendered_self.startswith("[12分钟前] 你回过：羊肉收下啦")

    # 2) 97 分钟前的短闲聊即使查询词完全命中发信人+内容，经半衰期与超 45 分钟降权后彻底过滤不闪回
    stale_hits = await memory.search_entries("PlayerA 猪排", kinds={KIND_PLAYER_CHAT}, limit=5)
    assert all("你要猪排吗" not in hit.text for hit in stale_hits)

    # 3) 工作文档/规矩不受时间衰减影响：97 分钟后依然稳健命中并附带相对时间前缀
    rule_hits = await memory.search_entries("公共高炉", kinds={KIND_WORK_DOC}, limit=3)
    assert len(rule_hits) == 1
    assert memory.format_hit_for_flash(rule_hits[0]).startswith("[1小时37分钟前] [长期规矩]")

    # 4) 同主题新旧公屏对比：1 分钟前得分显著高于 30 分钟前
    clock.tick(30 * 60_000)
    memory.record_player_chat("同伴乙", "红石中继器调成三档", timestamp_ms=clock.now_ms() - 30 * 60_000)
    memory.record_player_chat("同伴乙", "红石中继器改成四档", timestamp_ms=clock.now_ms() - 60_000)
    comp_hits = await memory.search_entries("红石中继器", kinds={KIND_PLAYER_CHAT}, limit=2)
    assert len(comp_hits) == 2
    assert "四档" in comp_hits[0].text and comp_hits[0].score > comp_hits[1].score * 2.0
