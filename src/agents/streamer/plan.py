"""DecisionPlan - 主播 Agent 决策阶段输出 / 表达阶段输入契约。

设计原则：
- 这是主播 Agent **内部**数据结构，不跨 Agent 共享，因此放在
  ``src/agents/streamer/`` 下，而不是 ``src/modules/types/``。
- Planner（决策阶段）产出 DecisionPlan，Replyer（表达阶段）消费它生成实际发言。
- 字段集最小化：只保留表达阶段真正需要的决策信息，不加投机性未来字段。
- ``extra="forbid"``：与代码库其他 Pydantic 模型一致，严格拒绝未知字段。

字段说明：
- ``should_reply``: 是否参与回复（Planner 核心裁决）
- ``target``: 要回应的弹幕 message_id 或片段（None 表示无特定目标）
- ``reply_to``: 本轮回复所指向弹幕的 message_id（Planner 从批次编号中选取；
  与 live_chat 观众行 message_id 构成"回复了哪条"的可查询关联）
- ``topic_summary``: 当前话题摘要（来自态势缓存，给表达阶段提供上下文）
- ``reply_guidance``: 给 Replyer 的回复指引（语气、重点等）
- ``confidence``: 参与判断置信度 [0.0, 1.0]
- ``silent_reason``: 静默原因标记。``low_confidence``=LLM 想回但被本地低置信度
  裁决压制（should_reply 改写为 False）；None=无本地压制（LLM 自己决定沉默）。
  仅由 Planner 降级路径填写，LLM 原生输出不产生该字段。
- ``may_advance`` / ``need_more_time`` / ``branch_id``: Planner 顺带评估（仅 Agenda 激活时有意义）

兼容性：
- ``may_advance`` / ``need_more_time`` / ``branch_id`` / ``reply_to`` / ``silent_reason``
  全部带默认值，缺少这些字段的 JSON 仍可正常解析。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class DecisionPlan(BaseModel):
    """决策阶段产出 / 表达阶段消费的结构化计划。

    所有字段都有默认值，允许决策阶段在"不参与"场景下直接 ``DecisionPlan()``
    返回一个语义清晰的空计划。
    """

    model_config = ConfigDict(extra="forbid")

    should_reply: bool = False
    target: Optional[str] = None
    reply_to: Optional[str] = None
    topic_summary: str = ""
    reply_guidance: str = ""
    confidence: float = 0.0
    silent_reason: Optional[str] = None
    may_advance: bool = False
    need_more_time: bool = False
    branch_id: Optional[str] = None
    version: str = "2.0"


__all__ = ["DecisionPlan"]
