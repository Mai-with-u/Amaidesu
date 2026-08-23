"""默认 DB 路径——推迟到 storage 模块导入时再解析（避免循环依赖）

Wave 3 阶段尚未与 config 完整对接，默认路径先指向 ``data/amaidesu.db``
（用户可在 W4 集成 config 后通过 ``set_default_store()`` 切换）。
"""

from __future__ import annotations

from pathlib import Path

# 默认数据目录：项目根 / data / amaidesu.db
_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH: Path = _ROOT / "data" / "amaidesu.db"


__all__ = ["DEFAULT_DB_PATH"]
