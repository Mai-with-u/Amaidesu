import asyncio
import logging
import random
import time
from typing import Optional

from ..mai_state import WarudoStateManager


class BlinkTask:
    """眨眼定时任务"""

    def __init__(self, state_manager: WarudoStateManager, logger: logging.Logger):
        self.state_manager = state_manager
        self.logger = logger

        # 眨眼间隔配置（秒）
        self.min_interval = 4.0  # 最小间隔
        self.max_interval = 8.0  # 最大间隔
        # self.max_interval = 0.4
        # self.min_interval = 0.2

        # 眨眼持续时间（秒）
        self.blink_duration = 0.15  # 眨眼持续时间

        # 任务状态
        self.task: Optional[asyncio.Task] = None
        self.running = False
        self.run_count = 0
        self.last_log_time = 0

        self.logger.info(f"眨眼任务已初始化，间隔范围: {self.min_interval}-{self.max_interval}秒")

    async def start(self):
        """启动眨眼任务"""
        if self.running:
            self.logger.warning("眨眼任务已在运行中")
            return

        self.running = True
        self.task = asyncio.create_task(self._run_loop())
        self.logger.info("眨眼任务已启动")

    async def stop(self):
        """停止眨眼任务"""
        if not self.running:
            return

        self.running = False

        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

        self.logger.info("眨眼任务已停止")

    async def _run_loop(self):
        """眨眼任务主循环"""
        self.logger.debug("眨眼任务循环开始")

        try:
            while self.running:
                # 等待随机间隔
                interval = random.uniform(self.min_interval, self.max_interval)
                await asyncio.sleep(interval)

                if not self.running:
                    break

                # 执行眨眼动作
                await self._perform_blink()

                self.run_count += 1

                # 定期输出状态信息（避免日志太频繁）
                now = time.time()
                if now - self.last_log_time > 30:  # 每30秒输出一次
                    self.logger.debug(f"[眨眼任务] 已执行{self.run_count}次眨眼动作")
                    self.last_log_time = now

        except asyncio.CancelledError:
            self.logger.debug("眨眼任务循环被取消")
        except Exception as e:
            self.logger.error(f"眨眼任务循环出错: {e}", exc_info=True)
        finally:
            self.logger.debug("眨眼任务循环结束")

    async def _perform_blink(self):
        """执行一次眨眼动作"""
        try:
            # 检查是否有其他眼部动作正在进行
            if not self.state_manager.eye_state.can_blink():
                self.logger.info("当前有其他眼部动作，跳过眨眼")
                return

            # 记录眨眼前的状态（用于恢复）
            self.state_manager.eye_state.set_blinking(True)

            # 等待眨眼持续时间
            await asyncio.sleep(self.blink_duration)

            # 睁眼（设置为0表示不闭眼）
            self.state_manager.eye_state.set_blinking(False)
            self.logger.debug("执行眨眼: 睁眼")

        except Exception as e:
            self.logger.error(f"执行眨眼动作时出错: {e}", exc_info=True)
