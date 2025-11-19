import difflib
import time
import json
from collections import defaultdict, deque
from typing import Dict, Optional, Set, Any

from maim_message import MessageBase
from src.core.pipeline_manager import MessagePipeline


class SimilarMessageFilterPipeline(MessagePipeline):
    """
    相似消息过滤管道，用于过滤短时间内内容高度相似的重复消息，减少消息冗余。
    适用于直播弹幕、评论等场景。
    原理：保留第一条消息，丢弃后来的相似消息，不修改任何消息内容。
    """

    # 默认优先级，介于较早处理和较晚处理之间
    priority = 500

    def __init__(self, config: Dict[str, Any]):
        """
        初始化相似消息过滤管道。

        Args:
            config: 合并后的配置字典，期望包含以下键:
                message_types (List[str]): 要处理的消息类型列表 (默认: ["text", "danmu", "comment"])
                similarity_threshold (float): 消息相似度阈值 (0.0-1.0) (默认: 0.85)
                time_window (float): 检查窗口的时间范围（秒）(默认: 5.0)
                min_message_length (int): 最小处理消息长度 (默认: 3)
                cross_user_filter (bool): 是否跨用户过滤相似消息 (默认: True)
        """
        super().__init__(config)  # 调用基类构造函数并传递配置

        # 从配置中读取参数，如果未提供则使用默认值
        self.message_types = self.config.get("message_types", ["text", "danmu", "comment"])
        self.similarity_threshold = self.config.get("similarity_threshold", 0.85)
        self.time_window = self.config.get("time_window", 5.0)
        self.min_message_length = self.config.get("min_message_length", 3)
        self.cross_user_filter = self.config.get("cross_user_filter", True)

        # 消息缓存，按群组ID分组存储
        # 结构: {group_id: deque([(timestamp, message_id, content, user_id), ...]}
        self.message_cache: Dict[str, deque] = defaultdict(deque)

        # 已处理的消息ID集合，用于避免重复处理
        self.processed_message_ids: Set[str] = set()

        # 记录被过滤的消息ID集合
        self.filtered_message_ids: Set[str] = set()

        # 清理线程状态
        self._cleanup_active = False
        self._last_cleanup_time = time.time()

        self.logger.info(
            f"相似消息过滤管道初始化: 相似度阈值={self.similarity_threshold}, 时间窗口={self.time_window}秒, "
            f"处理的消息类型={self.message_types}, 跨用户过滤={self.cross_user_filter}"
        )

    async def on_connect(self) -> None:
        """连接建立时初始化缓存"""
        self._cleanup_active = True
        self.logger.info("相似消息过滤管道已激活")

    async def on_disconnect(self) -> None:
        """连接断开时清理资源"""
        self._cleanup_active = False
        self._clear_caches()
        self.logger.info("相似消息过滤管道已清理资源")

    def _clear_caches(self) -> None:
        """清理所有缓存"""
        self.message_cache.clear()
        self.processed_message_ids.clear()
        self.filtered_message_ids.clear()
        self.logger.debug("已清理相似消息过滤管道的所有缓存")

    def _clean_expired_messages(self) -> None:
        """清理过期的消息缓存"""
        now = time.time()
        cutoff_time = now - self.time_window

        # 跳过过于频繁的清理
        if now - self._last_cleanup_time < self.time_window / 2:
            return

        self._last_cleanup_time = now

        # 清理前的缓存状态
        cache_stats_before = {
            "group_count": len(self.message_cache),
            "message_count": sum(len(queue) for queue in self.message_cache.values()),
        }

        # 清理过期的消息缓存
        for group_id in list(self.message_cache.keys()):
            # 清理队列中的过期消息
            expired_count = 0
            while self.message_cache[group_id] and self.message_cache[group_id][0][0] < cutoff_time:
                self.message_cache[group_id].popleft()
                expired_count += 1

            if expired_count > 0:
                self.logger.debug(f"群组 {group_id} 清理了 {expired_count} 条过期消息")

            # 如果群组的队列为空，则删除该群组的记录
            if not self.message_cache[group_id]:
                del self.message_cache[group_id]
                self.logger.debug(f"群组 {group_id} 缓存为空，已删除")

        # 清理处理过的消息ID（保留近期的一些ID以防止重复处理）
        processed_ids_before = len(self.processed_message_ids)
        filtered_ids_before = len(self.filtered_message_ids)

        # 简单地保留最近的1000条处理过的消息ID
        if len(self.processed_message_ids) > 1000:
            extra_count = len(self.processed_message_ids) - 1000
            expired_ids = set(list(self.processed_message_ids)[:extra_count])
            self.processed_message_ids -= expired_ids

            # 同步清理已过滤的消息ID
            self.filtered_message_ids = {
                msg_id for msg_id in self.filtered_message_ids if msg_id in self.processed_message_ids
            }

        # 清理后的缓存状态
        cache_stats_after = {
            "group_count": len(self.message_cache),
            "message_count": sum(len(queue) for queue in self.message_cache.values()),
            "processed_ids": len(self.processed_message_ids),
            "filtered_ids": len(self.filtered_message_ids),
        }

        self.logger.debug(
            f"清理结果: 处理ID从{processed_ids_before}减至{cache_stats_after['processed_ids']}, "
            f"过滤ID从{filtered_ids_before}减至{cache_stats_after['filtered_ids']}, "
            f"当前缓存群组数={cache_stats_after['group_count']}, "
            f"消息数={cache_stats_after['message_count']}"
        )

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """
        计算两段文本的相似度。

        Args:
            text1: 第一段文本
            text2: 第二段文本

        Returns:
            相似度值，范围为0.0-1.0
        """
        # 使用difflib计算字符串相似度
        similarity = difflib.SequenceMatcher(None, text1, text2).ratio()

        # 增加额外的相似度检查：处理完全包含关系
        # 例如 "666" 和 "6666" 应该被视为相似
        if text1 in text2 or text2 in text1:
            longer = max(len(text1), len(text2))
            shorter = min(len(text1), len(text2))
            if shorter > 0 and shorter >= longer * 0.5:  # 较短的至少是较长的一半长度
                contained_similarity = shorter / longer
                similarity = max(similarity, contained_similarity)

        self.logger.debug(f"计算相似度: '{text1}' vs '{text2}' = {similarity:.4f}")
        return similarity

    def _get_message_content(self, message: MessageBase) -> Optional[str]:
        """
        从消息对象中提取文本内容。

        Args:
            message: 消息对象

        Returns:
            消息的文本内容，如果无法提取则返回None
        """
        if not message.message_segment:
            self.logger.debug("消息没有segment部分，无法提取内容")
            return None

        # 根据消息类型提取内容
        segment_type = message.message_segment.type

        if segment_type == "text":
            # 文本消息，直接从内容中提取
            try:
                content = message.message_segment.data
                if not isinstance(content, str):
                    # 尝试转换为字符串
                    try:
                        content = str(content)
                        self.logger.warning(f"文本消息内容不是字符串类型，已尝试转换: {content}")
                    except:
                        self.logger.warning(f"文本消息内容不是字符串类型且无法转换: {type(content)}")
                        return None
                self.logger.debug(f"提取到文本消息内容: '{content}'")
                return content
            except (AttributeError, TypeError) as e:
                self.logger.error(f"提取文本消息内容时出错: {e}，消息segment: {message.message_segment}")
                return None

        # 其他类型，暂不处理
        self.logger.debug(f"不支持的消息类型: {segment_type}，跳过处理")
        return None

    def _should_process_message(self, message: MessageBase) -> bool:
        """
        判断是否应该处理该消息。

        Args:
            message: 消息对象

        Returns:
            是否应该处理该消息
        """
        # 检查消息ID是否已处理过
        if message.message_info.message_id in self.processed_message_ids:
            self.logger.debug(f"消息ID {message.message_info.message_id} 已处理过，跳过")
            return False

        # 检查消息类型是否在处理列表中
        if not message.message_segment:
            self.logger.debug("消息没有segment部分，跳过处理")
            return False

        if message.message_segment.type not in self.message_types:
            self.logger.debug(f"消息类型 '{message.message_segment.type}' 不在处理列表中，跳过处理")
            return False

        # 检查消息内容长度是否满足最小长度要求
        content = self._get_message_content(message)
        if not content:
            self.logger.debug("无法提取消息内容，跳过处理")
            return False

        if len(content) < self.min_message_length:
            self.logger.debug(f"消息内容长度 {len(content)} 小于最小要求 {self.min_message_length}，跳过处理")
            return False

        return True

    def _check_similar_messages(self, group_id: str, user_id: str, message_id: str, content: str) -> bool:
        """
        检查是否有相似消息。如果找到相似消息，就将当前消息标记为需要过滤。

        Args:
            group_id: 群组ID
            user_id: 用户ID
            message_id: 消息ID
            content: 消息内容

        Returns:
            是否找到了相似消息（并需要过滤当前消息）
        """
        # 标记是否找到相似消息
        found_similar = False
        now = time.time()
        cutoff_time = now - self.time_window

        self.logger.debug(f"检查消息 '{content}' 是否有相似消息, 群组={group_id}, 用户={user_id}")

        # 检查缓存中是否有相似消息
        if group_id in self.message_cache:
            cache_size = len(self.message_cache[group_id])
            self.logger.debug(f"当前群组缓存消息数量: {cache_size}")

            for cached_ts, cached_msg_id, cached_content, cached_user_id in self.message_cache[group_id]:
                if cached_ts < cutoff_time:
                    continue  # 跳过过期消息

                # 如果不是跨用户过滤且用户不同，则跳过
                if not self.cross_user_filter and cached_user_id != user_id:
                    self.logger.debug(
                        f"不允许跨用户过滤，跳过不同用户的消息 (当前用户={user_id}, 缓存用户={cached_user_id})"
                    )
                    continue

                # 计算相似度
                similarity = self._calculate_similarity(content, cached_content)

                if similarity >= self.similarity_threshold:
                    # 找到相似消息，标记当前消息为已过滤
                    self.filtered_message_ids.add(message_id)
                    found_similar = True
                    self.logger.info(
                        f"消息 '{content}' 与缓存消息 '{cached_content}' 相似度为 {similarity:.2f}，已标记为过滤"
                    )
                    break
        else:
            self.logger.debug(f"群组 {group_id} 没有缓存消息")

        return found_similar

    async def process_message(self, message: MessageBase) -> Optional[MessageBase]:
        """
        处理消息，检查是否需要过滤相似消息。

        Args:
            message: 要处理的消息对象

        Returns:
            处理后的消息对象，如果消息被过滤则返回None
        """
        # 清理过期消息
        self._clean_expired_messages()

        # 尝试记录输入消息的内容（用于调试）
        try:
            message_dict = message.to_dict()
            message_type = message.message_segment.type if message.message_segment else "unknown"
            self.logger.debug(
                f"处理新消息: 类型={message_type}, 内容={json.dumps(message_dict, ensure_ascii=False)[:200]}..."
            )
        except Exception as e:
            self.logger.warning(f"记录输入消息内容时出错: {e}")

        # 获取消息属性
        message_id = message.message_info.message_id

        # 检查消息是否已处理
        if message_id in self.processed_message_ids:
            self.logger.debug(f"消息ID {message_id} 已处理过，跳过")
            # 如果已被过滤，返回None，否则返回原消息
            return None if message_id in self.filtered_message_ids else message

        # 如果消息ID已在过滤列表中，直接丢弃
        if message_id in self.filtered_message_ids:
            self.logger.info(f"消息 {message_id} 已被过滤，丢弃")
            self.processed_message_ids.add(message_id)  # 确保标记为已处理
            return None

        # 检查是否应该处理该消息
        if not self._should_process_message(message):
            # 不处理的消息直接返回原样
            self.logger.debug("消息不符合处理条件，直接返回原消息")
            return message

        # 获取群组ID和用户ID
        group_id = "default"
        user_id = "anonymous"
        user_name = "unknown"

        if message.message_info.group_info and message.message_info.group_info.group_id:
            group_id = message.message_info.group_info.group_id

        if message.message_info.user_info:
            if message.message_info.user_info.user_id:
                user_id = message.message_info.user_info.user_id
            if hasattr(message.message_info.user_info, "nickname") and message.message_info.user_info.nickname:
                user_name = message.message_info.user_info.nickname

        self.logger.debug(f"消息信息: ID={message_id}, 群组={group_id}, 用户={user_id}({user_name})")

        # 提取消息内容
        content = self._get_message_content(message)
        if not content:
            self.logger.warning(f"无法提取消息内容，返回原消息: {message_id}")
            return message

        self.logger.info(f"处理消息: '{content}', 用户={user_name}, ID={message_id}")

        # 记录当前时间作为消息时间戳
        timestamp = time.time()

        # 标记为已处理
        self.processed_message_ids.add(message_id)

        # 检查是否有相似消息
        found_similar = self._check_similar_messages(group_id, user_id, message_id, content)

        if found_similar:
            # 如果找到相似消息，丢弃当前消息
            self.logger.info(f"消息 '{content}' 与已有消息相似，丢弃此消息 ID={message_id}")
            return None
        else:
            # 没有找到相似消息，将当前消息添加到缓存，并返回原始消息
            self.message_cache[group_id].append((timestamp, message_id, content, user_id))
            self.logger.debug(f"没有找到相似消息，添加到缓存并返回原始消息: '{content}'")
            return message
