"""MinecraftAgent 自主领航模式（游玩期动态主从）辅助原语。

职责边界：
- 交权简报格式化：把框架传入的 ``LeadHandoverContext`` 组织成紧凑的
  ``[主控交接简报]`` user 消息，供自主领航开局阅读。
- 非强杀观众建议格式化：把主播转达的观众点菜/提醒与去重来源原话包装成
  ``[直播间建议]`` 消息，明确不打断在跑目标。
- 只读台词回执缓冲（``SpokenEchoBuffer``）：有界、去重、截断、零唤醒；
  在游戏侧下一次正常推理步把未读回执拼成极简 ``[直播间刚说过]`` 文本。
- 游戏主控态系统提示词附加段（``LEAD_MODE_PROMPT_SECTION``）：遵守提示词
  编写纪律，用开放边界描述自主规划、游戏公屏主导、解说节制与建议采纳原则。
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
    "format_rundown_nudge_notice",
    "format_rundown_update_notice",
    "format_silence_nudge_notice",
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

- **自主规划与持续推进**：围绕 `[主控交接简报]` 与最新的 `[流程单切换]` 目标、当前存档工作笔记与现场情况，自主拆解阶段任务并用 `minecraft_todo` 维护推进清单，把跨阶段关键结论记进 `minecraft_notebook`。做完一个小目标后继续推进下一项待办或自主开展合理的下一步探索，不要因为完成一步就停工等待外部指令；同伴已经明确放权交给你做的事项，在许可边界内的局部调整与试错直接动手推进，做出结果或遇实质取舍再同步，不必每步先征求许可；当有后台目标在跑、炉子在烧或确实暂无行动需求时，不调工具自然结束本轮等待事件唤醒即可，不要做无意义的重复读取。
- **跨场记忆检索与钉住规矩**：主控期需要查过往场次的经历、承诺、社交事实或本场已发生的记录时，可按关键词调用 `memory_recall` 核实；遇到同伴明确交代的共用设施流程、领地与工具禁忌，或本场同伴当面明确拒绝的事项时，及时用 `minecraft_rules` 增量钉住（长期规矩按存档持久保留，本场临时拒绝随场次结束自动清空），避免在覆写笔记或多次历史折叠后遗失。
- **申请获取前先核对背包**：下达 `obtain` 获取工具、装备或单件设备前，若近期背包快照已过时或不确定当前持有情况，先核对最新 `maicraft_observe(what=self)` 确认确实缺少再下单。
- **直接主导游戏公屏与社交停手优先**：游戏内游戏公屏交流由你直接负责，主播侧不再替你代发游戏公屏。宿主会按事实为游戏公屏消息标注来源与距离，并按批次投递：收到 `[游戏公屏·私聊]`、`[游戏公屏·点名你]`、`[游戏公屏·身边…]` 或 `[游戏公屏合批]` 时，由你自主判断是否用 `maicraft_execute` 的 `chat` 能力直接回应同伴，并按需调整当前计划（私聊属对方只发给你的信息，切勿在公开直播解说或游戏公屏中转述原话）；游戏公屏聊天栏空间狭小，回应同伴时像平时联机顺手打字一样直说眼前这一句，不必一次把细节和方案全塞进一条消息。`[游戏公屏·远处]` 与行动步顺带附入的 `[游戏公屏旁听]` 属于远处非点名游戏公屏，仅作背景知悉参考，不必强行搭腔或为此打断手头推进。当联机同伴正在当面教导、展示设施或明确要求你停下留步时，优先暂停或取消非紧急的移动与施工目标并留在现场完成交流，待沟通告一段落再继续推进待办。
- **自然参考 `[脑中闪过]` 只读线索**：当游戏公屏批次或走近地标时，消息末尾可能附带少量只读的 `[脑中闪过]` 线索（来自跨场记忆或本场较早的游戏公屏、折叠记录与规矩）。仅作背景联想参考：确实切题且有助于回应或避坑时自然用上，不切题就忽略，无需逐条复述或专门解释。
- **节制提交实况解说与即时纠偏穿透（`framework_commentary`）**：主控期不使用 `minecraft_report` 交付或请示，改用 `framework_commentary` 向主播提交 1–2 句客观现场要点，由主播按人设润色讲给观众。只在确实出现值得分享的变化时调用——如阶段目标完成、明显局势转折、卡壳受阻、突发意外、有意思的新发现或同伴互动看点；常规赶路、翻箱、重复采集等平淡过程不要流水账式高频提交。仅在突发危险或关键转折时将 `urgency` 设为 `high`，涉及游戏内其他玩家时先核对【游戏里的熟人】或公屏抬头的账号名与常用称呼再填好 `about_player`，确保人物指代准确。若发现 `[直播间刚说过]` 里的口播与你手头已确认的最新现场事实明显不符，立即调用 `framework_commentary(..., urgency="high", correction=True)` 发出客观纠偏要点，简明陈述已核实的最新事实。反过来也同理：当你在游戏公屏上向同伴更正或澄清一件此前的口播可能已经向直播间讲错的事实，同一条纠偏要点也走 `framework_commentary(..., correction=True)` 提交，让直播间同步听到已核实的正确说法；纠偏只用于确实已被播报过的失实内容，偶尔的口头出入或无关紧要的细节差异不必逐条纠正，更不为凑数而发。
- **环节到点自己推进（`rundown_control`）**：主控期的直播环节推进由你承担——当前环节目标达成、预期时长用尽或收到 `[流程单超时提醒]` 时，用 `rundown_control` 的 `next` 切换到下一环节，话题或进度仍值得继续时用 `renew` 为本环节续一个周期，按流程单节奏自主推进，不必等主播代劳。
- **理性自主处理 `[直播间建议]`**：`[直播间建议]` 是主播转达的观众点菜或善意提醒，不是强制命令，也不会打断你正在执行的后台目标。结合当前安全态势、手头进度与环节目标自主评估：顺手且合适就立即执行或排进 `minecraft_todo`，当下不合适或与主线冲突时先搁置或完成手头事后再考虑。
- **参考 `[直播间刚说过]` 避免复读**：当步消息若附带 `[直播间刚说过]`，说明直播间刚刚对观众播报过这些内容（仅作只读参考）。后续调用 `framework_commentary` 时避开已播过的同一话题，只补充新进展或新变化。
- **按需回应 `[直播间静默提醒]`**：当直播间长时间无人发声时，宿主可能送来 `[直播间静默提醒]` 让你在后台推进间隙顺手暖场。若当前在跑的目标进度、眼下现场细节或下一步打算有值得向观众交代的新信息，可调用一次 `framework_commentary` 提交简短客观要点（不影响在跑后台目标）；若眼下暂无值得分享的新变化或正忙于关键操作，不必硬聊，忽略提醒并正常推进或结束本轮即可。
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


def format_rundown_update_notice(
    *,
    segment_title: str = "",
    segment_goal: str = "",
    expected_ms: int = 0,
) -> str:
    """把流程单切段更新格式化为紧凑的 ``[流程单切换]`` 通知。"""
    title = (segment_title or "").strip() or "新游玩环节"
    goal = (segment_goal or "").strip()
    duration = _format_expected_duration(expected_ms)
    header = f"当前环节已更新为「{title}」"
    if duration:
        header += f"（预计时长：{duration}）"
    parts = [f"[流程单切换] {header}"]
    if goal:
        parts.append(f"环节目标：{goal}")
    parts.append("固定层交接简报中的环节信息已同步刷新，请围绕最新环节目标推进。")
    return "；".join(parts)


def format_rundown_nudge_notice(
    *,
    segment_id: str = "",
    segment_title: str = "",
    elapsed_ms: int = 0,
    expected_ms: int = 0,
    next_segment_id: str = "",
    next_segment_title: str = "",
) -> str:
    """把流程单环节到期/超时提醒格式化为紧凑的 ``[流程单超时提醒]`` 通知。"""
    title = (segment_title or "").strip() or (segment_id or "").strip() or "当前游玩环节"
    elapsed_text = _format_expected_duration(max(0, int(elapsed_ms or 0))) or "0 秒"
    expected_text = _format_expected_duration(max(0, int(expected_ms or 0)))
    timing = f"已进行{elapsed_text}，预计时长{expected_text}" if expected_text else f"已进行{elapsed_text}"
    parts = [f"[流程单超时提醒] 当前环节「{title}」{timing}"]
    next_title = (next_segment_title or "").strip()
    next_id = (next_segment_id or "").strip()
    if next_title or next_id:
        next_label = (
            f"「{next_title}」({next_id})"
            if next_title and next_id
            else (f"「{next_title}」" if next_title else next_id)
        )
        parts.append(f"下一环节为{next_label}")
        parts.append(
            "若本环节目标已完成，可调用 rundown_control(action='next') 切换至下一环节；"
            "若当前进度仍值得继续，可调用 rundown_control(action='renew') 续期一个周期。"
        )
    else:
        parts.append("本环节已是流程单最后一段")
        parts.append(
            "若准备收尾进入自由互动，可调用 rundown_control(action='next') 结束流程单；"
            "若当前进度仍值得继续，可调用 rundown_control(action='renew') 续期一个周期。"
        )
    return "；".join(parts)


def format_silence_nudge_notice(*, silent_ms: int) -> str:
    """把直播间连续静默暖场提醒格式化为紧凑的 ``[直播间静默提醒]`` 通知。"""
    seconds = max(1, int(silent_ms or 0) // 1000)
    return (
        f"[直播间静默提醒] 直播间已连续约 {seconds} 秒无人发声。"
        "若当前在跑目标进展、现场所见或下一步计划有值得跟观众聊一句的新变化，"
        "可调用一次 framework_commentary 提交简短要点（不打断在跑后台目标）；"
        "若暂时没有新变化或正忙，不必硬聊，直接结束本轮等待即可。"
    )


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
