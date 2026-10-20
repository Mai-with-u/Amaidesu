"""主播备忘录：状态容量纪律、工具名锁定、注入段与收播折叠。"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.agents.streamer.memory import (
    NOTEBOOK_TOOL_FULL_NAME,
    StreamerNotebook,
    build_notebook_tool_provider,
)
from src.agents.streamer.memory.tools import NOTEBOOK_SPEC
from src.agents.streamer.planner_context import AssemblerInputs, PlannerAssembler
from src.agents.streamer.replyer import Replyer
from src.modules.tools.models import ToolInvocation

# ---------------------------------------------------------------------------
# 工具名锁定（防拼写类事故：游戏侧曾有 maicraft_notebook 前缀拼错实锤）
# ---------------------------------------------------------------------------


def test_notebook_tool_full_name_is_locked() -> None:
    assert NOTEBOOK_SPEC.full_name == "streamer_notebook"
    assert NOTEBOOK_TOOL_FULL_NAME == "streamer_notebook"


# ---------------------------------------------------------------------------
# 状态：单条自由文本、全量覆盖写、容量封顶、会话清空
# ---------------------------------------------------------------------------


def test_notebook_starts_empty_and_overwrites_whole_text() -> None:
    notebook = StreamerNotebook()
    assert notebook.read() == ""
    notebook.set("答应观众下次画新皮肤")
    assert notebook.read() == "答应观众下次画新皮肤"
    notebook.set("翻篇：床名梗已退役")
    assert notebook.read() == "翻篇：床名梗已退役"


def test_notebook_capacity_truncates_to_limit() -> None:
    notebook = StreamerNotebook(max_chars=100)
    saved = notebook.set("字" * 250)
    assert len(saved) == 100
    assert notebook.read() == "字" * 100


def test_notebook_clear_resets_for_next_session() -> None:
    notebook = StreamerNotebook()
    notebook.set("本场结论")
    notebook.clear()
    assert notebook.read() == ""


# ---------------------------------------------------------------------------
# 工具行为：无参=读回全文；带 content=全量覆盖
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_read_returns_placeholder_when_empty() -> None:
    provider = build_notebook_tool_provider(StreamerNotebook())
    result = await provider.invoke(
        ToolInvocation(tool_name="streamer_notebook", arguments={})
    )
    assert "备忘录还是空的" in result.content


@pytest.mark.asyncio
async def test_tool_write_overwrites_then_read_returns_full_text() -> None:
    notebook = StreamerNotebook()
    provider = build_notebook_tool_provider(notebook)
    write = await provider.invoke(
        ToolInvocation(tool_name="streamer_notebook", arguments={"content": "第一版结论"})
    )
    assert write.content == "第一版结论"
    overwrite = await provider.invoke(
        ToolInvocation(tool_name="streamer_notebook", arguments={"content": "第二版结论"})
    )
    assert overwrite.content == "第二版结论"
    read = await provider.invoke(ToolInvocation(tool_name="streamer_notebook", arguments={}))
    assert read.content == "第二版结论"


@pytest.mark.asyncio
async def test_tool_empty_content_clears_notebook() -> None:
    notebook = StreamerNotebook()
    notebook.set("有内容")
    provider = build_notebook_tool_provider(notebook)
    await provider.invoke(ToolInvocation(tool_name="streamer_notebook", arguments={"content": ""}))
    assert notebook.read() == ""


# ---------------------------------------------------------------------------
# 注入：决策参考段（备忘录段在序列尾部，空内容省略段）
# ---------------------------------------------------------------------------


def test_assembler_appends_notebook_section_last() -> None:
    text = PlannerAssembler().assemble(
        AssemblerInputs(session_recap_section="上场回顾正文", notebook_section="备忘录正文")
    )
    assert "## 备忘录-内部参考\n备忘录正文" in text
    assert text.index("## 上场回顾-内部参考") < text.index("## 备忘录-内部参考")


def test_assembler_omits_notebook_section_when_empty() -> None:
    text = PlannerAssembler().assemble(AssemblerInputs(session_recap_section="回顾"))
    assert "备忘录" not in text
    assert PlannerAssembler().assemble(AssemblerInputs(notebook_section="")) == ""


# ---------------------------------------------------------------------------
# 注入：表达层 user 消息携带同段（空内容省略）
# ---------------------------------------------------------------------------


def _replyer_with_prompt_capture(notebook: StreamerNotebook) -> tuple[Replyer, MagicMock]:
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    replyer = Replyer(
        config={"bot_name": "麦麦", "personality": "", "style_constraints": "", "audience_salutation": "大家"},
        llm_service=MagicMock(),
        prompt_service=prompt,
        notebook_text_provider=notebook.read,
    )
    return replyer, prompt


def _render_turn(replyer: Replyer) -> None:
    from src.agents.streamer.plan import DecisionPlan

    replyer._render_turn_input(DecisionPlan(should_reply=True), [])


def test_replyer_turn_input_carries_notebook_section() -> None:
    notebook = StreamerNotebook()
    notebook.set("答应过 alice 画新皮肤")
    replyer, prompt = _replyer_with_prompt_capture(notebook)
    _render_turn(replyer)
    assert prompt.render.call_args.kwargs["notebook_section"] == "## 备忘录\n\n答应过 alice 画新皮肤"


def test_replyer_turn_input_omits_notebook_when_empty() -> None:
    replyer, prompt = _replyer_with_prompt_capture(StreamerNotebook())
    _render_turn(replyer)
    assert prompt.render.call_args.kwargs["notebook_section"] == ""


# ---------------------------------------------------------------------------
# 收播折叠：备忘录全文进入回顾原料
# ---------------------------------------------------------------------------


def test_recap_material_includes_notebook_snapshot() -> None:
    from src.agents.streamer.memory.recap import SessionRecapWriter

    writer = SessionRecapWriter(
        sessions_repo=MagicMock(),
        chat_repo=MagicMock(),
        recap_repo=MagicMock(),
        llm_service=MagicMock(),
        prompt_manager=MagicMock(),
        notebook_text_provider=lambda: "翻篇：床名梗已退役",
    )
    assert writer._render_notebook() == "翻篇：床名梗已退役"


def test_recap_collect_material_carries_notebook_section() -> None:
    from src.agents.streamer.memory.recap import SessionRecapWriter

    writer = SessionRecapWriter(
        sessions_repo=MagicMock(),
        chat_repo=MagicMock(),
        recap_repo=MagicMock(),
        llm_service=MagicMock(),
        prompt_manager=MagicMock(),
        notebook_text_provider=lambda: "翻篇：床名梗已退役",
    )
    # 场次头有内容（非空场次）才会走到原料拼接；各仓储 MagicMock 的 async 方法
    # 调用失败只降级省段，不影响备忘录段
    material = asyncio.run(
        writer._collect_material(1, {"started_at_ms": 1_760_000_000_000, "ended_at_ms": 1_760_003_600_000})
    )
    assert "【主播备忘录（本场结束时的工作笔记全文）】" in material
    assert "翻篇：床名梗已退役" in material


def test_recap_material_omits_notebook_without_provider_or_text() -> None:
    from src.agents.streamer.memory.recap import SessionRecapWriter

    no_provider = SessionRecapWriter(
        sessions_repo=MagicMock(),
        chat_repo=MagicMock(),
        recap_repo=MagicMock(),
        llm_service=MagicMock(),
        prompt_manager=MagicMock(),
    )
    assert no_provider._render_notebook() == ""
    empty = SessionRecapWriter(
        sessions_repo=MagicMock(),
        chat_repo=MagicMock(),
        recap_repo=MagicMock(),
        llm_service=MagicMock(),
        prompt_manager=MagicMock(),
        notebook_text_provider=lambda: "",
    )
    assert empty._render_notebook() == ""
