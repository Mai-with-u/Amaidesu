"""
Warudo ThrowFishTask - 抛鱼动画(单次触发)

动作契约: ON_WEBSOCKET_ACTION("throw_fish", Integer).

设计要点:
- 单次触发型(非循环)
- 通过 send_action_callback 直发 throw_fish (action 名匹配蓝图节点)
- 冷却机制由调用方控制(或外部检查 last_throw_time)
"""

import time
from typing import Any, Callable, Coroutine, Optional

from src.modules.logging import ModuleLogger, get_logger


class ThrowFishTask:
    """抛鱼动画(单次触发)"""

    def __init__(
        self,
        send_action_callback: Callable[[str, Any], Coroutine[Any, Any, bool]],
        logger: Optional[ModuleLogger] = None,
        cooldown_seconds: float = 5.0,
    ) -> None:
        """
        初始化抛鱼任务

        Args:
            send_action_callback: 发送动作的异步回调
            logger: 日志器
            cooldown_seconds: 触发冷却时间(秒)
        """
        self.send_action_callback = send_action_callback
        self.logger = logger or get_logger("WarudoThrowFish")
        self.cooldown_seconds = cooldown_seconds
        self.last_throw_time: float = 0.0

        self.logger.info("ThrowFishTask 已初始化")

    async def throw_fish(self) -> bool:
        """触发一次抛鱼动画

        - 距上次触发 < cooldown_seconds 时不触发（冷却经返回值告知调用方）
        - 直发 throw_fish 到 fish.json 蓝图(数据=1, Integer)

        Returns:
            本次是否真的触发了动画（False = 冷却中或发送失败）。
        """
        if time.time() - self.last_throw_time < self.cooldown_seconds:
            self.logger.debug("ThrowFish 冷却中,跳过")
            return False

        try:
            await self.send_action_callback("throw_fish", 1)
            self.last_throw_time = time.time()
            self.logger.debug("抛鱼动画已触发")
            return True
        except Exception as e:
            self.logger.error(f"抛鱼动画触发失败: {e}")
            return False
