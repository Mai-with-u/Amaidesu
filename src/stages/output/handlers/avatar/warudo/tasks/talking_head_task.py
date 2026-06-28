"""
Warudo TalkingHeadTask - 说话时的随机头部动作任务

迁移自旧插件 plugins_backup/warudo/small_actions/talking_head.py。
当 is_talking=True 时,周期性发送带轻微噪声的正弦波头部位置,
模拟人说话时的自然头部微动。停止说话时回归零位。

设计要点:
- 接收 send_action_callback 而非 WebSocket 引用(避免紧耦合)
- is_talking 由外部(ReplyState)控制
- 后台循环 + 0.1s 步进,正弦波 + 随机抖动
"""

import asyncio
import logging
import math
import random
from typing import Callable, Coroutine, Optional, Any

from src.modules.logging import get_logger


class TalkingHeadTask:
    """说话时随机头部动作任务"""

    def __init__(
        self,
        send_action_callback: Callable[[str, Any], Coroutine[Any, Any, bool]],
        logger: Optional[logging.Logger] = None,
        min_interval: float = 0.1,
    ):
        """
        初始化头部动作任务

        Args:
            send_action_callback: 发送动作的异步回调,签名
                async def send_action(name: str, data: Any) -> bool
            logger: 日志器
            min_interval: 循环步进间隔(秒)
        """
        self.send_action_callback = send_action_callback
        self.logger = logger or get_logger("WarudoTalkingHead")
        self.min_interval = min_interval

        self.is_talking: bool = False
        self.task: Optional[asyncio.Task] = None
        self.running: bool = False

        self.logger.info("TalkingHeadTask 已初始化")

    async def start(self) -> None:
        """启动后台循环"""
        if self.running:
            self.logger.warning("TalkingHeadTask 已在运行中")
            return
        self.running = True
        self.task = asyncio.create_task(self._run_loop(), name="Warudo_TalkingHead")
        self.logger.info("TalkingHeadTask 已启动")

    async def stop(self) -> None:
        """停止后台循环,并发送一次零位动作"""
        if not self.running:
            return
        self.running = False
        self.is_talking = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

        # 停止时发送一次零位
        try:
            await asyncio.sleep(1.0)
            await self.send_action_callback("head_action", {"x": 0, "y": 0, "z": 0})
        except Exception as e:
            self.logger.error(f"停止时发送零位头部动作失败: {e}")

        self.logger.info("TalkingHeadTask 已停止")

    async def _run_loop(self) -> None:
        """后台主循环:is_talking=True 时持续发送正弦波头部动作"""
        self.logger.debug("TalkingHeadTask 循环开始")
        t = 0.0
        try:
            while self.running:
                if not self.is_talking:
                    await asyncio.sleep(self.min_interval)
                    continue

                # 轻微抖动参数
                amplitude = 0.8 + random.uniform(-0.02, 0.02)
                period = 0.2 + random.uniform(-0.06, 0.1)
                base = amplitude * math.sin(t)
                jitter = random.uniform(-0.03, 0.08)
                y = base + jitter
                head_dict = {"x": 0, "y": y, "z": 0}

                try:
                    await self.send_action_callback("head_action", head_dict)
                except Exception as e:
                    self.logger.error(f"发送头部动作失败: {e}")

                t += period
                await asyncio.sleep(self.min_interval)
        except asyncio.CancelledError:
            self.logger.debug("TalkingHeadTask 循环被取消")
        except Exception as e:
            self.logger.error(f"TalkingHeadTask 循环异常: {e}", exc_info=True)
