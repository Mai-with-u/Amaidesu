"""
MockDanmakuProvider - 模拟弹幕Provider

生成随机弹幕用于测试。
"""

import asyncio
import random
from typing import Any, AsyncIterator, Dict

from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from src.utils.logger import get_logger


class MockDanmakuProvider(InputProvider):
    """
    模拟弹幕Provider

    生成随机弹幕用于测试。
    """

    # 模拟弹幕内容
    DANMAKU_TEMPLATES = [
        "这是一个测试弹幕",
        "666666",
        "主播好厉害！",
        "哈哈哈哈",
        "???",
        "原来如此",
        "这是什么操作",
        "学到了学到了",
        "太强了吧",
        "加油加油！",
        "下次一定",
        "真的吗？我不信",
        "卧槽，牛逼！",
        "这就很离谱",
        "哈哈哈哈哈",
        "理解一下",
        "可以可以",
        "好耶！",
        "泪目了",
        "破防了",
    ]

    def __init__(self, config: Dict[str, Any]):
        """
        初始化MockDanmakuProvider

        Args:
            config: 配置字典
        """
        super().__init__(config)
        self.logger = get_logger("MockDanmakuProvider")
        self._running = False

        # 读取配置
        self.send_interval = max(0.1, config.get("send_interval", 1.0))
        self.min_interval = max(0.1, config.get("min_interval", 1.0))
        self.max_interval = max(self.min_interval, config.get("max_interval", 3.0))

        self.logger.info(f"MockDanmakuProvider初始化完成 (间隔: {self.send_interval}s)")

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """
        生成模拟弹幕

        Yields:
            RawData: 原始弹幕数据
        """
        user_counter = 1000

        self.logger.info("开始生成模拟弹幕...")

        while self.is_running:
            # 生成随机弹幕
            user_id = f"user_{random.randint(1000, 9999)}"
            user_nickname = f"用户{user_counter % 100}"
            user_counter += 1

            danmaku_text = random.choice(self.DANMAKU_TEMPLATES)

            # 创建RawData
            yield RawData(
                content=danmaku_text,
                source="mock_danmaku",
                data_type="text",
                metadata={"user_id": user_id, "user_name": user_nickname, "platform": "mock"},
            )

            self.logger.debug(f"生成弹幕: {user_nickname}: {danmaku_text}")

            # 随机等待
            wait_time = random.uniform(self.min_interval, self.max_interval)
            await asyncio.sleep(wait_time)

        self.logger.info("模拟弹幕生成结束")

    async def _cleanup(self):
        """清理资源"""
        self.logger.info("MockDanmakuProvider cleanup完成")
