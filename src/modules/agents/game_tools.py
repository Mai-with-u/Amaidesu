"""游戏工具 Provider 公共执行管道。

三个游戏 Agent 的工具面（minecraft / text_adv / game_2048）共用同一条
执行管道：按工具全名等值分发、毫秒计时、异常转 :class:`ToolExecutionResult`
（永不外抛）、快照/失败/感知失败结果的构造口径。管道收在这里一处，
各包 Provider 只声明工具规格（list_tools）与处理器表（tool_handlers）。

两种处理器返回形态：
- 直接返回 :class:`ToolExecutionResult`（text_adv / game_2048）；
- 返回结构化 dict（minecraft）——经 :func:`run_dict_tool` 包装为成功
  结果，``ValueError`` 按业务拒绝降级（参数写错由调用方纠正，不熔断
  摘除工具），其余异常转执行失败。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, ClassVar, Dict, Optional

from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation, ToolSpec
from src.modules.tools.provider import BaseToolProvider
from src.modules.utils.time_utils import now_ms

__all__ = [
    "GameToolHandler",
    "GameToolProvider",
    "capture_failed_result",
    "emit_game_error",
    "fail_result",
    "run_dict_tool",
    "snapshot_result",
]

logger = get_logger("GameToolProvider")

# 工具处理器统一签名：入参（调用参数、计时起点毫秒）→ 工具结果
GameToolHandler = Callable[[Dict[str, Any], int], Awaitable[ToolExecutionResult]]


def fail_result(tool_name: str, started_ms: int, reason: str) -> ToolExecutionResult:
    """失败结果：原因进 ``error_message`` 与结构化 ``error``，不发事件。"""
    finished_ms = now_ms()
    return ToolExecutionResult(
        tool_name=tool_name,
        success=False,
        error_message=reason,
        structured_content={"error": reason},
        timestamp_ms=finished_ms,
        duration_ms=finished_ms - started_ms,
    )


def business_fail_result(tool_name: str, started_ms: int, reason: str) -> ToolExecutionResult:
    """业务拒绝结果（参数写错等由调用方纠正的失败）：不算执行故障。"""
    result = fail_result(tool_name, started_ms, reason)
    result.failure_kind = "business"
    return result


def capture_failed_result(tool_name: str, started_ms: int, detail: Optional[str]) -> ToolExecutionResult:
    """感知失败的成功形态：``success=True`` + 空 text + 结构化 error，不发事件。"""
    reason = detail or "未知原因"
    logger.warning(f"{tool_name} 感知失败: {reason}")
    finished_ms = now_ms()
    return ToolExecutionResult(
        tool_name=tool_name,
        success=True,
        content="",
        structured_content={"error": f"capture_failed: {reason}"},
        timestamp_ms=finished_ms,
        duration_ms=finished_ms - started_ms,
    )


def snapshot_result(
    tool_name: str,
    started_ms: int,
    snapshot: Dict[str, Any],
    *,
    content: str = "",
    notice: Optional[str] = None,
) -> ToolExecutionResult:
    """以状态快照构造成功结果（structured_content 键集由快照导出方唯一定义）。"""
    structured: Dict[str, Any] = dict(snapshot)
    if notice is not None:
        structured["notice"] = notice
    finished_ms = now_ms()
    return ToolExecutionResult(
        tool_name=tool_name,
        success=True,
        content=content,
        structured_content=structured,
        timestamp_ms=finished_ms,
        duration_ms=finished_ms - started_ms,
    )


async def run_dict_tool(
    tool_name: str,
    started_ms: int,
    run: Callable[[], Awaitable[Dict[str, Any]]],
    *,
    log_label: str,
) -> ToolExecutionResult:
    """执行返回结构化 dict 的工具处理器并包装为结果（minecraft 形态）。

    异常口径：``ValueError`` = 调用方参数问题，按业务拒绝降级（警告级
    日志、不摘除工具）；其余异常执行失败兜底。返回的 dict 原样进
    ``structured_content``，不另行文本化。
    """
    try:
        result = await run()
    except ValueError as exc:
        logger.warning(f"{log_label} 工具 '{tool_name}' 请求被拒绝: {exc}", exc=True)
        return business_fail_result(tool_name, started_ms, f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - 工具边界兜底，异常转失败结果
        logger.exception(f"{log_label} 工具 '{tool_name}' 执行失败: {type(exc).__name__}: {exc}")
        return fail_result(tool_name, started_ms, f"{type(exc).__name__}: {exc}")
    return ToolExecutionResult(
        tool_name=tool_name,
        success=True,
        structured_content=result,
        duration_ms=now_ms() - started_ms,
    )


async def emit_game_error(
    emit_event: Callable[[str, GamePayload], Awaitable[None]],
    game_name: str,
    message: str,
) -> None:
    """发射 ``game.error``（游戏 Agent 异常路径共用的上行口径）。"""
    payload = GamePayload(game=game_name, event_type="error", message=message)
    await emit_event(CoreEvents.GAME_ERROR, payload)


class GameToolProvider(BaseToolProvider):
    """游戏工具 Provider 基类：全名分发 + 计时 + 异常转结果的公共管道。

    子类只声明三件事：
    - ``provider 名``（``name`` 属性）
    - 工具规格（``list_tools``）
    - 处理器表（``tool_handlers``：对外全名 → 处理器，处理器签名见
      :data:`GameToolHandler`）

    未知工具按 :meth:`unknown_tool_reason` 回执失败；处理器抛出的异常
    统一转失败结果（永不外抛）。返回 dict 的处理器（minecraft 形态）
    在子类的 ``tool_handlers`` 里经 :func:`run_dict_tool` 包装。
    """

    # 工具分类（provider=提供者名、category=分组、tools.toml 段=配置地址，三者正交）
    category: ClassVar[str] = "game"

    # 日志前缀（区分三个游戏包的工具日志，如 "TextAdv" / "Game2048" / "Minecraft"）
    log_label: ClassVar[str] = "Game"

    def tool_handlers(self) -> Dict[str, GameToolHandler]:
        """处理器表：对外全名 → 处理器——子类必须实现。"""
        raise NotImplementedError("GameToolProvider 子类必须实现 tool_handlers()")

    def unknown_tool_reason(self, tool_name: str) -> str:
        """未知工具的回执文案（子类可覆写补充出路提示）。"""
        return f"未知 {self.log_label} 工具 '{tool_name}'"

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        name = invocation.tool_name
        args: Dict[str, Any] = dict(invocation.arguments or {})
        started_ms = now_ms()

        # 调用方使用的就是派生全名（<provider>_<工具名>），等值对照分发
        handler = self.tool_handlers().get(name)
        if handler is None:
            return fail_result(name, started_ms, self.unknown_tool_reason(name))
        try:
            return await handler(args, started_ms)
        except Exception as exc:  # noqa: BLE001 - 工具边界兜底，异常转失败结果
            logger.exception(f"{self.log_label} 工具 '{name}' 执行异常: {type(exc).__name__}: {exc}")
            return fail_result(name, started_ms, f"{type(exc).__name__}: {exc}")


def spec_named_handlers(pairs: list[tuple[ToolSpec, GameToolHandler]]) -> Dict[str, GameToolHandler]:
    """按 spec 的派生全名建处理器表（键不手拼前缀，从 ToolSpec 派生）。"""
    return {spec.full_name: handler for spec, handler in pairs}
