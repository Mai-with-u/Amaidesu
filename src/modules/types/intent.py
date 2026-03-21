"""跨域共享的 Intent 相关类型

这些类型被 Input/Decision/Output Domain 共享，
因此放在 src/modules/types/ 中避免循环依赖。

版本：自然语言版
- emotion/action 使用自然语言字符串
- 移除了类型化的枚举和类，使用更灵活的字符串
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# 向后兼容 type alias - 旧代码仍在导入这些类型
# 新代码应使用字符串直接赋值
EmotionType = str  # type: ignore[misc]
"""情感类型 - 自然语言字符串，如 "害羞"、"开心"、"生气" """

ActionType = str  # type: ignore[misc]
"""动作类型 - 自然语言字符串，如 "脸红并挥手"、"比心" """


class IntentMetadata(BaseModel):
    """意图元数据"""

    source_id: str = Field(description="来源ID")
    decision_time: int = Field(description="13位时间戳")
    parser_type: Optional[str] = Field(default=None, description="解析器类型（如 'llm', 'maicraft'）")
    llm_model: Optional[str] = Field(default=None, description="使用的 LLM 模型")
    replay_count: Optional[int] = Field(default=None, description="重放次数")
    extra: dict = Field(default_factory=dict, description="额外信息")


class Intent(BaseModel):
    """
    决策意图 - 平台无关

    核心职责：
    - 表示AI的决策意图
    - 使用自然语言描述情感、动作、回复
    - 作为 Output Domain 的输入

    字段说明：
    - emotion: 自然语言情感描述，如 "害羞"、"开心"、"生气"
    - action: 自然语言动作描述，如 "脸红并挥手"、"比心"
    - speech: AI 要说的话
    - context: 简短上下文信息
    - metadata: 元数据
    """

    model_config = ConfigDict(populate_by_name=True)

    emotion: Optional[str] = Field(default=None, description="自然语言情感描述，如 '害羞'、'开心'、'生气'")
    action: Optional[str] = Field(default=None, description="自然语言动作描述，如 '脸红并挥手'、'比心'")
    speech: Optional[str] = Field(default=None, description="AI 要说的话")
    context: Optional[str] = Field(default=None, description="简短上下文")
    metadata: IntentMetadata = Field(..., description="意图元数据")


__all__ = [
    # 类型别名（向后兼容）
    "EmotionType",
    "ActionType",
    # 核心类
    "IntentMetadata",
    "Intent",
]
