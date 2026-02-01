"""
MockDecisionProvider - 模拟决策Provider

基于简单规则生成回复，用于测试决策层。
"""

import random
import re
import asyncio
from typing import Dict, Any, TYPE_CHECKING

from src.core.base.decision_provider import DecisionProvider
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.core.base.normalized_message import NormalizedMessage
    from src.layers.decision.intent import Intent


class MockDecisionProvider(DecisionProvider):
    """
    模拟决策Provider

    使用简单的关键词匹配和随机回复来模拟AI决策。
    """

    # 关键词回复映射
    KEYWORD_RESPONSES = {
        r"你好|嗨|哈喽|hello|hi": [
            "你好呀！很高兴见到你~",
            "嗨！今天想聊点什么呢？",
            "Hello！有什么我可以帮你的吗？",
        ],
        r"哈哈|呵呵|嘻嘻": [
            "什么事情这么好笑呀？",
            "看你开心的样子，我也跟着高兴起来啦~",
            "哈哈哈哈！",
        ],
        r"谢谢|感谢": [
            "不客气！这是我应该做的~",
            "能帮到你我很开心！",
            "不用谢，随时为你服务！",
        ],
        r"厉害|牛逼|强": [
            "哪有哪有，还需要继续努力呢~",
            "谢谢夸奖！你也很厉害！",
            "过奖啦~",
        ],
        r"再见|拜拜|晚安": [
            "再见啦！下次见~",
            "拜拜！期待下次聊天！",
            "晚安，做个好梦！",
        ],
        r"你是谁|介绍": [
            "我是Amaidesu，一个AI虚拟助手~",
            "你好！我是Amaidesu，很高兴认识你！",
            "我是Amaidesu，一个可爱的AI助手！",
        ],
        r"天气": [
            "我不确定具体天气，不过希望你每天都能有好心情！",
            "天气怎么样呢？记得注意保暖哦~",
            "不管天气如何，记得保持好心情！",
        ],
    }

    # 默认回复
    DEFAULT_RESPONSES = [
        "嗯嗯，原来是这样~",
        "有趣！",
        "我明白了！",
        "真的吗？",
        "说得对！",
        "嗯嗯，继续说~",
        "原来如此！",
        "哈哈，有意思！",
        "我在听呢，继续~",
        "好的好的！",
    ]

    def __init__(self, config: Dict[str, Any]):
        """
        初始化MockDecisionProvider

        Args:
            config: 配置字典
        """
        super().__init__(config)
        self.logger = get_logger("MockDecisionProvider")

        # 读取配置
        self.response_delay = config.get("response_delay", 0.5)  # 模拟AI思考延迟
        self.enable_keyword_match = config.get("enable_keyword_match", True)
        self.add_random_variation = config.get("add_random_variation", True)

        self.logger.info("MockDecisionProvider初始化完成")

    async def _setup_internal(self):
        """内部设置逻辑"""
        self.logger.info("MockDecisionProvider设置完成")

    async def decide(self, message: "NormalizedMessage") -> "Intent":
        """
        决策（异步）

        根据NormalizedMessage生成决策结果(Intent)。

        Args:
            message: 标准化消息

        Returns:
            Intent: 决策意图
        """
        # 模拟AI思考延迟
        if self.response_delay > 0:
            await asyncio.sleep(self.response_delay)

        text = message.text.strip()

        # 选择回复
        response_text = self._generate_response(text)

        self.logger.info(f"决策结果: {response_text}")

        # 创建Intent（模拟）
        from src.layers.decision.intent import Intent, EmotionType, IntentAction, ActionType

        intent = Intent(
            original_text=text,
            response_text=response_text,
            emotion=EmotionType.NEUTRAL,
            actions=[
                IntentAction(
                    type=ActionType.EXPRESSION,
                    params={"expression": "neutral"},
                    priority=50,
                )
            ],
            metadata={
                "provider": "mock_decision",
                "response_time": self.response_delay,
            },
        )

        return intent

    def _generate_response(self, text: str) -> str:
        """
        生成回复文本

        Args:
            text: 输入文本

        Returns:
            回复文本
        """
        if self.enable_keyword_match:
            # 尝试关键词匹配
            for pattern, responses in self.KEYWORD_RESPONSES.items():
                if re.search(pattern, text, re.IGNORECASE):
                    response = random.choice(responses)
                    if self.add_random_variation:
                        # 有30%的概率添加语气词
                        if random.random() < 0.3:
                            response += random.choice(["~", "！", "🎉", "✨", "💫"])
                    return response

        # 使用默认回复
        return random.choice(self.DEFAULT_RESPONSES)

    async def _cleanup_internal(self):
        """内部清理逻辑"""
        self.logger.info("MockDecisionProvider清理完成")

