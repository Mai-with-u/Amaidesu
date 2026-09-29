"""模拟直播间的配置 Schema。"""

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


class SimulatorConfigSchema(BaseConfig):
    """模拟直播间调试工具配置 Schema"""

    enabled: bool = Field(
        default=False,
        title="是否启用",
        description="是否启用模拟器（装配开关；false 时零装配）",
    )
    mode: str = Field(
        default="generate",
        title="运行模式",
        description="运行模式: generate=LLM 生成 / replay=录制回放 / off=装配但不运行",
        pattern="^(generate|replay|off)$",
    )
    # ---- replay 模式参数 ----
    replay_date: str = Field(
        default="",
        title="回放日期",
        description="replay 模式默认回放的录制日期（YYYY-MM-DD，取 live_chat 表该日弹幕）；空串 = 不指定",
    )
    replay_speed: float = Field(
        default=1.0,
        title="回放速度",
        ge=0.1,
        le=100.0,
        description="回放速度倍率（相邻消息间隔除以该值；100 近似全速）",
    )
    replay_gap_cap_s: float = Field(
        default=60.0,
        title="回放间隔上限",
        ge=1.0,
        description="回放相邻消息的间隔上限（秒），截断超长冷场",
    )
    replay_simulated_only: bool = Field(
        default=False,
        title="仅回放模拟消息",
        description="回放时是否仅回放 simulated 消息（False 时真实+模拟录制内容都回放）",
    )
    base_rate_per_minute: float = Field(
        default=6.0,
        title="基础消息率",
        ge=0.1,
        le=60.0,
        description="基础消息率（条/分钟）",
    )
    burst_multiplier: float = Field(default=3.0, title="突发倍率", ge=1.0, le=10.0, description="突发期倍率")
    burst_min_interval_s: float = Field(default=30.0, title="突发最小间隔", ge=5.0, description="突发期最小间隔（秒）")
    burst_cooldown_s: float = Field(default=60.0, title="突发持续时长", ge=10.0, description="突发期持续时间")
    temp_passerby_ratio: float = Field(default=0.3, title="临时路人比例", ge=0.0, le=1.0, description="临时路人比例")
    gift_probability: float = Field(default=0.05, title="礼物概率", ge=0.0, le=0.5, description="每条消息是礼物的概率")
    sc_probability: float = Field(default=0.01, title="SC 概率", ge=0.0, le=0.1, description="每条消息是 SC 的概率")
    guard_probability: float = Field(
        default=0.05,
        title="上舰概率",
        ge=0.0,
        le=1.0,
        description="付费事件中上舰的触发概率（pay_roll < 该值走上舰分支）",
    )
    sc_pay_probability: float = Field(
        default=0.20,
        title="付费 SC 概率",
        ge=0.0,
        le=1.0,
        description="付费事件中 SC 的累计触发上限（pay_roll < 该值走 SC 分支，需大于上舰概率）",
    )
    idle_threshold_s: float = Field(
        default=300.0,
        title="空闲阈值",
        ge=60.0,
        description="主播无活动进入 idle 的阈值（秒）",
    )
    idle_rate_multiplier: float = Field(
        default=0.2,
        title="空闲速率倍率",
        ge=0.0,
        le=1.0,
        description="idle 模式下的生成率倍率",
    )
    warmup_duration_s: float = Field(default=300.0, title="暖场时长", ge=0.0, description="启动暖场期时长（秒）")
    llm_temperature: float = Field(default=0.9, title="生成温度", ge=0.0, le=2.0)
    token_budget_per_hour: int = Field(default=50000, title="Token 时预算", ge=1000, description="每小时 token 硬上限")
    max_concurrent_llm: int = Field(default=8, title="最大并发数", ge=1, le=32, description="最大并发 LLM 请求数")
    enable_hater: bool = Field(default=False, title="是否启用黑粉", description="是否启用黑粉人设（仅 dev）")
    language: str = Field(default="zh", title="生成语言", description="生成消息语言")
    cadence_mode: str = Field(
        default="uniform",
        title="节奏模式",
        description="节奏模式: uniform=均匀随机, fixed=固定间隔, auto=自适应突发",
    )
    fixed_interval_s: float = Field(
        default=10.0, title="固定间隔", ge=1.0, le=120.0, description="fixed 模式的固定间隔（秒）"
    )
