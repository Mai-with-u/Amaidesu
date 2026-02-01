"""
事件名称常量定义（5层架构）

使用常量替代魔法字符串，提供 IDE 自动补全和重构支持。
"""


class CoreEvents:
    """核心事件名称常量（5层架构）"""

    # Layer 1-2: 输入层（Input + Normalization）
    PERCEPTION_RAW_DATA_GENERATED = "perception.raw_data.generated"
    NORMALIZATION_MESSAGE_READY = "normalization.message_ready"

    # Layer 3: 决策层（Decision）
    DECISION_REQUEST = "decision.request"
    DECISION_INTENT_GENERATED = "decision.intent_generated"  # 5层架构：直接返回Intent
    DECISION_PROVIDER_CONNECTED = "decision.provider.connected"
    DECISION_PROVIDER_DISCONNECTED = "decision.provider.disconnected"

    # 已废弃（7层架构时期）
    # UNDERSTANDING_INTENT_GENERATED = "understanding.intent_generated"  # 已废弃：Layer 3直接生成Intent

    # Layer 4-5: 参数和渲染（Parameters + Rendering）
    EXPRESSION_PARAMETERS_GENERATED = "expression.parameters_generated"
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
