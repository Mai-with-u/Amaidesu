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
from .plan import DecisionPlan, assistant_speech_lines
from .speech_guard import (
    VerbatimRepeat,
    WordFilter,
    apply_word_filter,
    find_verbatim_repeat,
    fit_to_budget,
    strip_memory_numbers,
)
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
_DEFAULT_SPEECH_MAX_CHARS = 60
# 杂谈档一轮台词的长度上限（与 StreamerConfig.talk_speech_max_chars 默认值对齐）
_DEFAULT_TALK_SPEECH_MAX_CHARS = 150
# 接某位观众或玩家的话时篇幅多给这么多字：开头要先带上称呼或接住对方的词，不能挤掉正事
_TARGET_EXTRA_CHARS = 25

# 说话方式那一条规矩：边玩边说一轮一事；杂谈像跟朋友唠嗑，围绕一个话头说两三句
_SPEECH_STYLE_RULE = {
    "play": "像真人主播随口说话，不像写稿：一次只说一件事，一句话说完就停。",
    "talk": (
        "像真人主播跟朋友唠嗑，不像写稿：围绕这一个话头自然说两三句，把细节或你觉得好玩的地方讲出来，说完自然停住。"
    ),
}
# 设定档案的注入守则：只跟着档案一起出现，没配档案时不占提示词预算
_PROFILE_USAGE_NOTE = "这些是关于你自己的真实底子：自我介绍、话头聊到或观众问起时自然拿出来，聊别的事时不硬扯。"
# 每轮回复要求里的篇幅节奏
_PACE_RULE = {
    "play": "多数话一两句，有事才多说两句，",
    "talk": "这一轮围绕一个话头说两三个短句，有起头、有细节、有你自己的反应，",
}
# 游戏专属的规矩与要求：没启用游戏时整条省略，纯杂谈场不出现游戏用语
_GAME_RULE_LINE = '- 游戏里干活的就是你自己，用第一人称说，不说"活已经派下去了"，不提"游戏侧"这类幕后系统。\n'
_GAME_HEADING = "\n\n## 游戏里刚有人说的话\n\n"
_GAME_TURN_RULES = (
    "游戏里有人搭话时，按游戏聊天和交接稿说，别当成观众弹幕或冷场。\n"
    "台词里不念精确的游戏读数（血量、物品数、坐标、倒计时），换成处境白话（血见底了、材料差不多齐了）；"
    "观众问起或数字成了剧情（报惨、惊险逃生）才带精确数。\n"
)
# 聊天记录只带最近这么多条原文（与 StreamerConfig.replyer_history_items 默认值对齐）；0 = 带整份历史
_DEFAULT_HISTORY_ITEMS = 14

# "更早一点你说过的话"最多从这么多句里挑：与本窗历史同一份语料，取最近的几句，
# 让她开口前看清哪些话刚说过（聊天记录里已经看得到的那几句不重复列）
_RECENT_SPEECH_LINES = 6
# 这一段的保底行数：游戏聊天与玩家条目把预算挤光时也至少留两句——她刚说过的话是
# 防逐字复读的唯一原料，最忙的窗口（玩家连续在公屏说话、游戏事件刷屏）恰恰是
# 复读风险最高的窗口，那一段不能被挤没了
_RECENT_SPEECH_FLOOR = 2

# 单列她更早说过的话时用的小标题：这几句都滑出了聊天记录，比下面的记录早，标题点明时间，
# 免得她以为这些话是接在最新弹幕后面说的
_RECENT_SPEECH_HEADING = "## 更早一点你说过的话（比下面的聊天记录早）"

