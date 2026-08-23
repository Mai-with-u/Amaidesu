"""
ContextAssembler —— 纯函数上下文组装（Wave 3 / §1.44）

## 定位
- **纯函数组件**（无循环/无定时/无 LLM，被 Planner 每轮调用）
- 与 PromptManager 同级被调服务层（不取代旧 ContextService）
- 组装顺序：稳定 → 动态（缓存前缀优先）
- 时间块分钟级 + 放尾部
- 直播流非对话历史（多对一，不强行"一问一答"）
- 观察结果"用后即摘要"（全量给 LLM 后再压缩）

## 每类 Agent 一个 Assembler
- PlannerAss：全量（人格 + 工具 + 环节 + 时间线 + 直播流 + 摘要 + 工作记忆）
- ReplyerAss：精简（人格 + 风格 + 发言意图 + 话题子集）
- GameAgentAss：自身（目标 + 状态 + 指令）—— 自给，**框架不主动拼**

## 接口
- ``ContextAssembler``：基类（不可直接用）
- ``PlannerAssembler``：默认全量组装器
- ``ReplyerAssembler``：精简组装器（人格 + 话题子集 + 当前发言意图）
- ``assemble()`` 同步纯函数；返回 ``Snapshot``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.modules.context.snapshot import (
    EnvironmentBlock,
    TimelineBlock,
    WorkingMemoryTrace,
)
from src.modules.time_utils import now_ms

# =============================================================================
# Snapshot / Section 数据类
# =============================================================================


class SectionStability(str, Enum):
    """Section 稳定性 hint（用于 LLM 缓存前缀优先）"""

    STABLE = "stable"  # 缓存前缀——尽量不变（如系统人格）
    DYNAMIC = "dynamic"  # 每轮会变


@dataclass(slots=True)
class AssembledSection:
    """一组组装的 section（一段文本 + 元数据）"""

    title: str
    body: str
    stability: SectionStability = SectionStability.STABLE
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Snapshot:
    """组装快照

    Attributes:
        agent_kind: "planner" | "replyer"（GameAgent 自给，自装配，不进框架）
        sections: 拼接好的 section 列表（顺序 = 渲染顺序）
        rendered_text: 拼接渲染后的完整文本（按 sections 顺序合成）
        assembled_at_ms: 拼装时刻
        stable_prefix_hash: 稳定段渲染文本哈希（与旧值比较可判断前缀命中）
    """

    agent_kind: str
    sections: List[AssembledSection] = field(default_factory=list)
    rendered_text: str = ""
    assembled_at_ms: int = 0
    stable_prefix_hash: str = ""

    def get_stable_prefix(self) -> str:
        """稳定段渲染文本（用于缓存前缀命中判断）。"""
        parts: List[str] = []
        for sec in self.sections:
            if sec.stability == SectionStability.STABLE:
                parts.append(f"## {sec.title}\n{sec.body}\n")
        return "\n".join(parts)

    def get_dynamic_tail(self) -> str:
        """动态段渲染文本（每轮会变）。"""
        parts: List[str] = []
        for sec in self.sections:
            if sec.stability == SectionStability.DYNAMIC:
                parts.append(f"## {sec.title}\n{sec.body}\n")
        return "\n".join(parts)


# =============================================================================
# 输入材料（assemble 的输入）
# =============================================================================


@dataclass(slots=True)
class AssemblerInputs:
    """组装器输入材料集合（assemble 纯函数需要的所有数据）"""

    persona: str = ""  # 人格 system prompt
    tool_definitions_block: str = ""  # 工具定义（LLM 视角）
    current_stage_label: Optional[str] = None  # 当前环节（agenda_runtime.current）
    stage_descriptions: str = ""  # 环节描述
    timeline_blocks: List[TimelineBlock] = field(default_factory=list)  # 摘要层时间块
    recent_chat_window: str = ""  # 最近一段时间窗口内的直播聊天（多对一流）
    environment: Optional[EnvironmentBlock] = None
    working_memory: Optional[WorkingMemoryTrace] = None
    memory_recall_section: str = ""  # §1.50 记忆召回面（ContextAssembler 内由 memory.recall 取得）
    reply_intent: str = ""  # Replyer 专用：当前发言意图
    topic_subset: str = ""  # Replyer 专用：相关话题子集

    # 元信息（缓存前缀 hash 锚点）
    agent_kind: str = "planner"  # "planner" / "replyer" / 其他


# =============================================================================
# Assembler（基类 + 具体实现）
# =============================================================================


@dataclass(slots=True)
class _HashBuffer:
    """轻量 stable 段 hash 计算（无需外部库；DX 算法足够用于缓存 hint）"""

    state: int = 5381

    def feed(self, text: str) -> None:
        for ch in text:
            self.state = ((self.state << 5) + self.state + ord(ch)) & 0xFFFFFFFF

    def value(self) -> int:
        return self.state


class ContextAssembler:
    """组装器基类（abstract assemble）。"""

    agent_kind: str = "base"  # 子类覆写

    def assemble(self, inputs: AssemblerInputs) -> Snapshot:
        raise NotImplementedError


class PlannerAssembler(ContextAssembler):
    """Planner 全量组装器（§1.44）

    顺序（稳定 → 动态）：
    1. system 人格（stable）
    2. 工具定义（stable）
    3. 环节描述（stable）
    4. 时间线摘要（stable）
    5. 直播流历史窗口（stable 缓存前缀当窗口期内）
    6. 直播间快照（dynamic）
    7. 工作记忆（dynamic）
    """

    agent_kind = "planner"

    def assemble(self, inputs: AssemblerInputs) -> Snapshot:
        sections: List[AssembledSection] = []

        # 1. system 人格
        sections.append(
            AssembledSection(
                title="系统人格",
                body=inputs.persona or "（未配置 persona）",
                stability=SectionStability.STABLE,
            )
        )

        # 2. 工具定义
        sections.append(
            AssembledSection(
                title="可用工具",
                body=inputs.tool_definitions_block or "（无）",
                stability=SectionStability.STABLE,
            )
        )

        # 3. 环节描述
        sections.append(
            AssembledSection(
                title="环节描述",
                body=inputs.stage_descriptions or "（无）",
                stability=SectionStability.STABLE,
            )
        )

        # 4. 时间线摘要
        sections.append(
            AssembledSection(
                title="时间线摘要",
                body=_render_timeline_blocks(inputs.timeline_blocks),
                stability=SectionStability.STABLE,
            )
        )

        # 5. 直播流窗口
        sections.append(
            AssembledSection(
                title="直播流（窗口）",
                body=inputs.recent_chat_window or "（暂无）",
                stability=SectionStability.DYNAMIC,  # 窗口逐步前移也算 dynamic
            )
        )

        # 6. 直播间快照（环境）
        env_body = _render_environment(inputs.environment)
        sections.append(
            AssembledSection(
                title="直播间快照",
                body=env_body,
                stability=SectionStability.DYNAMIC,
            )
        )

        # 7. 工作记忆
        sections.append(
            AssembledSection(
                title="工作记忆",
                body=_render_working_memory(inputs.working_memory),
                stability=SectionStability.DYNAMIC,
            )
        )

        # 8. 记忆召回面（§1.50）
        sections.append(
            AssembledSection(
                title="记忆召回",
                body=inputs.memory_recall_section or "（暂无）",
                stability=SectionStability.DYNAMIC,
            )
        )

        # 拼接 + 计算 stable prefix hash
        rendered = _render_sections(sections)
        hash_buf = _HashBuffer()
        hash_buf.feed(_render_sections([s for s in sections if s.stability == SectionStability.STABLE]))

        return Snapshot(
            agent_kind=self.agent_kind,
            sections=sections,
            rendered_text=rendered,
            assembled_at_ms=now_ms(),
            stable_prefix_hash=hex(hash_buf.value()),
        )


class ReplyerAssembler(ContextAssembler):
    """Replyer 精简组装器（§1.44）

    顺序：人格 → 风格 / 发言意图 → 话题子集 → 时间块分钟级（置尾）
    """

    agent_kind = "replyer"

    def assemble(self, inputs: AssemblerInputs) -> Snapshot:
        sections: List[AssembledSection] = []

        # 1. 人格
        sections.append(
            AssembledSection(
                title="系统人格",
                body=inputs.persona or "（未配置 persona）",
                stability=SectionStability.STABLE,
            )
        )

        # 2. 当前发言意图
        sections.append(
            AssembledSection(
                title="发言意图",
                body=inputs.reply_intent or "（未提供）",
                stability=SectionStability.DYNAMIC,
            )
        )

        # 3. 话题子集
        sections.append(
            AssembledSection(
                title="话题子集",
                body=inputs.topic_subset or "（暂无）",
                stability=SectionStability.STABLE,
            )
        )

        # 4. 时间块分钟级（置尾——§1.44：时间块分钟级 + 放尾部）
        # 即使 Replyer 也遵守这一约定，把时间块稳定前置使 LLM 缓存命中前缀
        timeline_body = _render_timeline_blocks(inputs.timeline_blocks)
        sections.append(
            AssembledSection(
                title="时间块（分钟级）",
                body=timeline_body or "（暂无）",
                stability=SectionStability.DYNAMIC,  # 时间块即使分钟级也在尾
            )
        )

        rendered = _render_sections(sections)
        hash_buf = _HashBuffer()
        hash_buf.feed(_render_sections([s for s in sections if s.stability == SectionStability.STABLE]))

        return Snapshot(
            agent_kind=self.agent_kind,
            sections=sections,
            rendered_text=rendered,
            assembled_at_ms=now_ms(),
            stable_prefix_hash=hex(hash_buf.value()),
        )


# =============================================================================
# 渲染辅助（纯函数）
# =============================================================================


def _render_sections(sections: List[AssembledSection]) -> str:
    """按顺序渲染 sections。"""
    parts: List[str] = []
    for sec in sections:
        parts.append(f"## {sec.title}\n{sec.body}")
    return "\n\n".join(parts)


def _render_timeline_blocks(blocks: List[TimelineBlock]) -> str:
    """渲染时间线块——按时间序列出，每块一行。"""
    if not blocks:
        return ""
    parts: List[str] = []
    for blk in blocks:
        tag_part = f" [{','.join(blk.tags)}]" if blk.tags else ""
        duration_min = (blk.end_ms - blk.start_ms) // 60000
        parts.append(f"- [{blk.start_ms}ms ~ {blk.end_ms}ms (≈{duration_min}m)]{tag_part} {blk.summary}")
    return "\n".join(parts)


def _render_environment(env: Optional[EnvironmentBlock]) -> str:
    """渲染直播间快照。"""
    if env is None:
        return "（暂无）"
    lines: List[str] = []
    lines.append(f"- 时刻(分钟级): {env.minute_bucket_ms}ms")
    lines.append(f"- 已开播时长: {env.duration_so_far_ms}ms")
    if env.current_stage_label:
        lines.append(f"- 当前环节: {env.current_stage_label}")
    if env.unread_summary:
        lines.append(f"- 未读摘要: {env.unread_summary}")
    if env.key_changes:
        lines.append("- 关键变化:")
        for change in env.key_changes:
            lines.append(f"  - {change}")
    return "\n".join(lines)


def _render_working_memory(wm: Optional[WorkingMemoryTrace]) -> str:
    """渲染工作记忆（本轮工具链——§1.51 归一化）。"""
    if wm is None or (not wm.tool_call_pairs and not wm.pending_tool_calls):
        return "（空）"
    lines: List[str] = []
    for pair in wm.tool_call_pairs:
        # pair 形如 {"call": "...", "result": "..."}
        lines.append(f"- {pair.get('call', '')} → {pair.get('result', '')}")
    if wm.pending_tool_calls:
        lines.append("待返回: " + ", ".join(wm.pending_tool_calls))
    return "\n".join(lines)


__all__ = [
    "ContextAssembler",
    "PlannerAssembler",
    "ReplyerAssembler",
    "SectionStability",
    "AssembledSection",
    "Snapshot",
    "AssemblerInputs",
]
