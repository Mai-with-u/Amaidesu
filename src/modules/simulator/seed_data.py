"""模拟器内置种子数据。

常驻人设与礼物目录是存储层运行时数据（sim_personas / sim_gifts 表），
不使用配置文件：全新安装由本模块提供初始数据，之后一切增删改走
SQLite（WebUI CRUD / 写穿），重启不丢。
"""

from __future__ import annotations

from typing import Any, Dict

from src.modules.logging import get_logger
from src.modules.storage.repos import SimRepo


logger = get_logger("SimulatorSeedData")

# 内置默认礼物清单（类别与权重：normal 普通 / medium 中级 / premium 高级 / sc 大额 SC）
DEFAULT_GIFTS: tuple[Dict[str, Any], ...] = (
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
)

# 内置默认常驻人设（覆盖主要角色；黑粉行默认存在，是否参与由 enable_hater 配置控制）
DEFAULT_PERSONAS: tuple[Dict[str, Any], ...] = (
    {
        "user_id": "sim_veteran_01",
        "user_nickname": "三楼老王",
        "role": "veteran",
        "personality": "直播间元老级观众，什么梗都接得住，偶尔提起以前直播间的旧事",
        "speaking_style": "老练随意，爱用缩写和老梗，偶尔倚老卖老",
        "fans_medal_level": 38,
        "guard_level": 3,
    },
    {
        "user_id": "sim_fan_01",
        "user_nickname": "小饼干",
        "role": "fan",
        "personality": "热情粉丝，主播说什么都想捧场，容易激动",
        "speaking_style": "语气活泼，爱用感叹号和颜文字",
        "fans_medal_level": 21,
        "guard_level": 1,
    },
    {
        "user_id": "sim_teaser_01",
        "user_nickname": "键盘侠克星",
        "role": "teaser",
        "personality": "爱调侃主播，损归损但没有恶意，负责活跃气氛",
        "speaking_style": "阴阳怪气但好笑，经常反问",
        "fans_medal_level": 12,
        "guard_level": 0,
    },
    {
        "user_id": "sim_newcomer_01",
        "user_nickname": "刚来问问",
        "role": "newcomer",
        "personality": "新来的观众，对直播间的一切好奇，经常问基础问题",
        "speaking_style": "礼貌试探，句子偏短",
        "fans_medal_level": 0,
        "guard_level": 0,
    },
    {
        "user_id": "sim_hater_01",
        "user_nickname": "理性发言人",
        "role": "hater",
        "personality": "杠精，看什么都不顺眼，说话带刺但不动真格",
        "speaking_style": "阴阳怪气，爱抬杠，句句带'就这'",
        "fans_medal_level": 0,
        "guard_level": 0,
    },
)


async def seed_simulator_data(sim_repo: SimRepo) -> None:
    """启动期一次性种子导入：空表才插入内置默认值，已有数据一律不动。

    幂等：非空表跳过；重复调用无副作用。
    """
    if await sim_repo.count_sim_personas() == 0:
        for item in DEFAULT_PERSONAS:
            await sim_repo.insert_sim_persona(
                user_id=item["user_id"],
                user_nickname=item["user_nickname"],
                role=item["role"],
                personality=item["personality"],
                speaking_style=item["speaking_style"],
                fans_medal_level=item.get("fans_medal_level", 0),
                guard_level=item.get("guard_level", 0),
            )
        logger.info(f"sim_personas 空表，已导入 {len(DEFAULT_PERSONAS)} 个内置常驻人设")

    if await sim_repo.count_sim_gifts() == 0:
        for item in DEFAULT_GIFTS:
            await sim_repo.insert_sim_gift(
                gift_id=item["gift_id"],
                gift_name=item["gift_name"],
                category=item["category"],
                weight=item["weight"],
                data_type=item["data_type"],
                sc_amount_rmb=item.get("sc_amount_rmb"),
            )
        logger.info(f"sim_gifts 空表，已导入 {len(DEFAULT_GIFTS)} 个内置礼物")


__all__ = ["DEFAULT_GIFTS", "DEFAULT_PERSONAS", "seed_simulator_data"]
