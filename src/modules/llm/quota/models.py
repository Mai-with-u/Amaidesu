"""平台额度快照的数据模型（Dashboard 上下文面板 Usage 区的数据契约）。"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class QuotaWindow(BaseModel):
    """一条额度：套餐限额窗口或账户余额。

    限额窗口（``kind="limit"``）用 ``used_percent`` 画进度条、``resets_at_ms``
    算重置倒计时；余额（``kind="balance"``）用 ``amount`` + ``currency`` 显示
    金额，平台同时给出总额时也带 ``used_percent``。
    """

    kind: Literal["limit", "balance"] = Field(description="limit = 套餐限额窗口；balance = 账户余额")
    label: str = Field(description="展示名（如 5h / Weekly / Balance）")
    used_percent: Optional[float] = Field(default=None, description="已用百分比 0-100；平台未给出时 None")
    resets_at_ms: Optional[int] = Field(default=None, description="下次重置时刻（Unix 毫秒）；无重置周期时 None")
    amount: Optional[float] = Field(default=None, description="剩余金额（余额类）；限额窗口为 None")
    currency: Optional[str] = Field(default=None, description="金额币种（CNY / USD）")


class ProviderQuota(BaseModel):
    """某个 provider 一次查询得到的平台额度快照。"""

    provider_name: str = Field(description="model.toml 中的 provider 名")
    platform: str = Field(description="识别出的平台标识（minimax / deepseek / ...）")
    platform_label: str = Field(description="平台展示名")
    plan_label: str = Field(description="计费方式展示名（Token Plan / Pay-as-you-go / ...）")
    windows: List[QuotaWindow] = Field(description="额度条目，按展示顺序排列")
    fetched_at_ms: int = Field(description="查询时刻（Unix 毫秒）")
