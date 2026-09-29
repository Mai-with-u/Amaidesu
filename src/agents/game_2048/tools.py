"""2048 Agent 工具面（game_2048_press / get_state）

主播与游戏之间的全部接口，名单全部 ``["streamer"]``（名单映射由
:func:`build_game_2048_visible_to` 提供，注册处声明）。

工具语义要点：
- ``press`` 是五个按键的单一入口：``key`` 取 up/down/left/right（滑动）
  或 restart（重开一局）——按键就是按键，不做第二个动作工具；
  **没有自动走子**（AI 代打等于开挂），每一步都由主播按出来
- 每次按键返回当前局势快照（board/score/moves/max_tile/over），
  终局后按键为成功但未生效的快照（``moved`` 语义经 over 字段表达）
- 工具本身不发事件——快照事件由 Agent 落子路径统一发射

对外全名由 ``ToolSpec.full_name`` 派生（``game_2048_<工具名>``），
工具名里不手写前缀。
"""

from __future__ import annotations

import time
from typing import Any, ClassVar, Dict, Iterable, List, Optional

from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolInvocation,
    ToolSpec,
)
from src.modules.tools.provider import BaseToolProvider

from .agent import Game2048Agent
from .board import MoveDirection


logger = get_logger("Game2048Tools")

# 提供者标识统一来源（ToolSpec.provider / 追溯用），避免字面量重复
PROVIDER_NAME = "game_2048"

# press.key 的合法取值（四个方向 + 重开）
PRESS_KEYS: tuple[str, ...] = ("up", "down", "left", "right", "restart")


# ---------------------------------------------------------------------------
# ToolSpec 工厂
# ---------------------------------------------------------------------------

# 快照返回形状（press / set_auto / get_state 共用同一键集）
_SNAPSHOT_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "board": {
            "type": "array",
            "description": "4x4 棋盘矩阵（0 表示空格，行优先自上而下）",
            "items": {"type": "array", "items": {"type": "integer"}},
        },
        "score": {"type": "integer", "description": "当前累计得分"},
        "moves": {"type": "integer", "description": "已完成的有效步数"},
        "max_tile": {"type": "integer", "description": "当前盘面最大块值"},
        "over": {"type": "boolean", "description": "是否终局（true 时按 restart 重开）"},
    },
    "required": ["board", "score", "moves", "max_tile", "over"],
}


def build_press_spec() -> ToolSpec:
    """``game_2048_press`` 工具规格——五键单一入口（四方向 + 重开）"""
    key_desc = "滑动方向（up/down/left/right）或 restart（重开一局）"
    return ToolSpec(
        name="press",
        description=(
            "操控 2048 棋盘：key 传 up/down/left/right 滑动合并方块（返回新"
            "局势），或传 restart 重开一局。何时用：想让主播亲自走一步"
            "（比如回应观众指挥）就传方向；棋局终局（over=true）或想换局"
            "就传 restart。无效移动（该方向推不动）不算失败，快照原样返回。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "enum": list(PRESS_KEYS),
                    "description": key_desc,
                },
            },
            "required": ["key"],
        },
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema=_SNAPSHOT_OUTPUT_SCHEMA,
    )


def build_get_state_spec() -> ToolSpec:
    """``game_2048_get_state`` 工具规格——局势快照只读查询"""
    return ToolSpec(
        name="get_state",
        description=(
            "查询 2048 当前局势快照：board（4x4 矩阵，0 是空格）/ score /"
            " moves / max_tile / over。只读、不落子。何时用：接手棋局时"
            "补看盘面，或解说前确认当前得分与最大块。"
        ),
        parameters_schema={"type": "object", "properties": {}, "required": []},
        kind="sync",
        provider=PROVIDER_NAME,
        output_schema=_SNAPSHOT_OUTPUT_SCHEMA,
    )


def build_game_2048_visible_to() -> Dict[str, List[str]]:
    """两个工具的可见名单映射（注册处声明）：全部仅主播可见。"""
    return {spec.full_name: ["streamer"] for spec in (build_press_spec(), build_get_state_spec())}


# ---------------------------------------------------------------------------
# Provider（注册到 ToolRegistry）
# ---------------------------------------------------------------------------


