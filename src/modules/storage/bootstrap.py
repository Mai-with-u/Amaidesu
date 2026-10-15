"""存储组合根装配入口：从 ``[sqlite]`` 段构造并初始化 ``SQLiteDatabase``。

装配原则：
- **显式注入**——``config`` 由调用方传入；本模块不接触 ``sqlite_database()`` 默认单例
- **路径不随 cwd 漂移**——相对 ``db_path`` 一律相对于项目根解析
- **时间单位**——配置 ``busy_timeout_ms`` 是毫秒，``SQLiteDatabase.timeout``
  是秒（sqlite3.connect 的口径），只在这里做一次换算

调用示例（main.py 装配阶段）::

    from src.modules.storage.bootstrap import build_database

    database = await build_database(config)  # 读 config["sqlite"]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.modules.logging import get_logger
from src.modules.storage._default_path import DEFAULT_DB_PATH
from src.modules.storage.database import SQLiteDatabase

logger = get_logger("StorageBootstrap")


def _resolve_db_path(sqlite_cfg: Dict[str, Any]) -> Path:
    """从 ``config['sqlite']``（storage.toml 顶层段）取出 db_path 并解析为绝对路径。

    - 缺失 / 非 str：回退到 ``DEFAULT_DB_PATH``（项目根 ``data/amaidesu.db``）
    - 相对路径：相对于**项目根**（``DEFAULT_DB_PATH.parent.parent``，即 data 之上）
      —— 不依赖 cwd，避免组合根在不同目录下启动时路径漂移
    """
    raw = sqlite_cfg.get("db_path") if isinstance(sqlite_cfg, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return DEFAULT_DB_PATH
    p = Path(raw)
    if p.is_absolute():
        return p
    project_root = DEFAULT_DB_PATH.parent.parent
    return (project_root / raw).resolve()


def _resolve_busy_timeout_seconds(sqlite_cfg: Dict[str, Any]) -> float:
    """从 ``sqlite_cfg`` 读 ``busy_timeout_ms``（毫秒 int）并换算为秒。

    schema 默认 5000ms → 5s。缺省 / 非数字 / 负数走 5s 默认；至少 1 秒
    （避免 0 秒导致繁忙等待死循环），上限 300 秒防御异常配置。
    """
    raw = sqlite_cfg.get("busy_timeout_ms") if isinstance(sqlite_cfg, dict) else None
    if not isinstance(raw, (int, float)):
        return 5.0
    ms = int(raw)
    if ms < 0:
        return 5.0
    seconds = max(1, min(ms // 1000, 300))
    return float(seconds)


async def build_database(config: Dict[str, Any]) -> SQLiteDatabase:
    """构造并 ``initialize()`` 存储库（建表 + 迁移 + 自检）。

    Args:
        config: 扁平化后的 ConfigService dict；只读其中
            ``{"sqlite": {"db_path": "...", "busy_timeout_ms": 5000}}``，
            段缺失时全部走默认值。

    Raises:
        TypeError: ``config`` 不是 dict
        OSError: 数据库目录创建失败
    """
    if not isinstance(config, dict):
        raise TypeError(f"build_database: config 必须是 dict，得到 {type(config).__name__}")

    sqlite_cfg_raw = config.get("sqlite")
    sqlite_cfg = sqlite_cfg_raw if isinstance(sqlite_cfg_raw, dict) else {}

    db_path = _resolve_db_path(sqlite_cfg)
    busy_timeout_s = _resolve_busy_timeout_seconds(sqlite_cfg)

    database = SQLiteDatabase(db_path=db_path, timeout=busy_timeout_s)
    await database.initialize()
    logger.info(f"存储已初始化 path={db_path} timeout={busy_timeout_s}s")
    return database


__all__ = ["build_database"]
