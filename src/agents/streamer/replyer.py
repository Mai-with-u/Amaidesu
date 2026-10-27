"""主播表达引擎：按复古提示词格式生成纯文本台词。

人设与注意事项放进 system；聊天历史、当前消息与回复要求合并进一条 user。
模型只负责措辞，不接收工具。情绪、强度及输出元数据由上层 reply 调用组装。
词表净化只处理台词，避免思考正文进入发声管线。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.modules.logging import get_logger

from . import canonical
from .plan import DecisionPlan
from .speech_guard import WordFilter, apply_word_filter, fit_to_budget
from .timeline import CHAPTER_MESSAGE_TYPE, GAME_MESSAGE_TYPE

# 默认人设兜底值（cold-start 兜底：构造 config 未提供对应字段时使用）
#
# 人设唯一来源是 StreamerAgent 构造期注入的 config（agents.toml
# [agents.streamer.persona]）；本模块 _DEFAULT_* 常量仅在构造 config
# 完全缺失/字段缺位时兜底，避免冷启动崩。
#
# _DEFAULT_BOT_NAME = '麦麦'、personality/style_constraints 文本与
# StreamerConfig.persona 默认值对齐；不允许 config 模块反向依赖 agents 层，
# 故这里复制文本（保持依赖方向 agents → config 干净）。
_DEFAULT_BOT_NAME = "麦麦"
_DEFAULT_PERSONALITY = "活泼开朗，有些调皮，喜欢和观众互动"
_DEFAULT_STYLE_CONSTRAINTS = "口语化，短句为主，偶尔玩个梗，不堆网络流行语"
_DEFAULT_AUDIENCE_SALUTATION = "大家"
# 一句台词的长度上限（与 StreamerConfig.speech_max_chars 默认值对齐）；0 = 不限制
_DEFAULT_SPEECH_MAX_CHARS = 40

# 复古消息布局：稳定人设放进 system，历史与本轮要求合并进单条 user。
_REPLYER_SYSTEM_TEMPLATE = "amaidesu_replyer_system"
_REPLYER_TEMPLATE = "amaidesu_replyer"

# Replyer 绑定的 LLM profile（封闭六成员之一）。绑定由代码显式声明，
# 配置不承载绑定、无静默兜底——表达引擎固定走 replyer 档位。
REPLYER_PROFILE = "replyer"


class Replyer:
    """消费决策与对话上下文，只生成台词，不决定情绪或调用工具。"""

    def __init__(
        self,
        config: Dict[str, Any],
        llm_service: Any,
        prompt_service: Any,
        tool_registry: Optional[Any] = None,
        word_filter: Optional["WordFilter"] = None,
        notebook_text_provider: Optional[Callable[[], str]] = None,
    ) -> None:
        """初始化 Replyer。

        Args:
            config: 配置字典（兼容 StreamerConfig 的子集字段），
                    读取 bot_name / personality / style_constraints /
                    audience_salutation（人设四件套由 StreamerAgent 构造期注入；
                    LLM profile 绑定不读配置，固定 REPLYER_PROFILE 常量）。
            llm_service: LLM 管理器（使用 profile 指定的高质量客户端）。
            prompt_service: 提示词管理器（渲染 amaidesu_replyer_system /
                    amaidesu_replyer 两个模板，分别承载 system 稳定段与本轮变化段）。
            tool_registry: 工具注册表（仅作占位注入，表达引擎自身不消费工具列表）。
            word_filter: 敏感词过滤器（输出端净化入口；None 表示不净化）。
            notebook_text_provider: 本场备忘录全文查询（None 或空串 = 无备忘录段）。
        """
        self._config: Dict[str, Any] = config or {}
        # LLM profile 绑定是模块级显式常量（REPLYER_PROFILE），不读配置、无兜底；
        # 保留为实例属性以兼容日志输出（仅展示用）。
        self.profile: str = REPLYER_PROFILE
        self._bot_name: str = self._config.get("bot_name", _DEFAULT_BOT_NAME)
        self._personality: str = self._config.get("personality", _DEFAULT_PERSONALITY)
        self._style_constraints: str = self._config.get("style_constraints", _DEFAULT_STYLE_CONSTRAINTS)
        self._audience_salutation: str = self._config.get("audience_salutation", _DEFAULT_AUDIENCE_SALUTATION)
        self._speech_max_chars: int = int(self._config.get("speech_max_chars", _DEFAULT_SPEECH_MAX_CHARS))

        self._llm_service = llm_service
        self._prompt_service = prompt_service
        self._tool_registry = tool_registry
        self._word_filter = word_filter
        self._notebook_text_provider = notebook_text_provider
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
    ) -> Optional[Dict[str, Any]]:
        """按一条 system 与一条 user 的复古布局请求纯文本台词。

        历史只包含实际观众消息和主播发言，压缩后放入 user 的聊天记录块。
        游戏聊天、目标原文与流程单保持可见；不发送 tools 或 JSON 输出约束。
        ``state_facts`` 是状态跃迁事实块（客观事实底稿，规则式提取器产出），
        以"当前状态（客观事实）"分节进 user；空串时占位为"（无）"。
        返回台词与请求历史指针，供上层组装发声数据；失败或空台词返回 None。
        """
        # 防御：Planner 已裁决 should_reply=True 才会进入此处；False 直接放弃。
        if not plan.should_reply:
            self.logger.debug("DecisionPlan.should_reply=False，Replyer 跳过生成")
            return None

        system_prompt = self._render_system_prompt()
        turn_input = self._render_turn_input(
            plan, batch, rundown, history=history, game_chat=game_chat, state_facts=state_facts
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

        if not getattr(response, "success", False):
            self.logger.warning(f"Replyer LLM 返回失败: {getattr(response, 'error', 'unknown')}, silent 降级")
            return None

        speech = _plain_text_speech(getattr(response, "content", None))
        if not speech:
            self.logger.warning("Replyer 返回空台词，本轮不发声")
            return None
        request_id = getattr(response, "request_id", None) or None

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
            if getattr(response, "success", False):
                shortened = _plain_text_speech(getattr(response, "content", None))
                request_id = getattr(response, "request_id", None) or request_id
            else:
                self.logger.warning(f"台词重说失败，改为直接截短: {getattr(response, 'error', 'unknown')}")
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
        """渲染 system 提示词（全程稳定段）。

        人设分离承诺：$personality / $bot_name /
        $audience_salutation 人设字段均读构造期注入的自身人设（StreamerAgent
        从 config.persona 单点构建）。稳定段每轮渲染结果逐字一致——请求前缀
        缓存命中靠它。
        """
        return self._prompt_service.render(
            _REPLYER_SYSTEM_TEMPLATE,
            bot_name=self._bot_name,
            personality=self._personality,
            audience_salutation=self._audience_salutation,
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
    ) -> str:
        """历史与本轮信息合并成一条 user，表达风格放在输出指令之前。"""
        # 流程单上下文：None / 空串时用占位文本，与 Planner 对齐
        rundown_render = rundown if rundown else "（当前无流程单）"
        target_text = canonical.find_target_message(plan.target, batch, list(history or []))
        # 已落库的弹幕保留在历史中的原位置，避免挪到主播回复后面；历史已由时间线控制在预算内。
        # 前情提要与游戏记录带标记：渲染时原样输出，不按"昵称: 内容"拆成某人说的话
        history_messages: List[Dict[str, Any]] = [
            {
                **canonical.turn_to_message(msg),
                "verbatim": getattr(msg, "message_type", "") in (CHAPTER_MESSAGE_TYPE, GAME_MESSAGE_TYPE),
            }
            for msg in list(history or [])
        ]
        # 批消息可能尚未落库或已被历史预算裁掉，只追加历史中缺失的消息。
        # 按出现次数匹配，无 ID 的同文消息也不会被全部合并成一条。
        seen = Counter((message["role"], message["content"]) for message in history_messages)
        seen_ids = {
            match.group(1)
            for message in history_messages
            if message["role"] == "user" and (match := re.search(r" \[id:(.+)\]$", message["content"]))
        }
        for item in batch:
            message = canonical.batch_item_to_message(item)
            message_id = getattr(item, "message_id", "")
            key = (message["role"], message["content"])
            if message_id and message_id in seen_ids:
                continue
            if seen[key] > 0:
                seen[key] -= 1
                continue
            history_messages.append(message)
            if message_id:
                seen_ids.add(message_id)
        dialogue_lines = []
        for message in history_messages:
            content = message["content"]
            if message.get("verbatim"):
                dialogue_lines.append(content)
            elif message["role"] == "assistant":
                dialogue_lines.append(f"{self._bot_name}(你)说：{content}")
            else:
                nickname, separator, body = content.partition(": ")
                dialogue_lines.append(f"{nickname}说：{body}" if separator else content)
        # 备忘录段：本场工作笔记全文（空 = 省略段），读失败只省段不挡表达
        notebook_section = ""
        if self._notebook_text_provider is not None:
            try:
                notebook_text = self._notebook_text_provider() or ""
                if notebook_text:
                    notebook_section = f"## 备忘录\n\n{notebook_text}"
            except Exception as exc:
                self.logger.warning(f"读取备忘录失败，本轮省略备忘录段: {exc}", exc=True)
        return self._prompt_service.render(
            _REPLYER_TEMPLATE,
            plan=_render_plan_text(plan, target_text=target_text),
            dialogue_prompt="\n".join(dialogue_lines) or "（暂无聊天记录）",
            bot_name=self._bot_name,
            style_constraints=self._style_constraints,
            turn_context=(
                "本轮有新观众消息，请结合聊天记录与回复指引接话。"
                if batch
                else "本轮没有新观众消息；有游戏聊天时按游戏情况说，否则这是主动发言。"
            ),
            game_chat="\n".join(game_chat) if game_chat else "（无）",
            rundown=rundown_render,
            state_facts=state_facts.strip() or "（无）",
            notebook_section=notebook_section,
            # 长度预算先在提示里说清，多数台词一次就落在预算内；出口处还有截短兜底
            length_rule=f"整句不超过 {self._speech_max_chars} 字。" if self._speech_max_chars > 0 else "",
        )

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


def _plain_text_speech(content: Any) -> str:
    """取纯文本台词，剔除闭合或未闭合的思考段，避免把推理念出来。"""
    if not isinstance(content, str):
        return ""
    text = _THINK_UNCLOSED.sub("", _THINK_BLOCK.sub("", content)).strip()
    return text


def _render_plan_text(plan: DecisionPlan, *, target_text: str = "") -> str:
    """把 DecisionPlan 渲染为供 prompt 使用的可读文本；找到 target 原话时紧跟在 target 后面。"""
    target = plan.target or "（无特定目标，面向全体观众）"
    target_line = f"target 原文: {target_text}\n" if target_text else ""
    return (
        f"target: {target}\n"
        f"{target_line}"
        f"topic_summary: {plan.topic_summary or '（无特定话题）'}\n"
        f"reply_guidance: {plan.reply_guidance or '（无额外指引）'}\n"
    )


# WordFilter / fit_to_budget 现居 speech_guard（台词出口守卫模块）；此处
# 再导出维持既有 import 路径（历史调用方与测试按 replyer 引用）。
__all__ = ["Replyer", "WordFilter", "fit_to_budget"]
