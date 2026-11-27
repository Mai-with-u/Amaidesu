"""对话 canonical 映射——live_chat 行 / 弹幕批 → 原生消息的单一序列化点。

## 定位
- **纯函数**（无状态、无 IO）：直播对话进入 LLM 消息数组的唯一映射
- **批与历史同形**：弹幕批与 live_chat 历史行经同一函数序列化，消除
  "批格式 vs 历史格式"双轨——两条链路产出的 `{role, content}` dict 逐字同构
- **跨窗稳定**：序列化只依赖行自身字段（角色/昵称/内容/类型/消息 ID），
  不含时间等易变量，同一行跨决策回合序列化结果字节一致（append-only 缓存前提）

## 内容格式（canonical content）
- 观众行：``[类型前缀] 昵称: 内容 [id:消息ID]``（类型前缀由 message_type
  登记表的 prompt 模板承载；`[id:…]` 目标机制保留在 content 内）
- 游戏聊天行行尾还可带 `` [提及…]`` 标注（采集器按文本判定点名了她的名字）：
  与 `[id:…]` 同一套"规划器拼、回复器剥"的办法——提示词判据锚它分"点名她的行"
  与"旁人对话"，回复器聊天记录渲染时去掉，不进她的台词
- 主播行：``[直播间] ``
  前缀 + 原样内容（发言即正文，不加昵称前缀）——与游戏条目的 ``[时刻 minecraft]``
  标注对称，标明这句只在直播间开口（观众听得到，游戏里的玩家看不到）
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from src.agents.streamer.paid_viewers import PAID_VIEWER_NOTE
from src.modules.logging import get_logger
from src.modules.types.guard_levels import DEFAULT_GUARD_NAME, GUARD_LEVEL_NAMES

__all__ = [
    "ASSISTANT_CHANNEL_PREFIX",
    "ROLE_LABELS",
    "batch_item_to_message",
    "canonical_content",
    "find_target_message",
    "live_chat_row_to_message",
    "normalize_message_id",
    "strip_assistant_channel_prefix",
    "to_text_view",
    "trim_batch_echo",
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
    # 时间线的前情提要与游戏记录：正文自带标题与时刻，原样渲染
    "chapter": "{content}",
    "game": "{content}",
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


#: 主播行的通道标注前缀：台词只在直播间开口，游戏里的玩家看不到——
#: 与游戏条目的 ``[时刻 minecraft]`` 标注对称，通道归属在对话形态上自明。
ASSISTANT_CHANNEL_PREFIX = "[直播间] "


def strip_assistant_channel_prefix(text: str) -> str:
    """去掉主播行的通道标注前缀，回到台词原文（比对与再加工用原始域）。"""
    body = text or ""
    return body[len(ASSISTANT_CHANNEL_PREFIX) :] if body.startswith(ASSISTANT_CHANNEL_PREFIX) else body


def canonical_content(
    *,
    role: str,
    nickname: str = "",
    text: str = "",
    message_type: str = "danmaku",
    message_id: str = "",
    mention: str = "",
) -> str:
    """单条直播消息 → canonical content（见模块 docstring 的格式约定）。"""
    if role == "assistant":
        content = f"{ASSISTANT_CHANNEL_PREFIX}{text}"
    else:
        line = prompt_template_for(message_type).format(nickname=nickname or "观众", content=text)
        content = line
        if message_id:
            content = f"{content} [id:{message_id}]"
        if mention:
            content = f"{content} [提及{mention}]"
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
            mention=_as_str(getattr(turn, "mention", None)),
        ),
    }


#: 历史末尾去重回扫短窗口：即使窗口内穿插了轻量进房欢迎或即时反应等 assistant 行，
#: 也能精准剔除与本批重复的观众消息，同时绝不波及更早的正常历史上下文。
_ECHO_TAIL_WINDOW = 8

#: 免费银瓜子币种：格式化礼物文案时不按付费人民币折算金额
_SILVER_CURRENCY = "bilibili_silver_coin"

#: 非观众弹幕批消息类型：去重扫描时始终保留，不与观众批次做文本匹配
_NON_VIEWER_MESSAGE_TYPES = frozenset({"chapter", "game"})


def _positive_int(value: Any) -> int:
    """提取正整数数值；布尔、None、非整数或非正数一律按 0 处理。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value > 0 else 0


