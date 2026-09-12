"""
Amaidesu 存储模块

本模块提供：
- SQLiteDatabase：存储装配入口（连接/生命周期/schema + 按域仓储分发）
- repos：按表域切分的仓储（消费者按需注入，不经宽门面）
- SQLiteConnectionManager：每线程连接 + WAL + SAVEPOINT 方案
- schema：业务表 + 私有表 DDL 权威定义与版本常量
- migrations：Schema 版本迁移注册表（一版本一文件）
- simulated 贯穿列 + 消费者排除常量
"""

from src.modules.storage.connection import ManagedSQLiteConnection, SQLiteConnectionManager
from src.modules.storage.database import SQLiteDatabase, sqlite_database
from src.modules.storage.repos import (
    ChatRepo,
    EventRepo,
    LLMRepo,
    RundownRepo,
    SessionRepo,
    SimRepo,
    TopicRepo,
    ViewerRepo,
)
from src.modules.storage.schema import (
    SCHEMA_VERSION,
    build_schema_sql,
    list_expected_tables,
    list_private_tables,
)
from src.modules.storage.storage_ledger import StorageLedger

__all__ = [
    "ManagedSQLiteConnection",
    "SQLiteConnectionManager",
    "ChatRepo",
    "EventRepo",
    "LLMRepo",
    "RundownRepo",
    "SessionRepo",
    "SimRepo",
    "TopicRepo",
    "ViewerRepo",
    "SCHEMA_VERSION",
    "build_schema_sql",
    "list_expected_tables",
    "list_private_tables",
    "SQLiteDatabase",
    "sqlite_database",
    "StorageLedger",
]