class Game2048ToolProvider(BaseToolProvider):
    """2048 Agent 工具 Provider（provider="game_2048"）

    持有 Agent 主体：动作（落子/重开）全部转发给 Agent 的公开方法，
    快照形状由 :meth:`Game2048Agent.get_state_snapshot` 唯一定义。

    注入模式（构造注入）：
        >>> provider = Game2048ToolProvider(agent=agent)
        >>> registry.register_provider(provider, visible_to=build_game_2048_visible_to())
    """

    # 工具分类（provider=提供者名、category=分组、agents.toml 段=配置地址，三者正交）
    category: ClassVar[str] = "game"

    def __init__(self, agent: Game2048Agent) -> None:
        self._agent = agent

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def list_tools(self) -> Iterable[ToolSpec]:
        return [build_press_spec(), build_get_state_spec()]

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        name = invocation.tool_name
        args: Dict[str, Any] = invocation.arguments or {}
        started_ms = int(time.time() * 1000)

        try:
            # 调用方使用的就是派生全名（game_2048_press 等），等值对照分发
            if name == "game_2048_press":
                return await self._invoke_press(args, started_ms)
            if name == "game_2048_get_state":
                return self._snapshot_result("game_2048_get_state", started_ms)
            return self._fail(name, started_ms, f"未知 Game2048 工具 '{name}'")
        except Exception as exc:  # noqa: BLE001 - 工具边界兜底，异常转失败结果
            logger.exception(f"Game2048 工具 '{name}' 执行异常: {type(exc).__name__}: {exc}")
            return self._fail(name, started_ms, f"{type(exc).__name__}: {exc}")

    # ==================================================================
    # game_2048_press：方向滑动 / 重开
    # ==================================================================

    async def _invoke_press(self, args: Dict[str, Any], started_ms: int) -> ToolExecutionResult:
        key = args.get("key")
        if not isinstance(key, str) or key not in PRESS_KEYS:
            return self._fail(
                "game_2048_press",
                started_ms,
                f"key 必须是 {list(PRESS_KEYS)} 之一，得到 {key!r}",
            )

        if key == "restart":
            await self._agent.restart()
            return self._snapshot_result("game_2048_press", started_ms, content="已重开一局")

        outcome = await self._agent.press(MoveDirection(key))
        if not outcome.moved and not self._agent.get_state_snapshot()["over"]:
            # 未终局却推不动：该方向确实无效，不算失败但提示换向
            notice = f"方向 {key} 推不动（盘面未变化），请换方向"
            return self._snapshot_result("game_2048_press", started_ms, content=notice, notice=notice)
        return self._snapshot_result(
            "game_2048_press",
            started_ms,
            content=self._agent.render_board(),
        )

    # ==================================================================
    # 内部辅助
    # ==================================================================

    def _snapshot_result(
        self,
        tool_name: str,
        started_ms: int,
        *,
        content: Optional[str] = None,
        notice: Optional[str] = None,
    ) -> ToolExecutionResult:
        """以 Agent 当前状态快照构造成功结果（键集由 get_state_snapshot 唯一定义）。"""
        snapshot = self._agent.get_state_snapshot()
        structured: Dict[str, Any] = dict(snapshot)
        if notice is not None:
            structured["notice"] = notice
        finished_ms = int(time.time() * 1000)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            content=content if content is not None else self._agent.render_board(),
            structured_content=structured,
            timestamp_ms=finished_ms,
            duration_ms=finished_ms - started_ms,
        )

    def _fail(self, tool_name: str, started_ms: int, reason: str) -> ToolExecutionResult:
        """失败结果：原因进 ``error_message`` 与结构化 ``error``，不发事件。"""
        finished_ms = int(time.time() * 1000)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=False,
            error_message=reason,
            structured_content={"error": reason},
            timestamp_ms=finished_ms,
            duration_ms=finished_ms - started_ms,
        )


__all__ = [
    "PRESS_KEYS",
    "PROVIDER_NAME",
    "Game2048ToolProvider",
    "build_game_2048_visible_to",
    "build_get_state_spec",
    "build_press_spec",
]
