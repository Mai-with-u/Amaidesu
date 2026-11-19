"""MinecraftAgent 自主领航模式（游玩期动态主从）辅助原语。

职责边界：
- 交权简报格式化：把框架传入的 ``LeadHandoverContext`` 组织成紧凑的
  ``[主控交接简报]`` user 消息，供自主领航开局阅读。
- 非强杀观众建议格式化：把主播转达的观众点菜/提醒与去重来源原话包装成
  ``[直播间建议]`` 消息，明确不打断在跑目标。
- 只读台词回执缓冲（``SpokenEchoBuffer``）：有界、去重、截断、零唤醒；
  在游戏侧下一次正常推理步把未读回执拼成极简 ``[直播间刚说过]`` 文本。
- 游戏主控态系统提示词附加段（``LEAD_MODE_PROMPT_SECTION``）：遵守提示词
  编写纪律，用开放边界描述自主规划、公屏主导、解说节制与建议采纳原则。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional, Sequence

from src.modules.agents.lead import LeadHandoverContext

__all__ = [
    "LEAD_MODE_PROMPT_SECTION",
    "MAX_SPOKEN_ECHOES",
    "MAX_SPOKEN_ECHO_CHARS",
    "SpokenEchoEntry",
    "SpokenEchoBuffer",
    "format_lead_handover_brief",
    "format_suggestion_message",
]

# 只读台词回执缓冲上限与单条截断长度：只留最近几句刚播过的话，避免挤占游戏推理上下文
MAX_SPOKEN_ECHOES = 5
MAX_SPOKEN_ECHO_CHARS = 120
# 观众建议附带的来源原话条数与单条截断长度
_MAX_SUGGESTION_DIALOGUE_LINES = 5
_MAX_SUGGESTION_DIALOGUE_CHARS = 120

# 游戏主控态追加到系统提示词末尾的自主领航段（开放边界描述，不列成品句范例）
LEAD_MODE_PROMPT_SECTION = """

## 游戏自主领航模式（当前生效）

当前由你自主主控本段游戏节奏，直播间主播处于轻从侧负责观众互动与统一口播，不再逐条向你下达委派单。以下规则在主控期内优先于上文中的委派交付与上报规则：

