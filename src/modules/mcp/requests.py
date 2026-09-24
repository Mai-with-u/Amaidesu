"""给 MCP 请求设置完整等待期限，并记录可关联的开始和结束事件。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

from src.modules.logging import get_logger

logger = get_logger("McpClient")


class McpRequestTimeout(TimeoutError):
    """只确认本地未收到回执，不推断远端是否已执行请求。"""

    def __init__(self, request_id: str, operation: str, timeout_ms: int, elapsed_ms: int) -> None:
        self.request_id = request_id
        self.operation = operation
        self.timeout_ms = timeout_ms
        self.elapsed_ms = elapsed_ms
        super().__init__(f"MCP {operation} 等待超过 {timeout_ms}ms；远端结果未知，请核实后再决定是否重试")


async def bounded_request(call: Callable[[], Awaitable[Any]], *, server: str, operation: str, timeout_ms: int) -> Any:
    """总期限覆盖发送、收包与解析；超时不自动重发，调用者中断继续向上传播。"""
    request_id = uuid4().hex[:12]
    started = time.monotonic()
    status = "failed"
    log = logger.info if operation.startswith("tools/call") else logger.debug
    label = f"MCP 请求 server={server} operation={operation} request_id={request_id}"
    # 不输出工具参数或请求头；关联编号足以定位哪一次调用没有结束。
    log(f"{label} 开始 timeout_ms={timeout_ms}")
    deadline = asyncio.timeout(timeout_ms / 1000)
    try:
        async with deadline:
            result = await call()
        status = "completed"
        return result
    except TimeoutError as exc:
        if not deadline.expired():
            raise  # 底层自己的超时保留原始分类，不能冒充本地总期限耗尽。
        status = "timeout"
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.warning(f"{label} 超时 elapsed_ms={elapsed_ms} timeout_ms={timeout_ms}", exc=exc)
        raise McpRequestTimeout(request_id, operation, timeout_ms, elapsed_ms) from exc
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    finally:
        log(f"{label} 结束 status={status} elapsed_ms={int((time.monotonic() - started) * 1000)}")
