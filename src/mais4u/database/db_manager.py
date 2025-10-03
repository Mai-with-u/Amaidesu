"""
SQLite 数据库连接管理器
提供数据库连接、表创建和基本操作功能
"""

import sqlite3
import asyncio
import aiosqlite
from pathlib import Path
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager
from src.common.logger import get_logger

logger = get_logger("database")


class DatabaseManager:
    """SQLite 数据库管理器"""
    
    def __init__(self, db_path: str = "data/s4u_messages.db"):
        """
        初始化数据库管理器
        
        Args:
            db_path: 数据库文件路径
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: Optional[aiosqlite.Connection] = None
        
    async def initialize(self) -> None:
        """初始化数据库连接和表结构"""
        try:
            self._connection = await aiosqlite.connect(self.db_path)
            await self._create_tables()
            logger.info(f"数据库初始化完成: {self.db_path}")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            raise
    
    async def close(self) -> None:
        """关闭数据库连接"""
        if self._connection:
            await self._connection.close()
            logger.info("数据库连接已关闭")
    
    @asynccontextmanager
    async def get_connection(self):
        """获取数据库连接的上下文管理器"""
        if not self._connection:
            await self.initialize()
        
        try:
            yield self._connection
        except Exception as e:
            logger.error(f"数据库操作失败: {e}")
            raise
    
    async def _create_tables(self) -> None:
        """创建数据库表结构"""
        async with self.get_connection() as conn:
            # 消息表
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT UNIQUE NOT NULL,
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_nickname TEXT NOT NULL,
                    group_id TEXT,
                    group_name TEXT,
                    message_text TEXT NOT NULL,
                    processed_text TEXT,
                    message_type TEXT DEFAULT 'normal',
                    is_gift BOOLEAN DEFAULT FALSE,
                    gift_name TEXT,
                    gift_count INTEGER DEFAULT 0,
                    is_fake_gift BOOLEAN DEFAULT FALSE,
                    voice_done TEXT,
                    priority_info TEXT,
                    timestamp REAL NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 用户表
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    user_nickname TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    message_count INTEGER DEFAULT 0,
                    total_gifts INTEGER DEFAULT 0
                )
            """)
            
            # 群组表
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    group_id TEXT PRIMARY KEY,
                    group_name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    message_count INTEGER DEFAULT 0
                )
            """)
            
            # 创建索引
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_group_id ON messages(group_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_platform ON messages(platform)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(message_type)")
            
            await conn.commit()
            logger.info("数据库表结构创建完成")
    
    async def execute_query(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """执行查询并返回结果"""
        async with self.get_connection() as conn:
            async with conn.execute(query, params) as cursor:
                columns = [description[0] for description in cursor.description]
                rows = await cursor.fetchall()
                return [dict(zip(columns, row)) for row in rows]
    
    async def execute_update(self, query: str, params: tuple = ()) -> int:
        """执行更新操作并返回影响的行数"""
        async with self.get_connection() as conn:
            cursor = await conn.execute(query, params)
            await conn.commit()
            return cursor.rowcount


# 全局数据库管理器实例
_db_manager: Optional[DatabaseManager] = None


def get_db_manager() -> DatabaseManager:
    """获取全局数据库管理器实例"""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


async def initialize_database() -> None:
    """初始化数据库"""
    db_manager = get_db_manager()
    await db_manager.initialize()


async def close_database() -> None:
    """关闭数据库连接"""
    global _db_manager
    if _db_manager:
        await _db_manager.close()
        _db_manager = None
