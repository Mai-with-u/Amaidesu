"""v14 → v15：场次回顾表上线 + 删除观众事实表。

主播记忆改为"场次回顾 + 常客名册"两样：

- ``session_recaps`` 为新建表，由建库 DDL 直接创建，本回调不处理；
- ``viewer_facts``（后台循环从弹幕里抠出来的"观众事实"中间表）退役：
  常客印象改在收播时直接从该观众本场的弹幕原话与付费记录生成，不再
  经事实中间层。存量事实不搬迁——它们只是旧画像的原料，画像文本本身
  仍留在 ``viewer_profiles``。

回调幂等：表不存在时直接返回。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.migrations._common import table_exists


def migrate(conn: sqlite3.Connection) -> None:
    """原地修改、幂等：``DROP TABLE viewer_facts``（存在才删）。"""
    if table_exists(conn, "viewer_facts"):
        conn.execute("DROP TABLE viewer_facts")


__all__ = ["migrate"]