def _format_yuan(total_price: int, currency: str) -> str:
    """把平台最小虚拟货币单位（B 站 1000 金瓜子 = 1 元）渲染成简洁金额文本；银瓜子或 0 返回空串。"""
    if total_price <= 0 or currency == _SILVER_CURRENCY:
        return ""
    yuan = total_price / 1000.0
    if yuan.is_integer():
        return f"¥{int(yuan)}"
    return f"¥{yuan:.2f}".rstrip("0").rstrip(".")


def _format_batch_item_text(msg: Any, message_type: str) -> str:
    """将弹幕批载荷正文渲染为简洁可读中文文本；结构化消息缺 content 时按字段拼装，绝不回退裸对象 repr。

    执行顺序：
    - 优先读取非空 ``content``（兼容弹幕、带正文留言的 SC/上舰与历史文本测试样本）；
    - ``gift`` / ``super_chat`` / ``guard`` / ``enter`` 载荷正文为空或需补金额时，按结构化子字段
      渲染为可读中文，缺字段时给出干净兜底文案，避免把 400 字 ``RoomMessagePayload(...)`` 裸串灌进上下文。
    """
    raw = _as_str(getattr(msg, "content", None)).strip()
    if message_type == "gift":
        if raw:
            return raw
        gift = getattr(msg, "gift", None)
        gift_name = _as_str(_field(gift, "name")).strip() if gift is not None else ""
        count = _positive_int(_field(gift, "count")) if gift is not None else 0
        total_price = _positive_int(_field(gift, "total_price")) if gift is not None else 0
        currency = _as_str(_field(gift, "currency")).strip() if gift is not None else ""
        price_text = _format_yuan(total_price, currency)
        if gift_name:
            base = f"送出 {gift_name} ×{count}" if count >= 1 else f"送出 {gift_name}"
            return f"{base}（{price_text}）" if price_text else base
        if count >= 1:
            return f"送出礼物 ×{count}（{price_text}）" if price_text else f"送出礼物 ×{count}"
        return f"送出了一份礼物（{price_text}）" if price_text else "送出了一份礼物"

    if message_type == "super_chat":
        sc = getattr(msg, "sc", None)
        total_price = _positive_int(_field(sc, "total_price")) if sc is not None else 0
        currency = _as_str(_field(sc, "currency")).strip() if sc is not None else ""
        price_text = _format_yuan(total_price, currency)
        if raw:
            return f"{raw}（{price_text}）" if price_text else raw
        return f"发送了醒目留言（{price_text}）" if price_text else "发送了醒目留言"

    if message_type == "guard":
        if raw:
            return raw
        guard = getattr(msg, "guard", None)
        level = _positive_int(_field(guard, "guard_level")) if guard is not None else 0
        level_name = GUARD_LEVEL_NAMES.get(level, DEFAULT_GUARD_NAME)
        guard_num = _positive_int(_field(guard, "guard_num")) if guard is not None else 0
        guard_unit = _as_str(_field(guard, "guard_unit")).strip() if guard is not None else ""
        if guard_num >= 1 and guard_unit:
            return f"开通了 {guard_num}{guard_unit}{level_name}"
        if guard_num >= 1:
            return f"开通了 {guard_num} 份{level_name}"
        return f"开通了{level_name}"

    if message_type == "enter":
        return raw or "进入直播间"

    if raw:
        return raw
    return msg.strip() if isinstance(msg, str) else ""


def _turn_role(turn: Any) -> str:
    """将历史 turn 的角色属性归一为 ``assistant`` 或 ``user``。"""
    role_raw = getattr(turn, "role", None)
    role_str = getattr(role_raw, "value", str(role_raw)) if role_raw else "user"
    return "assistant" if role_str == "assistant" else "user"


def batch_item_to_message(msg: Any, *, paid: bool = False) -> Dict[str, str]:
    """弹幕批成员（RoomMessagePayload 鸭子类型）→ 消息 dict（与历史同形）。

    ``paid=True`` 时在昵称后附「（付费观众）」标注——这位观众付过费，决策时
    值得优先满足他的要求（判据见 ``paid_viewers`` 模块）。
    """
    user = getattr(msg, "user", None)
    nickname = (getattr(user, "name", None) or getattr(user, "id", None)) if user is not None else None
    message_type = _as_str(getattr(msg, "message_type", None)) or "danmaku"
    text = _format_batch_item_text(msg, message_type)
    display = _as_str(nickname) or "观众"
    if paid:
        display = f"{display}{PAID_VIEWER_NOTE}"
    return {
        "role": "user",
        "content": canonical_content(
            role="user",
            nickname=display,
            text=text,
            message_type=message_type,
            message_id=_as_str(getattr(msg, "message_id", None)),
        ),
    }


