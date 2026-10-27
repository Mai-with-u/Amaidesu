"""2048 Agent 工具面（game_2048_press / get_state）

主播与游戏之间的全部接口，名单全部 ``["streamer"]``（各 spec 的
``visible_to`` 字段声明）。

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

from typing import Any, ClassVar, Dict, Iterable, Optional, Protocol

from src.modules.agents.game_tools import GameToolProvider, fail_result, snapshot_result, spec_named_handlers
from src.modules.logging import get_logger
from src.modules.tools.models import (
    ToolExecutionResult,
    ToolSpec,
)

from .board import MoveDirection, MoveOutcome


logger = get_logger("Game2048Tools")

# 提供者标识统一来源（ToolSpec.provider / 追溯用），避免字面量重复
PROVIDER_NAME = "game_2048"

# press.key 的合法取值（四个方向 + 重开）
PRESS_KEYS: tuple[str, ...] = ("up", "down", "left", "right", "restart")

# 工具面使用说明（给主播的玩法）：2048 是遥控型游戏——每一步都是主播自己按出来的，
# 没有后台任务，也就没有委派、补充要求、任务号这些事。主播的 Planner 把它拼进
# 【当前游戏的玩法】，据此知道该直接调 press 而不是 framework_delegate。
STREAMER_GUIDE = (
    "2048 是遥控型游戏：棋盘在主播自己手里，每一步都由她亲自按出来——"
    "用 game_2048_press(key=up/down/left/right) 让她滑动一步，想换局就 press(key=restart)，"
    "用 game_2048_get_state 看当前盘面与得分。它没有后台任务：不要对它用 framework_delegate "
    "或 framework_prompt，观众指挥方向时直接按，按完就安排她讲盘面变化；终局会以游戏记录上报。"
)


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
        visible_to=["streamer"],
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
        visible_to=["streamer"],
        output_schema=_SNAPSHOT_OUTPUT_SCHEMA,
    )


# ---------------------------------------------------------------------------
# Provider（注册到 ToolRegistry）
# ---------------------------------------------------------------------------


class Game2048ToolHost(Protocol):
    """工具面对 Agent 的依赖切片（等价方法值）：落子/重开动作 + 快照与渲染出口。

    工具层只依赖本协议声明的四个方法，不感知 Agent 的其余接口（事件
    上报、里程碑等仍归 Agent 的落子路径）；Agent 本体结构性满足本协议，
    两模块互不 import（依赖方向：agent → tools 单向）。
    """

    async def press(self, direction: MoveDirection) -> MoveOutcome:
        """按一个方向键（事件发射归 Agent 的落子路径）。"""
        ...

    async def restart(self) -> None:
        """重开一局。"""
        ...

    def get_state_snapshot(self) -> Dict[str, Any]:
        """导出状态快照（工具 structured_content 的键集唯一来源）。"""
        ...

    def render_board(self) -> str:
        """渲染人类/LLM 可读的文本棋盘（工具 content 默认来源）。"""
        ...


class Game2048ToolProvider(GameToolProvider):
    """2048 Agent 工具 Provider（provider="game_2048"）

    依赖收窄到 :class:`Game2048ToolHost` 切片：动作（落子/重开）经切片
    方法转发，快照形状由 :meth:`Game2048ToolHost.get_state_snapshot`
    唯一定义。分发、计时与异常转结果走共享管道（:class:`GameToolProvider`）。

    注入模式（构造注入）：
        >>> provider = Game2048ToolProvider(agent=agent)
        >>> registry.register_provider(provider)
    """

    # 日志前缀（共享管道的异常日志区分用）
    log_label: ClassVar[str] = "Game2048"

    def __init__(self, agent: Game2048ToolHost) -> None:
        self._agent = agent

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def list_tools(self) -> Iterable[ToolSpec]:
        return [build_press_spec(), build_get_state_spec()]

    def guide_for(self, agent_name: str) -> str:
        """玩法说明：两个工具只对主播可见，读者固定是主播，不按读者区分。"""
        del agent_name
        return STREAMER_GUIDE

    def tool_handlers(self) -> Dict[str, Any]:
        """对外全名 → 处理器（键从 spec 派生，不手拼前缀）。"""
        return spec_named_handlers(
            [
                (build_press_spec(), self._invoke_press),
                (build_get_state_spec(), self._invoke_get_state),
            ]
        )

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

    async def _invoke_get_state(self, args: Dict[str, Any], started_ms: int) -> ToolExecutionResult:
        del args
        return self._snapshot_result("game_2048_get_state", started_ms)

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
        """以快照构造成功结果：content 缺省回落文本棋盘渲染。"""
        return snapshot_result(
            tool_name,
            started_ms,
            self._agent.get_state_snapshot(),
            content=content if content is not None else self._agent.render_board(),
            notice=notice,
        )

    def _fail(self, tool_name: str, started_ms: int, reason: str) -> ToolExecutionResult:
        """失败结果（构造细节见共享层 ``fail_result``）。"""
        return fail_result(tool_name, started_ms, reason)


__all__ = [
    "PRESS_KEYS",
    "PROVIDER_NAME",
    "STREAMER_GUIDE",
    "Game2048ToolHost",
    "Game2048ToolProvider",
    "build_get_state_spec",
    "build_press_spec",
]
