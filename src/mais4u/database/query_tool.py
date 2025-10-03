"""
数据库查询工具
提供简单的命令行接口来查询和操作数据库
"""

import asyncio
import argparse
from src.mais4u.database.db_manager import get_db_manager
from src.mais4u.database.message_storage import MessageStorage
from src.common.logger import get_logger

logger = get_logger("db_query_tool")


class DatabaseQueryTool:
    """数据库查询工具类"""
    
    def __init__(self):
        self.db_manager = get_db_manager()
        self.message_storage = MessageStorage()
    
    async def show_recent_messages(self, limit: int = 10):
        """显示最近的消息"""
        print(f"\n=== 最近 {limit} 条消息 ===")
        messages = await self.message_storage.get_recent_messages(limit)
        
        for msg in messages:
            print(f"[{msg['timestamp']:.0f}] {msg['user_nickname']}: {msg['message_text'][:100]}...")
            if msg['is_gift']:
                print(f"  🎁 礼物: {msg['gift_name']} x{msg['gift_count']}")
    
    async def show_user_stats(self, user_id: str):
        """显示用户统计信息"""
        print(f"\n=== 用户统计: {user_id} ===")
        stats = await self.message_storage.get_user_stats(user_id)
        
        if stats:
            print(f"用户昵称: {stats['user_nickname']}")
            print(f"平台: {stats['platform']}")
            print(f"首次出现: {stats['first_seen_at']}")
            print(f"最后出现: {stats['last_seen_at']}")
            print(f"总消息数: {stats['total_messages']}")
            print(f"礼物消息数: {stats['gift_messages']}")
            print(f"总礼物数: {stats['total_gifts']}")
        else:
            print("用户不存在")
    
    async def show_group_stats(self, group_id: str):
        """显示群组统计信息"""
        print(f"\n=== 群组统计: {group_id} ===")
        stats = await self.message_storage.get_group_stats(group_id)
        
        if stats:
            print(f"群组名称: {stats['group_name']}")
            print(f"平台: {stats['platform']}")
            print(f"首次出现: {stats['first_seen_at']}")
            print(f"最后出现: {stats['last_seen_at']}")
            print(f"总消息数: {stats['total_messages']}")
            print(f"唯一用户数: {stats['unique_users']}")
        else:
            print("群组不存在")
    
    async def search_messages(self, keyword: str, limit: int = 20):
        """搜索消息"""
        print(f"\n=== 搜索关键词: {keyword} ===")
        messages = await self.message_storage.search_messages(keyword, limit)
        
        for msg in messages:
            print(f"[{msg['timestamp']:.0f}] {msg['user_nickname']}: {msg['message_text']}")
    
    async def show_gift_messages(self, limit: int = 20):
        """显示礼物消息"""
        print(f"\n=== 最近 {limit} 条礼物消息 ===")
        messages = await self.message_storage.get_gift_messages(limit)
        
        for msg in messages:
            print(f"[{msg['timestamp']:.0f}] {msg['user_nickname']} 送出了 {msg['gift_name']} x{msg['gift_count']}")
    
    async def show_database_stats(self):
        """显示数据库统计信息"""
        print("\n=== 数据库统计信息 ===")
        
        # 总消息数
        result = await self.db_manager.execute_query("SELECT COUNT(*) as count FROM messages")
        total_messages = result[0]['count']
        print(f"总消息数: {total_messages}")
        
        # 用户数
        result = await self.db_manager.execute_query("SELECT COUNT(*) as count FROM users")
        total_users = result[0]['count']
        print(f"总用户数: {total_users}")
        
        # 群组数
        result = await self.db_manager.execute_query("SELECT COUNT(*) as count FROM groups")
        total_groups = result[0]['count']
        print(f"总群组数: {total_groups}")
        
        # 礼物消息数
        result = await self.db_manager.execute_query("SELECT COUNT(*) as count FROM messages WHERE is_gift = TRUE")
        gift_messages = result[0]['count']
        print(f"礼物消息数: {gift_messages}")
        
        # 平台分布
        result = await self.db_manager.execute_query("SELECT platform, COUNT(*) as count FROM messages GROUP BY platform")
        print("\n平台分布:")
        for row in result:
            print(f"  {row['platform']}: {row['count']} 条消息")


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="数据库查询工具")
    parser.add_argument("--recent", type=int, help="显示最近N条消息")
    parser.add_argument("--user", type=str, help="显示指定用户的统计信息")
    parser.add_argument("--group", type=str, help="显示指定群组的统计信息")
    parser.add_argument("--search", type=str, help="搜索包含关键词的消息")
    parser.add_argument("--gifts", type=int, help="显示最近N条礼物消息")
    parser.add_argument("--stats", action="store_true", help="显示数据库统计信息")
    
    args = parser.parse_args()
    
    tool = DatabaseQueryTool()
    
    try:
        if args.recent:
            await tool.show_recent_messages(args.recent)
        elif args.user:
            await tool.show_user_stats(args.user)
        elif args.group:
            await tool.show_group_stats(args.group)
        elif args.search:
            await tool.search_messages(args.search)
        elif args.gifts:
            await tool.show_gift_messages(args.gifts)
        elif args.stats:
            await tool.show_database_stats()
        else:
            # 默认显示最近10条消息和统计信息
            await tool.show_recent_messages(10)
            await tool.show_database_stats()
    
    except Exception as e:
        logger.error(f"查询失败: {e}")
    finally:
        await tool.db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
