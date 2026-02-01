"""
事件名称常量定义

使用常量替代魔法字符串，提供 IDE 自动补全和重构支持。
"""


class CoreEvents:
    """核心事件名称常量"""

    # Layer 1: 输入感知
    PERCEPTION_RAW_DATA_GENERATED = "perception.raw_data.generated"

    # Layer 2: 输入标准化
    NORMALIZATION_TEXT_READY = "normalization.text.ready"

    # Layer 4: 决策层
    DECISION_REQUEST = "decision.request"
    DECISION_RESPONSE_GENERATED = "decision.response_generated"
    DECISION_PROVIDER_CONNECTED = "decision.provider.connected"
    DECISION_PROVIDER_DISCONNECTED = "decision.provider.disconnected"

    # Layer 5: 表现理解
    UNDERSTANDING_INTENT_GENERATED = "understanding.intent_generated"

    # Layer 6: 表现生成
    EXPRESSION_PARAMETERS_GENERATED = "expression.parameters_generated"

    # Layer 7: 渲染呈现
    RENDER_COMPLETED = "render.completed"
    RENDER_FAILED = "render.failed"

    # 系统事件
    CORE_STARTUP = "core.startup"
    CORE_SHUTDOWN = "core.shutdown"
    CORE_ERROR = "core.error"


class PluginEventPrefix:
    """插件事件前缀工具"""

    @staticmethod
    def create(plugin_name: str, event_name: str) -> str:
        """
        创建插件事件名称

        Args:
            plugin_name: 插件名称（snake_case）
            event_name: 事件名称

        Returns:
            完整的插件事件名称

        Example:
            >>> PluginEventPrefix.create("bili_danmaku", "gift_received")
            "plugin.bili_danmaku.gift_received"
        """
        return f"plugin.{plugin_name}.{event_name}"
