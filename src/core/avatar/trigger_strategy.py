"""
智能触发策略引擎
负责判断是否应该触发虚拟形象表情调整

采用三层过滤机制：
1. 简单回复过滤（正则匹配）
2. 时间间隔控制（时间戳比较）
3. LLM智能判断（重要性 + 情感 + 变化检测）
"""

import re
import time
import json
from typing import Dict, Optional, Tuple
from collections import deque
from dataclasses import dataclass

from src.utils.logger import get_logger


@dataclass
class EmotionRecord:
    """情感记录"""
    emotion: str          # 情感名称（如 "happy_expression"）
    timestamp: float      # 时间戳
    intensity: float      # 强度 (0.0-1.0)
    text: str            # 原始文本（用于调试）


class SimpleReplyFilter:
    """简单回复过滤器

    使用正则表达式匹配常见的确认语，如"好的"、"嗯"、"收到"等。
    """

    def __init__(self, config: Dict):
        self.enabled = config.get("simple_reply_filter_enabled", True)
        patterns = config.get("simple_reply_patterns", [
            "^[好的好的]$",
            "^[好呀]$",
            "^[好的]$",
            "^[嗯嗯]$",
            "^[嗯]$",
            "^[收到]$",
            "^[知道了]$",
            "^[明白]$",
            "^[OK]$",
            "^[ok]$",
            "^(好|行|可以)[呀啊嘛。！!]*$"
        ])
        self.patterns = [re.compile(p) for p in patterns]
        self.logger = get_logger("SimpleReplyFilter")

    def is_simple_reply(self, text: str) -> bool:
        """检查是否是简单回复

        Args:
            text: 输入文本

        Returns:
            是否匹配简单回复模式
        """
        if not self.enabled:
            return False

        text_stripped = text.strip()
        for pattern in self.patterns:
            if pattern.match(text_stripped):
                self.logger.debug(f"简单回复过滤: '{text_stripped}' 匹配模式")
                return True

        return False


class TimeIntervalController:
    """时间间隔控制器

    确保两次触发之间有最小时间间隔，避免频繁调整。
    """

    def __init__(self, config: Dict):
        self.enabled = config.get("time_interval_enabled", True)
        self.min_interval = config.get("min_time_interval", 5.0)
        self.last_trigger_time: float = 0.0
        self.logger = get_logger("TimeIntervalController")

    def should_skip_by_time(self) -> bool:
        """检查是否应该因时间间隔而跳过

        Returns:
            是否应该跳过
        """
        if not self.enabled:
            return False

        current_time = time.monotonic()
        elapsed = current_time - self.last_trigger_time

        if elapsed < self.min_interval:
            self.logger.debug(
                f"时间间隔控制: 距离上次触发 {elapsed:.2f}秒 < {self.min_interval}秒，跳过"
            )
            return True

        return False

    def update_trigger_time(self):
        """更新触发时间"""
        self.last_trigger_time = time.monotonic()


class EmotionHistory:
    """情感历史记录管理

    维护最近N次情感状态，用于LLM判断情感变化。
    """

    def __init__(self, config: Dict):
        max_history = config.get("max_emotion_history", 5)
        self.history: deque = deque(maxlen=max_history)
        self.logger = get_logger("EmotionHistory")

    def get_last_emotion(self) -> Optional[str]:
        """获取上一次情感

        Returns:
            上一次情感名称，如果没有历史记录返回 None
        """
        if not self.history:
            return None
        return self.history[-1].emotion

    def record_emotion(self, emotion: str, intensity: float, text: str):
        """记录情感

        Args:
            emotion: 情感名称
            intensity: 强度
            text: 原始文本
        """
        record = EmotionRecord(
            emotion=emotion,
            timestamp=time.monotonic(),
            intensity=intensity,
            text=text
        )
        self.history.append(record)
        self.logger.debug(f"记录情感: {emotion} (强度: {intensity:.2f})")


