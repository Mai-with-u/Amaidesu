"""模拟器 LLM 调用包装器。

封装模拟直播间收集器所需的全部 LLM 调用：
- 路人 / 常驻观众 / SuperChat / 暖场等 4 类消息生成
- 常驻人设批量生成（generate_personas）
- 并发控制 (``asyncio.Semaphore``)
- 响应清洗（剥 ``<system>`` / ``think`` 标签、引号、空白、长度截断）
- Token 用量累计与预算阈值
"""

# pyright: reportDeprecated=false

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Dict, Optional, Tuple

from src.modules.llm.manager import LLMManager, LLMResponse
from src.modules.logging import get_logger
from src.modules.prompts import get_prompt_manager
from src.modules.prompts.manager import PromptManager

from .config_schema import SimulatorConfigSchema
from .types import GeneratedMessage, Persona, PersonaRole, StreamerContextSnapshot


def _parse_persona_json(text: str) -> list[dict]:
    """从 LLM 输出中解析人设 JSON 数组（容忍 markdown 代码块包裹与前后杂讯）。"""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


class SimulatorLLMWrapper:
    """模拟器 LLM 调用包装器：处理 prompt 渲染、调用、清洗、token 统计。

    职责范围：
    - 渲染 ``simulator/*`` 系列的 prompt 模板
    - 通过 :class:`LLMManager` 发起 chat 调用，受信号量约束
    - 清洗 LLM 原始输出（去 ``<system>`` / ``think`` 标签、首尾引号、空白）
    - 按 ``max_message_chars`` 截断
    - 累加 token 用量、按预算阈值判断是否豁免

    线程/并发模型：
    - 同一实例可在同一 asyncio event loop 内并发被调用（多路并发场景）
    - 通过 :class:`asyncio.Semaphore` 限制同时活跃的 LLM 请求数

    Note:
        - 当前任务用累计总量与 ``token_budget_per_hour`` 做简单阈值比较；
          Task 15 会接入基于滑动窗口的更精细预算控制。
        - 本类不直接读写 :class:`Persona`，所有 ``Persona`` 实例由调用方持有。
    """

    # === 响应清洗用的正则 ===

    # LLM 偶发会输出 <system>...</system> 或 自己的内部提示，需剥除
    _SYSTEM_TAG_RE = re.compile(r"<system>.*?</system>", re.DOTALL)

    # 推理类模型的内部思考过程，对用户不可见
    _THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

    # 需要被剥离的引号字符（英文 / 中文 引号）
    # 使用 chr() 显式构造，避免源码相邻字符串被解析器拼接的歧义
    _QUOTE_CHARS = (
        chr(0x22)  # ASCII 双引号
        + chr(0x27)  # ASCII 单引号
        + chr(0x201C)  # 中文左双引号
        + chr(0x201D)  # 中文右双引号
        + chr(0x2018)  # 中文左单引号
        + chr(0x2019)  # 中文右单引号
    )
    _QUOTES_RE = re.compile(rf"^[{re.escape(_QUOTE_CHARS)}]+|[{re.escape(_QUOTE_CHARS)}]+$")

    def __init__(
        self,
        config: SimulatorConfigSchema,
        llm_manager: LLMManager,
        prompt_manager: Optional[PromptManager] = None,
    ) -> None:
        """初始化包装器。

        Args:
            config: 模拟器配置（决定 LLM client 类型、温度、并发上限、预算等）。
            llm_manager: 共享的 LLM 管理器，调用方负责先完成 ``setup``。
            prompt_manager: 可选的自定义 prompt 管理器；缺省使用全局单例。
        """
        self._config: SimulatorConfigSchema = config
        self._llm: LLMManager = llm_manager
        self._prompts: PromptManager = prompt_manager or get_prompt_manager()
        self._logger = get_logger("SimulatorLLMWrapper")
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(config.max_concurrent_llm)
        self._total_tokens: int = 0

    # === 公共 API：4 类消息生成 ===

    async def generate_viewer_message(
        self,
        persona: Persona,
        context: StreamerContextSnapshot,
    ) -> Optional[GeneratedMessage]:
        """生成常驻观众的普通弹幕。"""
        recent_speech = "\n".join(context.recent_messages[-3:]) if context.recent_messages else "(主播尚未发言)"

        prompt = self._prompts.render_safe(
            "simulator/viewer_message",
            persona_name=persona.user_nickname,
            persona_role=persona.role.value,
            persona_personality=persona.personality,
            persona_speaking_style=persona.speaking_style,
            streamer_recent_speech=recent_speech,
            streamer_emotion=context.recent_emotion or "未知",
            recent_chat_context="(略)",
            language=self._config.language,
        )

        result = await self._call_llm(prompt)
        if result is None:
            return None
        text, tokens = result
        if not text:
            return None
        return GeneratedMessage(
            text=text,
            persona=persona,
            data_type="text",
            tokens_used=tokens,
        )

    async def generate_sc_message(
        self,
        persona: Persona,
        context: StreamerContextSnapshot,
        amount_rmb: int,
    ) -> Optional[GeneratedMessage]:
        """生成 SuperChat 付费留言。"""
        recent_speech = "\n".join(context.recent_messages[-3:]) if context.recent_messages else "(主播尚未发言)"

        prompt = self._prompts.render_safe(
            "simulator/sc_message",
            persona_name=persona.user_nickname,
            persona_personality=persona.personality,
            streamer_recent_speech=recent_speech,
            amount_rmb=int(amount_rmb),
            language=self._config.language,
        )

        result = await self._call_llm(prompt)
        if result is None:
            return None
        text, tokens = result
        if not text:
            return None
        return GeneratedMessage(
            text=text,
            persona=persona,
            data_type="super_chat",
            sc_amount_rmb=int(amount_rmb),
            tokens_used=tokens,
        )

    async def generate_passerby_message(
        self,
        persona: Persona,
        context: StreamerContextSnapshot,
    ) -> Optional[GeneratedMessage]:
        """生成路人的随机弹幕。"""
        recent_speech = "\n".join(context.recent_messages[-3:]) if context.recent_messages else "(主播尚未发言)"

        prompt = self._prompts.render_safe(
            "simulator/passerby_message",
            streamer_recent_speech=recent_speech,
            recent_chat_context="(略)",
            language=self._config.language,
        )

        result = await self._call_llm(prompt)
        if result is None:
            return None
        text, tokens = result
        if not text:
            return None
        return GeneratedMessage(
            text=text,
            persona=persona,
            data_type="text",
            tokens_used=tokens,
        )

    async def generate_warmup_message(self, persona: Persona) -> Optional[GeneratedMessage]:
        """生成暖场期弹幕（主播尚未开口阶段）。"""
        prompt = self._prompts.render_safe(
            "simulator/warmup_message",
            persona_name=persona.user_nickname,
            persona_personality=persona.personality,
            persona_speaking_style=persona.speaking_style,
            language=self._config.language,
        )

        result = await self._call_llm(prompt)
        if result is None:
            return None
        text, tokens = result
        if not text:
            return None
        return GeneratedMessage(
            text=text,
            persona=persona,
            data_type="text",
            tokens_used=tokens,
        )

    async def generate_personas(
        self,
        count: int = 1,
        roles: Optional[list[str]] = None,
        existing_nicknames: Optional[list[str]] = None,
    ) -> list[Persona]:
        """批量生成常驻观众人设（贴近真实 B 站观众）。

        Args:
            count: 生成数量（1-20）
            roles: 允许的角色列表；缺省为全部非路人角色
            existing_nicknames: 直播间已有的常驻观众昵称；传入后提示词会要求 LLM 避开

        Returns:
            生成的 Persona 列表（user_id 由本方法生成）；LLM 失败或解析失败时返回空列表
        """
        role_pool = roles or [r.value for r in PersonaRole if r != PersonaRole.PASSERBY]
        existing_hint = ""
        if existing_nicknames:
            existing_hint = "以下昵称已被占用，严禁使用：" + "、".join(existing_nicknames[:30]) + "。"
        prompt = self._prompts.render_safe(
            "simulator/persona_generation",
            count=count,
            roles_hint="、".join(role_pool),
            existing_nicknames_hint=existing_hint,
            language=self._config.language,
        )

        result = await self._call_llm(prompt, truncate=False)
        if result is None:
            return []
        text, _tokens = result

        personas: list[Persona] = []
        parsed_items = _parse_persona_json(text)[:count]
        if not parsed_items:
            self._logger.warning(f"generate_personas: LLM 输出无法解析为人设 JSON (len={len(text)}): {text[:200]!r}")
        for item in parsed_items:
            try:
                personas.append(
                    Persona(
                        user_id=f"resident_{uuid.uuid4().hex[:8]}",
                        user_nickname=item["user_nickname"],
                        role=PersonaRole(item.get("role", "fan")),
                        personality=item.get("personality", ""),
                        speaking_style=item.get("speaking_style", ""),
                        fans_medal_level=int(item.get("fans_medal_level", 0)),
                        guard_level=int(item.get("guard_level", 0)),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        return personas

    # === 公共 API：统计 / 配置更新 ===

    def get_token_usage(self) -> Dict[str, int]:
        """返回当前累计 token 用量。

        Returns:
            形如 ``{"total": int}`` 的字典，仅暴露总体计数。
        """
        return {"total": self._total_tokens}

    def is_budget_exceeded(self) -> bool:
        """判断累计 token 是否已达到 :attr:`SimulatorConfigSchema.token_budget_per_hour`。

        当前用总累计量做粗略阈值；后续 Task 15 将替换为滑动窗口实现。
        """
        return self._total_tokens >= self._config.token_budget_per_hour

    def update_config(self, config: SimulatorConfigSchema) -> None:
        """热更新配置。

        - 替换 :attr:`_config`
        - 若并发上限发生变化，重新创建 :class:`asyncio.Semaphore`

        Note:
            已激活的 in-flight 调用仍按旧信号量走完，新调用按新信号量排队。
        """
        old_max = self._config.max_concurrent_llm
        self._config = config
        if config.max_concurrent_llm != old_max:
            self._semaphore = asyncio.Semaphore(config.max_concurrent_llm)

    # === 内部：LLM 调用统一入口 ===

    async def _chat_once(self, prompt: str, max_tokens: Optional[int]) -> Optional[LLMResponse]:
        """单次 LLM 调用（信号量约束），失败返回 None。

        Args:
            max_tokens: 单次输出上限；None 表示不限制（交由 LLM profile/API 默认），
                总消耗由 ``token_budget_per_hour`` 预算控制。
        """
        try:
            async with self._semaphore:
                response: LLMResponse = await self._llm.chat(
                    prompt,
                    client_type=self._config.llm_client_type,
                    temperature=self._config.llm_temperature,
                    max_tokens=max_tokens,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.error(f"LLM 调用异常: {exc!r}", exc_info=True)
            return None

        if not response.success:
            self._logger.warning(f"LLM 调用未成功 (client={self._config.llm_client_type}, error={response.error!r})")
            return None
        return response

    async def _call_llm(
        self,
        prompt: str,
        *,
        truncate: bool = True,
        max_tokens: Optional[int] = None,
    ) -> Optional[Tuple[str, int]]:
        """调用 LLM 并清洗响应。

        流程：
        1. 等待信号量并调用 :meth:`LLMManager.chat`
        2. 判断 ``success`` 与 ``content``
        3. 清洗：去 ``<system>`` / ``think`` 块、首尾引号、空白
        4. 推理模型兜底：content 为空但存在 thinking（reasoning_content）时，
           视为模型未输出正文，保持相同参数重试一次
        5. 按 :attr:`_config.max_message_chars` 截断（``truncate=True`` 时）
        6. 累计 token 用量

        Args:
            prompt: 提示词
            truncate: 是否按消息长度截断（结构化工件如 JSON 应传 False）
            max_tokens: 单次输出上限；None 表示不限制（由 profile/API 默认决定），
                总消耗由 ``token_budget_per_hour`` 预算控制

        Returns:
            ``(cleaned_text, tokens_used)`` 元组；
            任意环节失败（信号量拒绝、调用失败、空响应、异常）返回 ``None``。
        """
        response = await self._chat_once(prompt, max_tokens)
        if response is None:
            return None

        tokens_used = self._extract_total_tokens(response)
        raw_content = response.content or ""
        cleaned = self._clean_response(raw_content)

        reasoning = getattr(response, "reasoning_content", None)
        if not cleaned and reasoning:
            self._logger.warning(f"_call_llm: content 为空但 thinking 存在 (len={len(reasoning)})，重试一次")
            response = await self._chat_once(prompt, max_tokens)
            if response is None:
                return None
            tokens_used = self._extract_total_tokens(response)
            raw_content = response.content or ""
            cleaned = self._clean_response(raw_content)

        if not cleaned:
            self._logger.warning(f"_call_llm: raw={raw_content!r} → cleaned 为空，跳过")
            return None

        if truncate:
            cleaned = self._truncate(cleaned, self._config.max_message_chars)
            if not cleaned:
                self._logger.warning(f"_call_llm: truncated 为空，跳过 (cleaned={cleaned!r})")
                return None

        self._logger.info(f"_call_llm: 成功 (tokens={tokens_used}, len={len(cleaned)})")
        self._logger.debug(f"_call_llm: raw={raw_content!r} → cleaned={cleaned!r}")
        self._total_tokens += tokens_used
        return cleaned, tokens_used

    # === 内部：响应清洗辅助 ===

    @staticmethod
    def _extract_total_tokens(response: LLMResponse) -> int:
        """从 :class:`LLMResponse.usage` 安全读取 ``total_tokens``。

        :attr:`LLMResponse.usage` 是 ``Optional[Dict[str, int]]``；
        缺字段 / 类型不对 / 缺失字典都按 0 处理。
        """
        usage = response.usage
        if not isinstance(usage, dict):
            return 0
        try:
            value = usage.get("total_tokens", 0)
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _clean_response(cls, raw: str) -> str:
        """按固定顺序清洗 LLM 原始输出。

        1. 去掉 ``<system>...</system>`` 块
        2. 去掉 ``think...think`` 块
        3. ``strip()``
        4. 去掉首尾成对引号（英文 / 中文）
        5. 再次 ``strip()``
        """
        if not raw:
            return ""
        text = cls._SYSTEM_TAG_RE.sub("", raw)
        text = cls._THINK_TAG_RE.sub("", text)
        text = text.strip()
        if not text:
            return ""
        text = cls._QUOTES_RE.sub("", text)
        return text.strip()

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        """按字符数截断；过长时在最近的 ``,。!?！？`` 边界处收尾，避免半句话。"""
        if not text or max_chars <= 0:
            return ""
        if len(text) <= max_chars:
            return text

        truncated = text[:max_chars]
        # 使用 chr() 显式构造，避免源码相邻字符串被解析器拼接的歧义
        sep_chars = (
            chr(0x3002)
            + chr(0xFF01)
            + chr(0x3F)
            + chr(0xFF1F)
            + chr(0x21)
            + chr(0xFF01)
            + chr(0x2E)
            + chr(0x2C)
            + chr(0x3001)
            + chr(0x3B)
            + chr(0x3A)
            + chr(0xFF1B)
            + chr(0xFF1A)
            + chr(0xFF0C)
            + chr(0xA)
        )
        for sep in sep_chars:
            idx = truncated.rfind(sep)
            # 至少保留 1/3 内容避免截太狠
            if idx >= max_chars // 3:
                truncated = truncated[: idx + 1]
                break
        return truncated.strip()


__all__ = ["SimulatorLLMWrapper"]
