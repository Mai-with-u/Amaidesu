"""DecisionPlan - 主播 Agent Stage 1 输出 / Stage 2 输入契约（Wave 6 / §1.4）。

设计原则（与原 ``plan.py`` 保持一致）：
- 这是主播 Agent **内部**数据结构，不跨 Agent 共享，因此放在
  ``src/agents/streamer/`` 下，而不是 ``src/modules/types/``。
- Planner（Stage 1）产出 DecisionPlan，Replyer（Stage 2）消费它生成实际发言。
- 字段集最小化：只保留 Stage 2 真正需要的决策信息，不加投机性未来字段。
- ``extra="forbid"``：与代码库其他 Pydantic 模型一致，严格拒绝未知字段。

字段说明：
- ``should_reply``: 是否参与回复（Planner 核心裁决）
- ``target``: 要回应的弹幕 message_id 或片段（None 表示无特定目标）
- ``topic_summary``: 当前话题摘要（来自态势缓存，给 Stage 2 提供上下文）
- ``reply_guidance``: 给 Replyer 的回复指引（语气、重点等）
- ``confidence``: 参与判断置信度 [0.0, 1.0]
- ``may_advance`` / ``need_more_time`` / ``branch_id``: Planner 顺带评估（仅 Agenda 激活时有意义）

兼容性：
- ``may_advance`` / ``need_more_time`` / ``branch_id`` 全部带默认值，旧 Planner 输出
  （无这些字段）的 JSON 仍可正常解析。
- ``version`` 由 "1.1" 升到 "2.0"（Wave 6：Agent 重命名）。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class DecisionPlan(BaseModel):
    """Stage 1 决策产出 / Stage 2 消费的结构化计划。

    所有字段都有默认值，允许 Stage 1 在"不参与"场景下直接 ``DecisionPlan()``
    返回一个语义清晰的空计划。
    """

    model_config = ConfigDict(extra="forbid")

    should_reply: bool = False
    target: Optional[str] = None
    topic_summary: str = ""
    reply_guidance: str = ""
    confidence: float = 0.0
    may_advance: bool = False
    need_more_time: bool = False
    branch_id: Optional[str] = None
    version: str = "2.0"


__all__ = ["DecisionPlan"]