class TriggerStrategyEngine:
    """触发策略引擎

    整合所有过滤逻辑，提供统一的判断接口。
    """

    def __init__(self, config: Dict, avatar_manager):
        """

        Args:
            config: 配置字典
            avatar_manager: AvatarControlManager 实例（用于获取LLM客户端）
        """
        self.config = config
        self.avatar_manager = avatar_manager
        self.logger = get_logger("TriggerStrategyEngine")

        # 初始化各个过滤器
        self.simple_reply_filter = SimpleReplyFilter(config)
        self.time_controller = TimeIntervalController(config)
        self.emotion_history = EmotionHistory(config)

        # LLM 判断配置
        self.llm_judge_enabled = config.get("llm_judge_enabled", True)

        # 调试配置
        self.debug_mode = config.get("debug_mode", False)
        self.log_filtered = config.get("log_filtered_messages", True)

        # 可用表情列表
        self.available_emotions = [
            "happy_expression",
            "sad_expression",
            "surprised_expression",
            "angry_expression",
            "neutral"
        ]

    async def should_trigger(self, text: str) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """综合判断是否应该触发表情

        Args:
            text: 输入文本

        Returns:
            (是否触发, 过滤原因, LLM结果字典)
        """
        # 1. 简单回复过滤（完全跳过）
        if self.simple_reply_filter.is_simple_reply(text):
            reason = "simple_reply"
            if self.log_filtered:
                self.logger.info(f"触发跳过 [简单回复]: '{text[:30]}...'")
            return False, reason, None

        # 2. 时间间隔控制
        if self.time_controller.should_skip_by_time():
            reason = "time_interval"
            if self.log_filtered:
                self.logger.info(f"触发跳过 [时间间隔]: '{text[:30]}...'")
            return False, reason, None

        # 3. LLM智能判断
        if self.llm_judge_enabled:
            try:
                llm_result = await self._llm_evaluate(text)

                # 解析LLM结果
                is_important = llm_result.get("is_important", False)
                has_changed = llm_result.get("has_changed", True)
                reason = llm_result.get("reason", "")

                if not is_important:
                    if self.log_filtered:
                        self.logger.info(f"触发跳过 [重要性低] {reason}: '{text[:30]}...'")
                    return False, "not_important", llm_result

                if not has_changed:
                    if self.log_filtered:
                        self.logger.info(f"触发跳过 [情感未变化] {reason}: '{text[:30]}...'")
                    return False, "emotion_unchanged", llm_result

                # 通过所有检查
                if self.debug_mode:
                    self.logger.debug(f"LLM判断通过: {llm_result}")

                return True, None, llm_result

            except Exception:
                self.logger.error("LLM判断失败，回退到允许触发", exc_info=True)
                # LLM失败时回退到允许触发（向后兼容）
                return True, None, None
        else:
            # 未启用LLM判断，直接允许触发
            return True, None, None

    async def _llm_evaluate(self, text: str) -> Dict:
        """使用LLM评估是否应该触发表情

        Args:
            text: 输入文本

        Returns:
            LLM返回的JSON结果字典
        """
        # 获取LLM客户端
        llm_client = self.avatar_manager._get_llm_client()
        if not llm_client:
            raise RuntimeError("LLM客户端不可用")

        # 构建上下文
        context = f"当前文本: {text}"
        last_emotion = self.emotion_history.get_last_emotion()
        if last_emotion:
            context += f"\n上次情感: {last_emotion}"

        # 结构化输出Prompt
        prompt = f"""你是一个虚拟形象表情控制系统。请分析以下文本，判断是否需要调整表情。

{context}

可用表情: {', '.join(self.available_emotions)}

【重要】你必须严格按照以下JSON格式返回结果，不要包含任何其他文字：
{{
    "is_important": true,
    "emotion": "happy_expression",
    "intensity": 0.8,
    "has_changed": true,
    "reason": "用户表达强烈的开心情感"
}}

判断标准：
1. is_important: 简单确认语（如"好的"、"嗯"、"收到"）返回false，有实际内容返回true
2. emotion: 根据文本情感选择最合适的表情
3. intensity: 情感强烈则接近1.0，平淡则接近0.3
4. has_changed: 与上次情感相比，明显不同则返回true；如果没有上次情感或明显变化，返回true

现在请分析上述文本并返回JSON格式结果："""

        # 调用LLM（使用llm_fast小模型）
        result = await llm_client.chat_completion(
            prompt=prompt,
            temperature=0.3
        )

        if not result.get("success"):
            raise RuntimeError(f"LLM调用失败: {result.get('error')}")

        # 解析JSON结果
        content = result.get("content", "").strip()

        # 尝试提取JSON（处理可能的markdown代码块）
        if "```json" in content:
            # 提取 ```json ... ``` 中的内容
            start = content.find("```json") + 7
            end = content.rfind("```")
            content = content[start:end].strip()
        elif "```" in content:
            # 提取 ``` ... ``` 中的内容
            start = content.find("```") + 3
            end = content.rfind("```")
            content = content[start:end].strip()

        try:
            llm_result = json.loads(content)
        except json.JSONDecodeError:
            self.logger.error(f"LLM返回的不是有效JSON: {content}")
            # 回退：返回默认值（允许触发）
            return {
                "is_important": True,
                "emotion": "neutral",
                "intensity": 1.0,
                "has_changed": True,
                "reason": "JSON解析失败，使用默认值"
            }

        return llm_result

    def record_trigger(self, emotion: str, intensity: float, text: str):
        """记录触发事件

        Args:
            emotion: 情感名称
            intensity: 强度
            text: 原始文本
        """
        self.time_controller.update_trigger_time()
        self.emotion_history.record_emotion(emotion, intensity, text)

        if self.debug_mode:
            self.logger.debug(
                f"记录触发: 情感={emotion}, 强度={intensity:.2f}, "
                f"文本='{text[:30]}...'"
            )
