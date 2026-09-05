"""
ContextAssembler 单元测试（Wave 3 / §1.44）

权威定案：
- 纯函数（无循环/无定时/无 LLM）
- 稳定 → 动态排序（缓存前缀优先）
- 时间块分钟级 + 置尾部
- 直播流非对话历史（多人多对一，不强行"一问一答"结构）
- 每类 Agent 一个 Assembler（Planner / Replyer）

Wave 3 仅测试组装行为；与旧 ContextService 并行共存，不动旧模块。
"""

from __future__ import annotations

import pytest

from src.modules.context import (
    AssembledSection,
    AssemblerInputs,
    ContextAssembler,
    EnvironmentBlock,
    PlannerAssembler,
    SectionStability,
    Snapshot,
    TimelineBlock,
    WorkingMemoryTrace,
)


# =============================================================================
# PlannerAssembler：稳定→动态排序
# =============================================================================


def test_planner_assembler_section_order() -> None:
    """Planner 组装顺序：稳定 → 动态（缓存前缀优先）。"""
    assembler = PlannerAssembler()
    inputs = AssemblerInputs(
        persona="你是一个虚拟主播",
        tool_definitions_block="[]",
        stage_descriptions="开场聊天",
        timeline_blocks=[
            TimelineBlock(start_ms=0, end_ms=60_000, summary="开场", tags=["intro"]),
        ],
        recent_chat_window="弹幕：好耶 x 5",
        environment=EnvironmentBlock(
            minute_bucket_ms=60_000,
            duration_so_far_ms=60_000,
            current_stage_label="开场",
            unread_summary="3 条未读",
        ),
        working_memory=WorkingMemoryTrace(
            tool_call_pairs=[{"call": "look_at_screen()", "result": "(画面……)"}],
        ),
        memory_recall_section="用户 alice 喜欢技术",
        agent_kind="planner",
    )

    snap: Snapshot = assembler.assemble(inputs)
    titles = [sec.title for sec in snap.sections]
    # 必须含：系统人格/可用工具/环节描述/时间线摘要/直播流/直播间快照/工作记忆/记忆召回
    expected_titles_order = [
        "系统人格",
        "可用工具",
        "环节描述",
        "时间线摘要",
        "直播流（窗口）",
        "直播间快照",
        "工作记忆",
        "记忆召回",
    ]
    assert titles == expected_titles_order, f"Planner 顺序应为 {expected_titles_order}, 实得 {titles}"

    # 稳定段在前，动态段在后
    stability_seq = [sec.stability for sec in snap.sections]
    # 至少前 4 是稳定
    assert stability_seq[:4] == [SectionStability.STABLE] * 4, "前 4 段应为稳定"
    # 动态段后置
    dynamic_idx = [i for i, s in enumerate(stability_seq) if s == SectionStability.DYNAMIC]
    if dynamic_idx:
        last_dynamic = max(dynamic_idx)
        for idx, s in enumerate(stability_seq):
            if s == SectionStability.STABLE:
                assert idx < last_dynamic, f"稳定段 idx={idx} 应早于最后一个动态段 idx={last_dynamic}"


def test_planner_assembler_snapshot_fields() -> None:
    """Snapshot 字段齐全。"""
    assembler = PlannerAssembler()
    snap = assembler.assemble(AssemblerInputs(agent_kind="planner"))
    assert snap.agent_kind == "planner"
    assert isinstance(snap.rendered_text, str)
    assert isinstance(snap.stable_prefix_hash, str)
    assert snap.stable_prefix_hash.startswith("0x") or len(snap.stable_prefix_hash) > 0
    assert snap.assembled_at_ms > 0


def test_stable_prefix_hash_depends_on_stable_sections_only() -> None:
    """stable_prefix_hash 只受稳定段影响——动态段变化不应改变。"""
    planner = PlannerAssembler()

    snap_a = planner.assemble(
        AssemblerInputs(
            persona="fixed persona",
            tool_definitions_block="def-A",
            stage_descriptions="stage-A",
            timeline_blocks=[TimelineBlock(0, 60_000, "stable")],
            agent_kind="planner",
        )
    )
    snap_b = planner.assemble(
        AssemblerInputs(
            persona="fixed persona",
            tool_definitions_block="def-A",
            stage_descriptions="stage-A",
            timeline_blocks=[TimelineBlock(0, 60_000, "stable")],
            # 全部动态段变化
            recent_chat_window="完全不同",
            environment=EnvironmentBlock(minute_bucket_ms=10_000, duration_so_far_ms=10_000),
            working_memory=WorkingMemoryTrace(tool_call_pairs=[{"call": "x()", "result": "y"}]),
            memory_recall_section="完全不同的召回",
            agent_kind="planner",
        )
    )

    assert snap_a.stable_prefix_hash == snap_b.stable_prefix_hash, (
        "动态段变化不应影响 stable_prefix_hash"
    )
    # 但 rendered_text 应不同（动态段影响整体渲染）
    assert snap_a.rendered_text != snap_b.rendered_text


def test_get_stable_prefix_only_stable_sections() -> None:
    """Snapshot.get_stable_prefix 只返回稳定段。"""
    planner = PlannerAssembler()
    snap = planner.assemble(
        AssemblerInputs(
            persona="固定",
            agent_kind="planner",
            recent_chat_window="WINDOW-CHANGEABLE",
        )
    )
    stable_text = snap.get_stable_prefix()
    assert "固定" in stable_text
    assert "WINDOW-CHANGEABLE" not in stable_text, "动态段不应出现在 stable prefix"


# ReplyerAssembler 已删除（无生产调用方，旧插件遗留）——对应测试随删


# =============================================================================
# 直播流非对话历史：多人多对一，不强制交替
# =============================================================================


def test_recent_chat_window_kept_as_continuous_stream() -> None:
    """recent_chat_window 直接作为一段（多对一，非"问-答"交替结构）。"""
    planner = PlannerAssembler()
    chat = "alice: 好耶\nbob: 加油\ncharlie: 好厉害\n(crowd noise)"
    snap = planner.assemble(
        AssemblerInputs(
            persona="X",
            recent_chat_window=chat,
            agent_kind="planner",
        )
    )
    # 找直播流 section
    flow_section = next(s for s in snap.sections if s.title == "直播流（窗口）")
    assert flow_section.body == chat, "直播流段应保持原始文本（多对一流）"


# =============================================================================
# AssembledSection & Snapshot 工具方法
# =============================================================================


def test_assembled_section_defaults() -> None:
    """默认 stability = STABLE。"""
    sec = AssembledSection(title="t", body="b")
    assert sec.stability == SectionStability.STABLE


def test_snapshot_get_dynamic_tail() -> None:
    """Snapshot.get_dynamic_tail 只取动态段。"""
    planner = PlannerAssembler()
    snap = planner.assemble(
        AssemblerInputs(
            persona="固定",
            recent_chat_window="DYNAMIC-CHAT",
            agent_kind="planner",
        )
    )
    tail = snap.get_dynamic_tail()
    assert "DYNAMIC-CHAT" in tail
    # 不应包含纯稳定内容
    stable = snap.get_stable_prefix()
    if stable:
        # tail 与 stable 不重叠
        assert stable not in tail


# =============================================================================
# 基类：assemble 是抽象方法
# =============================================================================


def test_context_assembler_is_abstract() -> None:
    """ContextAssembler 基类 assemble 不应直接调用。"""
    base = ContextAssembler()
    try:
        base.assemble(AssemblerInputs())
    except NotImplementedError:
        pass
    else:
        pytest.fail("基类 assemble 应抛 NotImplementedError")
