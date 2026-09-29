"""2048 棋盘小部件服务。

订阅 ``game.state.changed`` 高频快照事件，持单槽最新棋盘并经回调广播给
前端 WebSocket 客户端（``/ws/game2048``）。与弹幕小部件服务同构——呈现层
订阅语义域事件做展示翻译；差别只在历史语义：棋盘只需要单槽最新快照
（页面加载/重连立刻恢复当前棋盘，不等 AI 走下一手）。
"""

from typing import Any, Callable, Dict, Optional

from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GameBoardStatePayload
from src.modules.logging import get_logger


class Game2048WidgetService:
    """2048 棋盘小部件服务

    订阅游戏状态快照事件并广播到前端。用于 Warudo / OBS 等网页道具场景。
    """

    def __init__(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus
        self.logger = get_logger("Game2048WidgetService")

        self._latest: Optional[Dict[str, Any]] = None
        self._state_callback: Optional[Callable[[dict], Any]] = None

        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def set_state_callback(self, callback: Callable[[dict], Any]) -> None:
        self._state_callback = callback

    async def start(self) -> None:
        if self._is_running:
            self.logger.warning("Game2048WidgetService 已经在运行中")
            return

        self.event_bus.on(
            CoreEvents.GAME_STATE_CHANGED,
            self._on_game_state,
            model_class=GameBoardStatePayload,
        )
        self._is_running = True
        self.logger.info("Game2048WidgetService 已启动")

    async def stop(self) -> None:
        if not self._is_running:
            return

        self.event_bus.off(CoreEvents.GAME_STATE_CHANGED, self._on_game_state)
        self._is_running = False
        self.logger.info("Game2048WidgetService 已停止")

    async def _on_game_state(
        self,
        event_name: str,
        payload: GameBoardStatePayload,
        source: str,
    ) -> None:
        try:
            data = payload.model_dump(mode="json")
            self._latest = data

            if self._state_callback:
                try:
                    await self._state_callback({"type": "state", "state": data})
                except Exception as e:
                    self.logger.exception(f"广播棋盘状态到 game2048 端失败: {e}")
        except Exception as e:
            self.logger.exception(f"处理游戏状态快照失败: {e}")

    def get_latest_state(self) -> Optional[Dict[str, Any]]:
        """最近一帧棋盘快照（无快照为 ``None``；WS 接入时的 history 单槽）。"""
        return self._latest

    def get_stats(self) -> dict:
        return {
            "is_running": self._is_running,
            "has_state": self._latest is not None,
            "score": (self._latest or {}).get("score", 0),
        }
