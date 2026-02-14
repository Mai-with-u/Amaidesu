"""
脏话过滤管道

过滤 Intent 中的敏感词，适用于所有输出。

3域架构中的位置：
- Output Domain: 在 Intent → OutputProvider 转换中处理 Intent
- 在 OutputProviderManager._on_decision_intent() 之后、Provider 之前调用
"""

import os
import re
from typing import TYPE_CHECKING, Any, Dict, Optional, Set

from src.domains.output.pipelines.base import OutputPipelineBase
from src.modules.types.base.pipeline_stats import PipelineStats

if TYPE_CHECKING:
    from src.modules.types import Intent


class ProfanityFilterPipeline(OutputPipelineBase):
    """
    敏感词过滤管道

    检测并过滤 Intent 中的敏感词。
    """

    priority = 100  # 高优先级，确保在输出前过滤

    # 扩展统计信息，添加过滤计数
    class ExtendedPipelineStats(PipelineStats):
        """扩展的统计信息，包含过滤计数"""

        filtered_words_count: int = 0

    def __init__(self, config: Dict[str, Any]):
        """
        初始化脏话过滤管道

        Args:
            config: 配置字典，支持以下键:
                words (List[str]): 敏感词列表
                replacement (str): 替换文本 (默认: "【已过滤】")
                words_file (str): 敏感词文件路径（每行一个词）
                case_sensitive (bool): 是否区分大小写 (默认: False)
                drop_on_match (bool): 匹配到敏感词是否丢弃整个消息 (默认: False)
        """
        super().__init__(config)

        self._replacement = self.config.get("replacement", "【已过滤】")
        self._case_sensitive = self.config.get("case_sensitive", False)
        self._drop_on_match = self.config.get("drop_on_match", False)

        # 使用扩展的统计信息
        self._stats = self.ExtendedPipelineStats()

        # 加载敏感词列表
        self._profanity_words: Set[str] = set()
        self._load_profanity_words()

        self.logger.info(
            f"ProfanityFilterPipeline 初始化: "
            f"敏感词数量={len(self._profanity_words)}, "
            f"替换文本='{self._replacement}', "
            f"丢弃消息={self._drop_on_match}"
        )

    def _load_profanity_words(self) -> None:
        """加载敏感词列表"""
        # 从配置加载
        words_from_config = self.config.get("words", [])
        if isinstance(words_from_config, list):
            self._profanity_words.update(words_from_config)

        # 从文件加载（支持两种配置键：words_file 和 wordlist_file）
        words_file = self.config.get("words_file") or self.config.get("wordlist_file")
        if words_file:
            file_path = os.path.abspath(words_file)
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        words_from_file = [line.strip() for line in f if line.strip()]
                        self._profanity_words.update(words_from_file)
                        self.logger.info(f"从文件加载敏感词: {file_path} ({len(words_from_file)} 个)")
                except Exception as e:
                    self.logger.error(f"加载敏感词文件失败: {e}", exc_info=True)
            else:
                self.logger.warning(f"敏感词文件不存在: {file_path}")

        # 处理大小写
        if not self._case_sensitive:
            self._profanity_words = {word.lower() for word in self._profanity_words}

    async def _process(self, intent: "Intent") -> Optional["Intent"]:
        """
        处理 Intent，过滤敏感词

        Args:
            intent: 待处理的 Intent

        Returns:
            处理后的 Intent，或 None（如果配置了 drop_on_match）
        """
        has_match = False

        # 过滤 response_text
        if intent.response_text:
            filtered_text, text_has_match = self._filter_text(intent.response_text)
            if text_has_match:
                has_match = True
                intent.response_text = filtered_text
                self.logger.debug(f"response_text 已过滤: '{filtered_text}'")

        # 如果匹配到敏感词且配置了丢弃
        if has_match and self._drop_on_match:
            self._stats.dropped_count += 1
            self.logger.debug("敏感词过滤: 消息已丢弃（配置了 drop_on_match）")
            return None

        return intent

    def _filter_text(self, text: str) -> tuple[str, bool]:
        """
        过滤文本中的敏感词

        Args:
            text: 待过滤的文本

        Returns:
            (过滤后的文本, 是否匹配到敏感词)
        """
        if not self._profanity_words:
            return text, False

        filtered_text = text
        has_match = False
        matched_words = set()

        # 检查每个敏感词
        for profanity_word in sorted(self._profanity_words, key=len, reverse=True):
            search_text = filtered_text if self._case_sensitive else filtered_text.lower()
            search_word = profanity_word if self._case_sensitive else profanity_word.lower()

            if search_word in search_text:
                has_match = True
                matched_words.add(profanity_word)
                # 替换敏感词（保持原文大小写）
                if self._case_sensitive:
                    filtered_text = filtered_text.replace(profanity_word, self._replacement)
                else:
                    # 不区分大小写时，需要更复杂的替换逻辑
                    pattern = re.compile(re.escape(profanity_word), re.IGNORECASE)
                    filtered_text = pattern.sub(self._replacement, filtered_text)

        if has_match:
            self._stats.filtered_words_count += len(matched_words)
            self.logger.debug(f"检测到敏感词: {matched_words}")

        return filtered_text, has_match

    def get_info(self) -> Dict[str, Any]:
        """获取 Pipeline 信息"""
        info = super().get_info()
        info.update(
            {
                "replacement": self._replacement,
                "case_sensitive": self._case_sensitive,
                "drop_on_match": self._drop_on_match,
                "profanity_words_count": len(self._profanity_words),
            }
        )
        return info

    def add_profanity_word(self, word: str) -> None:
        """
        添加敏感词

        Args:
            word: 敏感词
        """
        if not self._case_sensitive:
            word = word.lower()
        self._profanity_words.add(word)
        self.logger.info(f"添加敏感词: '{word}'")

    def remove_profanity_word(self, word: str) -> None:
        """
        移除敏感词

        Args:
            word: 敏感词
        """
        if not self._case_sensitive:
            word = word.lower()
        self._profanity_words.discard(word)
        self.logger.info(f"移除敏感词: '{word}'")

    def get_profanity_words(self) -> Set[str]:
        """获取所有敏感词"""
        return self._profanity_words.copy()
