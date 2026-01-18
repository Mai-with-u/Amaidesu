"""
Amaidesu 7层架构

层级顺序（数据流向）：
1. input (输入感知) → 采集原始数据
2. normalization (输入标准化) → 统一转换为文本
3. canonical (中间表示) → 构建CanonicalMessage
4. decision (决策) → DecisionProvider处理
5. understanding (表现理解) → 解析为Intent
6. expression (表现生成) → 生成RenderParameters
7. rendering (渲染呈现) → 并发渲染到多个设备
"""

__all__ = [
    "InputLayer",
    "NormalizationLayer",
    "CanonicalLayer",
    "DecisionLayer",
    "UnderstandingLayer",
    "ExpressionLayer",
    "RenderingLayer",
]
