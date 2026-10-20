"""
EventBus 事件名 → Dashboard WS 类型名（单一事实源）

WS 类型规则：``type = 事件名``（精确直通）；例外是族折叠——同族子事件在
前端按族聚合消费，本模块只承载这些折叠类型名。
"""

# room.message.* 族事件的折叠类型名（前端按消息族聚合过滤）
ROOM_MESSAGE_TYPE = "room.message"

# room.state.* 族事件的折叠类型名（前端按状态快照聚合取最新值）
ROOM_STATE_TYPE = "room.state"

__all__ = ["ROOM_MESSAGE_TYPE", "ROOM_STATE_TYPE"]
