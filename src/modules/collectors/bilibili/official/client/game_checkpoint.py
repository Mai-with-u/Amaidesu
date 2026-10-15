# B 站开放平台 game_id 检查点：进程内 game_id 一死即忘，
# 落盘一份供下次启动前幂等清理服务端残留的游戏会话（一个 app_id
# 同时只允许一场进行中的游戏）。文件属运行时素材，随 data/ 不入库。

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.modules.logging import get_logger

DEFAULT_CHECKPOINT_PATH = Path("data/bili_game_id.json")


class GameIdCheckpoint:
    """game_id 检查点文件读写（JSON 单键）

    读失败按无残留处理（宁多 end 一次无害，不能因检查点损坏阻塞启动）。
    """

    def __init__(self, path: Path = DEFAULT_CHECKPOINT_PATH) -> None:
        self.path = path
        self.logger = get_logger("GameIdCheckpoint")

    def load(self) -> str:
        """读取上次会话遗留的 game_id；文件缺失/损坏/为空一律返回空串。"""
        if not self.path.exists():
            return ""
        try:
            data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
            game_id = str(data.get("game_id", ""))
            return game_id
        except (OSError, ValueError) as e:
            self.logger.warning(f"检查点文件读取失败，按无残留处理: {self.path}", exc=e)
            return ""

    def save(self, game_id: str) -> None:
        """app/start 成功后写入当前 game_id。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"game_id": game_id}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            # 落盘失败只降级（下次启动清不了残留），不影响本次会话
            self.logger.error(f"检查点文件写入失败: {self.path}", exc=e)

    def clear(self) -> None:
        """会话已正常 end（或残留已清理）后删除检查点。"""
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError as e:
            self.logger.warning(f"检查点文件删除失败: {self.path}", exc=e)
