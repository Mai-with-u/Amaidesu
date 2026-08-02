"""DecisionPlan - 双阶段决策器（AmaidesuDecider）的 Stage 1 输出 / Stage 2 输入契约。

设计原则：
- 这是 decision 阶段**内部**的数据结构，不跨阶段共享，因此放在 decider 文件夹下，
  而不是 `src/modules/types/`。
- Stage 1（节奏门控 + 内容门控）产出 DecisionPlan，Stage 2（Replyer）消费它生成最终 Intent。
- 字段集最小化：只保留 Stage 2 真正需要的决策信息，不加投机性的未来字段。
- `extra="forbid"`：与代码库其他 Pydantic 模型保持一致（Intent / NormalizedMessage 等），
  严格拒绝未知字段，避免拼写错误被静默吞掉。

字段说明：
- `should_reply`: 是否参与回复（Stage 1 的核心裁决）
- `target`: 要回应的弹幕 message_id 或片段（None 表示无特定目标）
- `topic_summary`: 当前话题摘要（来自态势缓存，给 Stage 2 提供上下文）
- `reply_guidance`: 给 Replyer 的回复指引（语气、重点等）
- `confidence`: 参与判断置信度 [0.0, 1.0]
- `version`: schema 版本号，未来字段迁移时用于兼容性判断
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class DecisionPlan(BaseModel):
    """Stage 1 决策产出 / Stage 2 消费的结构化计划。

    所有字段都有默认值，允许 Stage 1 在"不参与"场景下直接 `DecisionPlan()`
    返回一个语义清晰的空计划。
    """

    model_config = ConfigDict(extra="forbid")

    should_reply: bool = False
    target: Optional[str] = None
    topic_summary: str = ""
    reply_guidance: str = ""
    confidence: float = 0.0
    version: str = "1.0"


__all__ = ["DecisionPlan"]
