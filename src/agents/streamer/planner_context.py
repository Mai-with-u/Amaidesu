"""Planner 参考段组装——元数据参考内容的纯函数渲染。

## 定位
- **纯函数组件**（无循环/无定时/无 LLM，被 Planner 每轮调用）
- 对话部分（历史 + 本批弹幕 + 主播发言）由 canonical 映射承载为原生
  user/assistant 消息，**不经过本模块**；本模块只渲染元数据参考段
  （情境标注之外的环节描述 / 直播间快照 / 记忆召回），由 Planner 固定在
  消息序列尾部追加（append-only，不插中间）
- 空段规则：有数据才渲染段（无占位文本）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple


@dataclass(slots=True)
class EnvironmentBlock:
    """直播间环境快照（动态段）。"""

    minute_bucket_ms: int  # 分钟级时刻（避免秒级毒化缓存）
    duration_so_far_ms: int
    current_stage_label: Optional[str] = None  # 当前环节（agenda_runtime.current）
    unread_summary: str = ""  # 未读行为流摘要
    key_changes: List[str] = field(default_factory=list)


@dataclass(slots=True)
class AssemblerInputs:
    """参考段输入材料集合（assemble 纯函数需要的所有数据）。

    对话材料（历史窗口/弹幕批）不在此处——它们走 canonical 映射的原生
    消息通道；本集合只收参考性质的元数据。
    """

    stage_descriptions: str = ""  # 环节描述
    environment: Optional[EnvironmentBlock] = None
    memory_recall_section: str = ""  # 记忆召回面（由 memory.recall 取得）


class PlannerAssembler:
    """Planner 参考段组装器

    组装顺序：环节描述 → 直播间快照 → 记忆召回。顺序由测试锁定
    （test_planner_context.py）；整段作为一条 user 消息固定在消息序列尾部，
    ReAct 循环的 assistant/tool 消息追加在其后（append-only，服务端 LLM
    前缀缓存可命中既有前缀）。

    空段规则：有数据才渲染段（无占位文本）——沿用工具段省略先例。
    """

    def assemble(self, inputs: AssemblerInputs) -> str:
        sections: List[Tuple[str, str]] = []

        if inputs.stage_descriptions:
            sections.append(("环节描述", inputs.stage_descriptions))

        env_body = _render_environment(inputs.environment)
        if env_body:
            sections.append(("直播间快照", env_body))

        if inputs.memory_recall_section:
            sections.append(("记忆召回", inputs.memory_recall_section))

        return "\n\n".join(f"## {title}\n{body}" for title, body in sections)


def _render_environment(env: Optional[EnvironmentBlock]) -> str:
    """渲染直播间快照（内部毫秒在渲染层转人类可读，数据载体不变）。"""
    if env is None:
        return ""
    lines: List[str] = []
    local_time = datetime.fromtimestamp(env.minute_bucket_ms / 1000).strftime("%Y-%m-%d %H:%M")
    lines.append(f"- 时刻: {local_time}")
    # 未开播/数据源缺失时为 0，渲染无信息量——跳过而非显示 "0 分钟"
    if env.duration_so_far_ms > 0:
        lines.append(f"- 已开播时长: {_format_duration_minutes(env.duration_so_far_ms)}")
    if env.current_stage_label:
        lines.append(f"- 当前环节: {env.current_stage_label}")
    if env.unread_summary:
        lines.append(f"- 未读摘要: {env.unread_summary}")
    if env.key_changes:
        lines.append("- 关键变化:")
        for change in env.key_changes:
            lines.append(f"  - {change}")
    return "\n".join(lines)


def _format_duration_minutes(ms: int) -> str:
    """毫秒时长 → 分钟粒度文本（决策场景秒级精度无益）。"""
    total_minutes = max(0, ms) // 60_000
    if total_minutes < 1:
        return "不足 1 分钟"
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    return f"{minutes} 分钟"


__all__ = [
    "AssemblerInputs",
    "EnvironmentBlock",
    "PlannerAssembler",
]
