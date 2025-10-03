"""
消息存储操作类
提供消息的增删改查功能
"""

import json
import time
from typing import Optional, Dict, Any, List
from src.chat.message_receive.message import MessageRecvS4U
from src.mais4u.database.db_manager import get_db_manager
from src.common.logger import get_logger

logger = get_logger("message_storage")


class MessageStorage:
    """消息存储类，提供基于 SQLite 的消息存储功能"""
    
    def __init__(self):
        """初始化消息存储"""
        self.db_manager = get_db_manager()
    
    async def store_message(self, message: MessageRecvS4U, chat_stream: Any) -> None:
        """
        存储消息到数据库
        
        Args:
            message: 要存储的消息对象
            chat_stream: 聊天流对象
        """
        try:
            # 提取消息信息
            message_info = message.message_info
            user_info = message_info.user_info
            group_info = message_info.group_info
            
            # 准备消息数据
            message_data = {
                'message_id': message_info.message_id,
                'platform': message_info.platform,
                'user_id': user_info.user_id,
                'user_nickname': user_info.user_nickname,
                'group_id': group_info.group_id if group_info else None,
                'group_name': group_info.group_name if group_info else None,
                'message_text': message.processed_plain_text,
                'processed_text': message.processed_plain_text,
                'message_type': getattr(message, 'message_type', 'normal'),
                'is_gift': message.is_gift,
                'gift_name': getattr(message, 'gift_name', ''),
                'gift_count': getattr(message, 'gift_count', 0),
                'is_fake_gift': getattr(message, 'is_fake_gift', False),
                'voice_done': getattr(message, 'voice_done', ''),
                'priority_info': json.dumps(message.priority_info) if message.priority_info else None,
                'timestamp': time.time()
            }
            
            # 插入消息
            await self._insert_message(message_data)
            
            # 更新用户和群组信息
            await self._update_user_info(user_info, message_info.platform)
            if group_info:
                await self._update_group_info(group_info, message_info.platform)
            
            logger.debug(f"消息已存储: {user_info.user_nickname} - {message.processed_plain_text[:50]}...")
            
        except Exception as e:
            logger.error(f"存储消息失败: {e}", exc_info=True)
    
    async def _insert_message(self, message_data: Dict[str, Any]) -> None:
        """插入消息到数据库"""
        query = """
            INSERT OR REPLACE INTO messages 
            (message_id, platform, user_id, user_nickname, group_id, group_name, 
             message_text, processed_text, message_type, is_gift, gift_name, 
             gift_count, is_fake_gift, voice_done, priority_info, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        params = (
            message_data['message_id'],
            message_data['platform'],
            message_data['user_id'],
            message_data['user_nickname'],
            message_data['group_id'],
            message_data['group_name'],
            message_data['message_text'],
            message_data['processed_text'],
            message_data['message_type'],
            message_data['is_gift'],
            message_data['gift_name'],
            message_data['gift_count'],
            message_data['is_fake_gift'],
            message_data['voice_done'],
            message_data['priority_info'],
            message_data['timestamp']
        )
        
        await self.db_manager.execute_update(query, params)
    
    async def _update_user_info(self, user_info: Any, platform: str) -> None:
        """更新用户信息"""
        query = """
            INSERT OR REPLACE INTO users 
            (user_id, user_nickname, platform, last_seen_at, message_count)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, 
                   COALESCE((SELECT message_count FROM users WHERE user_id = ?), 0) + 1)
        """
        
        params = (user_info.user_id, user_info.user_nickname, platform, user_info.user_id)
        await self.db_manager.execute_update(query, params)
    
    async def _update_group_info(self, group_info: Any, platform: str) -> None:
        """更新群组信息"""
        query = """
            INSERT OR REPLACE INTO groups 
            (group_id, group_name, platform, last_seen_at, message_count)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP,
                   COALESCE((SELECT message_count FROM groups WHERE group_id = ?), 0) + 1)
        """
        
        params = (group_info.group_id, group_info.group_name, platform, group_info.group_id)
        await self.db_manager.execute_update(query, params)
    
    async def get_messages_by_user(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取指定用户的消息"""
        query = """
            SELECT * FROM messages 
            WHERE user_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """
        return await self.db_manager.execute_query(query, (user_id, limit))
    
    async def get_messages_by_group(self, group_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取指定群组的消息"""
        query = """
            SELECT * FROM messages 
            WHERE group_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """
        return await self.db_manager.execute_query(query, (group_id, limit))
    
    async def get_recent_messages(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取最近的消息"""
        query = """
            SELECT * FROM messages 
            ORDER BY timestamp DESC 
            LIMIT ?
        """
        return await self.db_manager.execute_query(query, (limit,))
    
    async def get_gift_messages(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取礼物消息"""
        query = """
            SELECT * FROM messages 
            WHERE is_gift = TRUE 
            ORDER BY timestamp DESC 
            LIMIT ?
        """
        return await self.db_manager.execute_query(query, (limit,))
    
    async def search_messages(self, keyword: str, limit: int = 50) -> List[Dict[str, Any]]:
        """搜索包含关键词的消息"""
        query = """
            SELECT * FROM messages 
            WHERE message_text LIKE ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """
        return await self.db_manager.execute_query(query, (f'%{keyword}%', limit))
    
    async def get_user_stats(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户统计信息"""
        query = """
            SELECT 
                u.*,
                COUNT(m.id) as total_messages,
                COUNT(CASE WHEN m.is_gift = TRUE THEN 1 END) as gift_messages,
                SUM(CASE WHEN m.is_gift = TRUE THEN m.gift_count ELSE 0 END) as total_gifts
            FROM users u
            LEFT JOIN messages m ON u.user_id = m.user_id
            WHERE u.user_id = ?
            GROUP BY u.user_id
        """
        results = await self.db_manager.execute_query(query, (user_id,))
        return results[0] if results else None
    
    async def get_group_stats(self, group_id: str) -> Optional[Dict[str, Any]]:
        """获取群组统计信息"""
        query = """
            SELECT 
                g.*,
                COUNT(m.id) as total_messages,
                COUNT(DISTINCT m.user_id) as unique_users
            FROM groups g
            LEFT JOIN messages m ON g.group_id = m.group_id
            WHERE g.group_id = ?
            GROUP BY g.group_id
        """
        results = await self.db_manager.execute_query(query, (group_id,))
        return results[0] if results else None
    
    async def cleanup_old_messages(self, days: int = 30) -> int:
        """清理指定天数前的旧消息"""
        cutoff_time = time.time() - (days * 24 * 60 * 60)
        query = "DELETE FROM messages WHERE timestamp < ?"
        return await self.db_manager.execute_update(query, (cutoff_time,))
