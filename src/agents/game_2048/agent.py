"""Game2048Agent —— 2048 棋局宿主（按键驱动，零 LLM、零自主循环）

归类：游戏 Agent（命令驱动）。棋局即"办事过程"：从发牌到终局是一个
有状态、有起止的过程，由本 Agent 持有；主播经 ``game_2048_press`` 工具
按键驱动推进（每步方向由主播决定，本 Agent 只忠实地执行规则、维护
局面、把变化广播出去）。**没有自动走子**——AI 代打等于开挂，主持人的
手就是主播的手。

职责边界：
- 规则全部进程内：棋盘是 ``Board2048``（同包 board.py），每次按键按
  经典规则迁移一次状态——LLM 不参与、代码策略也不参与
- 每步有效落子发 ``game.state.changed``（高频快照，呈现流）；首次合成出
  新档位大块发 ``game.milestone``；终局发 ``game.report``（delivery 语义，
  主播据此解说）；按键驱动失败走工具结果，不污染事件流
- 工具绝不发事件：快照事件统一由本 Agent 的落子路径发射
"""

from __future__ import annotations

import random
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from src.modules.agents.base import BaseAgent
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GameBoardStatePayload, GamePayload
from src.modules.logging import get_logger
from src.modules.tools import ToolSpec
from src.modules.tools.registry import ToolRegistry

from .board import Board2048, MoveDirection, MoveOutcome
from .config import Game2048Config


__all__ = [
    "Game2048Agent",
    "build_game_2048_agent",
]

# 操作历史环缓冲容量（条）——快照 payload 的 history 字段截断口径
HISTORY_MAX = 16


