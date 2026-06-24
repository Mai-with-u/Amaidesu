"""
事件名称常量定义（3阶段架构）

使用常量替代魔法字符串，提供 IDE 自动补全和重构支持。

命名规范:
- 格式: {phase}.{entity}.{verb}
- 分隔符: 统一使用点号 (.)
- 常量命名: 全大写 + 下划线
- 动词链（按阶段流转）: received → generated → dispatched
  - Input 阶段:   ...received  （Collector 接收外部数据）
  - Decision 阶段: ...generated （Decider 生成意图）
  - Output 阶段:  ...dispatched（OutputHandler 派发渲染）
- 前缀去重: 阶段名已隐含组件类型，无需重复
  - ✗ decision.decider.connected → ✓ decision.connected
  - ✗ input.collector.connected  → ✓ input.connected

详细规范请参考: docs/architecture/event-naming-convention.md
"""


class CoreEvents:
    """核心事件名称常量（3阶段架构）"""

    # ========== Core: 核心系统事件 ==========
    # 核心启动/关闭事件
    CORE_STARTUP = "core.startup"
    CORE_SHUTDOWN = "core.shutdown"
    CORE_ERROR = "core.error"

    # ========== Input 阶段: 输入阶段 ==========
    # 标准化消息接收事件（由 Input 阶段发布，Decision 阶段订阅）
    INPUT_MESSAGE_RECEIVED = "input.message.received"

    # 组件 连接状态事件（去 collector 前缀：阶段名已隐含组件类型）
    INPUT_CONNECTED = "input.connected"
    INPUT_DISCONNECTED = "input.disconnected"

    # ========== Decision 阶段: 决策阶段 ==========
    # 意图生成完成事件（由 Decision 阶段发布，Output 阶段订阅）
    DECISION_INTENT_GENERATED = "decision.intent.generated"

    # 组件 连接状态事件（去 decider 前缀：阶段名已隐含组件类型）
    DECISION_CONNECTED = "decision.connected"
    DISCONNECTED = "decision.disconnected"

    # ========== Output 阶段: 输出阶段 ==========
    # 过滤后意图派发事件（由 OutputHandlerManager 发布，OutputHandlers 订阅）
    OUTPUT_INTENT_DISPATCHED = "output.intent.dispatched"

    # 组件 连接状态事件（DEPRECATED 兼容垫片：实际未发射，保留以兼容外部代码如 broadcaster.py）
    OUTPUT_HANDLER_CONNECTED = "output.handler.connected"  # DEPRECATED: not emitted, retained for backward compat
    OUTPUT_HANDLER_DISCONNECTED = "output.handler.disconnected"  # DEPRECATED: not emitted, retained for backward compat

    # OBS 控制事件（统一入口，所有 OBS 操作通过 payload.command 区分）
    OUTPUT_OBS_COMMAND = "output.obs.command"

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
