"""
事件名称常量定义（3域架构）

使用常量替代魔法字符串，提供 IDE 自动补全和重构支持。

命名规范:
- 格式: {domain}.{component}.{action}
- 分隔符: 统一使用点号 (.)
- 常量命名: 全大写 + 下划线

详细规范请参考: docs/architecture/event-naming-convention.md
"""


class CoreEvents:
    """核心事件名称常量（3域架构）"""

    # ========== Input Domain: 输入域 ==========
    # 标准化消息就绪事件（由 Input Domain 发布，Decision Domain 订阅）
    INPUT_MESSAGE_READY = "input.message.ready"

    # ========== Decision Domain: 决策域 ==========
    # 意图生成完成事件（由 Decision Domain 发布，Output Domain 订阅）
    DECISION_INTENT_GENERATED = "decision.intent.generated"

    # Provider 连接状态事件
    DECISION_PROVIDER_CONNECTED = "decision.provider.connected"
    DECISION_PROVIDER_DISCONNECTED = "decision.provider.disconnected"

    # ========== Output Domain: 输出域 ==========
    # 过滤后意图就绪事件（由 OutputProviderManager 发布，OutputProviders 订阅）
    OUTPUT_INTENT_READY = "output.intent.ready"

    # OBS 控制事件
    OUTPUT_OBS_SEND_TEXT = "output.obs.send_text"
    OUTPUT_OBS_SWITCH_SCENE = "output.obs.switch_scene"
    OUTPUT_OBS_SET_SOURCE_VISIBILITY = "output.obs.set_source_visibility"

    # 远程流事件
    OUTPUT_REMOTE_STREAM_REQUEST_IMAGE = "output.remote_stream.request_image"

    @classmethod
    def get_all_events(cls) -> tuple[str, ...]:
        """
        获取所有定义的事件名

        通过反射自动收集所有符合命名规范的事件常量。
        筛选条件：
        1. 不以下划线开头（排除私有属性）
        2. 值为字符串
        3. 值为小写（排除类名等）
        4. 包含点号（事件特征）
        """
        return tuple(
            value
            for name, value in vars(cls).items()
            if not name.startswith("_") and isinstance(value, str) and value.islower() and ("." in value)
        )

    # 所有事件名集合（用于事件验证等）
    # 模块加载时自动更新
    ALL_EVENTS = ()  # 占位符，模块末尾会被更新


# 在类定义后，模块级别自动更新 ALL_EVENTS
CoreEvents.ALL_EVENTS = CoreEvents.get_all_events()
