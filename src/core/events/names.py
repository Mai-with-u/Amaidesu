"""
事件名称常量定义（3域架构）

使用常量替代魔法字符串，提供 IDE 自动补全和重构支持。
"""


class CoreEvents:
    """核心事件名称常量（3域架构）"""

    # Input Domain: 输入域（数据采集 + 标准化）
    PERCEPTION_RAW_DATA_GENERATED = "perception.raw_data.generated"
    NORMALIZATION_MESSAGE_READY = "normalization.message_ready"

    # Decision Domain: 决策域（意图生成）
    DECISION_REQUEST = "decision.request"
    DECISION_INTENT_GENERATED = "decision.intent_generated"
    DECISION_RESPONSE_GENERATED = "decision.response_generated"  # MaiCore响应事件
    DECISION_PROVIDER_CONNECTED = "decision.provider.connected"
    DECISION_PROVIDER_DISCONNECTED = "decision.provider.disconnected"

    # Output Domain: 输出域（参数生成 + 渲染）
    EXPRESSION_PARAMETERS_GENERATED = "expression.parameters_generated"
    RENDER_COMPLETED = "render.completed"
    RENDER_FAILED = "render.failed"

    # 系统事件
    CORE_STARTUP = "core.startup"
    CORE_SHUTDOWN = "core.shutdown"
    CORE_ERROR = "core.error"
