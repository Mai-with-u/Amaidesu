"""验证程序分页读取按原索引和字符偏移恢复完整证据。"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.agents.minecraft.readback import read_value


def reference(path: str) -> dict[str, Any]:
    """引用明确指向省略位置，不将概要当作实际完整值。"""
    return {"omitted": True, "type": "object", "detail_path": path}


async def test_nested_pages_restore_false_values_and_unicode() -> None:
    """跨页保留 false 与表情，文本偏移使用 Mod 的 UTF-16 单位。"""

    async def page(path: str, offset: int) -> dict[str, Any]:
        if path == "/result":
            return {
                "path": path,
                "type": "object",
                "offset": offset,
                "total": 2,
                "items": [
                    {"key": "allowed", "value": False},
                    {"key": "text", "value": reference(path + "/text")},
                ],
            }
        if offset == 0:
            return {"path": path, "type": "string", "offset": 0, "total": 3, "value": "🌲", "next_offset": 2}
        return {"path": path, "type": "string", "offset": 2, "total": 3, "value": "木"}

    assert await read_value(page, "/result") == {"allowed": False, "text": "🌲木"}
    with pytest.raises(ValueError, match="预算"):
        await read_value(page, "/result", max_calls=1)


@pytest.mark.parametrize(
    "bad",
    [
        {"path": "/other", "offset": 0},
        {"path": "", "offset": 0, "type": "array", "total": 1, "items": []},
        {"path": "", "offset": 0, "type": "array", "total": 1, "items": [{"index": 1, "value": 2}]},
        {"path": "", "offset": 0, "type": "string", "total": 5, "value": "a", "next_offset": 0},
    ],
)
async def test_incomplete_pages_are_rejected(bad: dict[str, Any]) -> None:
    """缺页、重复偏移和错位索引必须失败，不能交付看起来完整的空证据。"""
    with pytest.raises(ValueError):
        await read_value(AsyncMock(return_value=bad), "")
