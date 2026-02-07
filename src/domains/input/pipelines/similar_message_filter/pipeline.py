"""
相似文本过滤管道（TextPipeline 版本）

过滤短时间内内容高度相似的重复消息，减少消息冗余。
适用于直播弹幕、评论等场景。

3域架构中的位置：
- InputLayer (Input Domain): 在 RawData → NormalizedMessage 转换中处理文本
- 在 InputLayer.normalize() 方法中调用
- 用于过滤相似输入消息

这是 SimilarMessageFilterPipeline 的现代化版本，使用 TextPipeline 接口。

原理：保留第一条消息，丢弃后来的相似消息。
"""

import difflib
import time
from collections import defaultdict, deque
from typing import Any, Dict, Optional

from src.domains.input.pipelines.manager import TextPipelineBase


class SimilarTextFilterPipeline(TextPipelineBase):
    """
    相似文本过滤管道（TextPipeline 版本）

    使用滑动窗口和文本相似度算法过滤重复消息。
    """

    priority = 500  # 中等优先级

    def __init__(self, config: Dict[str, Any]):
        """
        初始化相似文本过滤管道

        Args:
            config: 配置字典，支持以下键:
                similarity_threshold (float): 相似度阈值 (0.0-1.0) (默认: 0.85)
                time_window (float): 检查窗口的时间范围（秒）(默认: 5.0)
                min_text_length (int): 最小处理文本长度 (默认: 3)
                cross_user_filter (bool): 是否跨用户过滤相似消息 (默认: True)
        """
        super().__init__(config)

        self._similarity_threshold = self.config.get("similarity_threshold", 0.85)
        self._time_window = self.config.get("time_window", 5.0)
        self._min_text_length = self.config.get("min_text_length", 3)
        self._cross_user_filter = self.config.get("cross_user_filter", True)

        # 文本缓存，按 group_id 分组存储
        # 结构: {group_id: deque([(timestamp, text, user_id), ...]}
        self._text_cache: Dict[str, deque] = defaultdict(deque)

        # 清理时间记录
        self._last_cleanup_time = time.time()

        self.logger.info(
            f"SimilarTextFilterPipeline 初始化: "
            f"相似度阈值={self._similarity_threshold}, "
            f"时间窗口={self._time_window}秒, "
            f"跨用户过滤={self._cross_user_filter}"
        )

    async def _process(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
        """
        处理文本，检查是否需要过滤

        Args:
            text: 待处理的文本
            metadata: 元数据（需要 user_id、group_id）

        Returns:
            原始文本（允许通过）或 None（相似消息被过滤）
        """
        # 清理过期缓存
        self._clean_expired_texts()

        # 检查文本长度
        if len(text) < self._min_text_length:
            self.logger.debug(f"文本长度 {len(text)} 小于最小要求 {self._min_text_length}，跳过过滤")
            return text

        user_id = metadata.get("user_id", "unknown")
        group_id = metadata.get("group_id", "default")
        now = time.time()

        # 检查是否有相似文本
        if self._has_similar_text(group_id, user_id, text):
            self._stats.dropped_count += 1
            self.logger.info(
                f"相似文本过滤: text_preview='{text[:50]}{'...' if len(text) > 50 else ''}', "
                f"user_id={user_id}, group_id={group_id}"
            )
            return None  # 丢弃

        # 添加到缓存
        self._text_cache[group_id].append((now, text, user_id))
        self.logger.debug(f"文本通过过滤并添加到缓存: '{text[:30]}...'")

        return text

    def _clean_expired_texts(self) -> None:
        """清理过期的文本缓存"""
        now = time.time()

        # 避免过于频繁的清理
        if now - self._last_cleanup_time < self._time_window / 2:
            return

        self._last_cleanup_time = now
        cutoff_time = now - self._time_window

        # 清理各组的过期文本
        for group_id in list(self._text_cache.keys()):
            while self._text_cache[group_id] and self._text_cache[group_id][0][0] < cutoff_time:
                self._text_cache[group_id].popleft()

            # 如果组为空，删除
            if not self._text_cache[group_id]:
                del self._text_cache[group_id]

    def _has_similar_text(self, group_id: str, user_id: str, text: str) -> bool:
        """
        检查缓存中是否有相似文本

        Args:
            group_id: 组ID
            user_id: 用户ID
            text: 待检查的文本

        Returns:
            是否找到相似文本
        """
        if group_id not in self._text_cache:
            return False

        now = time.time()
        cutoff_time = now - self._time_window

        for cached_ts, cached_text, cached_user_id in self._text_cache[group_id]:
            if cached_ts < cutoff_time:
                continue

            # 如果不是跨用户过滤且用户不同，则跳过
            if not self._cross_user_filter and cached_user_id != user_id:
                continue

            # 计算相似度
            similarity = self._calculate_similarity(text, cached_text)

            if similarity >= self._similarity_threshold:
                self.logger.debug(
                    f"发现相似文本 (相似度={similarity:.2f}): '{text[:30]}...' vs '{cached_text[:30]}...'"
                )
                return True

        return False

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """
        计算两段文本的相似度

        Args:
            text1: 第一段文本
            text2: 第二段文本

        Returns:
            相似度值 (0.0-1.0)
        """
        # 使用 difflib 计算字符串相似度
        similarity = difflib.SequenceMatcher(None, text1, text2).ratio()

        # 处理包含关系：如 "666" 和 "6666"
        if text1 in text2 or text2 in text1:
            longer = max(len(text1), len(text2))
            shorter = min(len(text1), len(text2))
            if shorter > 0 and shorter >= longer * 0.5:
                contained_similarity = shorter / longer
                similarity = max(similarity, contained_similarity)

        return similarity

    def get_info(self) -> Dict[str, Any]:
        """获取 Pipeline 信息"""
        info = super().get_info()
        info.update(
            {
                "similarity_threshold": self._similarity_threshold,
                "time_window": self._time_window,
                "min_text_length": self._min_text_length,
                "cross_user_filter": self._cross_user_filter,
                "cache_groups": len(self._text_cache),
                "cached_texts": sum(len(q) for q in self._text_cache.values()),
            }
        )
        return info

    async def reset(self) -> None:
        """重置所有状态"""
        self._text_cache.clear()
        self._last_cleanup_time = time.time()
        self.reset_stats()
        self.logger.info("SimilarTextFilterPipeline 已重置状态")
