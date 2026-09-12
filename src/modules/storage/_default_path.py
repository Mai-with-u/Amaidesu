"""默认 DB 路径——推迟到 storage 模块导入时再解析（避免循环依赖）

默认路径指向 ``data/amaidesu.db``；需要自定义路径时直接实例化
``SQLiteDatabase(db_path=...)``。
"""

from __future__ import annotations

from pathlib import Path

# 默认数据目录：项目根 / data / amaidesu.db
_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH: Path = _ROOT / "data" / "amaidesu.db"


__all__ = ["DEFAULT_DB_PATH"]
