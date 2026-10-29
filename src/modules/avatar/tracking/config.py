"""面捕合成器调参（TOML 段位：avatar.toml 顶层 ``[tracking]``）

面捕合成器是形象模块的共享部件（和口型分析器同级），调参与具体平台无关；
平台自己的换算校准（某个输入值在模型上算"正常睁眼"）写在各平台段里。
"""

from __future__ import annotations

from typing import Dict

from pydantic import Field, field_validator

from src.modules.config.schemas.base import BaseConfig

from .expression import check_recipe_overrides


class TrackingConfig(BaseConfig):
    """面捕合成器调参"""

    enabled: bool = Field(
        title="是否启用面捕合成器",
        default=True,
        description="关闭后形象不再被逐帧驱动：不眨眼、不动、发言也不带表情",
    )
    tick_hz: int = Field(
        title="每秒帧数",
        default=30,
        ge=5,
        le=60,
        description="每秒算几帧并发给平台；超过平台自身的渲染帧率没有意义（VTS 每渲染一帧才处理一次注入，常见约 30 帧/秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    rest_smile: float = Field(
        title="静止时的嘴角",
        default=0.1,
        ge=-1.0,
        le=1.0,
        description="人设基线：不说话、没有表情时嘴角的位置（0 = 中性，正数微笑，负数下垂）",
    )
    head_wander_enabled: bool = Field(
        title="是否启用头部微动",
        default=True,
        description="没有动作时头部按不规则节奏轻微晃动，形象不僵硬",
    )
    head_wander_deg: float = Field(
        title="头部微动幅度",
        default=2.5,
        ge=0.0,
        le=15.0,
        description="头部微动的最大角度（度）",
        json_schema_extra={"x-ui-advanced": True},
    )
    blink_enabled: bool = Field(title="是否自动眨眼", default=True, description="按状态变化的频率自动眨眼")
    blink_per_min_idle: float = Field(
        title="空闲时每分钟眨眼次数",
        default=15.0,
        ge=1.0,
        le=60.0,
        description="不说话时的平均眨眼频率（真人安静时约 17 次/分）",
        json_schema_extra={"x-ui-advanced": True},
    )
    blink_per_min_speaking: float = Field(
        title="说话时每分钟眨眼次数",
        default=24.0,
        ge=1.0,
        le=60.0,
        description="说话时的平均眨眼频率（真人交谈时约 26 次/分）",
        json_schema_extra={"x-ui-advanced": True},
    )
    gaze_enabled: bool = Field(
        title="是否启用眼神微动",
        default=True,
        description="盯着镜头时眼球有细小跳动，偶尔瞥向别处再看回来",
    )
    blink_per_min_reading: float = Field(
        title="读弹幕时每分钟眨眼次数",
        default=6.0,
        ge=1.0,
        le=60.0,
        description="读弹幕时的平均眨眼频率（真人阅读时约 4.5 次/分，明显少于平时）",
        json_schema_extra={"x-ui-advanced": True},
    )
    chat_gaze_x: float = Field(
        title="弹幕区方向（左右）",
        default=0.45,
        ge=-1.0,
        le=1.0,
        description="看弹幕时视线的左右位置（-1~1，正方向与头部左右转同号）；按直播画面里弹幕区相对形象的位置调",
    )
    chat_gaze_y: float = Field(
        title="弹幕区方向（上下）",
        default=-0.35,
        ge=-1.0,
        le=1.0,
        description="看弹幕时视线的上下位置（-1~1，正数向上）",
    )
    thinking_gaze_enabled: bool = Field(
        title="思考时是否移开视线",
        default=True,
        description="主播在想下一句时视线移开、眉心微紧（真人思考时会避开视觉刺激）；刚来过弹幕就先读弹幕再想",
    )
    reading_ms: int = Field(
        title="回应前读弹幕多久",
        default=2000,
        ge=0,
        le=10_000,
        description="开始思考前几秒内来过弹幕时，先看着弹幕区读这么久再移开视线（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    room_reactions_enabled: bool = Field(
        title="是否对直播间消息做反应",
        default=True,
        description="弹幕到达时瞥一眼弹幕区；礼物、SC、上舰时再眼睛一亮",
    )
    game_reactions_enabled: bool = Field(
        title="是否对游戏事件做反应",
        default=True,
        description='游戏里有重大进展时眼睛一亮，需要关注时"诶？"一下，出错时皱眉一缩（比开口说话先到）',
    )
    speech_nods_enabled: bool = Field(
        title="说话时是否随韵律点头",
        default=True,
        description="说话时在音节起点轻点头、短语开头偶尔挑眉，说完有时点一下头",
    )
    expression_linger_ms: int = Field(
        title="表情句末停留",
        default=1500,
        ge=0,
        le=10_000,
        description="一句话播完后表情再停留多久才开始回落（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    expression_min_dwell_ms: int = Field(
        title="表情最短停留",
        default=1500,
        ge=0,
        le=10_000,
        description="换上一个表情后至少停留多久才换下一个（毫秒；惊、怕这类强情绪不受限）",
        json_schema_extra={"x-ui-advanced": True},
    )
    expression_tool_hold_ms: int = Field(
        title="工具设表情的保持时长",
        default=6000,
        ge=500,
        le=60_000,
        description="主播调工具设置的表情保持多久后回落（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    intensity_floor: float = Field(
        title="表情强度下限",
        default=0.15,
        ge=0.0,
        le=1.0,
        description="表情强度压缩：实际幅度 = 下限 + 系数 × 给定强度（说话时的表情多数是淡的）",
        json_schema_extra={"x-ui-advanced": True},
    )
    intensity_gain: float = Field(
        title="表情强度系数",
        default=0.65,
        ge=0.0,
        le=1.0,
        description="表情强度压缩的系数（下限 + 系数 = 满强度时的实际幅度）",
        json_schema_extra={"x-ui-advanced": True},
    )
    mood_enabled: bool = Field(
        title="是否启用心情",
        default=True,
        description="连着几句开心的话后静止时也带着笑、打累了动作会慢下来：表情会慢慢累积成心情，心情给身体一个底色",
    )
    mood_half_life_ms: int = Field(
        title="心情回落半衰期",
        default=90_000,
        ge=5_000,
        le=1_800_000,
        description="没有新刺激时心情回到人设基线的快慢：过这么久回落一半（毫秒）",
        json_schema_extra={"x-ui-advanced": True},
    )
    mood_gain: float = Field(
        title="心情累积系数",
        default=0.35,
        ge=0.0,
        le=1.0,
        description="每个表情推动心情的幅度（越大越容易被一两句话带动）",
        json_schema_extra={"x-ui-advanced": True},
    )
    speech_ms_per_char: int = Field(
        title="无语音时每字时长",
        default=220,
        ge=50,
        le=1000,
        description="没开 TTS 时按字数估算一句话说多久（毫秒/字），表情据此保持",
        json_schema_extra={"x-ui-advanced": True},
    )
    recipes: Dict[str, Dict[str, float]] = Field(
        title="表情配方覆盖",
        default_factory=dict,
        description=(
            "按人设改某个情绪的表演配方：写了哪个情绪，就整份替换它满强度时各通道的偏移"
            "（浮现快慢不变），motion 是头部微动幅度倍数；没写的情绪用默认配方。"
            "一般在形象调校页里看着效果调好再保存，不用手写"
        ),
        json_schema_extra={"x-ui-type": "object", "x-ui-advanced": True},
    )

    @field_validator("recipes")
    @classmethod
    def _check_recipes(cls, value: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
        check_recipe_overrides(value)
        return value


__all__ = ["TrackingConfig"]
