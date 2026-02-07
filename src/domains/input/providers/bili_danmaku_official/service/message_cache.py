# src/domains/input/providers/bili_danmaku_official/service/message_cache.py

from typing import Dict, List, Optional
from maim_message import MessageBase


class MessageCacheService:
    """消息缓存服务，用于存储和检索消息"""

    def __init__(self, max_cache_size: int = 1000):
        """
        初始化消息缓存服务

        Args:
            max_cache_size: 最大缓存消息数量
        """
        self.cache: Dict[str, MessageBase] = {}
        self.max_cache_size = max_cache_size
        self.access_order: List[str] = []  # 用于LRU淘汰

    def cache_message(self, message: MessageBase):
        """
        缓存消息

        Args:
            message: 要缓存的消息
        """
        message_id = message.message_info.message_id

        # 如果消息已存在，更新访问顺序
        if message_id in self.cache:
            self.access_order.remove(message_id)
            self.access_order.append(message_id)
            # 更新消息内容
            self.cache[message_id] = message
        else:
            # 如果缓存已满，删除最旧的消息
            if len(self.cache) >= self.max_cache_size:
                oldest_id = self.access_order.pop(0)
                del self.cache[oldest_id]

            # 添加新消息
            self.cache[message_id] = message
            self.access_order.append(message_id)

    def get_message(self, message_id: str) -> Optional[MessageBase]:
        """
        根据消息ID获取缓存的消息

        Args:
            message_id: 消息ID

        Returns:
            缓存的消息，如果不存在则返回None
        """
        if message_id in self.cache:
            # 更新访问顺序（LRU）
            self.access_order.remove(message_id)
            self.access_order.append(message_id)
            return self.cache[message_id]
        return None

    def clear_cache(self):
        """清空缓存"""
        self.cache.clear()
        self.access_order.clear()

    def get_cache_size(self) -> int:
        """获取当前缓存大小"""
        return len(self.cache)

    def get_recent_messages(self, count: int = 10) -> List[MessageBase]:
        """
        获取最近的消息

        Args:
            count: 要获取的消息数量

        Returns:
            最近的消息列表
        """
        recent_ids = self.access_order[-count:] if count <= len(self.access_order) else self.access_order
        return [self.cache[msg_id] for msg_id in recent_ids if msg_id in self.cache]

    def remove_message(self, message_id: str) -> bool:
        """
        移除指定的消息

        Args:
            message_id: 要移除的消息ID

        Returns:
            是否成功移除
        """
        if message_id in self.cache:
            del self.cache[message_id]
            self.access_order.remove(message_id)
            return True
        return False