# 游戏里最近找她的人那一段的标题：谁说的话、谁做的事以这一段为准
_GAME_PEOPLE_HEADING = "## 游戏里最近找你的人（谁说的、谁做的以此为准）"
# 玩家条目整段至多几条（最近的）、较早条目的正文至多几个字：满批时整份输入只剩个位数 token 的余量。
# 本窗刚在公屏说的话（游戏聊天里也有）从游戏聊天那一段拿掉；其中她要接的那一句（回应对象说的，
# 没有回应对象时是最新的一句）给得长一些，同窗别的玩家说的和较早条目一样截短——几位玩家同窗各说
# 一句长话时，全文照给会让满批超预算
_PEOPLE_LINES_MAX = 3
_PEOPLE_NOTE_MAX_CHARS = 18
_PEOPLE_FRESH_MAX_CHARS = 40
# 游戏聊天一段：有玩家条目时，没进条目的公屏话（条目只留最近 3 条，多出来的、认不出是谁的）只报句数——
# 满批加三条玩家条目时整份输入贴近上限；没有玩家条目时留最近几句、每句截短，并和"更早一点你说过
# 的话"合计至多 :data:`_RECENT_SPEECH_LINES` 行（游戏聊天占几行，她更早说过的话就少几行，
# 她的原话另有 :data:`_RECENT_SPEECH_FLOOR` 行保底，由输入上限里的固定占位承担）
_GAME_CHAT_LINES_MAX = 3
_GAME_CHAT_LINE_MAX_CHARS = 30
# 她已经回应过的条目：正文截得更短、行尾标"已接过"，她知道这件事接过了，换话题时不再谢一遍
_PEOPLE_ANSWERED_MAX_CHARS = 10
_ANSWERED_MARK = "（已接过）"
# 有玩家条目时，玩家条目和"更早一点你说过的话"合计至多几行：玩家条目优先（同一位玩家送礼、跳、
# 开口三件事要一起在），她更早说过的话用剩下的行数，但保底留 _RECENT_SPEECH_FLOOR 行；
# 没有玩家条目时那一段照常（游戏聊天占几行让几行，同样不破保底）
_PEOPLE_AND_SPEECH_LINES = 3

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
        self._talk_speech_max_chars: int = int(
            self._config.get("talk_speech_max_chars", _DEFAULT_TALK_SPEECH_MAX_CHARS)
        )
        # 结构化人物设定档案（本地文件渲染出的整段）：整场静态，拼在"你是谁"末尾吃前缀缓存
        profile_text = str(self._config.get("persona_profile", "") or "").strip()
        self._persona_profile_section: str = f"\n\n{profile_text}\n{_PROFILE_USAGE_NOTE}" if profile_text else ""
        # 有没有启用游戏：支持布尔值或无参查询回调（运行期随工具表装配态动态生效）
        raw_has_game = self._config.get("has_game", True)
        self._has_game_source: Any = raw_has_game if callable(raw_has_game) else bool(raw_has_game)
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
        speech_mode: str = "play",
    ) -> Optional[Dict[str, Any]]:
        """按一条 system 与一条 user 的复古布局请求纯文本台词。

        ``speech_mode`` 是本轮说话档位：``play`` 边玩边说（一轮一事，上限 ``speech_max_chars``），
        ``talk`` 杂谈唠嗑（两三句，上限 ``talk_speech_max_chars``）；接某位观众或玩家的话时
        两档上限都多给一点，开头的称呼或接话钩子不能被删减挤掉。

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

        mode = "talk" if speech_mode == "talk" else "play"
        has_target = bool(plan.target or reply_to)
        budget = self._budget_for(mode, has_target=has_target)
        system_prompt = self._render_system_prompt(mode)
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
            mode=mode,
            budget=budget,
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
        if budget > 0 and len(speech) > budget:
            speech, request_id = await self._shorten(
                system_prompt, speech, request_id, budget=budget, keep_hook=has_target
            )

        # 近逐字复读机械兜底：初稿把更早某句话几乎原样又说了一遍时，打回让表达层换个说法重说一次，
        # 不直接吞掉这轮发言；重说仍撞车才退回给决策层换话题
        earlier_lines = list(recent_speeches) if recent_speeches is not None else assistant_speech_lines(history)
        repeat = find_verbatim_repeat(speech, earlier_lines)
        if repeat is not None:
            rephrased, request_id = await self._rephrase_verbatim(
                system_prompt=system_prompt,
                turn_input=turn_input,
                draft=speech,
                repeat=repeat,
                earlier_lines=earlier_lines,
                request_id=request_id,
                budget=budget,
            )
            if not rephrased:
                return None
            speech = rephrased

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

    async def _shorten(
        self,
        system_prompt: str,
        speech: str,
        request_id: Optional[str],
        *,
        budget: int,
        keep_hook: bool = False,
    ) -> Tuple[str, Optional[str]]:
        """把超出长度预算的台词说短些：重说一次，仍超或重说失败就按完整句子截短。

        重说只带人设（system 稳定段，前缀缓存可命中）和原台词，不重发整段上下文——
        要的只是"同一个人把这句话说短"，事实都在原台词里。接观众或玩家的话时（``keep_hook``）
        明确要求保留开头的称呼或接话钩子、删后面的枝节：钩子被削掉，直播间就听不出她在接谁。
        """
        hook_note = "开头那个称呼或接住对方的词要留着，删后面的枝节；" if keep_hook else ""
        instruction = (
            f"你刚想说的这段话有 {len(speech)} 字，太长了，像在写稿：\n{speech}\n\n"
            f"说短一点，像口头说话那样，不超过 {budget} 字，不分段。{hook_note}"
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

    async def _rephrase_verbatim(
        self,
        *,
        system_prompt: str,
        turn_input: str,
        draft: str,
        repeat: VerbatimRepeat,
        earlier_lines: Sequence[str],
        request_id: Optional[str],
        budget: int,
    ) -> Tuple[str, Optional[str]]:
        """初稿命中近逐字复读时，带着本轮输入与撞车初稿让表达模型换个说法重说一次。"""
        self.logger.info(f"Replyer 初稿命中近逐字复读（重合度 {repeat.ratio:.2f}），打回重说: {repeat.sentence!r}")
        budget_note = f"，不超过 {budget} 字" if budget > 0 else ""
        retry_prompt = (
            f"刚才这句「{repeat.sentence}」你前面已经几乎一字不差地说过了（前面说的是「{repeat.earlier}」）。"
            f"顺着上面的本次回复要求，换一个不一样的切入点和措辞重新说{budget_note}，不要加交接稿里没有的事。"
            "只输出台词。"
        )
        retry_messages = [
            {"role": "user", "content": turn_input},
            {"role": "assistant", "content": draft},
            {"role": "user", "content": retry_prompt},
        ]
        try:
            response = await self._llm_service.generate(retry_messages, system=system_prompt, profile=self.profile)
        except Exception as exc:
            self.logger.warning(f"Replyer 近逐字重说异常，放弃本句: {exc}", exc=True)
            return "", request_id
        if not response.ok:
            self.logger.warning(f"Replyer 近逐字重说失败，放弃本句: {response.error or 'unknown'}")
            return "", request_id
        request_id = response.request_id or request_id
        rephrased, _ = strip_memory_numbers(_plain_text_speech(response.content))
        if not rephrased:
            self.logger.warning("Replyer 近逐字重说返回空台词，放弃本句")
            return "", request_id
        if budget > 0 and len(rephrased) > budget:
            rephrased = fit_to_budget(rephrased, budget)
        still_repeat = find_verbatim_repeat(rephrased, earlier_lines)
        if still_repeat is not None:
            self.logger.info(
                f"Replyer 重说后仍近逐字复读（重合度 {still_repeat.ratio:.2f}），退回决策层: {still_repeat.sentence!r}"
            )
            return "", request_id
        return rephrased, request_id

    # ==================== prompt 渲染（人设注入核心） ====================

    def _budget_for(self, mode: str, *, has_target: bool) -> int:
        """本轮台词上限：按档位取基础上限，接某位观众或玩家的话时多给一点；0 = 不限制。"""
        base = self._talk_speech_max_chars if mode == "talk" else self._speech_max_chars
        if base <= 0:
            return 0
        return base + _TARGET_EXTRA_CHARS if has_target else base

    def _game_enabled(self) -> bool:
        """当前是否挂有游戏（回调异常时按有游戏保底，不误删游戏规矩）。"""
        if callable(self._has_game_source):
            try:
                return bool(self._has_game_source())
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"读取游戏启用状态失败（按有游戏处理）: {exc}", exc=True)
                return True
        return bool(self._has_game_source)

    def _render_system_prompt(self, mode: str = "play") -> str:
        """渲染 system 提示词（全程稳定段）：人设块在前，说话规矩在后。

        人设分离承诺：人设字段均读构造期注入的自身人设（StreamerAgent 从
        config.persona 单点构建），可选段在构造期渲染好。"被问到时不否认自己是 AI"
        写死在模板的规矩块里，运营者改人设删不掉。稳定段每轮渲染结果逐字一致——
        请求前缀缓存命中靠它。
        """
        # 说话档位只换规矩里的一条，人设块整段不变，同一档位内仍逐字一致
        return self._prompt_service.render(
            _REPLYER_SYSTEM_TEMPLATE,
            bot_name=self._bot_name,
            personality=self._personality,
            style_section=self._style_section,
            voice_examples_section=self._voice_examples_section,
            boundaries_rule=self._boundaries_rule,
            persona_profile_section=self._persona_profile_section,
            speech_style_rule=_SPEECH_STYLE_RULE.get(mode, _SPEECH_STYLE_RULE["play"]),
            game_rule_line=_GAME_RULE_LINE if self._game_enabled() else "",
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
        mode: str = "play",
        budget: Optional[int] = None,
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
        # 玩家条目截短封顶，本窗在公屏说的那句从游戏聊天一段拿掉（条目里已有、还带着是谁说的）。
        # 游戏聊天整段都挪进了条目时写"见下一段"：写"无"会和下一段里刚在公屏说的话对不上
        chat_before = list(game_chat or [])
        # 交接稿的 target 不是弹幕、而是指着一句游戏聊天（那句的原文片段或说话人）：那一句的原话一定给到，
        # 哪怕说话人认不出、或是挤不进条目的第 4 位玩家；回应对象（to_player）的条目同样一定在
        unresolved = _unresolved_target(plan.target)
        pinned_chat = "" if target_text or unresolved == _NO_TARGET else _chat_line_for(unresolved, chat_before)
        people_lines, game_chat = _people_lines(game_people, chat_before, reply_to=reply_to, pinned_chat=pinned_chat)
        game_chat_text = _game_chat_text(
            game_chat,
            has_people=bool(people_lines),
            had_chat=bool(chat_before),
            pinned=pinned_chat if pinned_chat in game_chat else "",
        )
        # 没有玩家条目时游戏聊天列出几句，她更早说过的话就让出几行：满批时整份输入不超
        chat_rows = 0 if people_lines else min(len(game_chat), _GAME_CHAT_LINES_MAX)
        # 玩家条目一段占的行数：条目本身，加上有已接过的条目时多出的那句规矩
        people_rows = len(people_lines) + (1 if _has_answered(people_lines) else 0)
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
            # 游戏里最近找她的人：玩家的话与举动单列，谁说的、谁做的一眼分清（较早的截短、整段封顶）
            game_people_section=_game_people_section(people_lines) if self._game_enabled() else "",
            dialogue_prompt="\n".join(dialogue_lines) or "（暂无聊天记录）",
            # 有玩家条目时，她更早说过的话只用玩家条目一段剩下的行数：玩家条目从这一段让出预算，
            # 但她的原话保底留两句（防逐字复读的原料，最忙时也不能归零）；满批时整份输入略超的
            # 余量由这一段的两行短句兜住
            recent_speech_section=_recent_speech_section(
                recent_speeches,
                shown_speeches,
                max_lines=max(
                    _RECENT_SPEECH_FLOOR,
                    (
                        max(0, _PEOPLE_AND_SPEECH_LINES - people_rows)
                        if people_lines
                        else _RECENT_SPEECH_LINES - chat_rows
                    ),
                ),
            ),
            remembered_section=_remembered_section(remembered),
            bot_name=self._bot_name,
            turn_context=(
                "本轮有新观众消息，请结合聊天记录与回复指引接话。"
                if batch
                else (
                    "本轮没有新观众消息；有游戏聊天时按游戏情况说，否则这是主动发言。"
                    if self._game_enabled()
                    else "本轮没有新观众消息，这是你的主动发言，围绕当前话头或流程单自然展开。"
                )
            ),
            # 没启用游戏：游戏聊天一节连同标题整段省略，纯杂谈场不出现游戏用语
            game_chat=game_chat_text if self._game_enabled() else "",
            game_heading=_GAME_HEADING if self._game_enabled() else "",
            game_turn_rules=_GAME_TURN_RULES if self._game_enabled() else "",
            pace_rule=_PACE_RULE.get(mode, _PACE_RULE["play"]),
            rundown=rundown_render,
            state_facts=state_facts.strip() or "（无）",
            # 长度预算先在提示里说清，多数台词一次就落在预算内；出口处还有截短兜底
            length_rule=_length_rule(self._speech_max_chars if budget is None else budget, mode),
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


def _length_rule(budget: int, mode: str) -> str:
    """本轮回复要求里的字数要求：杂谈档说两三句，边玩边说一两句；预算 0 = 不写。"""
    if budget <= 0:
        return ""
    if mode == "talk":
        return f"不超过 {budget} 字，两三句。"
    return f"不超过 {budget} 字，可以是一两句。"


def _note_body(line: str) -> str:
    """条目行去掉行首 ``[时刻 游戏 公屏]`` 之后的部分（"玩家 X：原话"），和游戏聊天行比对用。"""
    head, separator, body = line.partition("] ")
    return body if line.startswith("[") and separator else line


def _people_lines(
    game_people: Sequence[PlayerNote],
    game_chat: Sequence[str],
    *,
    reply_to: str = "",
    pinned_chat: str = "",
) -> Tuple[List[str], List[str]]:
    """玩家条目渲染成行，并把已经在条目里的游戏聊天行拿掉：返回（条目行, 剩下的游戏聊天行）。

    只留最近 :data:`_PEOPLE_LINES_MAX` 条，回应对象的条目被挤出去时换回来（见 :func:`_pick_notes`）。
    本窗刚在公屏说的话里，她要接的那一句正文至多 :data:`_PEOPLE_FRESH_MAX_CHARS` 字，其余条目截到
    :data:`_PEOPLE_NOTE_MAX_CHARS` 字。要接的那一句：交接稿 target 指着的那句（``pinned_chat``）在条目里就是它，
    不在条目里（游戏聊天一段单列）时条目都截短；没有时是回应对象说的，再没有是最新的一句。
    没进条目的游戏聊天怎么写见 :func:`_game_chat_text`。满批时整份输入只剩个位数 token 的余量，
    几位玩家同窗各说一句长话原样列出会超。同一句不在两段里各出现一次。
    """
    chat_bodies = {_note_body(line) for line in game_chat}
    notes = _pick_notes([note for note in game_people if note.line], reply_to)
    fresh = [
        not note.answered and note.said and any(_note_body(note.line).startswith(chat) for chat in chat_bodies)
        for note in notes
    ]
    newest_fresh = list(reversed(range(len(notes))))
    if pinned_chat:
        pinned_body = _note_body(pinned_chat)
        focus = next((i for i in newest_fresh if fresh[i] and _note_body(notes[i].line).startswith(pinned_body)), None)
    else:
        # 回应对象本窗说的最近一句；没有回应对象（或他本窗没开口）时取最新的一句本窗公屏话
        focus = next((i for i in newest_fresh if fresh[i] and notes[i].who == reply_to), None)
        if focus is None:
            focus = next((i for i in newest_fresh if fresh[i]), None)
    lines: List[str] = []
    shown_chat: set[str] = set()
    for index, note in enumerate(notes):
        body = _note_body(note.line)
        if fresh[index]:
            # 本窗公屏话已经在条目里（带着是谁说的），游戏聊天一段不再重复
            shown_chat.update(chat for chat in chat_bodies if body.startswith(chat))
        head = note.line[: len(note.line) - len(body)]
        # 公屏话截"玩家 X："之后的原话，动作条目截"玩家 X "之后的那件事：名字再长也不被截掉
        name, colon, said = body.partition("：")
        actor = f"玩家 {note.who} "
        if colon:
            prefix, text = f"{name}{colon}", said
        elif note.who and body.startswith(actor):
            prefix, text = actor, body[len(actor) :]
        else:
            prefix, text = "", body
        # 已接过的条目只留个大概（她回过了），行尾标出来；她要接的那一句给得长一些
        if note.answered:
            limit = _PEOPLE_ANSWERED_MAX_CHARS
        elif index == focus:
            limit = _PEOPLE_FRESH_MAX_CHARS
        else:
            limit = _PEOPLE_NOTE_MAX_CHARS
        clipped = text if len(text) <= limit else f"{text[:limit]}…"
        lines.append(f"{head}{prefix}{clipped}{_ANSWERED_MARK if note.answered else ''}")
    remaining = [line for line in game_chat if _note_body(line) not in shown_chat]
    return lines, remaining


def _pick_notes(notes: Sequence[PlayerNote], reply_to: str) -> List[PlayerNote]:
    """玩家条目留最近 :data:`_PEOPLE_LINES_MAX` 条；回应对象的条目都被挤出去时，换掉最早的一条、留下他最新的
    那条（仍按先后排）：交接稿要她回他，表达侧却看不到他说了什么、做了什么，只能瞎接。"""
    picked = list(notes[-_PEOPLE_LINES_MAX:])
    if not reply_to or any(note.who == reply_to for note in picked):
        return picked
    his = next((note for note in reversed(notes[:-_PEOPLE_LINES_MAX]) if note.who == reply_to), None)
    return picked if his is None else [his, *picked[1:]]


def _chat_line_for(target: str, game_chat: Sequence[str]) -> str:
    """交接稿 target 指着的那句游戏聊天：target 是那句的原文片段或说话人名字，取含它的最新一句；对不上为空串。"""
    text = target.strip()
    if len(text) < 2:
        return ""
    return next((line for line in reversed(game_chat) if text in line), "")


def _game_chat_text(remaining: Sequence[str], *, has_people: bool, had_chat: bool, pinned: str = "") -> str:
    """游戏聊天一段的正文：有玩家条目时只报没列出的句数，没有时留最近几句、每句截短。

    交接稿 target 指着的那句（``pinned``，没进条目的）一定列出：有条目时单列在句数前面、给到
    :data:`_PEOPLE_FRESH_MAX_CHARS` 字；没有条目时它比最近几句早就换掉其中最早的一句。
    整段挪进了玩家条目时写"见下一段"：写"无"会和下一段里刚在公屏说的话对不上。
    """
    if has_people:
        rest = [line for line in remaining if line != pinned]
        parts = [_clip_chat_line(pinned, _PEOPLE_FRESH_MAX_CHARS)] if pinned else []
        if rest:
            parts.append(f"（另有 {len(rest)} 句没列出；找你的人见下一段）")
        if parts:
            return "\n".join(parts)
        return "（见下一段）" if had_chat else "（无）"
    if not remaining:
        return "（无）"
    shown = list(remaining[-_GAME_CHAT_LINES_MAX:])
    if pinned and pinned not in shown:
        shown = [pinned, *shown[1:]]
    return "\n".join(_clip_chat_line(line) for line in shown)


def _clip_chat_line(line: str, limit: int = _GAME_CHAT_LINE_MAX_CHARS) -> str:
    """一句游戏聊天截短：说话人（冒号之前）留着，原话至多 ``limit`` 字。"""
    head, colon, said = line.partition("：")
    if not colon:
        return line if len(line) <= limit else f"{line[:limit]}…"
    return f"{head}{colon}{said if len(said) <= limit else said[:limit] + '…'}"


def _game_people_section(lines: Sequence[str]) -> str:
    """最近几分钟游戏里玩家的公屏与动作条目连同归因规矩：整段前面留一个空行，没有条目时为空串（整段省略）。

    规矩只在有条目时出现，平常的决策窗不多占输入。几句规矩各对一种线上出过的错：交接稿里指她自己的
    "她"被当成刚点过名的女仆，玩家的公屏话随之安到女仆头上；玩家扔来的东西说成天上掉的；全服公屏里远处
    玩家在别处探险的环境感叹被她硬套到自己脚下工位。有她已回应过的条目时多一句，防止换话题时又谢一遍。
    """
    if not lines:
        return ""
    # 有游戏玩家条目时注入归因边界：按交接稿核实的归属与在场关系说，禁止默认把全服公屏里的异地感叹认领到自己脚下
    rules = (
        '交接稿的"她"就是你；转述说清是谁，别安到女仆、宠物或观众头上；写明谁送的别说成来路不明；'
        "公屏全服可见，谁那边的处境按交接稿说，没说在你身边就别往自己脚下套。"
    )
    # 有已接过的条目时才多一句：没有时这一句白占输入
    if _has_answered(lines):
        rules += "标着已接过的你回过了，交接稿没提就别再说一遍。"
    return f"\n\n{_GAME_PEOPLE_HEADING}\n\n" + "\n".join(lines) + f"\n{rules}"


def _has_answered(lines: Sequence[str]) -> bool:
    """条目行里有没有她已回应过的（行尾标着已接过）。"""
    return any(line.endswith(_ANSWERED_MARK) for line in lines)


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


def _recent_speech_section(
    recent_speeches: Optional[Sequence[str]],
    shown_speeches: Sequence[str],
    *,
    max_lines: int = _RECENT_SPEECH_LINES,
) -> str:
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
    for line in recent[-max_lines:] if max_lines > 0 else []:
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