class Game2048Agent(BaseAgent):
    """2048 游戏 Agent（棋局宿主）。

    - 棋盘状态由本 Agent 持有；工具侧经 ``press`` / ``restart`` / ``get_state``
      三个公开方法触达，绝不直接改棋盘
    - ``rng`` 构造注入（固定种子复现整局）
    - 事件上报走基类 ``emit_event``（event_bus 为 None 时静默跳过）
    """

    # ----- 元数据 -----
    name = "game_2048"
    description = "2048 游戏 Agent（棋局宿主，主播按键驱动，无自动走子）"

    # ----- 事件族声明 -----
    emits_events = (
        CoreEvents.GAME_STATE_CHANGED,
        CoreEvents.GAME_MILESTONE,
        CoreEvents.GAME_REPORT,
        CoreEvents.GAME_ERROR,
    )

    def __init__(
        self,
        config: Game2048Config,
        *,
        tool_registry: Optional[ToolRegistry] = None,
        event_bus: Optional[EventBus] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        """构造注入全部依赖。

        Args:
            config: Game2048Config 实例（里程碑档位等运行参数）
            tool_registry: 可选 ToolRegistry（启动期注册工具面；未注入时
                工具面不注册）
            event_bus: 可选 EventBus（game.* 事件发射）
            rng: 棋盘随机源（新块生成）；注入固定种子实例可复现整局
        """
        super().__init__(event_bus=event_bus)
        self.typed_config = config
        self._tool_registry = tool_registry
        # 函数内 import：tools 模块反向引用本模块的 Game2048Agent（循环 import 规避）
        from .tools import Game2048ToolProvider

        self._tool_provider: "Game2048ToolProvider" = Game2048ToolProvider(agent=self)

        # 内容状态：棋盘即状态（不设独立 state 对象——快照导出直接走棋盘视图）
        self._board = Board2048(rng=rng)
        self._over_reported: bool = False
        self._milestone_announced: set[int] = set()
        self._history: Deque[str] = deque(maxlen=HISTORY_MAX)

        self._logger = get_logger("Game2048Agent")
        self._logger.info("Game2048Agent 已构造（等待主播按键开局）")

    # ==================================================================
    # 落子（主播按键驱动）
    # ==================================================================

    async def press(self, direction: MoveDirection) -> MoveOutcome:
        """按一个方向键。

        首次按键自动开局；终局后按键为静默空操作；每步有效落子发
        ``game.state.changed``（携带本次方向与操作历史），首次合成新档位
        大块追加 ``game.milestone``，终局补 ``game.report``（delivery 语义）。
        """
        if self._board.over:
            return MoveOutcome(moved=False, gained=0)
        self._ensure_game_started()
        outcome = self._board.apply_move(direction)
        if outcome.moved:
            self._history.append(direction.value)
            await self._maybe_emit_milestone()
            await self._emit_state(last_direction=direction.value)
        await self._finish_game_if_over()
        return outcome

    async def restart(self) -> None:
        """重开一局：清盘发牌、清空操作历史并推送新局快照（终局与否均可调用）。"""
        self._board.new_game()
        self._over_reported = False
        self._milestone_announced.clear()
        self._history.clear()
        self._logger.info("已重开一局")
        await self._emit_state()

    def _ensure_game_started(self) -> None:
        """空盘时开局发牌（首次按键的懒开局）。"""
        if self._board.moves == 0 and self._board.max_tile == 0:
            self._board.new_game()
            self._over_reported = False
            self._milestone_announced.clear()
            self._history.clear()
            self._logger.info("开局：新的一局 2048")

    # ==================================================================
    # 事件上报（game.* 语义域）
    # ==================================================================

    async def _maybe_emit_milestone(self) -> None:
        """首次合成出 ≥ 配置档位的新块值档发 ``game.milestone``（每档只播一次）。"""
        tile = self._board.max_tile
        threshold = self.typed_config.milestone_tile
        if tile < threshold:
            return
        tier = threshold
        while tier * 2 <= tile:
            tier *= 2
        if tier in self._milestone_announced:
            return
        self._milestone_announced.add(tier)
        payload = GamePayload(
            game=self.name,
            event_type="milestone",
            message=f"合成了 {tier}！当前得分 {self._board.score}",
        )
        await self.emit_event(CoreEvents.GAME_MILESTONE, payload)

    async def _emit_state(self, last_direction: Optional[str] = None) -> None:
        """发 ``game.state.changed``：当前棋盘快照（呈现流，带方向与操作历史）。"""
        payload = GameBoardStatePayload(
            game=self.name,
            board=self._board.to_matrix(),
            score=self._board.score,
            moves=self._board.moves,
            max_tile=self._board.max_tile,
            over=self._board.over,
            last_direction=last_direction,
            history=list(self._history),
        )
        await self.emit_event(CoreEvents.GAME_STATE_CHANGED, payload)

    async def _finish_game_if_over(self) -> None:
        """终局收尾（一局恰好一次）：终局快照 + ``game.report``（delivery 语义）。"""
        if not self._board.over or self._over_reported:
            return
        self._over_reported = True
        await self._emit_state()
        board = self._board
        message = f"本局结束：得分 {board.score}，最大块 {board.max_tile}，共 {board.moves} 步"
        payload = GamePayload(
            game=self.name,
            event_type="report",
            message=message,
            report_kind="delivery",
        )
        await self.emit_event(CoreEvents.GAME_REPORT, payload)
        self._logger.info(message)

    async def emit_error(self, message: str) -> None:
        """emit ``game.error``（供异常路径与测试使用）。"""
        payload = GamePayload(game=self.name, event_type="error", message=message)
        await self.emit_event(CoreEvents.GAME_ERROR, payload)

    # ==================================================================
    # 协议六项：工具提供
    # ==================================================================

    def list_tools(self) -> List[ToolSpec]:
        """声明工具面（provider="game_2048" 的两工具）。"""
        return list(self._tool_provider.list_tools())

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def _on_start(self) -> None:
        """启动钩子：注册工具面（按键驱动，无后台循环）。"""
        # 函数内 import：tools 模块反向引用本模块（循环 import 规避）
        from .tools import build_game_2048_visible_to

        count = self.register_tool_provider(
            self._tool_provider,
            registry=self._tool_registry,
            visible_to=build_game_2048_visible_to(),
        )
        self._logger.info(f"Game2048Agent 已启动（工具面注册 {count} 个；等待主播按键）")

    async def _on_stop(self) -> None:
        """停止钩子：摘除本 Agent 注册的工具面。"""
        removed = self.unregister_tool_providers()
        if removed:
            self._logger.info(f"Game2048Agent 已停止（摘除 {removed} 个工具）")

    # ==================================================================
    # 状态导出
    # ==================================================================

    def get_state_snapshot(self) -> Dict[str, Any]:
        """导出状态快照（工具返回与测试断言的统一形状）。"""
        return {
            "board": self._board.to_matrix(),
            "score": self._board.score,
            "moves": self._board.moves,
            "max_tile": self._board.max_tile,
            "over": self._board.over,
        }

    def render_board(self) -> str:
        """渲染人类/LLM 可读的文本棋盘（工具 content 用）。"""
        return self._board.render()


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def build_game_2048_agent(
    *,
    config: Game2048Config,
    tool_registry: Optional[ToolRegistry] = None,
    event_bus: Optional[EventBus] = None,
    rng: Optional[random.Random] = None,
) -> Game2048Agent:
    """便捷构造函数（装配任务接线用；依赖显式传参，不隐式拉起任何后端）。

    Returns:
        未启动的 Game2048Agent 实例（生命周期由调用方管理）
    """
    return Game2048Agent(
        config,
        tool_registry=tool_registry,
        event_bus=event_bus,
        rng=rng,
    )
