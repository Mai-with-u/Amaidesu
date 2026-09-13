"""PlannerAssembler 参考段组装器单元测试。

- 3 输入字段（stage_descriptions / environment / memory_recall_section）——
  对话内容走 canonical 原生消息通道，不进组装器
- 空段规则：有数据才渲染段，无占位文本
- 段顺序锁定：环节描述 → 直播间快照 → 记忆召回；整段作为参考段固定在
  消息序列尾（append-only，服务端 LLM 前缀缓存命中既有前缀）
"""

from __future__ import annotations

from src.agents.streamer.planner_context import AssemblerInputs, EnvironmentBlock, PlannerAssembler


def _production_inputs() -> AssemblerInputs:
    """生产型输入——模拟 planner 生产路径实际传递的数据形态。"""
    return AssemblerInputs(
        stage_descriptions="## 开场聊天\n自由闲聊，观察弹幕节奏。",
        environment=EnvironmentBlock(
            minute_bucket_ms=1_760_000_000_000,
            duration_so_far_ms=45 * 60_000,
            current_stage_label="开场",
            unread_summary="3 条未读互动",
            key_changes=[],
        ),
        memory_recall_section="- alice 上次问过新皮肤",
    )


def test_production_inputs_render_all_sections() -> None:
    """生产型输入 → 3 段全部渲染。"""
    text = PlannerAssembler().assemble(_production_inputs())
    assert "## 环节描述" in text
    assert "## 直播间快照" in text
    assert "## 记忆召回" in text


def test_no_placeholder_text_in_output() -> None:
    """任何输入组合下都不出现旧占位文本（（未配置 persona）/（暂无）/（空）/（无））。"""
    assembler = PlannerAssembler()
    cases = [
        AssemblerInputs(),
        AssemblerInputs(stage_descriptions="有环节"),
        AssemblerInputs(environment=EnvironmentBlock(minute_bucket_ms=0, duration_so_far_ms=0)),
        _production_inputs(),
    ]
    for inputs in cases:
        text = assembler.assemble(inputs)
        for placeholder in ("（未配置 persona）", "（暂无）", "（空）", "（无）"):
            assert placeholder not in text


def test_empty_sections_omitted() -> None:
    """空输入 → 空段整段省略（段标题也不出现）。"""
    text = PlannerAssembler().assemble(AssemblerInputs())
    assert text == ""
    assert "环节描述" not in text
    assert "直播间快照" not in text
    assert "记忆召回" not in text


def test_section_order_locked() -> None:
    """段顺序锁定：环节描述 → 直播间快照 → 记忆召回。"""
    text = PlannerAssembler().assemble(_production_inputs())
    assert text.index("## 环节描述") < text.index("## 直播间快照")
    assert text.index("## 直播间快照") < text.index("## 记忆召回")


def test_environment_render_details() -> None:
    """快照渲染：分钟时刻转人类可读；零时长/空字段行省略。"""
    text = PlannerAssembler().assemble(
        AssemblerInputs(environment=EnvironmentBlock(minute_bucket_ms=0, duration_so_far_ms=0))
    )
    assert "- 时刻: 1970-01-01 08:00" in text  # 本机 UTC+8
    assert "已开播时长" not in text
    assert "0 分钟" not in text
