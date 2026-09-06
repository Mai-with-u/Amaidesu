"""maicraft slim 适配层（唯一知道 maicraft_* 工具名 / 参数 / 返回结构的地方）

架构约定（解耦 maicraft，MCP 接口快速迭代）：
- 领域核心（MinecraftAgent/state/tools）**零 maicraft 知识**，只依赖本适配器的
  语义方法：perceive / execute / poll_task / resolve_decision
- 本文件是唯一接触 ``maicraft_*`` 工具名与参数结构处——maicraft 接口变更只改这里
- **不建模游戏数据**（health/food/坐标）：maicraft 返回原样文本/结构给 LLM，
  不翻译成领域模型；本适配器只做"调用转发 + 基本错误归一"
- 无 maicraft 可用时：所有方法返回失败结果（Agent 循环降级，不阻断启动）

maicraft 工具集（经 ToolRegistry，provider="maicraft"）：
- ``maicraft_perceive``（8 视图：situation/surroundings/abilities/tasks/attention/...）
- ``maicraft_plan``（语义目标 → 计划，不执行）
- ``maicraft_execute``（执行计划 → task_id，异步）
- ``maicraft_task``（get/list/pause/resume/cancel/answer；waiting_for_decision 应答）
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.modules.logging import get_logger
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry

logger = get_logger("MinecraftMaicraftAdapter")


class MaicraftAdapter:
    """maicraft 语义操作适配器（经 ToolRegistry 转发，永远不抛异常）

    Attributes:
        tool_registry: 转发目标（MCP 工具已注册于此，provider="maicraft"）
        server_id: MCP 侧 server 别名（作为 maicraft 调用参数透传）
    """

    def __init__(self, tool_registry: Optional[ToolRegistry], server_id: str = "") -> None:
        self._registry = tool_registry
        self._server_id = server_id

    # ---- 感知 ----

    async def perceive(self, view: str = "situation", **extra: Any) -> Dict[str, Any]:
        """调用 ``maicraft_perceive``；返回原样结构（不翻译）。失败→{'ok': False}。"""
        return await self._call(
            "maicraft_perceive",
            {"view": view, "server_id": self._server_id, **extra},
        )

    # ---- 执行路径 ----

    async def execute(self, goal: Dict[str, Any]) -> Dict[str, Any]:
        """调用 ``maicraft_execute``（语义目标 → task_id）。"""
        return await self._call("maicraft_execute", {"goal": goal, "server_id": self._server_id})

    async def plan(self, goal: Dict[str, Any]) -> Dict[str, Any]:
        """调用 ``maicraft_plan``（编译语义目标为计划，不执行）。"""
        return await self._call("maicraft_plan", {"goal": goal, "server_id": self._server_id})

    async def poll_task(self, task_id: str) -> Dict[str, Any]:
        """调用 ``maicraft_task``（get：查询任务状态）。"""
        return await self._call("maicraft_task", {"task_id": task_id, "server_id": self._server_id})

    async def answer_decision(self, task_id: str, decision_id: str, choice: str, **details: Any) -> Dict[str, Any]:
        """调用 ``maicraft_task``（answer：应答 waiting_for_decision）。"""
        return await self._call(
            "maicraft_task",
            {
                "task_id": task_id,
                "action": "answer",
                "decision_id": decision_id,
                "choice": choice,
                "details": details,
                "server_id": self._server_id,
            },
        )

    # ---- 私有 ----

    async def _call(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """经 ToolRegistry 转发单次调用；永远不抛异常（失败→{'ok': False, 'error': ...}）。"""
        if self._registry is None:
            return {"ok": False, "error": "tool_registry 未注入"}
        try:
            result = await self._registry.invoke(
                ToolInvocation(tool_name=tool_name, arguments=arguments, source="minecraft")
            )
        except Exception as exc:  # noqa: BLE001 - 边界兜底
            logger.error(f"maicraft '{tool_name}' 调用异常: {type(exc).__name__}: {exc}")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        if result.success:
            content = result.structured_content if isinstance(result.structured_content, dict) else {}
            return {**(content or {}), "ok": True}
        return {"ok": False, "error": result.error_message or "maicraft 调用失败"}


__all__ = ["MaicraftAdapter"]