- **自主规划与持续推进**：围绕 `[主控交接简报]` 中的本段目标、当前存档工作笔记与现场情况，自主拆解阶段任务并用 `minecraft_todo` 维护推进清单，把跨阶段关键结论记进 `minecraft_notebook`。做完一个小目标后继续推进下一项待办或自主开展合理的下一步探索，不要因为完成一步就停工等待外部指令；当有后台目标在跑、炉子在烧或确实暂无行动需求时，不调工具自然结束本轮等待事件唤醒即可，不要做无意义的重复读取。
- **直接主导游戏公屏**：游戏内公屏交流由你直接负责，主播侧不再替你代发公屏。收到 `[公屏]` 或 `[公屏·私聊]` 玩家消息时，由你自主判断是否用 `maicraft_execute` 的 `chat` 能力直接回应同伴，并按需调整当前计划；保持玩家之间简洁自然的沟通节奏。
- **节制提交实况解说（`framework_commentary`）**：主控期不使用 `minecraft_report` 交付或请示，改用 `framework_commentary` 向主播提交 1–2 句客观现场要点，由主播按人设润色讲给观众。只在确实出现值得分享的变化时调用——如阶段目标完成、明显局势转折、卡壳受阻、突发意外、有意思的新发现或同伴互动看点；常规赶路、翻箱、重复采集等平淡过程不要流水账式高频提交。仅在突发危险或关键转折时将 `urgency` 设为 `high`，涉及游戏内其他玩家时填好 `about_player`。
- **理性自主处理 `[直播间建议]`**：`[直播间建议]` 是主播转达的观众点菜或善意提醒，不是强制命令，也不会打断你正在执行的后台目标。结合当前安全态势、手头进度与环节目标自主评估：顺手且合适就立即执行或排进 `minecraft_todo`，当下不合适或与主线冲突时先搁置或完成手头事后再考虑。
- **参考 `[直播间刚说过]` 避免复读**：当步消息若附带 `[直播间刚说过]`，说明直播间刚刚对观众播报过这些内容（仅作只读参考）。后续调用 `framework_commentary` 时避开已播过的同一话题，只补充新进展或新变化。
"""


@dataclass(frozen=True, slots=True)
class SpokenEchoEntry:
    """一条主播已播出的只读台词回执。"""

    text: str
    source: str = ""
    timestamp_ms: int = 0


class SpokenEchoBuffer:
    """主播口播回执的有界滚动缓冲（同步写入、零唤醒、按需取出未读项）。"""

    def __init__(self, *, maxlen: int = MAX_SPOKEN_ECHOES, max_chars: int = MAX_SPOKEN_ECHO_CHARS) -> None:
        self._maxlen = max(1, maxlen)
        self._max_chars = max(16, max_chars)
        self._entries: Deque[SpokenEchoEntry] = deque(maxlen=self._maxlen)

    def push(self, text: str, *, source: str = "", timestamp_ms: int = 0) -> bool:
        """写入一条刚播出的台词：空白忽略、与队列已有同文去重、超长截断；返回是否入队。"""
        normalized = " ".join((text or "").split())
        if not normalized:
            return False
        if len(normalized) > self._max_chars:
            normalized = normalized[: self._max_chars - 1].rstrip() + "…"
        # 去重：避免同一句回声重复挤占有限槽位
        if any(entry.text == normalized for entry in self._entries):
            return False
        self._entries.append(
            SpokenEchoEntry(text=normalized, source=(source or "").strip(), timestamp_ms=int(timestamp_ms or 0))
        )
        return True

    def drain_notice(self) -> str:
        """取出全部未读回执并清空缓冲，拼成极简 ``[直播间刚说过]`` 提示；无未读返回空串。"""
        if not self._entries:
            return ""
        items = list(self._entries)
        self._entries.clear()
        lines = ["[直播间刚说过]（只读参考，避免重复刚播过的话题）"]
        for entry in items:
            lines.append(f"- {entry.text}")
        return "\n".join(lines)

    def clear(self) -> None:
        """清空缓冲（交权开局、收权退出或下播收工时调用）。"""
        self._entries.clear()

    def snapshot(self) -> List[SpokenEchoEntry]:
        """只读快照（观测与测试断言用）。"""
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


def _format_expected_duration(expected_ms: int) -> str:
    """把预计时长毫秒转成紧凑可读文本；<=0 返回空串。"""
    if expected_ms <= 0:
        return ""
    total_seconds = max(1, expected_ms // 1000)
    minutes, seconds = divmod(total_seconds, 60)
    if minutes > 0 and seconds > 0:
        return f"约 {minutes} 分 {seconds} 秒"
    if minutes > 0:
        return f"约 {minutes} 分钟"
    return f"约 {seconds} 秒"


def _dedupe_clean_lines(
    lines: Sequence[str],
    *,
    exclude: Optional[str] = None,
    limit: int = _MAX_SUGGESTION_DIALOGUE_LINES,
    max_chars: int = _MAX_SUGGESTION_DIALOGUE_CHARS,
) -> List[str]:
    """清理空白、截断并保序去重，可选排除与正文完全相同的条目。"""
    excluded = " ".join((exclude or "").split())
    seen: set[str] = set()
    result: List[str] = []
    for raw in lines:
        cleaned = " ".join(str(raw or "").split())
        if not cleaned:
            continue
        if len(cleaned) > max_chars:
            cleaned = cleaned[: max_chars - 1].rstrip() + "…"
        if cleaned == excluded or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def format_lead_handover_brief(context: LeadHandoverContext, *, fallback_save_name: str = "") -> str:
    """把主控交接上下文格式化为紧凑的 ``[主控交接简报]`` user 消息。"""
    lines: List[str] = ["[主控交接简报] 现在由你进入游戏自主领航模式，自主规划并推进本段游玩内容："]

    title = (context.segment_title or "").strip()
    goal = (context.segment_goal or "").strip()
    duration = _format_expected_duration(context.expected_ms)
    if title or duration:
        header = title or "游玩环节"
        if duration:
            header = f"{header}（预计时长：{duration}）"
        lines.append(f"- 当前环节：{header}")
    if goal:
        lines.append(f"- 环节目标：{goal}")

    save_name = (context.save_name or "").strip() or (fallback_save_name or "").strip()
    if save_name:
        lines.append(f"- 当前存档：{save_name}")

    memory_brief = (context.memory_brief or "").strip()
    if memory_brief:
        lines.append(f"- 跨场记忆与承诺：{memory_brief}")

    operator_notes = _dedupe_clean_lines(context.operator_notes, limit=5, max_chars=160)
    if operator_notes:
        lines.append("- 运营前置提醒：" + "；".join(operator_notes))

    recent_speech = _dedupe_clean_lines(context.recent_speech, limit=5, max_chars=140)
    if recent_speech:
        lines.append("- 开场铺垫（直播间刚说过）：" + " / ".join(recent_speech))

    lines.append(
        "- 行动口径：请结合下方 [开局资料] 与已有待办/笔记自主决定下一步；直接用 chat 回应游戏内玩家，有值得分享的转折或成果再调 framework_commentary。"
    )
    return "\n".join(lines)


def format_suggestion_message(
    content: str,
    *,
    source: str = "streamer",
    source_dialogue: Optional[List[str]] = None,
) -> str:
    """把非强杀观众建议包装为 ``[直播间建议]`` 消息（附带去重后的来源原话）。"""
    text = " ".join((content or "").split())
    src_label = (source or "").strip() or "streamer"
    lines: List[str] = [
        f"[直播间建议]（来源：{src_label}；非强制命令，不打断当前在跑目标，可自主决定立刻做、记进 todo 或先搁置）",
        f"建议内容：{text}",
    ]
    dialogue = _dedupe_clean_lines(source_dialogue or (), exclude=text)
    if dialogue:
        lines.append("来源原话：")
        for item in dialogue:
            lines.append(f"- {item}")
    return "\n".join(lines)
