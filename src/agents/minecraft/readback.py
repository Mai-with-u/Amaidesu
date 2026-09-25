"""为程序取回明确需要的 MaiCraft 分页证据，读取期间不重发任何游戏操作。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from src.modules.logging import get_logger

logger = get_logger("MinecraftReadback")
PageReader = Callable[[str, int], Awaitable[dict[str, Any]]]


class ResourceReader(Protocol):
    """借用已连接的只读资源通道，不负责连接或玩家任务的生命周期。"""

    async def read_resource(self, uri: str) -> Any: ...


def is_reference(value: Any) -> bool:
    """只识别带明确省略标记的协议引用，普通游戏对象的同名字段不作取回指令。"""
    return isinstance(value, dict) and value.get("omitted") is True and isinstance(value.get("detail_path"), str)


async def read_value(reader: PageReader, path: str, *, max_calls: int = 128, max_chars: int = 1_000_000) -> Any:
    """按键、索引和 UTF-16 偏移重组证据；页序错误或预算耗尽时明确失败，不交付半份事实。"""
    calls = 0
    chars = 0

    async def read(current: str, depth: int) -> Any:
        nonlocal calls, chars
        if depth > 32:
            raise ValueError("回执嵌套超过读取范围")
        offset = 0
        kind: str | None = None
        total: int | None = None
        values: list[Any] = []
        fields: dict[str, Any] = {}
        while True:
            calls += 1
            if calls > max_calls:
                raise ValueError("回执读取页数超出预算，请缩小需要的证据范围")
            page = await reader(current, offset)
            chars += len(json.dumps(page, ensure_ascii=False, separators=(",", ":")))
            if chars > max_chars:
                raise ValueError("回执读取大小超出预算，请缩小需要的证据范围")
            if page.get("path") != current or type(page.get("offset")) is not int or page["offset"] != offset:
                raise ValueError("回执路径或偏移与请求不一致")
            if kind is None:
                kind, total = page.get("type"), page.get("total")
            elif page.get("type") != kind or page.get("total") != total:
                raise ValueError("回执翻页期间类型或总量发生变化")
            if kind in {"null", "scalar"}:
                if offset or "value" not in page or page.get("next_offset") is not None:
                    raise ValueError("标量回执不是完整单值")
                if (
                    kind == "null"
                    and page["value"] is not None
                    or kind == "scalar"
                    and type(page["value"]) not in {bool, int, float}
                ):
                    raise ValueError("标量回执的值与类型不符")
                return page["value"]
            if type(total) is not int or total < 0:
                raise ValueError("回执缺少有效总量")
            if kind == "string":
                text = page.get("value")
                if not isinstance(text, str):
                    raise ValueError("文本页没有正文")
                values.append(text)
                end = offset + len(text.encode("utf-16-le")) // 2
            elif kind in {"array", "object"}:
                rows = page.get("items")
                if not isinstance(rows, list):
                    raise ValueError("集合页缺少条目")
                for index, row in enumerate(rows, offset):
                    if not isinstance(row, dict) or "value" not in row:
                        raise ValueError("集合页条目不完整")
                    key = str(index) if kind == "array" else row.get("key")
                    if kind == "array" and (type(row.get("index")) is not int or row["index"] != index):
                        raise ValueError("数组回执索引不连续")
                    if not isinstance(key, str) or kind == "object" and key in fields:
                        raise ValueError("对象回执键缺失或重复")
                    child = current + "/" + key.replace("~", "~0").replace("/", "~1")
                    value = row["value"]
                    if is_reference(value):
                        if value["detail_path"] != child:
                            raise ValueError("省略引用不属于当前证据字段")
                        value = await read(child, depth + 1)
                    if kind == "array":
                        values.append(value)
                    else:
                        fields[key] = value
                end = offset + len(rows)
            else:
                raise ValueError("不支持的回执类型")
            next_offset = page.get("next_offset")
            if end > total or next_offset is None and end != total:
                raise ValueError("回执未完整覆盖所声明的总量")
            if next_offset is None:
                return "".join(values) if kind == "string" else values if kind == "array" else fields
            if type(next_offset) is not int or next_offset != end or not offset < end < total:
                raise ValueError("回执续页偏移不连续或没有推进")
            offset = next_offset

    return await read(path, 0)


async def read_receipt(client: ResourceReader, reference: dict[str, Any]) -> Any:
    """沿同一冻结回执读取；不接受资料正文提供的任意外部 URI，也不在失效后重做原动作。"""
    uri = reference.get("resource_uri")
    if not isinstance(uri, str):
        raise ValueError("省略回执缺少资源读取入口")
    parsed = urlsplit(uri)
    if parsed.scheme != "maicraft" or parsed.netloc != "receipts" or parsed.fragment:
        raise ValueError("不是 MaiCraft 冻结回执 URI")
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    path = query.get("path", [""])
    if len(path) != 1 or query.get("offset", ["0"]) != ["0"] or path[0] != reference.get("detail_path"):
        raise ValueError("冻结回执入口未指向所需完整字段")

    async def page(pointer: str, offset: int) -> dict[str, Any]:
        requested = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode({"path": pointer, "offset": offset, "limit": 20}), "")
        )
        raw = await client.read_resource(requested)
        contents = raw.get("contents", []) if isinstance(raw, dict) else getattr(raw, "contents", raw)
        if not isinstance(contents, (list, tuple)) or len(contents) != 1:
            raise ValueError("冻结回执未返回单份完整页面")
        text = contents[0].get("text") if isinstance(contents[0], dict) else getattr(contents[0], "text", None)
        try:
            value = json.loads(text) if isinstance(text, str) else None
        except json.JSONDecodeError as exc:
            logger.warning("冻结回执页面不是完整 JSON", exc=exc)
            raise ValueError("冻结回执页面格式错误") from exc
        expected = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        if (
            not isinstance(value, dict)
            or value.get("snapshot_only") is not True
            or value.get("details_uri") != expected
        ):
            raise ValueError("冻结回执缺少快照身份")
        return value

    # 后台采集不能被一串慢页面占住；失败保持原游标，由已有重试节拍重新取得实际证据。
    async with asyncio.timeout(10):
        return await read_value(page, path[0])


def has_references(value: Any) -> bool:
    """程序需要完整校验结果时检查省略标记，普通模型观察可继续保留引用。"""
    if is_reference(value):
        return True
    if isinstance(value, dict):
        return any(has_references(item) for item in value.values())
    if isinstance(value, list):
        return any(has_references(item) for item in value)
    return False
