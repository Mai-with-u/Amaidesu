"""前端 WS 事件词汇镜像对账（契约测试）。

``dashboard/src/types/events.ts`` 的 EVENT 表是前端唯一事实源，镜像对象是
broadcaster 的广播词汇（不是 EventBus 事件全集——未订阅的事件不进 WS）。
本测试双向对账：

- broadcaster 直通精确名 → 前端 EVENT 表必须逐一有值（后端新增广播事件时红，
  前端强制过目）；
- 折叠族类型名 → 与 ``event_type_map`` 常量一致；
- 通配族 → 前端有对应前缀常量与模板字面量类型；
- 前端 EVENT 表的每个值都能被 broadcaster 词汇解释（防前端私造）。

解析用正则读 TS 源文本：测试只依赖仓库文件，不引入 Node 工具链。EVENT 表的
结构（键值形态）变化时须同步 ``_frontend_event_values`` 的解析。
"""

from __future__ import annotations

import re
from pathlib import Path

from src.modules.dashboard.websocket.broadcaster import EventBroadcaster
from src.modules.events import event_type_map
from src.modules.events.names import CoreEvents

FRONTEND_EVENTS_TS = Path(__file__).resolve().parents[2] / "dashboard" / "src" / "types" / "events.ts"

# 前端表中不属于"直通精确名"的三个值：两个折叠族类型名 + 通配族 game.chat 的已知成员
NON_EXACT_FRONTEND_VALUES = frozenset(
    {
        event_type_map.ROOM_MESSAGE_TYPE,
        event_type_map.ROOM_STATE_TYPE,
        CoreEvents.GAME_CHAT_RECEIVED,
    }
)


def _frontend_source() -> str:
    return FRONTEND_EVENTS_TS.read_text(encoding="utf-8")


def _frontend_event_values() -> dict[str, str]:
    """解析 EVENT 表的键值对（折叠族类型名 + 直通精确名）。"""
    block = re.search(r"const EVENT = \{(.*?)\n\} as const", _frontend_source(), re.S)
    assert block, "events.ts 缺少 EVENT 表：解析器需与表结构同步更新"
    pairs = dict(re.findall(r"(\w+): '([a-z0-9_.]+)'", block.group(1)))
    assert pairs, "EVENT 表解析为空：检查 events.ts 是否被重构"
    return pairs


def _frontend_family_prefixes() -> dict[str, str]:
    """解析 EVENT_FAMILY 表的键值对（通配族前缀）。"""
    block = re.search(r"const EVENT_FAMILY = \{(.*?)\n\} as const", _frontend_source(), re.S)
    assert block, "events.ts 缺少 EVENT_FAMILY 表：解析器需与表结构同步更新"
    return dict(re.findall(r"(\w+): '([a-z0-9_.]+)'", block.group(1)))


def _broadcaster_exact_names() -> set[str]:
    """broadcaster 精确订阅的事件名（通配键以 # 结尾，不属于精确名）。"""
    return {
        name
        for name in EventBroadcaster._SUBSCRIPTIONS
        if not name.endswith("#")  # noqa: SLF001
    }


def _broadcaster_wildcard_prefixes() -> set[str]:
    """broadcaster 通配订阅的族前缀（``tool.result.#`` → ``tool.result.``）。"""
    return {
        name[: -len("#")]
        for name in EventBroadcaster._SUBSCRIPTIONS
        if name.endswith("#")  # noqa: SLF001
    }


def test_exact_names_mirror_broadcaster_subscriptions() -> None:
    """直通精确名双向一致：后端订阅的每个精确名前端都有，前端也没有私造名。"""
    frontend_values = set(_frontend_event_values().values()) - NON_EXACT_FRONTEND_VALUES
    assert _broadcaster_exact_names() == frontend_values


def test_fold_family_names_match_event_type_map() -> None:
    """折叠族类型名与后端 event_type_map 常量逐字一致。"""
    values = _frontend_event_values()
    assert values.get("roomMessage") == event_type_map.ROOM_MESSAGE_TYPE
    assert values.get("roomState") == event_type_map.ROOM_STATE_TYPE


def test_game_chat_known_member_matches_core_events() -> None:
    """通配族 game.chat 的已知成员值与 CoreEvents 常量一致。"""
    assert _frontend_event_values().get("gameChatReceived") == CoreEvents.GAME_CHAT_RECEIVED


def test_wildcard_families_have_prefix_and_template_type() -> None:
    """每个通配订阅族在前端都有同值前缀常量与模板字面量类型。

    模板类型由 EVENT_FAMILY 键派生（```${typeof EVENT_FAMILY.<key>}${string}```），
    前缀只在常量表出现一次——这里验证派生机制存在，而非要求文本重复。
    """
    source = _frontend_source()
    families = _frontend_family_prefixes()
    for prefix in sorted(_broadcaster_wildcard_prefixes()):
        keys = [key for key, value in families.items() if value == prefix]
        assert keys, f"EVENT_FAMILY 缺少通配族前缀 {prefix}"
        template = f"`${{typeof EVENT_FAMILY.{keys[0]}}}${{string}}`"
        assert template in source, f"events.ts 缺少派生模板字面量类型 {template}"
