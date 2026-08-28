"""
Amaidesu 存储模块（Wave 3 新增）

本模块提供：
- SQLiteConnectionManager：复用 MaiBot 的每线程连接 + WAL + SAVEPOINT 方案
- sqlite_store：asyncio.to_thread 封装的同步 store（防"忘 to_thread"）
- schema：11 表权威定义（参见 .omo/drafts/amaidesu-v2-storage-schema.md）
- simulated 贯穿列 + 消费者排除常量

权威参考：
- 架构主文档：.omo/drafts/amaidesu-v2-architecture.md §1.6 / §1.50
- 存储 Schema：.omo/drafts/amaidesu-v2-storage-schema.md
- MaiBot 参考：src/A_memorix/core/storage/sqlite_connection.py（176 行）
"""

from src.modules.storage.connection import ManagedSQLiteConnection, SQLiteConnectionManager
from src.modules.storage.schema import (
    SCHEMA_VERSION,
    SchemaMigration,
    build_schema_sql,
    list_expected_tables,
)
from src.modules.storage.sqlite_store import SQLiteStore, sqlite_store
from src.modules.storage.storage_ledger import StorageLedger, make_room_message

__all__ = [
    "ManagedSQLiteConnection",
    "SQLiteConnectionManager",
    "SCHEMA_VERSION",
    "SchemaMigration",
    "build_schema_sql",
    "list_expected_tables",
    "SQLiteStore",
    "sqlite_store",
    "StorageLedger",
    "make_room_message",
]
