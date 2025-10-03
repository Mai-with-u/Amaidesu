"""
数据库初始化脚本
在应用启动时初始化 SQLite 数据库
"""

import asyncio
from src.mais4u.database.db_manager import initialize_database, close_database
from src.common.logger import get_logger

logger = get_logger("database_init")


async def init_database():
    """初始化数据库"""
    try:
        await initialize_database()
        logger.info("数据库初始化完成")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise


async def cleanup_database():
    """清理数据库连接"""
    try:
        await close_database()
        logger.info("数据库连接已关闭")
    except Exception as e:
        logger.error(f"关闭数据库连接失败: {e}")


if __name__ == "__main__":
    # 测试数据库初始化
    asyncio.run(init_database())
