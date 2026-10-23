"""Warudo 表情档案：情绪 → 表情键的映射配置（与具体模型解耦的唯一位置）

适配器只认"表情键"：每个键是一个 WebSocket 动作名、数据是 0~1 权重，在模型上
驱动哪个形状由 Warudo 蓝图决定。默认档案用 VRM 1.0 标准表情名（happy / angry /
sad / relaxed / surprised），任何 VRM 模型都有这些槽；自定义形状名的模型（如麦麦，
见 ``profiles/maimai.toml``）换一份档案即可，代码零改动。

口型元音、眨眼、移眼是固定协议键，由后台任务和口型渲染器直接驱动，不进档案。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Dict, List

from pydantic import Field, field_validator

from src.modules.config.schemas.base import BaseConfig
from src.modules.types.emotion_vocab import Emotion

# 眨眼键：眨眼任务按它闭眼；情绪也可以写它（如困倦半闭眼），由眼部状态合并
BLINK_KEY = "eye_close"

# 口型元音键：口型渲染器按 A/I/U/E/O 写入，模板蓝图映射到 aa/ih/ou/ee/oh
VOWEL_KEYS = ("VowelA", "VowelI", "VowelU", "VowelE", "VowelO")

# 移眼键：移眼任务左右扫视时写入，模板蓝图映射到 lookLeft/lookRight/lookUp/lookDown
EYE_SHIFT_KEYS = ("eye_shift_left", "eye_shift_right", "eye_shift_up", "eye_shift_down")

# 档案里的情绪不得占用的键：这些键由固定通道驱动，情绪再写会两路互相覆盖
RESERVED_KEYS = frozenset(VOWEL_KEYS) | frozenset(EYE_SHIFT_KEYS)

# 麦麦模型的表情档案（仓库内只含名字映射，不含模型文件）
MAIMAI_PROFILE_PATH = Path(__file__).parent / "profiles" / "maimai.toml"


def _vrm_standard_emotions() -> Dict[str, Dict[str, float]]:
    """默认情绪映射：17 种情绪归并到 VRM 1.0 的 5 个标准情绪槽（可叠加），
    困倦和哭泣再叠一点闭眼。数值是通用起点，具体模型按观感在配置里调。"""
    return {
        "neutral": {},
        "happy": {"happy": 1.0},
        "sad": {"sad": 1.0},
        "angry": {"angry": 1.0},
        "surprised": {"surprised": 1.0},
        "scared": {"surprised": 0.6, "sad": 0.5},
        "disgusted": {"angry": 0.6, "sad": 0.2},
        "shy": {"happy": 0.5, "relaxed": 0.3},
        "embarrassed": {"happy": 0.3, "sad": 0.3},
        "confused": {"sad": 0.3, "surprised": 0.3},
        "love": {"happy": 0.8, "relaxed": 0.4},
        "excited": {"happy": 1.0, "surprised": 0.4},
        "smug": {"relaxed": 0.6, "happy": 0.3},
        "serious": {"angry": 0.3},
        "tired": {"relaxed": 0.6, BLINK_KEY: 0.4},
        "crying": {"sad": 1.0, BLINK_KEY: 0.3},
        "speechless": {"relaxed": 0.3, "sad": 0.2},
    }


class WarudoExpressionConfig(BaseConfig):
    """Warudo 表情档案（TOML 段位：[avatar.platform.warudo.expression]）"""

    blink_blocking_keys: List[str] = Field(
        default_factory=list,
        title="暂停眨眼的表情键",
        description="这些表情键生效时暂停自动眨眼（如笑眼）；情绪写了眨眼键 eye_close 时也自动暂停",
    )
    mouth_keys: List[str] = Field(
        default_factory=list,
        title="嘴部表情键",
        description="说话口型生效期间暂时归零、停嘴后恢复的表情键（避免嘴形和口型叠在一起）",
    )

    emotions: Dict[str, Dict[str, float]] = Field(
        default_factory=_vrm_standard_emotions,
        title="情绪映射",
        description=(
            "{情绪: {表情键: 满幅权重}}；表情键即发给 Warudo 的动作名，强度按权重线性缩放；"
            "未列出的情绪按中性处理（清空表情）"
        ),
    )

    @field_validator("emotions")
    @classmethod
    def _check_emotions(cls, value: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
        # 情绪名、表情键、权重三项逐一校验：错名在加载期点名拦下，不留到直播时才发现表情没动
        valid = {e.value for e in Emotion}
        unknown = sorted(set(value) - valid)
        if unknown:
            raise ValueError(f"未知情绪 {unknown}，合法情绪为 {sorted(valid)}")
        for emotion, weights in value.items():
            for key, weight in weights.items():
                if not key:
                    raise ValueError(f"情绪 {emotion} 含空表情键")
                if key in RESERVED_KEYS:
                    raise ValueError(f"情绪 {emotion} 的表情键 {key} 是口型/移眼专用键，不能写进情绪映射")
                if not 0.0 <= weight <= 1.0:
                    raise ValueError(f"情绪 {emotion} 的表情键 {key} 权重 {weight} 超出 0~1")
        return value

    def expression_keys(self) -> List[str]:
        """档案涉及的全部表情键（不含眨眼键，它由眼部状态单独管理）。

        换情绪时这些键全部重发一遍（不在新情绪里的发 0），保证旧表情不残留。
        """
        keys = {key for weights in self.emotions.values() for key in weights}
        keys.update(self.mouth_keys)
        keys.update(self.blink_blocking_keys)
        keys.discard(BLINK_KEY)
        return sorted(keys)


def read_profile_expression(path: Path) -> Dict[str, Any]:
    """读取档案文件里的 ``[platform.warudo.expression]`` 段（原样 dict，未校验）。

    档案文件用完整段路径书写，用户可以整段粘贴进 avatar.toml；配置升级钩子
    也从这里取麦麦档案，保证仓库里只有一份麦麦映射。
    """
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return data["platform"]["warudo"]["expression"]


__all__ = [
    "BLINK_KEY",
    "EYE_SHIFT_KEYS",
    "MAIMAI_PROFILE_PATH",
    "RESERVED_KEYS",
    "VOWEL_KEYS",
    "WarudoExpressionConfig",
    "read_profile_expression",
]
