"""DecisionPlan - reply_tool 与 Replyer 之间的表达意图契约。

设计原则：
- 这是主播 Agent **内部**数据结构，不跨 Agent 共享，因此放在
  ``src/agents/streamer/`` 下，而不是 ``src/modules/types/``。
- Planner（决策主体）的 ReAct 循环实际以 dict 形式流转决策产物
  （见 ``planner.py`` 的 outcome dict）；DecisionPlan 只在 reply_tool
  ↔ Replyer 边界构造——``tools/reply_tool.py`` 把工具调用参数适配为
  DecisionPlan，作为 ``Replyer.generate`` 的接口形态。
- 字段集最小化：只保留表达阶段真正需要的决策信息，不加投机性未来字段。
- ``extra="forbid"``：与代码库其他 Pydantic 模型一致，严格拒绝未知字段。

字段说明：
- ``should_reply``: 是否参与回复
- ``target``: 要回应的弹幕 message_id 或片段（None 表示无特定目标）
- ``topic_summary``: 当前话题摘要（来自态势缓存，给表达阶段提供上下文）
- ``reply_guidance``: 给 Replyer 的回复指引（语气、重点等）
- ``emotion`` / ``intensity``: Planner 决定的情绪与强度，上层组装发声数据使用
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from src.modules.types.emotion_vocab import Emotion


class DecisionPlan(BaseModel):
    """决策阶段产出 / 表达阶段消费的结构化计划。

    所有字段都有默认值，允许决策阶段在"不参与"场景下直接 ``DecisionPlan()``
    返回一个语义清晰的空计划。
    """

    model_config = ConfigDict(extra="forbid")

    should_reply: bool = False
    target: Optional[str] = None
    topic_summary: str = ""
    reply_guidance: str = ""
    emotion: Emotion = Emotion.NEUTRAL
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)


__all__ = ["DecisionPlan"]
