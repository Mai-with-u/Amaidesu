"""
Emotion Judge Decision Provider

情感判断决策Provider，使用LLM判断文本情感并触发热键。
"""

import time
from typing import Optional

from openai import AsyncOpenAI

from src.core.base.decision_provider import DecisionProvider
from src.utils.logger import get_logger


class EmotionJudgeDecisionProvider(DecisionProvider):
    """
    情感判断决策Provider

    使用LLM分析文本情感，触发热键。
    """

    def __init__(self, config: dict, event_bus=None):
        super().__init__(config, event_bus)
        self.logger = get_logger("EmotionJudgeDecisionProvider")

        # 配置
        self.base_url = self.config.get("base_url", "https://api.siliconflow.cn/v1/")
        self.api_key = self.config.get("api_key", "")
        self.model_config = self.config.get("model", {})

        # 冷却时间
        self.cool_down_seconds = self.config.get("cool_down_seconds", 10)
        self.last_trigger_time: float = 0.0

        # 初始化OpenAI客户端
        self.client = None
        if self.api_key:
            self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
            self.logger.info("EmotionJudgeDecisionProvider 初始化成功。")
        else:
            self.logger.warning("EmotionJudgeDecisionProvider 缺少 API Key，功能将禁用。")

    async def _setup_internal(self):
        """内部设置"""
        # 注册事件监听器
        if self.event_bus:
            self.event_bus.on("canonical.message", self._handle_canonical_message, priority=100)
            self.logger.info("已注册 canonical.message 事件监听器")

    async def decide(self, canonical_message):
        """
        决策 - 判断情感并触发热键

        Args:
            canonical_message: 标准消息

        Returns:
            MessageBase: 决策结果（这里暂时返回None）
        """
        # 由于需要外部服务支持，这里暂时只做情感判断
        # 未来可以返回MessageBase来执行更复杂的操作
        return None

    async def _handle_canonical_message(self, event_name: str, data: any, source: str):
        """
        处理标准消息

        Args:
            event_name: 事件名称
            data: 事件数据
            source: 事件源
        """
        # 检查冷却时间
        current_time = time.monotonic()
        if current_time - self.last_trigger_time < self.cool_down_seconds:
            remaining_cooldown = self.cool_down_seconds - (current_time - self.last_trigger_time)
            self.logger.debug(f"情感判断冷却中，跳过消息处理。剩余 {remaining_cooldown:.1f} 秒")
            return

        # 获取文本内容
        text = self._extract_text(data)
        if not text:
            return

        self.logger.info(f"开始情感判断: '{text[:50]}...'")

        # 执行情感判断
        await self._judge_and_trigger(text)

    def _extract_text(self, data: any) -> Optional[str]:
        """
        从数据中提取文本

        Args:
            data: 事件数据

        Returns:
            文本内容或None
        """
        # 尝试从不同数据结构中提取文本
        if isinstance(data, str):
            return data
        elif hasattr(data, "text"):
            return data.text
        elif hasattr(data, "content"):
            return data.content
        elif hasattr(data, "raw_message"):
            return data.raw_message

        return None

    async def _judge_and_trigger(self, text: str):
        """
        使用 LLM 判断文本的情感并触发热键

        Args:
            text: 文本内容
        """
        if not self.client:
            self.logger.warning("EmotionJudgeDecisionProvider 缺少 API Key，跳过情感判断。")
            return

        # 获取热键列表
        hotkey_list = await self._get_hotkey_list()
        if not hotkey_list:
            self.logger.warning("无法获取热键列表，跳过情感判断。")
            return

        hotkey_name_list = [hotkey["name"] for hotkey in hotkey_list]
        self.logger.debug(f"获取到的热键列表: {hotkey_name_list}")

        # 将热键列表转换为字符串
        hotkey_list_str = "\n".join(hotkey_name_list)

        try:
            response = await self.client.chat.completions.create(
                model=self.model_config.get("name", "Qwen/Qwen2.5-7B-Instruct"),
                messages=[
                    {
                        "role": "system",
                        "content": self.model_config.get(
                            "system_prompt",
                            "你是一个主播的助手，根据主播的文本内容，判断主播的情感状态，确定触发哪一个Live2D热键以帮助主播更好地表达情感。只输出热键名称，不要包含其他任何文字或解释。以下为热键列表：\n",
                        )
                        + hotkey_list_str,
                    },
                    {"role": "user", "content": text},
                ],
                max_tokens=self.model_config.get("max_tokens", 10),
                temperature=self.model_config.get("temperature", 0.3),
            )

            if response.choices and response.choices[0].message:
                # 提取 LLM 返回的情感标签
                emotion = response.choices[0].message.content.strip()
                # 简单的后处理
                emotion = emotion.strip("'\"")
                self.logger.info(f"文本 '{text[:30]}...' 的情感判断结果: {emotion}")

                # 根据情感结果触发热键
                if emotion:
                    await self._trigger_hotkey(emotion)
                    # 更新上次触发时间
                    self.last_trigger_time = time.monotonic()
                    self.logger.info(f"热键 '{emotion}' 已触发，冷却时间开始。")
            else:
                self.logger.warning("OpenAI API 返回了无效的响应结构")

        except Exception as e:
            self.logger.error(f"调用 OpenAI API 时发生错误: {e}", exc_info=True)

    async def _get_hotkey_list(self) -> Optional[list]:
        """
        获取 VTS 的热键列表

        Returns:
            热键列表或None
        """
        # 这里需要一种方式从EventBus获取VTS服务
        # 暂时返回None，未来需要完善服务获取机制
        return None

    async def _trigger_hotkey(self, hotkey_id: str):
        """
        触发热键

        Args:
            hotkey_id: 热键ID
        """
        # 这里需要一种方式从EventBus获取VTS服务
        # 暂时只记录日志
        self.logger.info(f"触发热键: {hotkey_id}")

    async def _cleanup_internal(self):
        """内部清理"""
        # 关闭OpenAI客户端
        if self.client:
            await self.client.close()
            self.client = None
