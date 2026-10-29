"""面捕合成器调参（TOML 段位：avatar.toml 顶层 ``[tracking]``）

面捕合成器是形象模块的共享部件（和口型分析器同级），调参与具体平台无关；
平台自己的换算校准（某个输入值在模型上算"正常睁眼"）写在各平台段里。
"""

from __future__ import annotations

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


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
        description="每秒算几帧并发给平台；超过平台自身的渲染帧率没有意义（VTS 本机实测约 28 帧/秒）",
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
    speech_ms_per_char: int = Field(
        title="无语音时每字时长",
        default=220,
        ge=50,
        le=1000,
        description="没开 TTS 时按字数估算一句话说多久（毫秒/字），表情据此保持",
        json_schema_extra={"x-ui-advanced": True},
    )


__all__ = ["TrackingConfig"]
