"""上下文预算：按模型窗口比例换算 token 预算与三条水位线。

预算由两方共同决定：窗口是模型属性（``[[llm_models]].context_window``），
用多大比例、上限多少是消费方 Agent 的策略。窗口未配置（0）时按保守的
128k 窗口换算——不知道模型能吃多少时，按上限发过去可能直接被拒收。

三条水位线的含义（都以预算为基准）：

- ``budget``：工作层涨到这里必须做检查点，再往上就逼近窗口安全线；
- ``prepare``：越过这里就在后台提前准备检查点摘要，涨到 ``budget`` 时直接替换，
  决策热路径不用停下来等摘要；
- ``target``：检查点一次把工作层压回到这里，留出足够的追加空间，
  避免刚整理完又因几条新记录再整理一次。
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field, model_validator

__all__ = ["ContextBudgetConfig", "Watermarks", "resolve_watermarks", "UNKNOWN_WINDOW_TOKENS"]

# 模型窗口未配置时假定的窗口：主流模型都不小于它，按它换算不会撞窗口
UNKNOWN_WINDOW_TOKENS = 128_000


class ContextBudgetConfig(BaseModel):
    """Agent 的上下文预算策略（嵌进各 Agent 的配置段，默认值由 Agent 各自给定）。"""

    window_ratio: float = Field(
        default=0.25,
        gt=0.0,
        le=1.0,
        title="窗口占比",
        description="预算占模型上下文窗口的比例；窗口未配置时按 128k 窗口换算",
    )
    max_tokens: int = Field(
        default=128_000,
        ge=4_000,
        title="预算上限",
        description="上下文预算的 token 上限（本地分词估算口径）",
    )
    prepare_ratio: float = Field(
        default=0.7,
        gt=0.0,
        le=1.0,
        title="提前整理线",
        description="达到预算的这一比例时在后台提前准备检查点摘要",
    )
    target_ratio: float = Field(
        default=0.4,
        gt=0.0,
        lt=1.0,
        title="整理后回落线",
        description="检查点把工作层压回到预算的这一比例",
    )

    @model_validator(mode="after")
    def _check_order(self) -> "ContextBudgetConfig":
        """回落线必须低于提前整理线：否则一次检查点刚做完就又越过准备线，后台摘要会连轴转。"""
        if self.target_ratio >= self.prepare_ratio:
            raise ValueError(
                f"target_ratio={self.target_ratio} 必须小于 prepare_ratio={self.prepare_ratio}："
                "整理后回落线不低于提前整理线时，检查点刚做完就会再次触发"
            )
        return self


@dataclass(frozen=True)
class Watermarks:
    """一次请求装配时实际生效的三条水位线（token）。"""

    budget: int
    prepare: int
    target: int


def resolve_watermarks(config: ContextBudgetConfig, window_tokens: int) -> Watermarks:
    """按模型窗口与 Agent 策略算出本次生效的水位线。

    窗口每次装配现查：Dashboard 换了模型或改了窗口，下一次请求即按新窗口计。
    """
    window = window_tokens if window_tokens > 0 else UNKNOWN_WINDOW_TOKENS
    budget = min(int(window * config.window_ratio), config.max_tokens)
    return Watermarks(
        budget=budget,
        prepare=int(budget * config.prepare_ratio),
        target=int(budget * config.target_ratio),
    )
