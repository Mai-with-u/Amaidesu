"""嘴：游戏里出事时不经 Planner、几秒内当场出一句短反应（Agent 内部件，代码直连，不是工具）。

为什么拆出来：现在每句话都要走一整轮 Planner 加 Replyer，五六十秒才开一次口，被打、掉河里这种
当下的事只能事后讲段子。离线对照里，带一张现场卡的瘦嘴每句 4～5 秒、语气词多一倍，复读和现管线
持平，突发事件时会当场叫出来（"疼疼疼！怎么还有掠夺者啊"）。所以把"对遭遇的反应"交给一张嘴：

- 触发：主播 Agent 把游戏事件转进来（:meth:`react`）。同一时刻只生成一句，生成期间来的事件攒着，
  下一句合并成一个由头；同一类遭遇冷却一段时间；小事（急事开始结束、天色）只在她安静了一阵时才开口。
- 依据：系统提示词只有人设和几条规矩，事实全在每次附上的现场卡里（现在的状态、手上的目标、刚发生的事、
  她刚说过的话）——实验里没有这张卡的瘦嘴会理直气壮地编现场。
- 兜底：输出一到三行"情绪：台词"或"[沉默]"——情绪在一次反应里转了（先吓一跳再松口气）就拆成
  几行，每行一个情绪；没写情绪的行沿用上一行的。台词按句累计截到长度预算、过敏感词过滤、几行拼起来
  和她最近说过的话比重合度，重复的直接丢弃不重试。出声走发声分发器的反应通道：前一句没播完最多等一会
  儿，等不到整段作废；后面几行排在第一行后面，形象等轮到那句播放时再切表情。第一句真轮到播放时记一条
  日志，写明生成、等前一句、从事件到开始播各用了多久。

嘴只说话，不做事：委派、流程单、查询仍归 Planner。
"""

from __future__ import annotations

import asyncio
import re
from collections import deque
from dataclasses import dataclass
from functools import partial
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Sequence, Tuple

from src.modules.logging import get_logger
from src.modules.types.emotion_vocab import Emotion
from src.modules.utils.time_utils import now_ms

from .config import StreamerMouthConfig
from .scene_card import GoalLine, SceneInputs, build_scene_card
from .speech_guard import WordFilter, apply_word_filter, fit_to_budget, guard
from .tools.reply_tool import coerce_emotion
from .topic_gate import SpeechGate

__all__ = ["MOUTH_TEMPLATE", "Mouth", "SCENE_CHANGING_BODY_KINDS", "Trigger", "URGENT_BODY_KINDS", "URGENT_GAME_TYPES"]

MOUTH_TEMPLATE = "amaidesu_mouth"

#: 要当场反应的遭遇：被打、死亡、重生、换维度、身体处理不了的需求
URGENT_BODY_KINDS = frozenset({"attacked", "died", "respawned", "dimension_changed", "need_unhandled"})
#: 现场一下子变了的遭遇：读到就立即重读一句话现场
SCENE_CHANGING_BODY_KINDS = frozenset({"died", "respawned", "dimension_changed"})
#: 游戏进展类事件里要当场反应的：进展、交付或卡住（急事提醒和遭遇说的是同一件事，不另触发）。
#: 游戏 Agent 自己出的错（模型调用失败、请求被拒）不是游戏里发生的事，不给嘴，由 Planner 看着处理
URGENT_GAME_TYPES = frozenset({"milestone", "report"})

#: 事件到了先等这么久再生成：同一瞬间连发的几条并成一个由头
_DEBOUNCE_S = 0.3
#: 从事件发生到出声超过这么久就不说了：再晚就成了事后讲故事，那是 Planner 的活
_MAX_REACTION_AGE_MS = 20_000
#: 反应插到其他待播台词前面的优先级
_PRIORITY = 5
_SILENCE = "[沉默]"
_EMOTION_LINE = re.compile(r"^\s*([A-Za-z_]+)\s*[：:]\s*(.+)$")
#: 一次反应最多拆成几行（每行一个情绪）：再多就成了讲一段，那是 Planner 的活
_MAX_LINES = 3
#: 整行都包在括号里的是旁白或动作说明（"（挠头）"），不是要念出来的话
_STAGE_DIRECTION = re.compile(r"^[（(【\[].*[）)】\]]$")


@dataclass(frozen=True)
class _Record:
    """嘴自己留的一条最近记录：弹幕、她说过的话、白话的游戏条目。"""

    at_ms: int
    kind: str  # viewer / speech / game
    text: str
    sender: str = ""


