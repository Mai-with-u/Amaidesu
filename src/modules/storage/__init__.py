"""
Amaidesu 存储模块

本模块提供：
- SQLiteConnectionManager：每线程连接 + WAL + SAVEPOINT 方案
- SQLiteStore：asyncio.to_thread 封装的存储访问层（防"忘 to_thread"）
- schema：业务表 + 私有表 DDL 权威定义与版本常量
- migrations：Schema 版本迁移注册表（一版本一文件）
- simulated 贯穿列 + 消费者排除常量
"""

from src.modules.storage.connection import ManagedSQLiteConnection, SQLiteConnectionManager
from src.modules.storage.schema import (
    SCHEMA_VERSION,
    build_schema_sql,
    list_expected_tables,
    list_private_tables,
)
from src.modules.storage.sqlite_store import SQLiteStore, sqlite_store
from src.modules.storage.storage_ledger import StorageLedger

__all__ = [
    "ManagedSQLiteConnection",
    "SQLiteConnectionManager",
    "SCHEMA_VERSION",
    "build_schema_sql",
    "list_expected_tables",
    "list_private_tables",
    "SQLiteStore",
    "sqlite_store",
    "StorageLedger",
]
