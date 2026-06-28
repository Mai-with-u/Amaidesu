"""
Warudo ThrowFishTask - 抛鱼动画(单次触发)

迁移自旧插件 plugins_backup/warudo/small_actions/throw_fish.py。
旧实现逻辑: 5 秒冷却,触发时 1/5 概率发送 "throw_fish" 大动作,
否则发送 "throw_fish" 小动作。

设计要点:
- 单次触发型(非循环)
- 通过 send_action_callback 发送 body_action 类型
- 冷却机制由调用方控制(或外部检查 last_throw_time)
"""

import logging
import random
import time
from typing import Any, Callable, Coroutine, Optional

from src.modules.logging import get_logger


class ThrowFishTask:
    """抛鱼动画(单次触发)"""

    def __init__(
        self,
        send_action_callback: Callable[[str, Any], Coroutine[Any, Any, bool]],
        logger: Optional[logging.Logger] = None,
        cooldown_seconds: float = 5.0,
    ):
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

    async def throw_fish(self) -> None:
        """触发一次抛鱼动画

        - 距上次触发 < cooldown_seconds 时直接返回
        - 1/5 概率发送大动作(random_num == 5)
        - 否则发送小动作(random_num != 5,默认 1)
        """
        if time.time() - self.last_throw_time < self.cooldown_seconds:
            self.logger.debug("ThrowFish 冷却中,跳过")
            return

        random_num = random.randint(1, 5)
        try:
            if random_num == 5:
                await self.send_action_callback("body_action", "throw_fish_big")
            else:
                await self.send_action_callback("body_action", "throw_fish")
            self.last_throw_time = time.time()
            self.logger.debug(f"抛鱼动画已触发 (variant={random_num})")
        except Exception as e:
            self.logger.error(f"抛鱼动画触发失败: {e}")