@dataclass(frozen=True)
class Trigger:
    """一个由头：冷却按 ``key`` 算（同类遭遇共用），``urgent`` 决定要不要等她安静下来。"""

    at_ms: int
    key: str
    text: str
    urgent: bool
    refresh_scene: bool


class Mouth:
    """嘴：攒由头、拼现场卡、生成一句、兜底后经发声分发器出声。"""

    def __init__(
        self,
        config: StreamerMouthConfig,
        *,
        llm_service: Any,
        prompt_service: Any,
        speech: Any,
        room_state: Any,
        persona: Dict[str, str],
        speech_max_chars: int,
        gate: SpeechGate,
        word_filter: Optional[WordFilter],
        goals_provider: Callable[[], Sequence[GoalLine]],
        notebook_provider: Callable[[], str],
        segment_provider: Callable[[], str],
        scene_reader: Callable[[], Awaitable[str]],
        is_live: Callable[[], bool],
        long_memory_provider: Callable[[], str] = lambda: "",
        profile: str = "replyer",
        clock: Callable[[], int] = now_ms,
    ) -> None:
        """依赖全部构造注入：LLM、提示词、发声分发器（``dispatch_reaction``）、房间态势（``last_speech_ms`` /
        ``activity_line`` / ``record_speech``），以及现场卡各段的数据来源。"""
        self._config = config
        self._llm = llm_service
        self._prompt = prompt_service
        self._speech = speech
        self._room_state = room_state
        self._persona = persona
        self._speech_max_chars = speech_max_chars
        self._gate = gate
        self._word_filter = word_filter
        self._goals_provider = goals_provider
        self._notebook_provider = notebook_provider
        self._segment_provider = segment_provider
        self._scene_reader = scene_reader
        self._is_live = is_live
        self._long_memory_provider = long_memory_provider
        self._profile = profile
        self._clock = clock
        self._logger = get_logger("StreamerAgent.Mouth")

        # 最近记录多留一些：游戏条目多时她说过的话也不至于被挤出去
        self._records: Deque[_Record] = deque(maxlen=max(30, config.history_items * 3))
        self._pending: List[Trigger] = []
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None
        # 每类由头上次反应（或决定不说）的时刻：冷却期内同类只记进现场卡
        self._last_reacted: Dict[str, int] = {}
        self._scene_brief = ""
        self._scene_at_ms = 0

    # ---------------- 记录与触发（主播 Agent 调） ----------------

    def note_viewer(self, sender: str, text: str, at_ms: Optional[int] = None) -> None:
        self._records.append(_Record(at_ms or self._clock(), "viewer", text, sender))

    def note_speech(self, text: str, at_ms: Optional[int] = None) -> None:
        self._records.append(_Record(at_ms or self._clock(), "speech", text))

    def note_game(self, text: str, at_ms: Optional[int] = None) -> None:
        self._records.append(_Record(at_ms or self._clock(), "game", text))

    def react(
        self, key: str, text: str, *, urgent: bool, refresh_scene: bool = False, at_ms: Optional[int] = None
    ) -> None:
        """记一个由头并唤醒生成循环；不阻塞调用方（事件回调里直接调）。"""
        self._pending.append(Trigger(at_ms or self._clock(), key, text, urgent, refresh_scene))
        self._wake.set()

    def reset(self) -> None:
        """场次边界：清掉上一场的记录、由头、冷却与现场，开播后从头记。"""
        self._records.clear()
        self._pending.clear()
        self._last_reacted.clear()
        self._scene_brief, self._scene_at_ms = "", 0

    # ---------------- 生命周期 ----------------

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="StreamerAgent.Mouth")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        """单飞循环：被唤醒后稍等片刻把连发的由头并起来，一次只生成一句。"""
        while True:
            await self._wake.wait()
            await asyncio.sleep(_DEBOUNCE_S)
            self._wake.clear()
            triggers, self._pending = self._pending, []
            if not triggers:
                continue
            try:
                await self.react_once(triggers)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 一句反应失败不能拖垮嘴
                self._logger.exception(f"嘴生成反应失败: {exc}")

    # ---------------- 一次反应 ----------------

    async def react_once(self, triggers: Sequence[Trigger]) -> Optional[str]:
        """对一批由头生成一句并出声；返回说出口的台词，没说返回 None。"""
        if not self._is_live():
            return None
        now = self._clock()
        cooldown = self._config.same_kind_cooldown_ms
        fresh = [
            trigger
            for trigger in triggers
            if now - trigger.at_ms <= _MAX_REACTION_AGE_MS
            and now - self._last_reacted.get(trigger.key, -cooldown) >= cooldown
        ]
        if not fresh:
            return None
        urgent = any(trigger.urgent for trigger in fresh)
        last_speech = self._room_state.last_speech_ms
        if not urgent and last_speech is not None and now - last_speech < self._config.quiet_ms:
            # 小事：她刚说过话，就只记进现场卡，不为它开口
            return None
        for trigger in fresh:
            self._last_reacted[trigger.key] = now

        await self._refresh_scene(force=any(trigger.refresh_scene for trigger in fresh))
        started = self._clock()
        response = await self._llm.generate(self._messages(fresh), system=self._system_prompt(), profile=self._profile)
        generated = self._clock()
        if not response.ok:
            self._logger.warning(f"嘴生成失败: {response.error or 'unknown'}")
            return None
        segments = self._parse(response.content or "")
        if not segments:
            self._logger.info(f"嘴选择不说：由头={self._trigger_text(fresh)}")
            return None
        segments = self._guard(segments)
        if not segments:
            return None
        text = "".join(line for _, line in segments)
        (first_emotion, first_text), rest = segments[0], segments[1:]
        event_at = min(trigger.at_ms for trigger in fresh)
        if self._clock() - event_at > _MAX_REACTION_AGE_MS:
            self._logger.info(f"反应生成太慢，已过时效，不说了: {text}")
            return None
        # 延迟日志等这句真轮到播放时再记：被前一句压着排队的那段也算进"从事件到开始播"
        on_started = partial(
            self._log_started, text, self._trigger_text(fresh), generated - started, event_at, self._clock()
        )
        utterance_id = await self._speech.dispatch_reaction(
            first_text,
            emotion=first_emotion.value,
            emotion_intensity=0.7 if urgent else 0.5,
            wait_ms=self._config.reaction_ttl_ms,
            gap_ms=self._config.min_gap_ms,
            priority=_PRIORITY,
            round_id=f"mouth_{started}",
            llm_request_id=response.request_id or None,
            on_started=on_started,
            follow_ups=[(line, emotion.value) for emotion, line in rest],
        )
        if utterance_id is None:
            return None
        self._room_state.record_speech(self._clock())
        return text

    def _log_started(self, text: str, trigger_text: str, generate_ms: int, event_at_ms: int, ready_at_ms: int) -> None:
        """这句轮到播放（交给 TTS）时记一条：生成用了多久、等前一句等了多久、从事件到开始播一共多久。"""
        now = self._clock()
        self._logger.info(
            f"嘴：{text}（由头：{trigger_text}；生成 {generate_ms}ms，等前一句 {now - ready_at_ms}ms，"
            f"从事件到开始播 {now - event_at_ms}ms）"
        )

    # ---------------- 输入 ----------------

    def _system_prompt(self) -> str:
        return self._prompt.render(
            MOUTH_TEMPLATE,
            bot_name=self._persona.get("bot_name", "麦麦"),
            personality=self._persona.get("personality", ""),
            style_constraints=self._persona.get("style_constraints", ""),
            max_chars=str(self._speech_max_chars or 40),
            emotions="、".join(emotion.value for emotion in Emotion),
            long_memory=self._long_memory_block(),
        )

    def _long_memory_block(self) -> str:
        """长期记忆进系统提示词的那一段；没有就是空串。开播时读出、整场不变，系统提示词前缀稳定。"""
        try:
            text = (self._long_memory_provider() or "").strip()
        except Exception as exc:  # noqa: BLE001 - 读不到就不带，照常说话
            self._logger.warning(f"读长期记忆失败: {exc}", exc=True)
            return ""
        if not text:
            return ""
        return (
            "\n\n你自己整理的长期笔记（过了好几场还记得的事：用得上时再用，不必主动提起；"
            f"现场的事以【现场】为准）：\n{text}\n"
        )

    @staticmethod
    def _trigger_text(triggers: Sequence[Trigger]) -> str:
        return "；".join(dict.fromkeys(trigger.text for trigger in triggers))

    def _messages(self, triggers: Sequence[Trigger]) -> List[Dict[str, str]]:
        """最近记录按对话排开（她说过的是 assistant），最后一条附现场卡与由头。"""
        messages: List[Dict[str, str]] = []
        recent = list(self._records)[-self._config.history_items :] if self._config.history_items else []
        for record in recent:
            if record.kind == "speech":
                messages.append({"role": "assistant", "content": record.text})
            elif record.kind == "viewer":
                messages.append({"role": "user", "content": f"弹幕 {record.sender}：{record.text}"})
            else:
                messages.append({"role": "user", "content": f"（游戏里：{record.text}）"})
        card = build_scene_card(self._scene_inputs(triggers), max_chars=self._config.card_max_chars)
        messages.append(
            {"role": "user", "content": f"【现场】\n{card}\n\n轮到你了：想说就说一句，没什么想说的就只输出{_SILENCE}"}
        )
        return messages

    def _scene_inputs(self, triggers: Sequence[Trigger]) -> SceneInputs:
        now = self._clock()
        try:
            goals = list(self._goals_provider())
        except Exception as exc:  # noqa: BLE001 - 某段读不到只省这一段
            self._logger.warning(f"读手上的目标失败: {exc}", exc=True)
            goals = []
        try:
            notebook = self._notebook_provider() or ""
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"读备忘录失败: {exc}", exc=True)
            notebook = ""
        try:
            segment = self._segment_provider() or ""
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"读当前环节失败: {exc}", exc=True)
            segment = ""
        return SceneInputs(
            now_ms=now,
            trigger="刚才游戏里：" + self._trigger_text(triggers),
            scene_brief=self._scene_brief,
            scene_brief_ms=self._scene_at_ms,
            goals=goals,
            recent_events=[(record.at_ms, record.text) for record in self._records if record.kind == "game"],
            segment_title=segment,
            room_activity=self._room_state.activity_line(),
            notebook=notebook,
            recent_speeches=[record.text for record in self._records if record.kind == "speech"],
        )

    async def _refresh_scene(self, *, force: bool) -> None:
        """一句话现场最多按间隔重读一次；现场一下子变了（死亡、重生、换维度）时立即重读。"""
        now = self._clock()
        if not force and self._scene_brief and now - self._scene_at_ms < self._config.scene_refresh_ms:
            return
        try:
            brief = await self._scene_reader()
        except Exception as exc:  # noqa: BLE001 - 读不到就用旧的，卡里会写明是多久前看的
            self._logger.warning(f"读一句话现场失败: {exc}", exc=True)
            return
        if brief:
            self._scene_brief, self._scene_at_ms = brief, now

    # ---------------- 输出 ----------------

    def _parse(self, content: str) -> List[Tuple[Emotion, str]]:
        """取前几行非空输出，逐行拆成（情绪, 台词）；首行是"[沉默]"或没有输出时返回空列表。

        拆不出情绪的行沿用上一行的情绪（第一行按 neutral）——标签漏写只让表情少切一次，
        不丢台词；中途出现"[沉默]"就到此为止；整行包在括号里的旁白不念。
        """
        lines = [part.strip() for part in content.splitlines() if part.strip()]
        segments: List[Tuple[Emotion, str]] = []
        current = Emotion.NEUTRAL
        for line in lines:
            if len(segments) >= _MAX_LINES or _SILENCE in line or "【沉默】" in line:
                break
            match = _EMOTION_LINE.match(line)
            if match:
                current, _note = coerce_emotion(match.group(1))
                line = match.group(2).strip()
            if line and not _STAGE_DIRECTION.match(line):
                segments.append((current, line))
        return segments

    def _guard(self, segments: List[Tuple[Emotion, str]]) -> List[Tuple[Emotion, str]]:
        """长度按句累计到预算、敏感词过滤、整段和她最近说过的话比重合度；不过关返回空列表（直接丢弃不重试）。

        长度：第一句照常按预算截（截不出完整句时整句保留）；后面的句子放得下才要，放不下整句
        不要，不截成半句。敏感词逐句净化，命中"整条丢弃"就整段丢。重合度按几句拼起来比——拆成
        几行不该让复读漏网。判定与词汇定义收拢在 speech_guard（与 Replyer 同源），这里只保留嘴
        自己的丢弃日志。
        """
        budget = self._speech_max_chars
        kept: List[Tuple[Emotion, str]] = []
        used = 0
        for index, (emotion, line) in enumerate(segments):
            if budget > 0:
                if index == 0:
                    line = fit_to_budget(line, budget)
                elif used + len(line) > budget:
                    break
            filtered = apply_word_filter(line, self._word_filter)
            if filtered is None:
                self._logger.info("嘴的台词命中敏感词，整段丢弃")
                return []
            kept.append((emotion, filtered))
            used += len(filtered)
        joined = "".join(line for _, line in kept)
        verdict = guard(
            joined,
            [record.text for record in self._records if record.kind == "speech"],
            speech_max_chars=0,
            word_filter=None,
            gate=self._gate,
        )
        if verdict.text is None:
            self._logger.info(f"嘴的台词和最近说过的重复（重合度 {verdict.ratio:.2f}），丢弃: {joined}")
            return []
        return kept
