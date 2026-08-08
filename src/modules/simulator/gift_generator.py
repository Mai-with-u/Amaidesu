"""礼物和 SuperChat 消息生成器

加权随机选择礼物，SC 文本通过 LLM 生成。
"""

from __future__ import annotations

import random
import tomllib
from pathlib import Path
from typing import Any, List, Optional

from src.modules.logging import get_logger
from src.modules.simulator.config_schema import (
    SimulatorConfigSchema,
)
from src.modules.simulator.types import (
    GeneratedMessage,
    GiftItem,
    Persona,
    PersonaRole,
    StreamerContextSnapshot,
)


class GiftGenerator:
    """礼物和 SC 生成器

    加载 config/simulator_gifts.toml 的礼物预设表，按权重随机选择。
    普通礼物直接拼接模板，SC 通过 LLM 调用生成文本。
    """

    def __init__(
        self,
        config: SimulatorConfigSchema,
        llm_wrapper: Any = None,
        rng: Optional[random.Random] = None,
    ):
        self._config = config
        self._llm_wrapper = llm_wrapper
        self._rng = rng or random.Random()
        self._logger = get_logger("GiftGenerator")
        self._gifts: List[GiftItem] = []
        self._weights: List[int] = []

    async def load(self) -> None:
        """从 TOML 加载礼物预设表"""
        project_root = Path(__file__).resolve().parents[3]
        gifts_path = project_root / self._config.gifts_file

        try:
            with open(gifts_path, "rb") as f:
                data = tomllib.load(f)

            self._gifts = []
            self._weights = []
            for item in data["gifts"]["items"]:
                gift = GiftItem(
                    gift_id=item["gift_id"],
                    gift_name=item["gift_name"],
                    category=item["category"],
                    weight=item["weight"],
                    data_type=item["data_type"],
                    sc_amount_rmb=item.get("sc_amount_rmb"),
                )
                self._gifts.append(gift)
                self._weights.append(item["weight"])

            self._logger.info(f"已加载 {len(self._gifts)} 个礼物")
        except Exception as e:
            self._logger.error(f"加载礼物预设失败: {e}")

    def _pick_random_gift(self, exclude_categories: Optional[set[str]] = None) -> Optional[GiftItem]:
        """按权重随机选择一个礼物（可排除指定类别）"""
        if not self._gifts:
            return None
        if exclude_categories:
            candidates = [g for g in self._gifts if g.category not in exclude_categories]
            if not candidates:
                return None
            weights = [
                w for g, w in zip(self._gifts, self._weights, strict=True) if g.category not in exclude_categories
            ]
            return self._rng.choices(candidates, weights=weights, k=1)[0]
        return self._rng.choices(self._gifts, weights=self._weights, k=1)[0]

    async def generate_gift(self, context: StreamerContextSnapshot) -> Optional[GeneratedMessage]:
        """生成一条普通礼物消息

        Args:
            context: 主播上下文（用于事件驱动逻辑）

        Returns:
            GeneratedMessage（data_type="gift"）
        """
        gift = self._pick_random_gift(exclude_categories={"sc"})
        if gift is None:
            return None

        persona = self._pick_persona_for_gift()

        return GeneratedMessage(
            text="",
            persona=persona,
            data_type="gift",
            gift=gift,
            sc_amount_rmb=None,
            tokens_used=0,
        )

    async def generate_sc(self, context: StreamerContextSnapshot) -> Optional[GeneratedMessage]:
        """生成一条 SC 消息

        SC 文本通过 LLM 生成（如果 llm_wrapper 可用）。
        """
        # 选一个高级礼物作为 SC
        sc_gifts = [g for g in self._gifts if g.category == "sc"]
        if not sc_gifts:
            return None

        sc_gift = self._rng.choice(sc_gifts)
        persona = self._pick_persona_for_gift()

        # 尝试 LLM 生成 SC 文本
        text = ""
        tokens_used = 0
        if self._llm_wrapper is not None:
            try:
                msg = await self._llm_wrapper.generate_sc_message(persona, context, sc_gift.sc_amount_rmb or 0)
                if msg is not None:
                    text = msg.text
                    tokens_used = msg.tokens_used
            except Exception as e:
                self._logger.warning(f"SC 文本生成失败: {e}")
                text = ""

        return GeneratedMessage(
            text=text,
            persona=persona,
            data_type="super_chat",
            gift=sc_gift,
            sc_amount_rmb=sc_gift.sc_amount_rmb,
            tokens_used=tokens_used,
        )

    def _pick_persona_for_gift(self) -> Persona:
        """选择送礼的人设（优先 veteran 和 fan）"""
        # 创建一个临时送礼人设（硬编码的送礼人）
        return Persona(
            user_id="gift_sender_001",
            user_nickname="神秘送礼人",
            role=PersonaRole.FAN,
            personality="慷慨大方，喜欢捧场",
            speaking_style="简短直接",
            fans_medal_level=20,
            guard_level=2,
            is_temporary=True,
        )