def trim_batch_echo(history: List[Any], batch: List[Any]) -> List[Any]:
    """剔除历史末尾短窗口内与本批同源的观众消息（弹幕先落库再进决策，窗口尾部会与本批重复）。

    触发条件与执行顺序：
    1. 仅在历史末尾短窗口（至少 8 条、若单批更长则覆盖整批长度）内自后向前扫描，更早的同文历史保留；
    2. 遇到 ``assistant`` 行（如轻量进房欢迎、即时反应）或非观众时间线行（``chapter`` / ``game``）时
       原样保留且不中断扫描，避免穿插的助手发言阻断弹幕去重；
    3. 优先按 ``message_id`` 精确配额认领；历史行与批消息均无 ID 时，按同角色用户文本（双方均有昵称时
       要求同昵称）按配额认领，防止不同 ID 的旧同文弹幕被误删。
    """
    if not history or not batch:
        return list(history or [])

    id_quota: Dict[str, int] = {}
    unid_items: List[Tuple[str, str]] = []
    for msg in batch:
        msg_id = _as_str(getattr(msg, "message_id", None)).strip()
        if msg_id:
            id_quota[msg_id] = id_quota.get(msg_id, 0) + 1
            continue
        message_type = _as_str(getattr(msg, "message_type", None)) or "danmaku"
        msg_text = _format_batch_item_text(msg, message_type).strip()
        if not msg_text:
            continue
        user = getattr(msg, "user", None)
        msg_sender = _as_str(getattr(user, "name", None) if user is not None else None).strip()
        unid_items.append((msg_text, msg_sender))

    window_len = max(_ECHO_TAIL_WINDOW, len(batch))
    window_start = max(0, len(history) - window_len)
    drop_indices: set[int] = set()

    for idx in range(len(history) - 1, window_start - 1, -1):
        turn = history[idx]
        # 保留穿插在末尾短窗口里的主播发言与游戏/章节时间线行，且不阻断继续向前核对本批回显
        if _turn_role(turn) == "assistant":
            continue
        turn_type = _as_str(getattr(turn, "message_type", None))
        if turn_type in _NON_VIEWER_MESSAGE_TYPES:
            continue

        turn_id = _as_str(getattr(turn, "message_id", None)).strip()
        if turn_id:
            if id_quota.get(turn_id, 0) > 0:
                drop_indices.add(idx)
                id_quota[turn_id] -= 1
            continue

        if not unid_items:
            continue
        turn_text = _as_str(getattr(turn, "content", None)).strip()
        if not turn_text:
            continue
        turn_sender = _as_str(getattr(turn, "sender_name", None)).strip()
        matched_pos: Optional[int] = None
        for pos in range(len(unid_items) - 1, -1, -1):
            item_text, item_sender = unid_items[pos]
            if item_text != turn_text:
                continue
            if item_sender and turn_sender and item_sender != turn_sender:
                continue
            matched_pos = pos
            break
        if matched_pos is not None:
            drop_indices.add(idx)
            unid_items.pop(matched_pos)

    if not drop_indices:
        return list(history)
    return [turn for idx, turn in enumerate(history) if idx not in drop_indices]


#: 消息 ID 的引用写法：Planner 照着 canonical 行尾的 ``[id:…]`` 填 target，可能连括号一起抄
_ID_REFERENCE = re.compile(r"^\[?\s*id\s*[:：]\s*([^\]\s]+)\s*\]?$", re.IGNORECASE)


def normalize_message_id(target: str) -> str:
    """把 ``[id:xxx]`` / ``id:xxx`` / ``xxx`` 三种写法归一成裸消息 ID。"""
    text = (target or "").strip()
    match = _ID_REFERENCE.match(text)
    return match.group(1) if match else text


def find_target_message(target: Optional[str], batch: List[Any], history: List[Any]) -> str:
    """按消息 ID 找出要回应的那条原话（canonical content，与弹幕行同形）。

    本批优先，再从近到远查历史；找不到返回空串。只认 ID 精确命中，不按文本猜——
    把别人的话当成要回应的原话交给回复器，比不给更糟。
    """
    message_id = normalize_message_id(target) if isinstance(target, str) else ""
    if not message_id:
        return ""
    for msg in batch:
        if _as_str(getattr(msg, "message_id", None)) == message_id:
            return batch_item_to_message(msg)["content"]
    for turn in reversed(history):
        if _as_str(getattr(turn, "message_id", None)) == message_id:
            return turn_to_message(turn)["content"]
    return ""


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
