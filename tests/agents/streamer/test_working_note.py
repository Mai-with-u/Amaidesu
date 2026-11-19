"""单槽跨轮工作便签（streamer_note）：覆盖写入、清空、自然过期、可见名单隔离与 Planner 尾部渲染。"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.agents.streamer.game_now import GameNow
from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.tools.working_note import (
    WORKING_NOTE_MAX_CHARS,
    WORKING_NOTE_TOOL_NAME,
    WORKING_NOTE_TTL_MS,
    WorkingNoteStore,
    WorkingNoteToolProvider,
)
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from tests.modules.llm.fakes import FakeLLMManager


class _Clock:
    def __init__(self, initial_ms: int = 10_000_000) -> None:
        self.now_ms = initial_ms

    def __call__(self) -> int:
        return self.now_ms


async def test_working_note_store_write_truncate_clear_and_ttl() -> None:
    """便签单槽覆盖、超长截断、传空清空与 5 分钟自然过期。"""
    clock = _Clock(1_000_000)
    store = WorkingNoteStore(clock=clock)

    receipt = store.write("等同伴搭好熔炉再过去帮忙")
    assert receipt["ok"] is True
    assert receipt["cleared"] is False
    assert receipt["truncated"] is False
    assert store.read() == ("等同伴搭好熔炉再过去帮忙", 1_000_000)

    # 单槽覆盖 + 超出上限截断 + 空白折行
    long_text = "下一步打算\n" + ("查" * (WORKING_NOTE_MAX_CHARS + 20))
    receipt2 = store.write(long_text)
    assert receipt2["truncated"] is True
    assert len(receipt2["note"]) == WORKING_NOTE_MAX_CHARS
    assert "\n" not in receipt2["note"]

    # 未到 TTL 前仍可读，到达 TTL 后自动过期清空
    clock.now_ms += WORKING_NOTE_TTL_MS - 1
    assert store.read() is not None
    clock.now_ms += 1
    assert store.read() is None
    assert store.render() == ""

    # 写入后传空字符串清空
    store.write("一会儿切杂谈环节")
    assert store.read() is not None
    clear_receipt = store.write("   ")
    assert clear_receipt == {"ok": True, "cleared": True, "note": ""}
    assert store.read() is None


async def test_working_note_tool_visible_only_to_streamer_and_executes() -> None:
    """streamer_note 注册进 ToolRegistry 后仅对 streamer 可见，对其他 Agent 不可见，且可正常读写。"""
    clock = _Clock(2_000_000)
    store = WorkingNoteStore(clock=clock)
    registry = ToolRegistry()
    registry.register_provider(WorkingNoteToolProvider(store))

    streamer_tools = {spec.full_name for spec in registry.list_tools(for_agent="streamer")}
    mc_tools = {spec.full_name for spec in registry.list_tools(for_agent="minecraft")}
    assert WORKING_NOTE_TOOL_NAME in streamer_tools
    assert WORKING_NOTE_TOOL_NAME not in mc_tools
    assert registry.visible_to_of(WORKING_NOTE_TOOL_NAME) == ["streamer"]

    res = await registry.invoke(
        ToolInvocation(
            tool_name=WORKING_NOTE_TOOL_NAME,
            arguments={"note": "等玩家回话确认是否拆旧炉"},
            source="planner-react",
            invoking_agent="streamer",
        )
    )
    assert res.success is True
    assert store.read() == ("等玩家回话确认是否拆旧炉", 2_000_000)


async def test_planner_renders_working_note_at_reference_tail_adjacent_to_game_now() -> None:
    """有效工作便签渲染在动态参考段尾部、紧邻【游戏那边此刻】之前，不进系统提示词。"""
    clock = _Clock(3_000_000)
    store = WorkingNoteStore(clock=clock)
    store.write("等同伴喊一声就过去看机器")
    clock.now_ms += 30_000

    prompt_mgr = MagicMock()
    prompt_mgr.render = MagicMock(return_value="SYSTEM_BASE")
    llm = FakeLLMManager()
    planner = Planner(
        config=PlannerConfig(reference_tools=[]),
        llm_service=llm,
        prompt_service=prompt_mgr,
        room_state=RoomState(),
        tool_registry=None,
        env=PlannerEnv(
            clock=clock,
            working_note_provider=store.render,
            game_now_provider=lambda _agents: GameNow(self_chat=()),
        ),
    )

    ref_text, failure = await planner._assemble_reference(
        [], None, None, forced=False, proactive=True, overview='minecraft_glance: {"brief": "营地旁"}'
    )
    assert failure is None and ref_text is not None
    assert "【我的工作便签】" in ref_text
    assert "等同伴喊一声就过去看机器" in ref_text
    assert "世界事实一律以【游戏那边此刻】最新读数为准" in ref_text
    assert ref_text.index("【我的工作便签】") < ref_text.index("【游戏那边此刻】")
    # 确认系统提示词不受便签变动污染（前缀缓存稳定）
    system_prompt, prompt_err = planner._render_system_prompt()
    assert prompt_err is None and system_prompt is not None
    assert "等同伴喊一声就过去看机器" not in system_prompt
