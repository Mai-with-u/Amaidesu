"""
Warudo ReplyState - 回复状态管理器

管理说话状态（talking状态）
"""

from typing import Any


class ReplyState:
    """回复状态管理器"""

    def __init__(self, state_manager, logger):
        self.state_manager = state_manager
        self.logger = logger
        self.is_talking = False

    async def start_talking(self):
        """开始说话状态"""
        if not self.is_talking:
            self.is_talking = True
            self.logger.debug("开始说话状态")

    async def stop_talking(self):
        """停止说话状态"""
        if self.is_talking:
            self.is_talking = False
            self.logger.debug("停止说话状态")

    async def deal_state(self, state_data: Any):
        """
        处理状态数据

        Args:
            state_data: 状态数据
        """
        # 状态数据处理逻辑
        self.logger.debug(f"处理状态数据: {state_data}")
