"""v49 → v50：新增游戏 Agent 自建技能卡表 ``agent_learned_skills``。

游戏 Agent 在实际游玩中摸索或从同伴处学到的可复用操作流程，此前只能零散记入工作笔记，
既受 2000 字上限限制、又无法跨存档沉淀通用模组玩法。新增 ``agent_learned_skills`` 表，
按 ``(game, scope, save_id, name)`` 唯一存储（``scope='world'`` 存具体存档设施流程，
``scope='global'`` 存空 ``save_id`` 用于跨存档通用玩法）。

幂等保证：直接复用 ``schema.py`` 权威 DDL（全 ``IF NOT EXISTS``），对新库与已升级库重复执行均安全。
"""

from __future__ import annotations

import sqlite3

from src.modules.storage.schema import _AGENT_LEARNED_SKILLS_SQL


def migrate(conn: sqlite3.Connection) -> None:
    """原地幂等建 ``agent_learned_skills`` 表及查询索引。"""
    conn.executescript(_AGENT_LEARNED_SKILLS_SQL)
