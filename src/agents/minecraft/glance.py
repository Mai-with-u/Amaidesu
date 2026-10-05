"""主播"看一眼游戏"的精简视图：从原生现状与周边观察里取出直播叙事用得上的事实。

主播的职责是把身体在做什么、遇到了什么讲给观众听，不替身体做工程判断。
原始 situation / surroundings 观察里大半是给游戏 Agent 规划用的几何与证据
（视线向量、可站立区域、结构扫描、物品组件哈希），交给主播只会让上下文
膨胀、让它去猜方块坐标。这里只保留：身体状态、背包物品与数量、附近牌子
文字、附近生物与设施。上游没给的字段不写（缺失是"未知"，不是"没有"），
读取失败照实写出失败原因。
"""

from __future__ import annotations

from typing import Any, Dict, List

__all__ = ["SURROUNDINGS_SECTIONS", "glance_situation", "glance_surroundings"]

#: 周边观察只取叙事需要的分区（牌子文字、生物、设施），几何与结构扫描不读
SURROUNDINGS_SECTIONS = ["nearby_signs", "nearby_entities", "nearby_facilities"]


def _number(value: Any) -> float | None:
    """数值字段收敛：只接受 int/float（bool 不算数），其余视为未知。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _failure(observation: Dict[str, Any]) -> str:
    """读取失败时的原因文字（结构化错误取 message，否则整体转文本）。"""
    error = observation.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or "读取失败")


def glance_situation(observation: Dict[str, Any]) -> Dict[str, Any]:
    """身体现状：维度、整格坐标、血量/饥饿/氧气、时段天气、护甲与背包物品数量。"""
    if observation.get("ok") is False:
        return {"body_error": _failure(observation)}
    body: Dict[str, Any] = {}
    if observation.get("dimension"):
        body["dimension"] = observation["dimension"]
    position = observation.get("position")
    if isinstance(position, dict):
        axes = [_number(position.get(axis)) for axis in ("x", "y", "z")]
        if all(value is not None for value in axes):
            # 叙事只需要整格位置，小数点后的站位偏移对观众没有意义
            body["position"] = [int(round(value)) for value in axes if value is not None]
    health, max_health = _number(observation.get("health")), _number(observation.get("max_health"))
    if health is not None:
        body["health"] = f"{health:g}/{max_health:g}" if max_health is not None else f"{health:g}"
    for key in ("food", "time_phase", "weather"):
        if observation.get(key) is not None:
            body[key] = observation[key]
    air = _number(observation.get("air"))
    if air is not None and air < 300:
        # 氧气满格（300）时不提，憋气时才是值得讲的事
        body["air"] = int(air)
    states = [
        label for key, label in (("underwater", "在水下"), ("in_water", "在水中")) if observation.get(key) is True
    ]
    if states:
        body["states"] = states

    result: Dict[str, Any] = {"body": body}
    equipment = observation.get("equipment")
    if isinstance(equipment, dict):
        worn = {
            slot: item.get("item_id")
            for slot, item in equipment.items()
            if isinstance(item, dict) and item.get("item_id")
        }
        if worn:
            result["equipment"] = worn
    inventory = observation.get("inventory")
    if isinstance(inventory, list):
        # 只列物品与总数；组件哈希、变体明细是给游戏 Agent 选具体那一件用的
        result["inventory"] = [
            f"{item['item_id']} ×{item.get('count', '?')}"
            for item in inventory
            if isinstance(item, dict) and item.get("item_id")
        ]
    carried = observation.get("carried_storage")
    if isinstance(carried, dict) and isinstance(carried.get("unobserved_backpacks"), int):
        unseen = carried["unobserved_backpacks"]
        if unseen > 0:
            result["inventory_note"] = f"另有 {unseen} 个随身背包没打开看过，里面的物品不在上面的清单里"
    return result


def glance_surroundings(observation: Dict[str, Any]) -> Dict[str, Any]:
    """周边：生物群系、附近牌子文字、附近生物与设施（各自带距离）。"""
    if observation.get("ok") is False:
        return {"nearby_error": _failure(observation)}
    result: Dict[str, Any] = {}
    if observation.get("biome"):
        result["biome"] = observation["biome"]
    signs = observation.get("nearby_signs")
    if isinstance(signs, list):
        result["signs"] = [_sign(sign) for sign in signs if isinstance(sign, dict) and _sign_text(sign)]
    entities = observation.get("nearby_entities")
    if isinstance(entities, list):
        result["entities"] = [
            {"type": entity.get("type"), "distance": entity.get("distance")}
            for entity in entities
            if isinstance(entity, dict) and entity.get("type")
        ]
    facilities = observation.get("nearby_facilities")
    if isinstance(facilities, dict) and isinstance(facilities.get("facilities"), list):
        result["facilities"] = [
            {
                "block": facility.get("block_id"),
                "count": facility.get("count"),
                "nearest_distance": facility.get("nearest_distance"),
            }
            for facility in facilities["facilities"]
            if isinstance(facility, dict) and facility.get("block_id")
        ]
    return result


def _sign_text(sign: Dict[str, Any]) -> str:
    """牌子正反面的非空行连成一句（"MS社区食堂 / （旧工业区站点）"）。"""
    lines: List[str] = []
    for side in ("front_lines", "back_lines"):
        value = sign.get(side)
        if isinstance(value, list):
            lines.extend(str(line).strip() for line in value if str(line).strip())
    return " / ".join(lines)


def _sign(sign: Dict[str, Any]) -> Dict[str, Any]:
    """一块牌子：文字 + 距离（直播里说"旁边写着停机开关的牌子"就够了）。"""
    return {"text": _sign_text(sign), "distance": sign.get("distance")}
