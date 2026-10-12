"""BackgroundMaintainer 单元测试——事实提取与画像生成

聚焦切片 E 的后台行为：
- ``_parse_summary_and_facts``：JSON 解析 / Markdown 围栏容错 / 损坏输出
  降级纯文本摘要 / facts 条数限制
- ``_store_viewer_facts``：message_id 程序化归属；幻觉 id 与缺身份键丢弃
- ``_generate_profiles``：候选 → 增量生成 → 水位推进；LLM 失败逐人降级
- 提取开关关闭：不落事实、不投递画像任务

不做后台循环端到端——那是 integration 测试范畴（参 ``tests/integration/``）。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.background import BackgroundMaintainer
from src.modules.memory.simple_memory import SimpleMemory
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.repos import ViewerRepo


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db_path() -> Generator[Path, None, None]:
    td = Path(tempfile.mkdtemp(prefix="bg-extract-"))
    yield td / "test.db"
    shutil.rmtree(td, ignore_errors=True)


@pytest.fixture
async def memory(temp_db_path: Path) -> AsyncGenerator[SimpleMemory, None]:
    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    mem = SimpleMemory(store)
    await mem.initialize()
    yield mem
    await store.close()


@pytest.fixture
async def viewer_repo(temp_db_path: Path) -> ViewerRepo:
    store = SQLiteDatabase(temp_db_path)
    await store.initialize()
    repo = ViewerRepo(store.manager)
    await repo.upsert_viewer_message(
        platform="bilibili", user_id="u1", user_name="观众甲", timestamp_ms=1_000
    )
    await repo.upsert_viewer_message(
        platform="bilibili", user_id="u1", user_name="观众甲", timestamp_ms=1_000
    )
    await repo.upsert_viewer_message(
        platform="bilibili", user_id="u1", user_name="观众甲", timestamp_ms=1_000
    )
    return repo


def _make_maintainer(
    memory: SimpleMemory | None = None,
    *,
    llm_response: str = "",
    llm_success: bool = True,
    memory_policy: dict | None = None,
) -> tuple[BackgroundMaintainer, MagicMock]:
    """构造带真实 SimpleMemory / mock LLM 的 BackgroundMaintainer。

    prompt_manager 注入满足 ``render() -> str`` 的最小 fake（按模板名返回
    固定文本），LLM 返回预置文本。
    """
    room_state = MagicMock()
    rs_snap = MagicMock()
    rs_snap.heat = "low"
    rs_snap.topics = []
    rs_snap.topic_summary = ""
    room_state.get_snapshot = MagicMock(return_value=rs_snap)

    llm = MagicMock()
    response = MagicMock()
    response.success = llm_success
    response.content = llm_response
    llm.generate = AsyncMock(return_value=response)

    prompt_manager = MagicMock()
    prompt_manager.render = MagicMock(return_value="PROMPT")

    maintainer = BackgroundMaintainer(
        config={},
        room_state=room_state,
        llm_service=llm,
        memory=memory,
        memory_policy=memory_policy,
        prompt_manager=prompt_manager,
    )
    return maintainer, llm


# ---------------------------------------------------------------------------
# _parse_summary_and_facts
# ---------------------------------------------------------------------------


class TestParseSummaryAndFacts:
    def test_normal_json(self) -> None:
        maintainer, _ = _make_maintainer()
        summary, facts = maintainer._parse_summary_and_facts(
            '{"summary": "在聊新皮肤", "facts": [{"message_id": "m1", "fact": "想看新皮肤评测"}]}'
        )
        assert summary == "在聊新皮肤"
        assert facts == [{"message_id": "m1", "fact": "想看新皮肤评测"}]

    def test_markdown_fence_stripped(self) -> None:
        maintainer, _ = _make_maintainer()
        raw = '```json\n{"summary": "在聊考试", "facts": []}\n```'
        summary, facts = maintainer._parse_summary_and_facts(raw)
        assert summary == "在聊考试"
        assert facts == []

    def test_broken_json_falls_back_to_plain_summary(self) -> None:
        """损坏输出整体降级为纯文本摘要（旧契约），facts 为空——摘要不被拖垮。"""
        maintainer, _ = _make_maintainer()
        raw = "观众们在聊下周的考试安排"
        summary, facts = maintainer._parse_summary_and_facts(raw)
        assert summary == raw
        assert facts == []

    def test_facts_capped_per_batch(self) -> None:
        """facts 条数受 facts_per_batch 限制（默认 5）。"""
        maintainer, _ = _make_maintainer()
        items = [{"message_id": f"m{i}", "fact": f"事实{i}"} for i in range(9)]
        raw = '{"summary": "s", "facts": ' + str(items).replace("'", '"') + "}"
        _, facts = maintainer._parse_summary_and_facts(raw)
        assert len(facts) == 5

    def test_malformed_fact_entries_dropped(self) -> None:
        """缺 message_id 或 fact 的条目丢弃。"""
        maintainer, _ = _make_maintainer()
        raw = '{"summary": "s", "facts": [{"fact": "缺id"}, {"message_id": "m1"}, {"message_id": "m2", "fact": "合法"}]}'
        _, facts = maintainer._parse_summary_and_facts(raw)
        assert facts == [{"message_id": "m2", "fact": "合法"}]


# ---------------------------------------------------------------------------
# _store_viewer_facts（归属 + 落库）
# ---------------------------------------------------------------------------


class TestStoreViewerFacts:
    @pytest.mark.asyncio
    async def test_facts_attributed_via_message_id(self, memory: SimpleMemory) -> None:
        """message_id 反查批内消息归属 (platform, user_id)，不靠 LLM 报人名。"""
        maintainer, _ = _make_maintainer(memory)
        evidence = {"m1": ("bilibili", "u1")}
        await maintainer._store_viewer_facts(
            [{"message_id": "m1", "fact": "下周要考试"}], evidence
        )
        facts = await memory.list_viewer_facts(platform="bilibili", user_id="u1")
        assert len(facts) == 1
        assert facts[0].fact_text == "下周要考试"
        assert facts[0].source_message_id == "m1"

    @pytest.mark.asyncio
    async def test_hallucinated_message_id_dropped(self, memory: SimpleMemory) -> None:
        """引用批内不存在的 id → 丢弃（防幻觉）。"""
        maintainer, _ = _make_maintainer(memory)
        await maintainer._store_viewer_facts(
            [{"message_id": "m_ghost", "fact": "编造的事实"}], {"m1": ("bilibili", "u1")}
        )
        assert await memory.count_viewer_facts() == 0

    @pytest.mark.asyncio
    async def test_incomplete_identity_dropped(self, memory: SimpleMemory) -> None:
        """批内消息缺 platform / user_id → 丢弃（身份键不完整的原料不收）。"""
        maintainer, _ = _make_maintainer(memory)
        await maintainer._store_viewer_facts(
            [{"message_id": "m1", "fact": "没有平台的消息"}], {"m1": ("", "u1")}
        )
        await maintainer._store_viewer_facts(
            [{"message_id": "m2", "fact": "没有ID的消息"}], {"m2": ("bilibili", "")}
        )
        assert await memory.count_viewer_facts() == 0

    @pytest.mark.asyncio
    async def test_duplicate_fact_not_rewritten(self, memory: SimpleMemory) -> None:
        """同观众同文本去重由 SimpleMemory 承担。"""
        maintainer, _ = _make_maintainer(memory)
        evidence = {"m1": ("bilibili", "u1")}
        await maintainer._store_viewer_facts([{"message_id": "m1", "fact": "喜欢玩MC"}], evidence)
        await maintainer._store_viewer_facts([{"message_id": "m1", "fact": "喜欢玩MC"}], evidence)
        assert await memory.count_viewer_facts() == 1


# ---------------------------------------------------------------------------
# _generate_profiles（增量压缩）
# ---------------------------------------------------------------------------


class TestGenerateProfiles:
    @pytest.mark.asyncio
    async def test_generates_profile_and_advances_watermark(
        self, memory: SimpleMemory, viewer_repo: ViewerRepo
    ) -> None:
        """候选（门槛达标 + 水位后有新事实）→ LLM 压缩 → 画像写回 + 水位推进。"""
        await memory.add_viewer_fact(
            platform="bilibili", user_id="u1", fact_text="下周要考试，直播会少来", created_at_ms=100
        )
        maintainer, llm = _make_maintainer(memory, llm_response="观众甲：学生党，下周考试，近期直播互动会变少。")

        await maintainer._generate_profiles()

        profile = await memory.get_viewer_profile_with_watermark(platform="bilibili", user_id="u1")
        assert profile is not None
        assert "学生党" in profile.profile_text
        assert profile.last_compressed_at_ms > 100
        # LLM 收到的 prompt 含旧画像占位与新事实
        prompt_arg = llm.generate.await_args.args[0]
        assert "下周要考试" in prompt_arg

    @pytest.mark.asyncio
    async def test_candidate_below_threshold_skipped(self, memory: SimpleMemory) -> None:
        """互动量不达门槛 → 不生成（无 LLM 调用）。"""
        await memory.add_viewer_fact(
            platform="bilibili", user_id="lurker", fact_text="想看恐怖游戏", created_at_ms=100
        )
        maintainer, llm = _make_maintainer(memory, llm_response="画像")
        # lurker 不在 viewers 表（interaction_count=0 < 门槛 3）
        await maintainer._generate_profiles()
        llm.generate.assert_not_awaited()
        assert await memory.get_viewer_profile(platform="bilibili", user_id="lurker") is None

    @pytest.mark.asyncio
    async def test_recompressed_candidate_not_reprocessed(
        self, memory: SimpleMemory, viewer_repo: ViewerRepo
    ) -> None:
        """水位推进后无新事实 → 不再重复生成。"""
        await memory.add_viewer_fact(
            platform="bilibili", user_id="u1", fact_text="喜欢数码", created_at_ms=100
        )
        maintainer, llm = _make_maintainer(memory, llm_response="第一版画像")
        await maintainer._generate_profiles()
        assert llm.generate.await_count == 1

        await maintainer._generate_profiles()
        assert llm.generate.await_count == 1  # 无新事实，不空转

    @pytest.mark.asyncio
    async def test_llm_failure_leaves_profile_absent(
        self, memory: SimpleMemory, viewer_repo: ViewerRepo
    ) -> None:
        """LLM 失败 → 该观众无画像、水位不推进（下轮重试）。"""
        await memory.add_viewer_fact(
            platform="bilibili", user_id="u1", fact_text="喜欢数码", created_at_ms=100
        )
        maintainer, _ = _make_maintainer(memory, llm_success=False)
        await maintainer._generate_profiles()
        assert await memory.get_viewer_profile(platform="bilibili", user_id="u1") is None


class TestProfileMaterial:
    """画像原料的结构化直读:付费汇总 + 最近明细 + 付费时身份快照。"""

    @staticmethod
    def _make_chat_repo() -> MagicMock:
        """mock 三表明细读取(gifts/scs/guards 各 1 行,含身份快照列)。"""
        chat_repo = MagicMock()
        chat_repo.summarize_user_contributions = AsyncMock(
            return_value={"gift_total_count": 2, "gift_total_amount": 2000, "sc_total_amount": 50_000, "sc_total_count": 1}
        )
        gift_row = {
            "timestamp_ms": 100, "gift_name": "小星星", "quantity": 3,
            "fans_medal_level": 21, "fans_medal_name": "粉丝团", "guard_level": 3,
        }
        sc_row = {"timestamp_ms": 200, "message": "加油", "fans_medal_level": 21, "fans_medal_name": "粉丝团", "guard_level": 0}
        guard_row = {
            "timestamp_ms": 300, "guard_level": 3, "guard_num": 1, "guard_unit": "月",
            "fans_medal_level": 21, "fans_medal_name": "粉丝团",
        }
        chat_repo.list_user_gifts = AsyncMock(return_value=[gift_row])
        chat_repo.list_user_super_chats = AsyncMock(return_value=[sc_row])
        chat_repo.list_user_guards = AsyncMock(return_value=[guard_row])
        return chat_repo

    @pytest.mark.asyncio
    async def test_payment_material_lines(self) -> None:
        """原料行含付费汇总、三类明细与最近一次付费时的身份快照。"""
        maintainer, _ = _make_maintainer(memory=MagicMock())
        maintainer._chat_repo = self._make_chat_repo()

        lines = await maintainer._collect_payment_material("u1")

        joined = "\n".join(lines)
        assert "累计付费约 52 元" in joined  # (2000 + 50000) 金瓜子 → 52 元
        assert "送出礼物 小星星×3" in joined
        assert "发送 SC「加油」" in joined
        assert "开通舰长（1月）" in joined
        assert "舰长" in joined and "21 级牌" in joined  # 身份快照

    @pytest.mark.asyncio
    async def test_payment_material_degrades_without_chat_repo(self) -> None:
        """chat_repo 未注入 → 原料缺结构化行,不阻断画像生成。"""
        maintainer, _ = _make_maintainer(memory=MagicMock())
        assert await maintainer._collect_payment_material("u1") == []


# ---------------------------------------------------------------------------
# 提取开关(memory_policy.fact_extraction_enabled)
# ---------------------------------------------------------------------------


class TestExtractionToggle:
    @staticmethod
    def _make_summarize_maintainer(
        memory: SimpleMemory,
        *,
        llm_response: str,
        memory_policy: dict | None,
    ) -> tuple[BackgroundMaintainer, MagicMock]:
        """带 chat_repo / session_manager 的完整摘要链路构造。"""
        room_state = MagicMock()
        rs_snap = MagicMock()
        rs_snap.heat = "low"
        rs_snap.topics = []
        rs_snap.topic_summary = ""
        room_state.get_snapshot = MagicMock(return_value=rs_snap)

        llm = MagicMock()
        response = MagicMock()
        response.success = True
        response.content = llm_response
        llm.generate = AsyncMock(return_value=response)

        prompt_manager = MagicMock()
        prompt_manager.render = MagicMock(return_value="PROMPT")

        session_manager = MagicMock()
        session_manager.resolve_pk = AsyncMock(return_value=1)

        chat_repo = MagicMock()
        chat_repo.list_recent_live_chat = AsyncMock(
            return_value=[
                {
                    "message_id": "m1",
                    "sender_name": "观众甲",
                    "sender_id": "u1",
                    "platform": "bilibili",
                    "content": "下周要考试了",
                }
            ]
        )
        chat_repo.list_super_chats_since = AsyncMock(return_value=[])

        maintainer = BackgroundMaintainer(
            config={"summary_interval_ms": 1000},
            room_state=room_state,
            llm_service=llm,
            chat_repo=chat_repo,
            session_manager=session_manager,
            memory=memory,
            memory_policy=memory_policy,
            prompt_manager=prompt_manager,
        )
        return maintainer, llm

    @pytest.mark.asyncio
    async def test_disabled_policy_skips_fact_storage(self, temp_db_path: Path) -> None:
        """开关关闭：摘要照常出，但事实不落库、画像任务不投递。"""
        store = SQLiteDatabase(temp_db_path)
        await store.initialize()
        memory = SimpleMemory(store)
        await memory.initialize()
        try:
            maintainer, _llm = self._make_summarize_maintainer(
                memory,
                llm_response='{"summary": "在聊考试", "facts": [{"message_id": "m1", "fact": "观众甲下周要考试"}]}',
                memory_policy={"fact_extraction_enabled": False},
            )
            await maintainer._summarize_topic(now_ms=10_000)

            assert await memory.count_viewer_facts() == 0
            assert maintainer._compress_queue.empty(), "开关关闭时不应投递画像生成任务"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_enabled_policy_stores_facts_and_enqueues_profiles(self, temp_db_path: Path) -> None:
        """开关默认开：事实落库 + 画像任务投递。"""
        store = SQLiteDatabase(temp_db_path)
        await store.initialize()
        memory = SimpleMemory(store)
        await memory.initialize()
        try:
            maintainer, _llm = self._make_summarize_maintainer(
                memory,
                llm_response='{"summary": "在聊考试", "facts": [{"message_id": "m1", "fact": "观众甲下周要考试"}]}',
                memory_policy=None,
            )
            await maintainer._summarize_topic(now_ms=10_000)

            facts = await memory.list_viewer_facts(platform="bilibili", user_id="u1")
            assert len(facts) == 1
            assert facts[0].fact_text == "观众甲下周要考试"
            assert not maintainer._compress_queue.empty()
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_policy_defaults_applied(self, memory: SimpleMemory) -> None:
        """缺省策略走内置默认（提取开、门槛 3、条数 5）。"""
        maintainer, _ = _make_maintainer(memory)
        assert maintainer._fact_extraction_enabled is True
        assert maintainer._profile_min_interactions == 3
        assert maintainer._facts_per_batch == 5
        assert maintainer._profile_max_length == 400
