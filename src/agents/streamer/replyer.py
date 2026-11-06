"""主播表达引擎：按复古提示词格式生成纯文本台词。

system 只放稳定的两块：人设块（名字、直播身份、性格、说话风格、口吻示例）在前、占大头，
提炼过的直播说话规矩在后；整段每轮逐字一致，请求前缀缓存靠它命中。
user 是一条：她更早说过、聊天记录里已经看不到的几句，最近几条聊天记录原文，交接稿与本轮状态材料。
聊天记录只截最近几条（本批弹幕刷屏时也不超过一批的条数），整份输入不随场长增长——
后半场她也是先看到"自己是谁"，不会被几百条历史挤得像换了个人。

模型只负责措辞，不接收工具。情绪、强度及输出元数据由上层 reply 调用组装。
词表净化只处理台词，避免思考正文进入发声管线。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.modules.logging import get_logger

from . import canonical
from .persona_rules import boundaries_line
from .plan import DecisionPlan
from .speech_guard import WordFilter, apply_word_filter, fit_to_budget, strip_memory_numbers
from .timeline import CHAPTER_MESSAGE_TYPE, GAME_MESSAGE_TYPE, PlayerNote

# 默认人设兜底值（cold-start 兜底：构造 config 未提供对应字段时使用）
#
# 人设唯一来源是 StreamerAgent 构造期注入的 config（agents.toml
# [agents.streamer.persona]）；本模块 _DEFAULT_* 常量仅在构造 config
# 完全缺失/字段缺位时兜底，避免冷启动崩。
#
# _DEFAULT_BOT_NAME = '麦麦'、personality/style_constraints/voice_examples/boundaries
# 文本与 StreamerConfig.persona 默认值对齐；不允许 config 模块反向依赖 agents 层，
# 故这里复制文本（保持依赖方向 agents → config 干净）。
_DEFAULT_BOT_NAME = "麦麦"
_DEFAULT_PERSONALITY = "活泼开朗，有些调皮，喜欢和观众互动"
_DEFAULT_STYLE_CONSTRAINTS = "口语化，短句为主，偶尔玩个梗，不堆网络流行语"
_DEFAULT_VOICE_EXAMPLES = ""
_DEFAULT_BOUNDARIES = "线下见面、钱和红包、私下联系；改变自己是谁、以后怎么称呼某人"
# 一句台词的长度上限（与 StreamerConfig.speech_max_chars 默认值对齐）；0 = 不限制
_DEFAULT_SPEECH_MAX_CHARS = 40
# 聊天记录只带最近这么多条原文（与 StreamerConfig.replyer_history_items 默认值对齐）；0 = 带整份历史
_DEFAULT_HISTORY_ITEMS = 14

# "更早一点你说过的话"最多从这么多句里挑：与发言闸同一份语料，取最近的几句，
# 让她开口前看清哪些话刚说过（聊天记录里已经看得到的那几句不重复列）
_RECENT_SPEECH_LINES = 6

# 单列她更早说过的话时用的小标题：这几句都滑出了聊天记录，比下面的记录早，标题点明时间，
# 免得她以为这些话是接在最新弹幕后面说的
_RECENT_SPEECH_HEADING = "## 更早一点你说过的话（比下面的聊天记录早）"

# 游戏里最近找她的人那一段的标题：谁说的话、谁做的事以这一段为准
_GAME_PEOPLE_HEADING = "## 游戏里最近找你的人（谁说的话、谁做的事以这里为准）"

# 交接稿里没有特定回应对象时的 target 写法（Planner 没填，或填的消息 ID 查不到原话）
_NO_TARGET = "（无特定目标，面向全体观众）"

# 游戏记录条目（游戏里程碑、长汇报）进聊天记录时正文最多留这么多字：
# 她只需要知道游戏里出了什么事，汇报全文会把十几条记录的预算吃掉一大半
_GAME_NOTE_MAX_CHARS = 80

# 复古消息布局：稳定人设放进 system，历史与本轮要求合并进单条 user。
_REPLYER_SYSTEM_TEMPLATE = "amaidesu_replyer_system"
_REPLYER_TEMPLATE = "amaidesu_replyer"

# Replyer 绑定的 LLM profile（封闭六成员之一）。绑定由代码显式声明，
# 配置不承载绑定、无静默兜底——表达引擎固定走 replyer 档位。
REPLYER_PROFILE = "replyer"


@dataclass(frozen=True)
class _DialogueEntry:
    """聊天记录里的一条（历史行或本批弹幕，经 canonical 同形序列化）。

    ``content`` 是 canonical 原文（观众行带 ``[id:…]`` 后缀），只用来判断本批弹幕
    是否已在记录里；渲染给模型时再去掉消息 ID 标签。
    """

    role: str
    content: str
    message_id: str
    message_type: str


class _SeenMessages:
    """判断一条本批弹幕是否已经出现在某段聊天记录里。

    有消息 ID 时按 ID 精确匹配；没有 ID 时按 canonical 原文逐条计数匹配——两条同文
    无 ID 的弹幕各占一个名额，不会被合并成一条。
    """

    def __init__(self, entries: Sequence[_DialogueEntry]) -> None:
        self._texts: Counter[Tuple[str, str]] = Counter((entry.role, entry.content) for entry in entries)
        self._ids = {entry.message_id for entry in entries if entry.role == "user" and entry.message_id}

    def claim(self, entry: _DialogueEntry) -> bool:
        """这条弹幕已在记录里就返回 True（无 ID 的同文名额随之用掉一个）。"""
        if entry.message_id and entry.message_id in self._ids:
            return True
        key = (entry.role, entry.content)
        if self._texts[key] > 0:
            self._texts[key] -= 1
            return True
        return False

    def remember(self, entry: _DialogueEntry) -> None:
        """刚补进记录的弹幕记下 ID：同一批里同 ID 的重复投递只补一次。"""
        if entry.message_id:
            self._ids.add(entry.message_id)


class Replyer:
    """消费决策与对话上下文，只生成台词，不决定情绪或调用工具。"""

    def __init__(
        self,
        config: Dict[str, Any],
        llm_service: Any,
        prompt_service: Any,
        word_filter: Optional["WordFilter"] = None,
    ) -> None:
        """初始化 Replyer。

        Args:
            config: 配置字典（兼容 StreamerConfig 的子集字段），读取人设
                    bot_name / personality / style_constraints / voice_examples / boundaries
                    （由 StreamerAgent 构造期从 [agents.streamer.persona] 注入）、
                    speech_max_chars（一句台词的字数上限）与 history_items
                    （聊天记录带最近几条原文，0 = 带整份历史）；
                    LLM profile 绑定不读配置，固定 REPLYER_PROFILE 常量。
            llm_service: LLM 管理器（使用 profile 指定的高质量客户端）。
            prompt_service: 提示词管理器（渲染 amaidesu_replyer_system /
                    amaidesu_replyer 两个模板，分别承载 system 稳定段与本轮变化段）。
            word_filter: 敏感词过滤器（输出端净化入口；None 表示不净化）。
        """
        self._config: Dict[str, Any] = config or {}
        # LLM profile 绑定是模块级显式常量（REPLYER_PROFILE），不读配置、无兜底；
        # 保留为实例属性以兼容日志输出（仅展示用）。
        self.profile: str = REPLYER_PROFILE
        self._bot_name: str = self._config.get("bot_name", _DEFAULT_BOT_NAME)
        self._personality: str = self._config.get("personality", _DEFAULT_PERSONALITY)
        self._speech_max_chars: int = int(self._config.get("speech_max_chars", _DEFAULT_SPEECH_MAX_CHARS))
        self._history_items: int = max(0, int(self._config.get("history_items", _DEFAULT_HISTORY_ITEMS)))
        # 人设块与规矩块里的可选段在构造期一次渲染好：人设整场不变，system 每轮逐字一致
        self._style_section: str = _style_section(self._config.get("style_constraints", _DEFAULT_STYLE_CONSTRAINTS))
        self._voice_examples_section: str = _voice_examples_section(
            self._config.get("voice_examples", _DEFAULT_VOICE_EXAMPLES)
        )
        self._boundaries_rule: str = _boundaries_rule(self._config.get("boundaries", _DEFAULT_BOUNDARIES))

        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._word_filter = word_filter
        self.logger = get_logger("Replyer")

    async def generate(
        self,
        plan: DecisionPlan,
        batch: List[Any],
        history: Optional[List[Any]] = None,
        rundown: Optional[str] = None,
        on_delta: Optional[Any] = None,
        game_chat: Optional[List[str]] = None,
        state_facts: str = "",
        recent_speeches: Optional[Sequence[str]] = None,
        remembered: str = "",
        game_people: Sequence[PlayerNote] = (),
        reply_to: str = "",
    ) -> Optional[Dict[str, Any]]:
        """按一条 system 与一条 user 的复古布局请求纯文本台词。

        ``history`` 传整场对话（时间线投影）：要回应的那条原话按整份历史查，
        聊天记录只截最近 ``history_items`` 条原文。``recent_speeches`` 是她已经
        说过的台词（旧→新，与发言闸同一份语料），最近几句里聊天记录看不到的，
        单列在聊天记录前面（"更早一点你说过的话"）。
        游戏聊天、目标原文与流程单保持可见；不发送 tools 或 JSON 输出约束。
        ``state_facts`` 是状态跃迁事实块（客观事实底稿，规则式提取器产出），
        以"当前状态（客观事实）"分节进 user；空串时占位为"（无）"。
        ``game_people`` 是最近几分钟游戏里玩家找她的条目，``reply_to`` 是这一句回应的玩家名
        （没有特定玩家时为空），台词据此把话和举动归到对的人头上。
        返回台词与请求历史指针，供上层组装发声数据；失败或空台词返回 None。
        """
        # 防御：Planner 已裁决 should_reply=True 才会进入此处；False 直接放弃。
        if not plan.should_reply:
            self.logger.debug("DecisionPlan.should_reply=False，Replyer 跳过生成")
            return None

        system_prompt = self._render_system_prompt()
        turn_input = self._render_turn_input(
            plan,
            batch,
            rundown,
            history=history,
            game_chat=game_chat,
            state_facts=state_facts,
            recent_speeches=recent_speeches,
            remembered=remembered,
            game_people=game_people,
            reply_to=reply_to,
        )
        messages: List[Dict[str, Any]] = [{"role": "user", "content": turn_input}]

        try:
            self.logger.info(f"Replyer 生成回复中 (plan.target={plan.target!r}, client={self.profile})")
            response = await self._llm_service.generate(
                messages,
                system=system_prompt,
                profile=self.profile,
                on_delta=on_delta,
            )
        except Exception as e:
            self.logger.exception(f"Replyer LLM 调用异常: {e}")
            return None

        if not response.ok:
            self.logger.warning(f"Replyer LLM 返回失败: {response.error or 'unknown'}, silent 降级")
            return None

        speech = _plain_text_speech(response.content)
        # 交接稿里的编号已经洗过，她仍写出 档#N、约#N 说明有别的漏口：去掉并记警告，念出来观众听不懂
        speech, numbers = strip_memory_numbers(speech)
        if numbers:
            self.logger.warning(f"Replyer 台词里出现 {numbers} 处记忆编号，已去掉: {speech[:50]!r}")
        if not speech:
            self.logger.warning("Replyer 返回空台词，本轮不发声")
            return None
        request_id = response.request_id or None

        # 超出长度预算：先让模型重说成一句，仍超就截到预算内最后一个完整句子
        if self._speech_max_chars > 0 and len(speech) > self._speech_max_chars:
            speech, request_id = await self._shorten(system_prompt, speech, request_id)

        result = {
            "speech": speech,
            "llm_request_id": request_id,
        }

        # 敏感词净化只影响台词
        result = self._apply_word_filter(result)
        if result is None:
            self.logger.warning("Replyer 输出被 word filter 丢弃（drop_on_match=True）")
            return None

        self.logger.info(f"Replyer 生成回复: speech={result.get('speech', '')[:50]!r}")
        return result

    async def _shorten(self, system_prompt: str, speech: str, request_id: Optional[str]) -> Tuple[str, Optional[str]]:
        """把超出长度预算的台词压成一句：重说一次，仍超或重说失败就按完整句子截短。

        重说只带人设（system 稳定段，前缀缓存可命中）和原台词，不重发整段上下文——
        要的只是"同一个人把这句话说短"，事实都在原台词里。
        """
        budget = self._speech_max_chars
        instruction = (
            f"你刚想说的这段话有 {len(speech)} 字，太长了，像在写稿：\n{speech}\n\n"
            f"像主播随口说话那样重说：只留最想说的那一件事，一句话，不超过 {budget} 字，不分段。"
            "不要加原话里没有的事。只输出台词。"
        )
        shortened = ""
        try:
            response = await self._llm_service.generate(
                [{"role": "user", "content": instruction}], system=system_prompt, profile=self.profile
            )
            if response.ok:
                shortened = _plain_text_speech(response.content)
                request_id = response.request_id or request_id
            else:
                self.logger.warning(f"台词重说失败，改为直接截短: {response.error or 'unknown'}")
        except Exception as exc:
            self.logger.warning(f"台词重说异常，改为直接截短: {exc}", exc=True)
        candidate = shortened or speech
        fitted = fit_to_budget(candidate, budget)
        self.logger.info(
            f"台词超出长度预算（{len(speech)} 字 > {budget}）：重说后 {len(shortened) or '失败'}，最终 {len(fitted)} 字"
        )
        return fitted, request_id

    # ==================== prompt 渲染（人设注入核心） ====================

    def _render_system_prompt(self) -> str:
        """渲染 system 提示词（全程稳定段）：人设块在前，说话规矩在后。

        人设分离承诺：人设字段均读构造期注入的自身人设（StreamerAgent 从
        config.persona 单点构建），可选段在构造期渲染好。"被问到时不否认自己是 AI"
        写死在模板的规矩块里，运营者改人设删不掉。稳定段每轮渲染结果逐字一致——
        请求前缀缓存命中靠它。
        """
        return self._prompt_service.render(
            _REPLYER_SYSTEM_TEMPLATE,
            bot_name=self._bot_name,
            personality=self._personality,
            style_section=self._style_section,
            voice_examples_section=self._voice_examples_section,
            boundaries_rule=self._boundaries_rule,
        )

    def _render_turn_input(
        self,
        plan: DecisionPlan,
        batch: List[Any],
        rundown: Optional[str] = None,
        *,
        history: Optional[List[Any]] = None,
        game_chat: Optional[List[str]] = None,
        state_facts: str = "",
        recent_speeches: Optional[Sequence[str]] = None,
        remembered: str = "",
        game_people: Sequence[PlayerNote] = (),
        reply_to: str = "",
    ) -> str:
        """她更早说过的话、最近几条聊天记录与本轮材料合并成一条 user。

        顺序：先按整份历史 + 本批查出要回应的那条原话（截取不能让较早的目标查不到）
        → 截出最近几条原文、补上本批里还看不到的弹幕 → 去掉记录里已经看得到的台词，
        剩下的单列在聊天记录前面（它们都比记录早）→ 与交接稿、状态材料一起渲染。
        """
        full_history = list(history or [])
        # 流程单上下文：None / 空串时用占位文本，与 Planner 对齐
        rundown_render = rundown if rundown else "（当前无流程单）"
        # 要回应的那条原话在截取之前按整份历史查：被回应的弹幕可能已滑出最近几条
        target_message = canonical.find_target_message(plan.target, batch, full_history)
        target_text = _strip_id_tag(target_message, canonical.normalize_message_id(plan.target or ""))
        entries = self._dialogue_entries(full_history, batch)
        dialogue_lines = [self._render_entry(entry) for entry in entries]
        shown_speeches = [entry.content.strip() for entry in entries if entry.role == "assistant"]
        return self._prompt_service.render(
            _REPLYER_TEMPLATE,
            plan=_render_plan_text(
                plan,
                target_text=target_text,
                reply_to=reply_to,
                # 回应对象这一窗在公屏开过口：说"他刚在游戏公屏跟你说话"；只冲她做了事的说"刚在游戏里找过你"
                reply_to_spoke=any(note.said and note.who == reply_to for note in game_people),
            ),
            # 游戏里最近找她的人：玩家的话与举动单列、不截短，谁说的、谁做的一眼分清
            game_people_section=_game_people_section(game_people),
            dialogue_prompt="\n".join(dialogue_lines) or "（暂无聊天记录）",
            recent_speech_section=_recent_speech_section(recent_speeches, shown_speeches),
            remembered_section=_remembered_section(remembered),
            bot_name=self._bot_name,
            turn_context=(
                "本轮有新观众消息，请结合聊天记录与回复指引接话。"
                if batch
                else "本轮没有新观众消息；有游戏聊天时按游戏情况说，否则这是主动发言。"
            ),
            game_chat="\n".join(game_chat) if game_chat else "（无）",
            rundown=rundown_render,
            state_facts=state_facts.strip() or "（无）",
            # 长度预算先在提示里说清，多数台词一次就落在预算内；出口处还有截短兜底
            length_rule=f"整句不超过 {self._speech_max_chars} 字。" if self._speech_max_chars > 0 else "",
        )

    def _dialogue_entries(self, history: List[Any], batch: List[Any]) -> List[_DialogueEntry]:
        """截出给她看的聊天记录：最近几条原文，再补上本批里还看不到的弹幕。

        history_items > 0：前情提要、前情目录这类章节层条目是摘要不是原文，不带；
        弹幕、她说过的话、游戏记录合计取最近 history_items 条。本批补进来的弹幕
        占用记录名额——记录总行数不超过 max(history_items, 本批条数)：一批弹幕刷屏
        （满批 20 条）时窗口相应变短，先保住触发她开口的这批弹幕，整份输入不超预算。
        history_items = 0 是回退开关：整份历史（含章节层）原样给她看，不限行数。
        """
        batch_entries = [_batch_entry(item) for item in batch]
        if self._history_items <= 0:
            return _merge_batch([], list(history), batch_entries)
        originals = [turn for turn in history if getattr(turn, "message_type", "") != CHAPTER_MESSAGE_TYPE]
        limit = max(self._history_items, len(batch_entries))
        window_size = self._history_items
        # 窗口截短可能让本批里原本看得到的弹幕滑出去、改为补在最前面，补位行数随之变化，
        # 所以按"超出几行就再截几行"收敛；窗口为 0 时只剩本批弹幕，行数必然不超过本批条数
        while True:
            cut = max(0, len(originals) - window_size)
            entries = _merge_batch(originals[:cut], originals[cut:], batch_entries)
            overflow = len(entries) - limit
            if overflow <= 0 or window_size == 0:
                return entries
            window_size = max(0, window_size - overflow)

    def _render_entry(self, entry: _DialogueEntry) -> str:
        """一条记录渲染成一行：去掉消息 ID 标签；前情提要与游戏记录原样成行，游戏记录截短。

        消息 ID 只对 Planner 有用（它按 ID 指定要回应哪条）；要回应的原话已单独给出，
        她这边看 ID 只是噪声。
        """
        content = _strip_id_tag(entry.content, entry.message_id) if entry.role == "user" else entry.content
        if entry.message_type == GAME_MESSAGE_TYPE:
            return _clip_game_note(content)
        if entry.message_type == CHAPTER_MESSAGE_TYPE:
            return content
        if entry.role == "assistant":
            return f"{self._bot_name}(你)说：{content}"
        nickname, separator, body = content.partition(": ")
        return f"{nickname}说：{body}" if separator else content

    # ==================== 敏感词净化（输出端） ====================

    def _apply_word_filter(
        self,
        result: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """敏感词净化（Replyer 表达引擎内部净化；判定与词表收拢在 speech_guard）。

        Args:
            result: 待净化的台词 dict（含 speech 与请求历史指针）

        Returns:
            净化后的 result；``drop_on_match=True`` 且命中时返回 None（丢弃整条）。
            无 word_filter 注入时直接返回原 result（无净化）。
        """
        if result is None:
            return result
        speech = result.get("speech", "")
        if not isinstance(speech, str):
            return result
        cleaned_speech = apply_word_filter(speech, self._word_filter)
        if cleaned_speech is None:
            return None
        # 原 dict 复制以避免污染其他引用
        new_result = dict(result)
        new_result["speech"] = cleaned_speech
        return new_result


# ============================================================================
# 模块级辅助函数
# ============================================================================


#: 正文里的思考段：闭合的整段去掉；只有开头没有结尾的，从开头起全是思考，整段都不能念
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_UNCLOSED = re.compile(r"<think>.*", re.DOTALL)

#: 口吻示例每行开头的列表记号：运营者写成列表时去掉，渲染时统一加
_EXAMPLE_MARKS = "-*•· \t"


def _game_people_section(game_people: Sequence[PlayerNote]) -> str:
    """最近几分钟游戏里玩家找她的条目连同归因规矩：整段前面留一个空行，没有条目时为空串（整段省略）。

    规矩只在有条目时出现，平常的决策窗不多占输入。几句规矩各对一种线上出过的错：交接稿里指她自己的
    "她"被当成刚点过名的女仆，玩家的公屏话随之安到女仆头上；玩家扔来的东西说成天上掉的；玩家说她
    "自家地板打洞"，她反过来劝玩家别打洞。
    """
    lines = [note.line for note in game_people if note.line]
    if not lines:
        return ""
    return (
        f"\n\n{_GAME_PEOPLE_HEADING}\n\n"
        + "\n".join(lines)
        + '\n交接稿里的"她"就是你。转述时说清是谁，不安到女仆、宠物或观众头上；上面写着是谁扔来、送来的，就别说成来路不明；'
        "玩家公屏说的事多半说的是你，别安回他自己头上。"
    )


def _remembered_section(remembered: str) -> str:
    """Planner 点名的那条旧事：整段前后各留一个空行，没有时为空串（整段省略）。"""
    text = (remembered or "").strip()
    return f"\n\n{text}\n" if text else ""


def _plain_text_speech(content: Any) -> str:
    """取纯文本台词，剔除闭合或未闭合的思考段，避免把推理念出来。"""
    if not isinstance(content, str):
        return ""
    text = _THINK_UNCLOSED.sub("", _THINK_BLOCK.sub("", content)).strip()
    return text


def _text(value: Any) -> str:
    """鸭子字段取值收敛：仅接受 str，其余（None/Mock 等）按空处理。"""
    return value if isinstance(value, str) else ""


def _history_entry(turn: Any) -> _DialogueEntry:
    """历史行（时间线条目 / live_chat 行）→ 聊天记录条目。"""
    message = canonical.turn_to_message(turn)
    return _DialogueEntry(
        role=message["role"],
        content=message["content"],
        message_id=_text(getattr(turn, "message_id", None)),
        message_type=_text(getattr(turn, "message_type", None)),
    )


def _batch_entry(item: Any) -> _DialogueEntry:
    """本批弹幕 → 聊天记录条目（与历史行同形，便于判重）。"""
    message = canonical.batch_item_to_message(item)
    return _DialogueEntry(
        role=message["role"],
        content=message["content"],
        message_id=_text(getattr(item, "message_id", None)),
        message_type=_text(getattr(item, "message_type", None)),
    )


def _merge_batch(
    earlier: Sequence[Any], window: Sequence[Any], batch_entries: Sequence[_DialogueEntry]
) -> List[_DialogueEntry]:
    """把本批弹幕合进截出的记录：本轮触发她开口的消息必须看得到。

    已落库的弹幕保留在历史里的原位置，避免挪到主播回复后面。本批弹幕不在截出的
    记录里时分两种：在更早的历史里（滑出了最近几条）就放到记录最前面，保持先后；
    还没落库就追加在末尾。
    """
    shown = [_history_entry(turn) for turn in window]
    visible = _SeenMessages(shown)
    older: Optional[_SeenMessages] = None
    lead: List[_DialogueEntry] = []
    tail: List[_DialogueEntry] = []
    for entry in batch_entries:
        if visible.claim(entry):
            continue
        # 更早的历史可能有几百条，只在确有弹幕不在窗口里时才建索引
        if older is None:
            older = _SeenMessages([_history_entry(turn) for turn in earlier])
        (lead if older.claim(entry) else tail).append(entry)
        visible.remember(entry)
    return [*lead, *shown, *tail]


def _strip_id_tag(content: str, message_id: str) -> str:
    """去掉 canonical 观众行末尾的 `` [id:…]`` 标签；没有 ID 时原样返回。"""
    if not message_id:
        return content
    return content.removesuffix(f" [id:{message_id}]")


def _clip_game_note(content: str) -> str:
    """游戏记录正文截到上限字数：行首的 ``[时刻 标签]`` 保留，正文超长的部分换成省略号。"""
    head, separator, body = content.partition("] ")
    if not (content.startswith("[") and separator):
        head, separator, body = "", "", content
    if len(body) <= _GAME_NOTE_MAX_CHARS:
        return content
    return f"{head}{separator}{body[:_GAME_NOTE_MAX_CHARS]}…"


def _recent_speech_section(recent_speeches: Optional[Sequence[str]], shown_speeches: Sequence[str]) -> str:
    """她最近说过的几句里、聊天记录里已经看不到的那些，单列成"更早一点你说过的话"一段。

    取发言闸同一份语料的最近几句（旧→新）；已经在聊天记录里的那几句不再重复列，
    免得同一句话在输入里出现两遍、像她说了两次。剩下的都滑出了聊天记录、比记录早，
    所以这一段放在聊天记录前面，标题点明时间——排在最新弹幕后面会让她以为
    这些话是刚接着弹幕说的。全都看得到（安静时最常见）或没有语料时整段省略。
    渲染结果前后各带一个空行，夹在开场白与聊天记录之间。
    """
    recent = [line.strip() for line in recent_speeches or () if isinstance(line, str) and line.strip()]
    visible = Counter(shown_speeches)
    missing: List[str] = []
    for line in recent[-_RECENT_SPEECH_LINES:]:
        if visible[line] > 0:
            visible[line] -= 1
            continue
        missing.append(line)
    if not missing:
        return ""
    return f"\n\n{_RECENT_SPEECH_HEADING}\n\n" + "\n".join(f"- {line}" for line in missing) + "\n"


def _style_section(style_constraints: Any) -> str:
    """人设块里的说话风格一段（带前导空行）；人设里没写时整段省略。"""
    style = _text(style_constraints).strip()
    return f"\n\n说话风格：{style}" if style else ""


def _voice_examples_section(voice_examples: Any) -> str:
    """人设块里的口吻示例一段（带前导空行）：先说明只学口吻、不照搬句子，再一行一句列出。

    运营者一行写一句；空行与行首的列表记号去掉。没写示例时整段省略。
    """
    lines = [line.strip().lstrip(_EXAMPLE_MARKS).strip() for line in _text(voice_examples).splitlines()]
    examples = [line for line in lines if line]
    if not examples:
        return ""
    return "\n\n下面几句只学口吻，不照搬句子：\n" + "\n".join(f"- {line}" for line in examples)


def _boundaries_rule(boundaries: Any) -> str:
    """规矩块末尾的边界栏一行"- 不答应的事：…"（带前导换行）；没写边界时整行省略、不留空行。

    措辞与多行折成一行的规则由 persona_rules 统一定义，三条说话通路写法一致。
    """
    line = boundaries_line(_text(boundaries))
    return f"\n- {line}" if line else ""


def _unresolved_target(target: Optional[str]) -> str:
    """要回应的原话查不到时，交接稿里 target 一行的写法。

    Planner 填的是昵称或片段时照旧给出，她据此对号；填的是消息 ID 写法（``[id:…]`` /
    ``id:…``）却查不到原话时（ID 写错，或那条已被折进前情提要）不再给 ID——ID 对她
    只是噪声，按面向全体观众说。没填时同样面向全体观众。
    """
    raw = (target or "").strip()
    if not raw or canonical.normalize_message_id(raw) != raw:
        return _NO_TARGET
    return raw


def _render_plan_text(
    plan: DecisionPlan, *, target_text: str = "", reply_to: str = "", reply_to_spoke: bool = True
) -> str:
    """把 DecisionPlan 渲染为交接稿文本；查到要回应的原话时直接给原话，不再给消息 ID。

    原话找不到时按 :func:`_unresolved_target` 给出 target（昵称或片段照旧，ID 写法不给）。
    没在回弹幕、在回应游戏里的某位玩家时，先写回应对象：写明是哪位玩家——不写的话，她会把这句
    公屏话安到聊天记录里刚点过名的女仆、宠物头上。Planner 另填了 target（如公屏原话片段）时照旧跟在后面。
    """
    unresolved = _unresolved_target(plan.target)
    if target_text:
        target_line = f"target 原文: {target_text}\n"
    elif reply_to:
        how = "他刚在游戏公屏跟你说话" if reply_to_spoke else "他刚在游戏里找过你"
        target_line = f"回应对象: 游戏里的玩家 {reply_to}（{how}）\n"
        if unresolved != _NO_TARGET:
            target_line += f"target: {unresolved}\n"
    else:
        target_line = f"target: {unresolved}\n"
    return (
        f"{target_line}"
        f"topic_summary: {plan.topic_summary or '（无特定话题）'}\n"
        f"reply_guidance: {plan.reply_guidance or '（无额外指引）'}\n"
    )
