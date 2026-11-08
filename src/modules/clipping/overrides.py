"""录播文件归属的人工覆盖（本地 JSON 存储，不入库、不随导出带走）。

自动匹配（文件名时刻 × 场次时间窗）是默认事实源；人工只记"覆盖"：

- ``assign``：把某文件指派给某场（文件名解析不出开始时刻时必须人工给 ``start_ms``）；
- ``detach``：把自动匹配错的文件移出某场。

存储是实例本地的 ``data/clipping/overrides.json``：文件路径属机器本地信息，
按项目纪律不进 SQLite（复盘导出会打包库快照，路径进库就会随导出泄露），
也不进 git。整文件可随时删除——删掉即回到纯自动匹配。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from src.modules.logging import get_logger

logger = get_logger("RecordingOverrides")

# 默认存储位置（data/clipping/ 子目录，切片功能本地文件统一归拢；gitignored）
DEFAULT_OVERRIDES_PATH = Path("data") / "clipping" / "overrides.json"


@dataclass
class RecordingOverride:
    """一条人工覆盖记录（同 path + 场次 + 动作唯一，后写覆盖先写）。"""

    path: str  # 录播文件绝对路径（仅落本地 JSON，不入库）
    live_session_id: int
    action: str  # 'assign' = 指派到该场 / 'detach' = 移出该场
    start_ms: Optional[int] = None  # assign 且文件名解析不出时刻时的人工开始时刻

    def key(self) -> tuple[str, int, str]:
        return (self.path, self.live_session_id, self.action)


class RecordingOverrideStore:
    """覆盖记录的读写（原子写：临时文件 + replace；坏文件降级为空并告警）。"""

    def __init__(self, path: Path = DEFAULT_OVERRIDES_PATH) -> None:
        self._path = path

    def load(self) -> List[RecordingOverride]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"归属覆盖文件读取失败（按无覆盖处理）：{exc}")
            return []
        entries = raw.get("overrides", []) if isinstance(raw, dict) else []
        overrides: List[RecordingOverride] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            try:
                overrides.append(
                    RecordingOverride(
                        path=str(item["path"]),
                        live_session_id=int(item["live_session_id"]),
                        action=str(item["action"]),
                        start_ms=item.get("start_ms"),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(f"归属覆盖条目损坏（跳过）：{exc}")
        return overrides

    def save(self, overrides: List[RecordingOverride]) -> None:
        """全量写回（原子替换；写失败抛 OSError 由调用方决定如何上报）。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"overrides": [asdict(item) for item in overrides]}
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def upsert(self, override: RecordingOverride) -> None:
        """记一条覆盖（同 key 后写覆盖先写）。"""
        overrides = [item for item in self.load() if item.key() != override.key()]
        overrides.append(override)
        self.save(overrides)

    def replace_path(self, old_path: str, new_path: str) -> int:
        """文件改名后迁移其全部覆盖记录（指派/移出都跟着新路径走），返回迁移条数。"""
        overrides = self.load()
        migrated = 0
        for item in overrides:
            if item.path == old_path:
                item.path = new_path
                migrated += 1
        if migrated:
            self.save(overrides)
        return migrated

    def drop(self, override: RecordingOverride) -> bool:
        """撤销一条覆盖（如恢复被移出的文件），返回是否真的删了。"""
        remaining = [item for item in self.load() if item.key() != override.key()]
        if len(remaining) == len(self.load()):
            return False
        self.save(remaining)
        return True

    def for_session(self, live_session_id: int) -> List[RecordingOverride]:
        """某场的全部覆盖记录。"""
        return [item for item in self.load() if item.live_session_id == live_session_id]


__all__ = ["DEFAULT_OVERRIDES_PATH", "RecordingOverride", "RecordingOverrideStore"]
