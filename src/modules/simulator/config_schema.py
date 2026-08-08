"""模拟直播间 Collector 的配置 Schema。"""

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


class SimulatorConfigSchema(BaseConfig):
    """模拟直播间调试工具配置 Schema"""

    enabled: bool = Field(default=False, description="是否启用模拟器")
    residents_file: str = Field(default="config/simulator_residents.toml")
    gifts_file: str = Field(default="config/simulator_gifts.toml")
    base_rate_per_minute: float = Field(
        default=6.0,
        ge=0.1,
        le=60.0,
        description="基础消息率（条/分钟）",
    )
    burst_multiplier: float = Field(default=3.0, ge=1.0, le=10.0, description="突发期倍率")
    burst_min_interval_s: float = Field(default=30.0, ge=5.0, description="突发期最小间隔（秒）")
    burst_cooldown_s: float = Field(default=60.0, ge=10.0, description="突发期持续时间")
    temp_passerby_ratio: float = Field(default=0.3, ge=0.0, le=1.0, description="临时路人比例")
    gift_probability: float = Field(default=0.05, ge=0.0, le=0.5, description="每条消息是礼物的概率")
    sc_probability: float = Field(default=0.01, ge=0.0, le=0.1, description="每条消息是 SC 的概率")
    context_window_size: int = Field(default=5, ge=1, le=20, description="读取主播上下文的消息数")
    idle_threshold_s: float = Field(
        default=300.0,
        ge=60.0,
        description="主播无活动进入 idle 的阈值（秒）",
    )
    idle_rate_multiplier: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="idle 模式下的生成率倍率",
    )
    warmup_duration_s: float = Field(default=300.0, ge=0.0, description="启动暖场期时长（秒）")
    max_message_chars: int = Field(default=50, ge=10, le=200, description="单条消息最大字符数")
    llm_client_type: str = Field(default="llm_fast", description="使用的 LLM client 类型")
    llm_temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=128, ge=32, le=512)
    token_budget_per_hour: int = Field(default=50000, ge=1000, description="每小时 token 硬上限")
    max_concurrent_llm: int = Field(default=8, ge=1, le=32, description="最大并发 LLM 请求数")
    enable_hater: bool = Field(default=False, description="是否启用黑粉人设（仅 dev）")
    language: str = Field(default="zh", description="生成消息语言")
    session_strategy: str = Field(default="smart", description="session 选择策略")
    fallback_session_id: str = Field(default="simulated_viewers")
    stats_persistence: bool = Field(default=False, description="是否持久化统计（v1 仅 in-memory）")
    cadence_mode: str = Field(
        default="uniform", description="节奏模式: uniform=均匀随机, fixed=固定间隔, auto=自适应突发"
    )
    fixed_interval_s: float = Field(default=10.0, ge=1.0, le=120.0, description="fixed 模式的固定间隔（秒）")
