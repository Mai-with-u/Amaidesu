"""Schema 版本迁移注册表：一版本一文件。

纪律：
- **严格 +1**：``1..SCHEMA_VERSION`` 每个版本都必须在 ``SCHEMA_MIGRATIONS``
  内有条目，由守护测试（``tests/modules/storage/test_schema_version_upgrade.py``）
  断言完整性，杜绝"忘写迁移"静默通过。
- **一律升版本**：任何 DDL 变更（含 ``build_schema_sql()`` 能自动补齐的
  新表 / 新索引）都升 ``SCHEMA_VERSION`` 并补注册表条目；无数据变换的
  条目为显式 no-op 并注明该版本改了什么。
- **一版本一文件**：文件名 ``<版本>_<语义>.py``，每文件导出
  ``migrate(conn)``（原地修改、幂等）。
- 新建库的 DDL 已是最新形态；回调只需保证对旧库升级与重复执行安全
  （列存在性检查 / IF NOT EXISTS / IF EXISTS）。
"""

from __future__ import annotations

import sqlite3
from typing import Callable, Dict

from src.modules.storage.migrations import (
    v1_initial_schema,
    v2_sim_runtime_tables,
    v3_memory_private_tables,
    v4_session_semantics,
    v5_event_llm_history_tables,
    v6_rundowns_replace_agenda,
    v7_drop_profiles_table,
)

SCHEMA_MIGRATIONS: Dict[int, Callable[[sqlite3.Connection], None]] = {
    1: v1_initial_schema.migrate,
    2: v2_sim_runtime_tables.migrate,
    3: v3_memory_private_tables.migrate,
    4: v4_session_semantics.migrate,
    5: v5_event_llm_history_tables.migrate,
    6: v6_rundowns_replace_agenda.migrate,
    7: v7_drop_profiles_table.migrate,
}

__all__ = ["SCHEMA_MIGRATIONS"]
