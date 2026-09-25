"""平台虚拟货币的展示换算(代码常量,非数据库表)。

存储层金额统一为平台最小虚拟货币单位(B 站金瓜子,1000 金瓜子 = 1 元),
跨表聚合直接 SUM;现实货币换算只在"给人看"的展示层执行,换算率收敛
在本模块,新增币种只改此处。
"""

from __future__ import annotations

from decimal import Decimal

# 币种 → 人民币换算率(1 金瓜子 = 0.001 元);None = 无金钱价值(不计付费展示)
CURRENCY_TO_CNY: dict[str, Decimal | None] = {
    "bilibili_gold_coin": Decimal("0.001"),
    "bilibili_silver_coin": None,
}

# 历史数据兜底币种:迁移回填前 B 站是唯一生产平台,空币种行均产自金瓜子链路
_DEFAULT_BILI_CURRENCY = "bilibili_gold_coin"


def to_cny(amount: int, currency: str = "") -> float | None:
    """平台最小虚拟货币单位金额 → 人民币元(展示用)。

    - ``bilibili_silver_coin`` / 未知币种返回 ``None``(无金钱价值,展示层
      据此显示"免费"或省略)
    - 空币种按 B 站金瓜子处理(存量迁移行回填历史生产平台口径)
    """
    rate = CURRENCY_TO_CNY.get(currency, CURRENCY_TO_CNY.get(_DEFAULT_BILI_CURRENCY) if not currency else None)
    if rate is None:
        return None
    return float(Decimal(int(amount)) * rate)


__all__ = ["CURRENCY_TO_CNY", "to_cny"]
