"""
系统事件 Payload 定义

定义系统级事件的 Payload 类型。
- StartupPayload: 系统启动事件
- ShutdownPayload: 系统关闭事件
- ErrorPayload: 系统错误事件
"""

from typing import Any, Dict, Optional, List
from pydantic import BaseModel, Field, ConfigDict
import time

from src.core.events.payloads.base import BasePayload


class StartupPayload(BaseModel):
    """
    系统启动事件 Payload

    事件名：CoreEvents.CORE_STARTUP
    发布者：AmaidesuCore
    订阅者：任何需要在系统启动时初始化的组件

    表示核心系统已完成初始化并开始运行。
    """

    version: str = Field(..., description="版本号")
    start_time: float = Field(default_factory=time.time, description="启动时间戳")
    config_path: Optional[str] = Field(default=None, description="配置文件路径")
    debug_mode: bool = Field(default=False, description="是否调试模式")
    enabled_providers: Dict[str, list] = Field(default_factory=dict, description="启用的 Provider 列表")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "version": "0.1.0",
                "start_time": 1706745600.0,
                "config_path": "/path/to/config.toml",
                "debug_mode": False,
                "enabled_providers": {
                    "input": ["console_input", "bili_danmaku"],
                    "decision": ["maicore"],
                    "output": ["subtitle", "vts", "tts"],
                },
            }
        }
    )


class ShutdownPayload(BaseModel):
    """
    系统关闭事件 Payload

    事件名：CoreEvents.CORE_SHUTDOWN
    发布者：AmaidesuCore
    订阅者：任何需要在系统关闭时清理资源的组件

    表示核心系统正在关闭并清理资源。
    """

    reason: str = Field(default="user_initiated", description="关闭原因")
    shutdown_time: float = Field(default_factory=time.time, description="关闭时间戳")
    uptime_seconds: float = Field(..., description="运行时长（秒）")
    graceful: bool = Field(default=True, description="是否优雅关闭")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reason": "user_initiated",
                "shutdown_time": 1706749200.0,
                "uptime_seconds": 3600.0,
                "graceful": True,
            }
        }
    )


class ErrorPayload(BasePayload):
    """
    系统错误事件 Payload

    事件名：CoreEvents.CORE_ERROR
    发布者：任何组件
    订阅者：错误处理器、日志系统、监控组件

    表示系统中发生了需要关注的错误。
    """

    error_type: str = Field(..., description="错误类型（如异常类名）")
    error_message: str = Field(..., description="错误消息")
    source: str = Field(..., description="错误源（组件名称）")
    context: Dict[str, Any] = Field(default_factory=dict, description="上下文信息")
    timestamp: float = Field(default_factory=time.time, description="错误时间戳")
    recoverable: bool = Field(default=True, description="是否可恢复")
    stack_trace: Optional[str] = Field(default=None, description="堆栈跟踪")
    severity: str = Field(default="error", description="严重级别（warning, error, critical）")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error_type": "ConnectionError",
                "error_message": "无法连接到 MaiCore 服务",
                "source": "MaiCoreDecisionProvider",
                "context": {"endpoint": "ws://localhost:8000/ws", "timeout": 30},
                "timestamp": 1706745600.0,
                "recoverable": True,
                "severity": "error",
            }
        }
    )

    def _debug_fields(self) -> List[str]:
        """
        返回需要在 debug 日志中显示的字段名列表。

        Returns:
            字段名列表
        """
        return ["error_type", "error_message", "source"]

    @classmethod
    def from_exception(
        cls,
        exc: Exception,
        source: str,
        recoverable: bool = True,
        context: Optional[Dict[str, Any]] = None,
    ) -> "ErrorPayload":
        """
        从异常对象创建 Payload

        Args:
            exc: 异常对象
            source: 错误源（组件名称）
            recoverable: 是否可恢复
            context: 额外上下文信息

        Returns:
            ErrorPayload 实例
        """
        import traceback

        return cls(
            error_type=type(exc).__name__,
            error_message=str(exc),
            source=source,
            context=context or {},
            recoverable=recoverable,
            stack_trace=traceback.format_exc(),
            severity="critical" if not recoverable else "error",
        )
