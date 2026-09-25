"""对话 canonical 映射——live_chat 行 / 弹幕批 → 原生消息的单一序列化点。

## 定位
- **纯函数**（无状态、无 IO）：直播对话进入 LLM 消息数组的唯一映射
- **批与历史同形**：弹幕批与 live_chat 历史行经同一函数序列化，消除
  "批格式 vs 历史格式"双轨——两条链路产出的 `{role, content}` dict 逐字同构
- **跨窗稳定**：序列化只依赖行自身字段（角色/昵称/内容/类型/消息 ID），
  不含时间等易变量，同一行跨决策窗序列化结果字节一致（append-only 缓存前提）

## 内容格式（canonical content）
- 观众行：``[类型前缀] 昵称: 内容 [id:消息ID]``（类型前缀由 message_type
  登记表的 prompt 模板承载；`[id:…]` 目标机制保留在 content 内）
- 主播行：原样内容（发言即正文，不加昵称前缀）
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.modules.logging import get_logger

__all__ = [
    "ROLE_LABELS",
    "batch_item_to_message",
    "canonical_content",
    "live_chat_row_to_message",
    "to_text_view",
    "turn_to_message",
]

logger = get_logger("canonical")

#: live_chat ``message_type`` → AI 侧 prompt 模板（批与历史同源的单一映射；
#: 变量 ``{nickname}`` / ``{content}``）。模板值是下游解析对齐的契约，
#: 不要顺手优化文案。空串/未知类型按普通弹幕渲染。
PROMPT_TEMPLATES: Dict[str, str] = {
    "danmaku": "{nickname}: {content}",
    "gift": "[礼物] {nickname}: {content}",
    "super_chat": "[醒目留言] {nickname}: {content}",
    "guard": "[上舰] {nickname}: {content}",
    "enter": "[入场] {nickname}: {content}",
    "partner_speech": "{nickname}: {content}",
}


def prompt_template_for(message_type: str) -> str:
    """取 message_type 对应的 prompt 模板；空串/未知类型按普通弹幕渲染。"""
    return PROMPT_TEMPLATES.get(message_type or "danmaku", PROMPT_TEMPLATES["danmaku"])


#: 文本视图的角色标签（Replyer / 后台摘要复用的标签式历史渲染）。
ROLE_LABELS: Dict[str, str] = {
    "user": "观众",
    "assistant": "主播",
    "system": "系统",
}


def _as_str(value: Any) -> str:
    """鸭子字段取值收敛：仅接受 str，其余（None/Mock 等）按空处理。"""
    return value if isinstance(value, str) else ""


def _field(row: Any, name: str) -> Any:
    """按名取字段：dict / sqlite3.Row 走下标，普通对象走属性。"""
    if isinstance(row, dict):
        return row.get(name, "")
    try:
        return row[name]
    except (TypeError, KeyError, IndexError):
        return getattr(row, name, "")


def canonical_content(
    *,
    role: str,
    nickname: str = "",
    text: str = "",
    message_type: str = "danmaku",
    message_id: str = "",
) -> str:
    """单条直播消息 → canonical content（见模块 docstring 的格式约定）。"""
    if role == "assistant":
        content = text
    else:
        line = prompt_template_for(message_type).format(nickname=nickname or "观众", content=text)
        content = line
        if message_id:
            content = f"{content} [id:{message_id}]"
    # 弹幕、昵称与消息标识完整进入上下文，避免长消息丢失尾部要求。
    return content


def live_chat_row_to_message(row: Any) -> Dict[str, str]:
    """live_chat 行（sqlite3.Row / dict）→ ``{role, content}`` 消息 dict。"""
    role = "assistant" if _field(row, "sender_role") == "assistant" else "user"
    return {
        "role": role,
        "content": canonical_content(
            role=role,
            nickname=_as_str(_field(row, "sender_name")),
            text=_as_str(_field(row, "content")),
            message_type=_as_str(_field(row, "message_type")) or "danmaku",
            message_id=_as_str(_field(row, "message_id")),
        ),
    }


def turn_to_message(turn: Any) -> Dict[str, str]:
    """历史 turn（鸭子类型：role / content / sender_name / message_type / message_id）→ 消息 dict。"""
    role_raw = getattr(turn, "role", None)
    role_str = getattr(role_raw, "value", str(role_raw)) if role_raw else "user"
    role = "assistant" if role_str == "assistant" else "user"
    return {
        "role": role,
        "content": canonical_content(
            role=role,
            nickname=_as_str(getattr(turn, "sender_name", None)),
            text=_as_str(getattr(turn, "content", None)),
            message_type=_as_str(getattr(turn, "message_type", None)) or "danmaku",
            message_id=_as_str(getattr(turn, "message_id", None)),
        ),
    }


def batch_item_to_message(msg: Any) -> Dict[str, str]:
    """弹幕批成员（RoomMessagePayload 鸭子类型）→ 消息 dict（与历史同形）。"""
    user = getattr(msg, "user", None)
    nickname = (getattr(user, "name", None) or getattr(user, "id", None)) if user is not None else None
    text = getattr(msg, "content", None) or str(msg)
    return {
        "role": "user",
        "content": canonical_content(
            role="user",
            nickname=_as_str(nickname) or "观众",
            text=text if isinstance(text, str) else str(text),
            message_type=getattr(msg, "message_type", "danmaku") or "danmaku",
            message_id=_as_str(getattr(msg, "message_id", None)),
        ),
    }


def to_text_view(messages: List[Any]) -> str:
    """消息数组 → 标签式文本视图（Replyer 历史注入 / 后台摘要复用）。

    - 观众行渲染为 canonical content 本身（已含"昵称: 内容"）
    - 主播行加"主播:"前缀；主动发言占位（系统元数据）标"[系统]"避免误读
    """
    lines: List[str] = []
    for item in messages:
        if isinstance(item, dict):
            role_raw = item.get("role", "user")
            role = getattr(role_raw, "value", str(role_raw))
            content = item.get("content", "") or ""
        else:
            role_raw = getattr(item, "role", None)
            role = getattr(role_raw, "value", str(role_raw)) if role_raw else "user"
            content = getattr(item, "content", "") or ""
        if role == "user" and content.startswith("（主动发言"):
            lines.append(f"[系统] {content}")
            continue
        if role == "user":
            lines.append(content)
        else:
            lines.append(f"{ROLE_LABELS.get(role, role)}: {content}")
    return "\n".join(lines)
