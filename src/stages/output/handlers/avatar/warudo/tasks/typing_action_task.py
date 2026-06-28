"""
Warudo TypingActionTask - 打字时的手机抖动动作

迁移自旧插件 plugins_backup/warudo/small_actions/typing_action.py。
旧实现: 当 is_typing=True 时,周期性发送带噪声的 phone_position 动作,
模拟看手机打字时的轻微抖动。1/2 概率触发(模拟偶尔不看手机的情况)。

设计要点:
- 接收 send_action_callback 发送 body_action 类型
- is_typing 由外部(ReplyState)控制
- 后台循环 + 0.1s 步进,小幅度正弦波 + 抖动
"""

import asyncio
import logging
import math
import random
from typing import Any, Callable, Coroutine, Optional

from src.modules.logging import get_logger


class TypingActionTask:
    """打字动画任务"""

    def __init__(
        self,
        send_action_callback: Callable[[str, Any], Coroutine[Any, Any, bool]],
        logger: Optional[logging.Logger] = None,
        min_interval: float = 0.1,
    ):
        self.send_action_callback = send_action_callback
        self.logger = logger or get_logger("WarudoTypingAction")
        self.min_interval = min_interval

        self.is_typing: bool = False
        self.task: Optional[asyncio.Task] = None
        self.running: bool = False
        self.enabled: bool = True  # 1/2 概率启用

        self.logger.info("TypingActionTask 已初始化")

    async def start(self) -> None:
        """启动后台循环"""
        if self.running:
            self.logger.warning("TypingActionTask 已在运行中")
            return
        self.running = True
        self.task = asyncio.create_task(self._run_loop(), name="Warudo_TypingAction")
        self.logger.info("TypingActionTask 已启动")

    async def stop(self) -> None:
        """停止后台循环"""
        if not self.running:
            return
        self.running = False
        self.is_typing = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        self.logger.info("TypingActionTask 已停止")

    async def send_typing(self) -> None:
        """发送开始打字信号"""
        try:
            await self.send_action_callback("body_action", "typing_on")
        except Exception as e:
            self.logger.error(f"发送 typing_on 失败: {e}")

    async def send_finish_typing(self) -> None:
        """发送结束打字信号"""
        try:
            await self.send_action_callback("body_action", "typing_off")
        except Exception as e:
            self.logger.error(f"发送 typing_off 失败: {e}")

    async def _run_loop(self) -> None:
        """后台主循环:is_typing=True 时发送轻微手机位置抖动"""
        self.logger.debug("TypingActionTask 循环开始")
        t = 0.0
        try:
            while self.running:
                if not self.is_typing:
                    await asyncio.sleep(self.min_interval)
                    continue

                # 1/2 概率启用(模拟偶尔不看手机)
                if random.random() >= 0.5:
                    amplitude = 0.005 + random.uniform(-0.01, 0.01)
                    period = 1.2 + random.uniform(-0.6, 0.3)
                    base = amplitude * math.sin(t)
                    jitter = random.uniform(-0.005, 0.005)
                    y = base + jitter
                    position_dict = {"x": 0, "y": y, "z": 0}
                    try:
                        await self.send_action_callback(
                            "body_action", {"action_type": "phone_position", "data": position_dict}
                        )
                    except Exception as e:
                        self.logger.error(f"发送手机位置动作失败: {e}")
                    t += period

                await asyncio.sleep(self.min_interval)
        except asyncio.CancelledError:
            self.logger.debug("TypingActionTask 循环被取消")
        except Exception as e:
            self.logger.error(f"TypingActionTask 循环异常: {e}", exc_info=True)
        finally:
            # 结束时发送一次零位
            try:
                await self.send_action_callback(
                    "body_action", {"action_type": "phone_position", "data": {"x": 0, "y": 0, "z": 0}}
                )
            except Exception:
                pass
