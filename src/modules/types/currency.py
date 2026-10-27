"""平台虚拟货币的展示换算(代码常量,非数据库表)。

存储层金额统一为平台最小虚拟货币单位(B 站金瓜子,1000 金瓜子 = 1 元),
跨表聚合直接 SUM;现实货币换算只在"给人看"的展示层执行,换算率收敛
在本模块,新增币种只改此处。
"""

from __future__ import annotations

from decimal import Decimal

# B 站币种标识（currency 带平台前缀：{platform}_{unit}）；采集器构造金额
# 载荷与展示层换算共用这一份定义
CURRENCY_GOLD = "bilibili_gold_coin"
CURRENCY_SILVER = "bilibili_silver_coin"

# SC 官方单位换算：rmb 字段是人民币元，1 元 = 1000 金瓜子（整数无损）
RMB_TO_GOLD_COIN = 1000

# 币种 → 人民币换算率(1 金瓜子 = 0.001 元);None = 无金钱价值(不计付费展示)
CURRENCY_TO_CNY: dict[str, Decimal | None] = {
    CURRENCY_GOLD: Decimal("0.001"),
    CURRENCY_SILVER: None,
}

# 历史数据兜底币种:迁移回填前 B 站是唯一生产平台,空币种行均产自金瓜子链路
_DEFAULT_BILI_CURRENCY = CURRENCY_GOLD


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


__all__ = ["CURRENCY_GOLD", "CURRENCY_SILVER", "CURRENCY_TO_CNY", "RMB_TO_GOLD_COIN", "to_cny"]
