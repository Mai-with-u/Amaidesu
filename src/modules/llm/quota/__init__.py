"""LLM 平台额度查询（账户余额 / 套餐限额窗口）

Dashboard 上下文面板 Usage 区的数据源：按 provider 的 ``base_url`` 识别平台，
用该 provider 的推理 API Key 查询按量付费余额或套餐限额窗口（Token Plan 的
短窗口 / Weekly 等）。识别不到平台或查询失败一律返回 None，前端据此隐藏 Usage 区。
"""

from .models import ProviderQuota, QuotaWindow
from .platforms import QUOTA_PLATFORMS, QuotaPlatform, resolve_platform
from .service import ProviderQuotaService

__all__ = [
    "QUOTA_PLATFORMS",
    "ProviderQuota",
    "ProviderQuotaService",
    "QuotaPlatform",
    "QuotaWindow",
    "resolve_platform",
]
