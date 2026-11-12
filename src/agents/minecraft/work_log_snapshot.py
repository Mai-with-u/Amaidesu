"""读工作文档时附在笔记后面的当前状态快照。

工作笔记由模型全量覆盖写：模型会把当时的背包、装备、位置、血量一起抄进去，
下一批读到就照抄旧数字，说出来和现场对不上。读工作文档时服务端现查一次
``observe(self)``，把位置、背包、装备、血量写成一段附在笔记后面——读的人以这段
快照为准，笔记里的同类数字只当历史参考。

现查失败（游戏未连接、读取出错）时写明"当前不可用"，不中断读取。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .attention_matrix import dimension_name, item_name
from .maicraft import MaicraftReply
from .mod_facts import chain_mining_text, mod_features_text

__all__ = ["SNAPSHOT_TITLE", "build_snapshot", "unavailable_snapshot"]

#: 快照段的抬头
SNAPSHOT_TITLE = "【当前状态快照】"


def unavailable_snapshot(reason: str = "游戏连接还没就绪") -> str:
    """现查不可用时的快照段：写明当前不可用，不中断读取。"""
    return (
        f"{SNAPSHOT_TITLE}当前不可用（{reason}）：这一段没有现查的数字，"
        "笔记正文里的背包、装备、位置、血量只能当历史参考。"
    )


def _number(value: Any) -> Optional[str]:
    """数值 → 短字符串；不是数就当没有。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    rounded = round(float(value), 1)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def _position_text(data: Dict[str, Any]) -> Optional[str]:
    """当前位置：维度 + 坐标（没有坐标就只写维度）。"""
    position = data.get("position") if isinstance(data.get("position"), dict) else {}
    axes = [_number(position.get(axis)) for axis in ("x", "y", "z")]
    dimension = dimension_name(str(position.get("dimension") or ""))
    if all(axis is not None for axis in axes):
        where = f"{dimension} " if dimension else ""
        return f"{where}({', '.join(str(axis) for axis in axes)})"
    return dimension or None


def _health_text(data: Dict[str, Any]) -> Optional[str]:
    """血量与饱食度（Mod 只报还剩多少，报了才写）。"""
    health = _number(data.get("health"))
    food = _number(data.get("food"))
    parts = []
    if health is not None:
        parts.append(f"血量 {health}")
    if food is not None:
        parts.append(f"饱食 {food}")
    return "、".join(parts) if parts else None


def _equipment_text(data: Dict[str, Any]) -> Optional[str]:
    """身上的装备：护甲与手上拿的东西。"""
    parts: List[str] = []
    for item in data.get("armor") or []:
        if isinstance(item, str) and item:
            parts.append(item_name(item))
    held = data.get("held")
    if isinstance(held, str) and held:
        parts.append(f"手上拿着{item_name(held)}")
    return "、".join(parts) if parts else None


def _inventory_text(data: Dict[str, Any]) -> Optional[str]:
    """背包：按物品合并后的名称与数量。"""
    entries = data.get("inventory")
    if not isinstance(entries, list):
        return None
    parts: List[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = item_name(str(entry.get("item") or ""))
        if not name:
            continue
        count = _number(entry.get("count"))
        parts.append(f"{name} ×{count}" if count is not None else name)
    return "、".join(parts) if parts else None


def build_snapshot(reply: MaicraftReply, fallback_facts: Optional[Dict[str, str]] = None) -> str:
    """一次 ``observe(self)`` 的回复 → 给读者看的当前状态快照段。"""
    if not reply.ok:
        reason = reply.error_message or reply.error_code or "读取失败"
        return f"{SNAPSHOT_TITLE}当前不可用（{reason}）：这一段没有现查的数字，笔记里的同类数字只能当历史参考。"
    data = reply.data
    extra = fallback_facts or {}
    lines = [f"{SNAPSHOT_TITLE}读工作文档这一刻现查的，以这里的数字为准："]
    fields = [
        ("位置", _position_text(data)),
        ("状态", _health_text(data)),
        ("装备", _equipment_text(data)),
        ("背包", _inventory_text(data)),
        ("实例能力", mod_features_text(data) or extra.get("mod_features")),
        ("最近挖掘", chain_mining_text(data.get("last_chain_mining")) or extra.get("last_chain_mining")),
        ("近期留坑", extra.get("recent_pits")),
    ]
    written = False
    for label, value in fields:
        if value:
            lines.append(f"{label}：{value}")
            written = True
    if not written:
        lines.append("这次没读到可用的身体数据（位置、装备、背包、血量都没报）。")
    return "\n".join(lines)
