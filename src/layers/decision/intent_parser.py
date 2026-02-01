"""
IntentParser - LLM意图解析器

使用小参数LLM（如Claude Haiku）将MessageBase解析为Intent。
成本可控：~$0.00025/请求。

降级逻辑：
- LLM失败时使用规则引擎
- 保证系统稳定性
"""

import asyncio
import json
import re
from typing import Optional, TYPE_CHECKING

from src.layers.decision.intent import Intent, EmotionType, ActionType, IntentAction
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from maim_message import MessageBase
    from src.core.llm_service import LLMService


class IntentParser:
    """
    LLM意图解析器

    职责：
    - 使用小LLM解析MessageBase → Intent
    - 自动提取情感、动作、回复文本
    - 降级逻辑：LLM失败时使用规则引擎

    成本分析（Claude Haiku）：
    - 输入：~100 tokens (MessageBase)
    - 输出：~50 tokens (Intent JSON)
    - 成本：~$0.00025/请求
    - 10条/分钟：~$3.60/天

    使用示例：
        ```python
        parser = IntentParser(llm_service)
        await parser.setup()

        # 解析MessageBase
        message = MessageBase(...)
        intent = await parser.parse(message)

        await parser.cleanup()
        ```
    """

    # LLM系统提示词
    SYSTEM_PROMPT = """你是一个AI VTuber的意图分析助手。你的任务是将AI的回复消息解析为结构化的意图(Intent)。

分析消息内容并提取：
1. **情感(EmotionType)**: neutral/happy/sad/angry/surprised/love
2. **回复文本**: 提取主要回复内容
3. **动作(IntentAction)**: 识别应该执行的表现动作

动作类型说明：
- expression: 表情（params: {"name": "表情名称"}）
- hotkey: 热键（params: {"key": "按键名称"}）
- emoji: emoji表情（params: {"emoji": "实际emoji"}）
- blink: 眨眼
- nod: 点头
- shake: 摇头
- wave: 挥手
- clap: 鼓掌
- none: 无动作

输出格式（严格JSON）：
```json
{
  "emotion": "happy",
  "response_text": "回复内容",
  "actions": [
    {"type": "expression", "params": {"name": "smile"}, "priority": 50}
  ]
}
```

注意：
- emotion: 必须是预定义的6种之一
- response_text: 提取消息的主要文本内容
- actions: 数组，每个action包含type、params、priority(0-100)
- params: 根据type不同而不同
- 如果无法确定情感，默认使用"neutral"
- 如果没有明显动作，返回空数组
- 严格按照JSON格式输出，不要添加其他内容"""

    # 规则引擎的关键词映射
    EMOTION_KEYWORDS = {
        EmotionType.HAPPY: ["开心", "高兴", "哈哈", "快乐", "笑", "😊", "😄", "🎉"],
        EmotionType.SAD: ["难过", "伤心", "哭", "😢", "😭", "💔"],
        EmotionType.ANGRY: ["生气", "愤怒", "😠", "😡", "🔥"],
        EmotionType.SURPRISED: ["惊讶", "意外", "哇", "😲", "😱"],
        EmotionType.LOVE: ["爱", "喜欢", "❤️", "💕", "😍"],
    }

    def __init__(self, llm_service: "LLMService"):
        """
        初始化IntentParser

        Args:
            llm_service: LLM服务实例
        """
        self.llm_service = llm_service
        self.logger = get_logger("IntentParser")
        self._enabled = True

    async def setup(self):
        """设置IntentParser"""
        # 检查llm_fast是否可用
        if "llm_fast" not in self.llm_service._backends:
            self.logger.warning("llm_fast后端未配置，IntentParser将使用规则引擎降级")
            self._enabled = False
        else:
            self.logger.info("IntentParser初始化完成，使用LLM意图解析")

    async def parse(self, message: "MessageBase") -> Intent:
        """
        解析MessageBase为Intent

        Args:
            message: MaiCore返回的消息

        Returns:
            Intent: 解析后的意图

        Raises:
            ValueError: 如果消息解析失败
        """
        # 提取消息文本
        text = self._extract_text(message)
        if not text:
            self.logger.warning("消息为空，返回默认Intent")
            return self._create_default_intent("")

        # 尝试使用LLM解析
        if self._enabled:
            try:
                return await self._parse_with_llm(text, message)
            except Exception as e:
                self.logger.error(f"LLM意图解析失败: {e}，使用规则引擎降级", exc_info=True)

        # 降级到规则引擎
        return self._parse_with_rules(text, message)

    def _extract_text(self, message: "MessageBase") -> str:
        """
        提取消息文本

        Args:
            message: MessageBase对象

        Returns:
            消息文本
        """
        try:
            # 尝试获取消息内容
            if hasattr(message, "message_content"):
                content = message.message_content
                if hasattr(content, "content"):
                    return str(content.content)
            # 降级：转换为字符串
            return str(message)
        except Exception as e:
            self.logger.error(f"提取消息文本失败: {e}", exc_info=True)
            return ""

    async def _parse_with_llm(self, text: str, message: "MessageBase") -> Intent:
        """
        使用LLM解析意图

        Args:
            text: 消息文本
            message: 原始消息

        Returns:
            Intent: 解析后的意图

        Raises:
            ValueError: 如果LLM响应解析失败
        """
        # 调用LLM
        response = await self.llm_service.chat(
            prompt=f"请分析以下AI VTuber的回复消息，提取情感、回复文本和动作：\n\n{text}",
            backend="llm_fast",
            system_message=self.SYSTEM_PROMPT,
            temperature=0.3,  # 低温度，保证稳定输出
            max_tokens=200,
        )

        if not response.success:
            raise ValueError(f"LLM调用失败: {response.error}")

        # 解析JSON响应
        try:
            # 提取JSON（LLM可能添加markdown代码块）
            content = response.content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            intent_data = json.loads(content)

            # 构建Intent对象
            return Intent(
                original_text=text,
                response_text=intent_data.get("response_text", text),
                emotion=EmotionType(intent_data.get("emotion", "neutral")),
                actions=[
                    IntentAction(
                        type=ActionType(action["type"]),
                        params=action.get("params", {}),
                        priority=action.get("priority", 50),
                    )
                    for action in intent_data.get("actions", [])
                ],
                metadata={"llm_model": response.model, "llm_usage": response.usage},
            )
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM返回的JSON格式错误: {e}\n原始内容: {content}")
        except Exception as e:
            raise ValueError(f"解析LLM响应失败: {e}")

    def _parse_with_rules(self, text: str, message: "MessageBase") -> Intent:
        """
        使用规则引擎解析意图（降级方案）

        Args:
            text: 消息文本
            message: 原始消息

        Returns:
            Intent: 解析后的意图
        """
        # 情感识别（关键词匹配）
        emotion = EmotionType.NEUTRAL
        for emo, keywords in self.EMOTION_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                emotion = emo
                break

        # 动作识别（简单规则）
        actions = []

        # 礼物感谢
        if "感谢" in text or "谢谢" in text:
            actions.append(IntentAction(type=ActionType.EXPRESSION, params={"name": "thank"}, priority=70))

        # 问候
        if "你好" in text or "大家好" in text:
            actions.append(IntentAction(type=ActionType.WAVE, params={}, priority=60))

        # 点头（同意/肯定）
        if "是的" in text or "对" in text or "嗯" in text:
            actions.append(IntentAction(type=ActionType.NOD, params={}, priority=50))

        # 摇头（否定）
        if "不" in text or "不是" in text:
            actions.append(IntentAction(type=ActionType.SHAKE, params={}, priority=50))

        # 默认：如果没有动作，添加眨眼
        if not actions:
            actions.append(IntentAction(type=ActionType.BLINK, params={}, priority=30))

        return Intent(
            original_text=text,
            response_text=text,
            emotion=emotion,
            actions=actions,
            metadata={"parser": "rule_based"},
        )

    def _create_default_intent(self, text: str) -> Intent:
        """
        创建默认Intent

        Args:
            text: 消息文本

        Returns:
            默认Intent
        """
        return Intent(
            original_text=text,
            response_text=text or "...",
            emotion=EmotionType.NEUTRAL,
            actions=[IntentAction(type=ActionType.BLINK, params={}, priority=30)],
            metadata={"parser": "default"},
        )

    async def cleanup(self):
        """清理资源"""
        self.logger.info("IntentParser已清理")
