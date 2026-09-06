"""
眼部移动定时任务模块

管理 Warudo 虚拟形象的眼部左右移动功能。
"""

import asyncio
import logging
import random
import time
from typing import Optional


class ShiftTask:
    """眼部左右移动定时任务"""

    def __init__(self, state_manager, logger: logging.Logger):
        """
        初始化眼部移动任务

        Args:
            state_manager: WarudoStateManager 实例，用于控制模型状态
            logger: 日志记录器
        """
        self.state_manager = state_manager
        self.logger = logger

        # 移动间隔配置（秒）
        self.min_interval = 2  # 最小间隔
        self.max_interval = 12  # 最大间隔

        # 任务状态
        self.task: Optional[asyncio.Task] = None
        self.running = False
        self.run_count = 0
        self.last_log_time = 0

        self.logger.info(f"眼部移动任务已初始化，间隔范围: {self.min_interval}-{self.max_interval}秒")

    async def start(self):
        """启动眼部移动任务"""
        if self.running:
            self.logger.warning("眼部移动任务已在运行中")
            return

        self.running = True
        self.task = asyncio.create_task(self._run_loop())
        self.logger.info("眼部移动任务已启动")

    async def stop(self):
        """停止眼部移动任务"""
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

        self.logger.info("眼部移动任务已停止")

    async def _run_loop(self):
        """眼部移动任务主循环"""
        self.logger.debug("眼部移动任务循环开始")

        try:
            while self.running:
                # 等待随机间隔
                interval = random.randint(self.min_interval, self.max_interval)
                await asyncio.sleep(interval)

                if not self.running:
                    break

                # 执行眼部移动动作
                await self._perform_shift()

                self.run_count += 1

                # 定期输出状态信息（避免日志太频繁）
                now = time.time()
                if now - self.last_log_time > 60:  # 每60秒输出一次
                    self.logger.debug(f"[眼部移动任务] 已执行{self.run_count}次移动动作")
                    self.last_log_time = now

        except asyncio.CancelledError:
            self.logger.debug("眼部移动任务循环被取消")
        except Exception as e:
            self.logger.error(f"眼部移动任务循环出错: {e}", exc_info=True)

    async def _perform_shift(self):
        """执行一次眼部移动动作"""
        try:
            # 随机选择移动方向
            direction = random.choice(["left", "right"])
            direction_name = "左" if direction == "left" else "右"
            shape_key = "eye_shift_left" if direction == "left" else "eye_shift_right"

            # 开始移动
            self.state_manager.pupil_state.set_state(shape_key, 1.0)
            self.logger.debug(f"执行眼部移动: 向{direction_name}移动")

            # 等待移动持续时间
            shift_duration = random.randint(1, 10) / 10
            await asyncio.sleep(shift_duration)

            # 停止移动（设置为0）
            self.state_manager.pupil_state.set_state(shape_key, 0.0)
            self.logger.debug(f"执行眼部移动: 停止向{direction_name}移动")

        except Exception as e:
            self.logger.error(f"执行眼部移动动作时出错: {e}", exc_info=True)
