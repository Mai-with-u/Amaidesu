"""
事件 Payload 定义：tool.health.* 工具健康状态变更

定义 ``ToolHealthPayload`` 类（不绑定具体 ``tool.health.<name>`` 事件名）。

契约约定：
- 仅在状态跃迁时发射（熔断 open / 恢复 closed），不是每次工具调用都发
- emit 时使用具体名（``tool.health.<tool_name>``）
- 订阅者可一站式监听 ``tool.health.#`` 通配，按 ``tool_name`` 字段分发
"""

from typing import Literal

from pydantic import ConfigDict, Field

from src.modules.events.payloads.base import BasePayload
from src.modules.time_utils import now_ms


# 注意：ToolHealthPayload **不注册**到具体 ``tool.health.*`` 事件名
# （与 ToolResultPayload 同理：避免给通配族绑定具体名，污染 list_registered_events()）。
# 工具健康监控 emit 具体名 ``tool.health.<tool_name>`` 时使用本类作为 payload 类型；
# 订阅者通过 ``event_bus.on("tool.health.#", ...)`` 通配监听后，按 ``tool_name`` 字段分发。
class ToolHealthPayload(BasePayload):
    """
    工具健康状态变更事件 Payload（熔断/恢复跃迁广播）

    事件名：emit 时使用具体名 ``tool.health.<tool_name>``（如 ``tool.health.maicraft_speak``）；
    订阅者通常使用通配模式 ``event_bus.on("tool.health.#", ...)`` 一站式监听，
    handler 内按 ``tool_name`` 字段分发。

    发布者：ToolRegistry（熔断判定与探活恢复的统一出口）
    订阅者：Dashboard EventBroadcaster（转发 WebSocket 供前端展示健康徽标）

    Attributes:
        tool_name: 工具注册名（含 provider 前缀，如 "maicraft_speak"）
        provider: 工具所属 provider 标识（如 "maicraft"/"vision"）
        state: 跃迁后状态（open=已熔断摘除 / closed=恢复可用）
        failure_count: 触发跃迁时累计的连续失败次数
        last_error: 最近一次失败的错误信息（closed 跃迁时为空字符串）
        timestamp_ms: 跃迁时刻（Unix 毫秒）
    """

    tool_name: str = Field(..., description="工具注册名（含 provider 前缀）")
    provider: str = Field(default="", description="工具所属 provider 标识")
    state: Literal["open", "closed"] = Field(..., description="跃迁后状态（open=熔断摘除 / closed=恢复可用）")
    failure_count: int = Field(default=0, description="触发跃迁时的连续失败计数")
    last_error: str = Field(default="", description="最近一次失败的错误信息（closed 跃迁时为空）")
    timestamp_ms: int = Field(
        default_factory=lambda: now_ms(),
        description="跃迁时刻（Unix 毫秒）",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tool_name": "maicraft_speak",
                "provider": "maicraft",
                "state": "open",
                "failure_count": 3,
                "last_error": "ConnectionError: MCP server 未连接且重连失败",
                "timestamp_ms": 1706745600000,
            }
        }
    )


__all__ = ["ToolHealthPayload"]
