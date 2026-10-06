"""读取 Mod 报告的游戏安装环境，决定哪些模组玩法技能值得放进目录。"""

from __future__ import annotations

import json
from typing import Any, Optional

from src.modules.logging import get_logger

logger = get_logger("MinecraftEnvironment")

# Mod 在客户端启动时冻结的安装清单：加载器、游戏版本与全部模组编号及版本
ENVIRONMENT_URI = "maicraft://environment"


async def read_installed_mods(client: Any) -> Optional[frozenset[str]]:
    """读取已装模组编号；读不到、格式不符或 Mod 尚未登记清单时返回 ``None``（未知）。

    未知与"没装"必须分开：未知时技能目录保留有前提的技能并标注待确认，
    只有确认没装才把对应玩法移出目录。旧版 Mod 没有该资源时同样按未知处理。
    """
    raw = await client.read_resource(ENVIRONMENT_URI)
    if raw is None:
        return None
    contents = raw.get("contents", []) if isinstance(raw, dict) else getattr(raw, "contents", raw)
    if not isinstance(contents, (list, tuple)) or len(contents) != 1:
        logger.warning(f"安装环境资源未返回单份内容，已装模组按未知处理: {ENVIRONMENT_URI}")
        return None
    first = contents[0]
    text = first.get("text") if isinstance(first, dict) else getattr(first, "text", None)
    if not isinstance(text, str):
        logger.warning(f"安装环境资源不是文本，已装模组按未知处理: {ENVIRONMENT_URI}")
        return None
    try:
        page = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(f"安装环境资源不是合法 JSON，已装模组按未知处理: {exc}", exc=True)
        return None
    if not isinstance(page, dict) or page.get("mods_known") is not True or not isinstance(page.get("mods"), dict):
        logger.info("Mod 尚未登记已装模组清单，技能前提按未知处理")
        return None
    return frozenset(str(mod_id) for mod_id in page["mods"])


__all__ = ["ENVIRONMENT_URI", "read_installed_mods"]
