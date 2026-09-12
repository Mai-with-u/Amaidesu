"""礼物和 SuperChat 消息生成器

加权随机选择礼物，SC 文本通过 LLM 生成。
礼物目录持久化在 SQLite ``sim_gifts`` 表（运行时数据，WebUI 管理），
首次启动由内置种子导入，本类持有内存缓存，增删改写穿 DB。
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

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
from src.modules.storage.repos import SimRepo

# sim_gifts 允许通过 update_gift 更新的字段（与 DB 白名单一致的运行时防线）
_GIFT_UPDATABLE_FIELDS = frozenset({"gift_name", "category", "weight", "data_type", "sc_amount_rmb"})


class GiftGenerator:
    """礼物和 SC 生成器

    礼物目录来自 SQLite ``sim_gifts`` 表（启动时由内置种子导入），
    按权重随机选择。普通礼物直接拼接模板，SC 通过 LLM 调用生成文本。
    """

    def __init__(
        self,
        config: SimulatorConfigSchema,
        sim_repo: SimRepo,
        llm_wrapper: Any = None,
        rng: Optional[random.Random] = None,
    ):
        self._config = config
        self._sim = sim_repo
        self._llm_wrapper = llm_wrapper
        self._rng = rng or random.Random()
        self._logger = get_logger("GiftGenerator")
        self._gifts: List[GiftItem] = []
        self._weights: List[int] = []

    async def load(self) -> None:
        """从 DB 加载礼物目录到内存缓存。"""
        rows = await self._sim.list_sim_gifts()
        self._gifts = [
            GiftItem(
                gift_id=row["gift_id"],
                gift_name=row["gift_name"],
                category=row["category"],
                weight=row["weight"],
                data_type=row["data_type"],
                sc_amount_rmb=row["sc_amount_rmb"],
            )
            for row in rows
        ]
        self._weights = [gift.weight for gift in self._gifts]
        self._logger.info(f"已加载 {len(self._gifts)} 个礼物")

    # -------------------- 礼物目录 CRUD（写穿 DB + 刷新缓存） --------------------

    def list_gifts(self) -> List[GiftItem]:
        """返回礼物目录的列表副本。"""
        return list(self._gifts)

    async def add_gift(self, gift: GiftItem) -> bool:
        """新增礼物；gift_id 已存在时返回 False。"""
        if any(g.gift_id == gift.gift_id for g in self._gifts):
            return False
        await self._sim.insert_sim_gift(
            gift_id=gift.gift_id,
            gift_name=gift.gift_name,
            category=gift.category,
            weight=gift.weight,
            data_type=gift.data_type,
            sc_amount_rmb=gift.sc_amount_rmb,
        )
        self._gifts.append(gift)
        self._weights.append(gift.weight)
        return True

    async def update_gift(self, gift_id: str, fields: Dict[str, object]) -> bool:
        """按字段更新礼物（白名单校验）并刷新缓存。

        Returns:
            True 更新成功；False 礼物不存在。
        """
        unknown = set(fields) - _GIFT_UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"update_gift 非法字段: {sorted(unknown)}")
        updated = await self._sim.update_sim_gift(gift_id=gift_id, fields=fields)
        if not updated:
            return False
        row = next((r for r in await self._sim.list_sim_gifts() if r["gift_id"] == gift_id), None)
        target = next((g for g in self._gifts if g.gift_id == gift_id), None)
        if row is not None and target is not None:
            refreshed = GiftItem(
                gift_id=row["gift_id"],
                gift_name=row["gift_name"],
                category=row["category"],
                weight=row["weight"],
                data_type=row["data_type"],
                sc_amount_rmb=row["sc_amount_rmb"],
            )
            self._gifts[self._gifts.index(target)] = refreshed
            self._weights = [g.weight for g in self._gifts]
        return True

    async def delete_gift(self, gift_id: str) -> bool:
        """删除礼物并刷新缓存。

        Returns:
            True 删除成功；False 礼物不存在。
        """
        deleted = await self._sim.delete_sim_gift(gift_id=gift_id)
        if not deleted:
            return False
        target = next((g for g in self._gifts if g.gift_id == gift_id), None)
        if target is not None:
            self._gifts.remove(target)
            self._weights = [g.weight for g in self._gifts]
        return True

    # -------------------- 生成 --------------------

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
