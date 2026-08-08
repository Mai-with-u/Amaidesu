"""礼物和 SuperChat 消息生成器

加权随机选择礼物，SC 文本通过 LLM 生成。
礼物清单为运行时数据（data/simulator/gifts.toml）：首次加载时写入内置默认清单，
之后用户可自由编辑（增删礼物、调整权重/价格）。
"""

from __future__ import annotations

import random
import tomllib
from pathlib import Path
from typing import Any, List, Optional

import tomlkit

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

# 内置默认礼物清单（首次启动落盘到 data/simulator/gifts.toml 供用户编辑）
_DEFAULT_GIFTS: list[dict[str, Any]] = [
    {"gift_id": "small_heart", "gift_name": "小心心", "category": "normal", "weight": 10, "data_type": "gift"},
    {"gift_id": "la_tiao", "gift_name": "辣条", "category": "normal", "weight": 10, "data_type": "gift"},
    {"gift_id": "da_call", "gift_name": "打call", "category": "normal", "weight": 10, "data_type": "gift"},
    {"gift_id": "gan_bei", "gift_name": "干杯", "category": "normal", "weight": 10, "data_type": "gift"},
    {"gift_id": "bi_xin", "gift_name": "比心", "category": "normal", "weight": 10, "data_type": "gift"},
    {"gift_id": "hua_shi_kua_kua", "gift_name": "花式夸夸", "category": "medium", "weight": 5, "data_type": "gift"},
    {"gift_id": "miao_wu_bao_bao", "gift_name": "喵呜抱抱", "category": "medium", "weight": 5, "data_type": "gift"},
    {"gift_id": "dian_zan", "gift_name": "点赞", "category": "medium", "weight": 5, "data_type": "gift"},
    {"gift_id": "yan_hua", "gift_name": "烟花", "category": "medium", "weight": 5, "data_type": "gift"},
    {"gift_id": "fen_si_deng_pai", "gift_name": "粉丝团灯牌", "category": "premium", "weight": 2, "data_type": "gift"},
    {"gift_id": "jing_xi_mang_he", "gift_name": "惊喜盲盒", "category": "premium", "weight": 2, "data_type": "gift"},
    {"gift_id": "xiao_dian_shi", "gift_name": "小电视", "category": "premium", "weight": 2, "data_type": "gift"},
    {
        "gift_id": "sc_50",
        "gift_name": "SC 50元",
        "category": "sc",
        "weight": 1,
        "data_type": "super_chat",
        "sc_amount_rmb": 50,
    },
    {
        "gift_id": "sc_100",
        "gift_name": "SC 100元",
        "category": "sc",
        "weight": 1,
        "data_type": "super_chat",
        "sc_amount_rmb": 100,
    },
    {
        "gift_id": "sc_500",
        "gift_name": "SC 500元",
        "category": "sc",
        "weight": 1,
        "data_type": "super_chat",
        "sc_amount_rmb": 500,
    },
]


class GiftGenerator:
    """礼物和 SC 生成器

    礼物清单来自 ``data/simulator/gifts.toml``（运行时数据，首次自动生成默认清单），
    按权重随机选择。普通礼物直接拼接模板，SC 通过 LLM 调用生成文本。
    """

    def __init__(
        self,
        config: SimulatorConfigSchema,
        llm_wrapper: Any = None,
        rng: Optional[random.Random] = None,
        data_dir: Optional[Path] = None,
    ):
        self._config = config
        self._llm_wrapper = llm_wrapper
        self._rng = rng or random.Random()
        self._logger = get_logger("GiftGenerator")
        self._gifts: List[GiftItem] = []
        self._weights: List[int] = []
        self._data_dir = data_dir or Path(__file__).resolve().parents[3] / "data" / "simulator"

    async def load(self) -> None:
        """加载礼物清单；data/simulator/gifts.toml 不存在时写入内置默认清单"""
        gifts_path = self._data_dir / "gifts.toml"
        if not gifts_path.exists():
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._write_default_gifts(gifts_path)
            self._logger.info(f"已生成默认礼物清单: {gifts_path}")

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

    def _write_default_gifts(self, path: Path) -> None:
        """把内置默认礼物清单写入 gifts.toml"""
        doc = tomlkit.document()
        doc.add(tomlkit.comment("模拟直播间礼物清单（运行时数据，可自由编辑）"))
        doc.add(tomlkit.comment("类别与权重：normal 普通 / medium 中级 / premium 高级 / sc 大额 SC"))
        gifts_table = tomlkit.table()
        items = tomlkit.aot()
        for gift in _DEFAULT_GIFTS:
            item_table = tomlkit.table()
            for key, value in gift.items():
                item_table[key] = value
            items.append(item_table)
        gifts_table["items"] = items
        doc["gifts"] = gifts_table
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")

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
