"""
DGLab 服务配置

定义 DGLabService 的配置 Schema，使用 Pydantic 进行验证。
"""

from pydantic import BaseModel, Field, field_validator


class WaveformPreset:
    """波形预设常量"""

    SMALL = "small"  # 小波浪
    MEDIUM = "medium"  # 中等波浪
    BIG = "big"  # 大波浪
    RANDOM = "random"  # 随机波浪

    ALL = [SMALL, MEDIUM, BIG, RANDOM]


class DGLabConfig(BaseModel):
    """
    DGLab 服务配置

    Attributes:
        api_base_url: DG-Lab API 地址
        default_strength: 默认强度 (0-200)
        default_waveform: 默认波形预设
        shock_duration_seconds: 默认电击持续时间（秒）
        request_timeout: HTTP 请求超时时间（秒）
        max_strength: 最大强度限制（安全限制）
        enable_safety_limit: 是否启用安全限制
    """

    # API 配置
    api_base_url: str = Field(
        default="http://127.0.0.1:8081",
        description="DG-Lab API 地址（通常为 http://127.0.0.1:8081）",
    )

    request_timeout: float = Field(
        default=5.0,
        ge=1.0,
        le=30.0,
        description="HTTP 请求超时时间（秒），范围 1-30",
    )

    # 默认参数
    default_strength: int = Field(
        default=10,
        ge=0,
        le=200,
        description="默认强度 (0-200)，会受 max_strength 限制",
    )

    default_waveform: str = Field(
        default=WaveformPreset.BIG,
        description=f"默认波形预设，可选: {', '.join(WaveformPreset.ALL)}",
    )

    shock_duration_seconds: float = Field(
        default=2.0,
        ge=0.1,
        le=10.0,
        description="默认电击持续时间（秒），范围 0.1-10",
    )

    # 安全限制
    max_strength: int = Field(
        default=50,
        ge=0,
        le=200,
        description="最大强度限制（安全保护），任何调用都不会超过此值",
    )

    enable_safety_limit: bool = Field(
        default=True,
        description="是否启用安全限制（推荐启用）",
    )

    @field_validator("default_waveform")
    @classmethod
    def validate_waveform(cls, v: str) -> str:
        """验证波形预设"""
        if v not in WaveformPreset.ALL:
            raise ValueError(f"无效的波形预设 '{v}'，可选值: {', '.join(WaveformPreset.ALL)}")
        return v

    @field_validator("default_strength")
    @classmethod
    def validate_default_strength(cls, v: int, info) -> int:
        """验证默认强度不超过最大强度"""
        # 注意：这里还不能访问 max_strength，因为字段可能还未初始化
        # 实际限制会在 service 中应用
        return v

    def get_effective_max_strength(self) -> int:
        """获取实际生效的最大强度"""
        if self.enable_safety_limit:
            return min(self.default_strength, self.max_strength)
        return self.default_strength

    model_config = {"extra": "ignore"}
