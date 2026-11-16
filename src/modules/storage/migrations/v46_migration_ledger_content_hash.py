"""v45 → v46：迁移账本补内容指纹列，记账即存脚本指纹。

迁移机制隐含契约"同一版本号内容不可变"此前只靠纪律维持，账本里没有任何
内容凭据：同版本号的迁移脚本在执行后被改动，账本无从发现，差异静默存在。
本版给 ``schema_migrations`` 补 ``content_hash`` 列（脚本文件的 sha256），
执行迁移时记账即存指纹；存量记录按当前注册表对应脚本回填，注册表里已无
对应脚本的版本原样保留空指纹——启动校验会把空指纹的已记录版本判为不一致。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import column_exists, compute_content_hash


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：补 content_hash 列并按当前脚本回填存量指纹。"""
    if not column_exists(conn, "schema_migrations", "content_hash"):
        conn.execute("ALTER TABLE schema_migrations ADD COLUMN content_hash TEXT")

    # 函数内导入：本模块被注册表导入，顶层反向导入注册表构成循环
    from src.modules.storage.migrations import SCHEMA_MIGRATION_FILES

    recorded = [int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations").fetchall()]
    for version in recorded:
        file_path = SCHEMA_MIGRATION_FILES.get(version)
        if file_path is None:
            # 注册表已无此版本：保留空指纹，交由启动校验硬错拦下
            continue
        conn.execute(
            "UPDATE schema_migrations SET content_hash=? WHERE version=?",
            (compute_content_hash(file_path), version),
        )


__all__ = ["migrate"]
