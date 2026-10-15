"""各平台的额度接口适配：按 provider 的 base_url 识别平台，查询并解析成统一的额度条目。

只收用推理 API Key 本身就能查询的平台；需要控制台 Cookie 或 AK/SK 的平台
（阿里百炼、火山方舟等）不在此列。解析函数是纯函数，对缺字段 / 结构变化
返回空结果，不把缺值当成 0。
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from src.modules.logging import get_logger

from .models import QuotaWindow

logger = get_logger("ProviderQuota")

GetJson = Callable[[str], Awaitable[Any]]
"""按 URL 取 JSON 的回调（已带鉴权头）；HTTP 非 2xx 或返回非 JSON 时抛 QuotaFetchError。"""


class QuotaFetchError(Exception):
    """额度接口调用失败：HTTP 非 2xx、业务码非成功或返回结构不认识。"""


@dataclass(frozen=True)
class PlatformQuota:
    """单个平台一次查询的解析结果。"""

    plan_label: str
    windows: List[QuotaWindow]


QuotaFetcher = Callable[[GetJson, str], Awaitable[Optional[PlatformQuota]]]


@dataclass(frozen=True)
class QuotaPlatform:
    """一个可查询额度的平台：展示名、auto 识别用的域名后缀与查询实现。"""

    name: str
    label: str
    host_suffixes: Tuple[str, ...]
    fetch: QuotaFetcher


# ---------------------------------------------------------------------------
# 通用解析工具
# ---------------------------------------------------------------------------


def _number(value: Any) -> Optional[float]:
    """数字或数字字符串转 float；缺失 / 非数字返回 None。平台常把金额和百分比发成字符串。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str) and value.strip():
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _time_ms(value: Any) -> Optional[int]:
    """时刻统一成 Unix 毫秒：数字小于 1e12 视为秒；非数字字符串按 ISO 8601 解析。"""
    number = _number(value)
    if number is not None:
        if number <= 0:
            return None
        return int(number * 1000) if number < 1e12 else int(number)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return int(parsed.timestamp() * 1000)
    return None


def _clamp_percent(value: float) -> float:
    return max(0.0, min(100.0, value))


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _span_label(minutes: Optional[float], fallback: str) -> str:
    """窗口时长 → 展示名：一天内写小时数，整周写 Weekly，约一个月写 Monthly。"""
    if minutes is None or minutes <= 0:
        return fallback
    hours = minutes / 60
    if hours < 24:
        return f"{round(hours)}h" if hours >= 1 else f"{round(minutes)}m"
    days = hours / 24
    if round(days) == 7:
        return "Weekly"
    if 28 <= days <= 31:
        return "Monthly"
    return f"{round(days)}d"


def _host(base_url: str) -> str:
    return (urlsplit(base_url).hostname or "").lower()


def _origin(base_url: str) -> str:
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


def _china_currency(base_url: str, cn_suffixes: Tuple[str, ...]) -> str:
    """国内站点按人民币计价，国际站按美元。"""
    host = _host(base_url)
    return "CNY" if any(host == s or host.endswith("." + s) for s in cn_suffixes) else "USD"


def _balance(amount: float, currency: str, label: str = "Balance") -> QuotaWindow:
    return QuotaWindow(kind="balance", label=label, amount=amount, currency=currency)


# ---------------------------------------------------------------------------
# MiniMax：Token Plan 限额窗口，按量付费账户余额
# ---------------------------------------------------------------------------

_MINIMAX_CN_SUFFIXES = ("minimaxi.com", "minimax.cn", "minimaxi.chat", "minimax.chat")


def _minimax_checked(body: Any) -> Dict[str, Any]:
    """MiniMax 的鉴权失败等业务错误也回 HTTP 200，错误码在 base_resp.status_code。"""
    data = _dict(body)
    base_resp = _dict(data.get("base_resp"))
    code = _number(base_resp.get("status_code"))
    if code not in (None, 0):
        raise QuotaFetchError(f"MiniMax 业务错误 {int(code)}: {base_resp.get('status_msg', '')}")
    return data


def parse_minimax_remains(body: Dict[str, Any]) -> List[QuotaWindow]:
    """解析 Token Plan 剩余额度：只看 model_name=general 的文本模型行。

    短窗口命名为 Session 而不按起止时刻换算时长：实测时段按北京时间固定切分，
    跨零点的一档会被截短（20:00-24:00 只有 4 小时），按时长命名会随时段跳变。
    status=3 且剩余 100% 的行是"无此项权益"的占位，隐藏不展示。百分比字段是
    剩余比例，这里换算成已用比例。
    """
    rows = body.get("model_remains")
    if not isinstance(rows, list):
        rows = _dict(body.get("data")).get("model_remains")
    if not isinstance(rows, list):
        return []
    general = next((row for row in rows if isinstance(row, dict) and row.get("model_name") == "general"), None)
    if general is None:
        return []

    windows: List[QuotaWindow] = []
    lanes = (
        ("Session", "current_interval_status", "current_interval_remaining_percent", "end_time"),
        ("Weekly", "current_weekly_status", "current_weekly_remaining_percent", "weekly_end_time"),
    )
    for label, status_key, percent_key, end_key in lanes:
        remaining = _number(general.get(percent_key))
        if remaining is None:
            continue
        if _number(general.get(status_key)) == 3 and remaining >= 100:
            continue
        windows.append(
            QuotaWindow(
                kind="limit",
                label=label,
                used_percent=_clamp_percent(100 - remaining),
                resets_at_ms=_time_ms(general.get(end_key)),
            )
        )
    return windows


async def fetch_minimax(get_json: GetJson, base_url: str) -> Optional[PlatformQuota]:
    """先查 Token Plan（新旧两个接口），拿不到限额窗口再查按量付费余额。"""
    origin = _origin(base_url)
    for path in ("/v1/token_plan/remains", "/v1/api/openplatform/coding_plan/remains"):
        try:
            windows = parse_minimax_remains(_minimax_checked(await get_json(origin + path)))
        except QuotaFetchError as exc:
            # 按量付费的 Key 在套餐接口上会被拒，属预期路径，转查余额
            logger.debug(f"MiniMax 套餐额度接口 {path} 不可用，继续尝试下一个: {exc}", exc=exc)
            continue
        if windows:
            return PlatformQuota(plan_label="Token Plan", windows=windows)

    body = _minimax_checked(await get_json(origin + "/account/query_balance"))
    amount = _number(body.get("available_amount"))
    if amount is None:
        return None
    currency = _china_currency(base_url, _MINIMAX_CN_SUFFIXES)
    return PlatformQuota(plan_label="Pay-as-you-go", windows=[_balance(amount, currency)])


# ---------------------------------------------------------------------------
# DeepSeek：账户余额
# ---------------------------------------------------------------------------


def parse_deepseek_balance(body: Dict[str, Any]) -> Optional[QuotaWindow]:
    """balance_infos 每个币种一行：取余额最多的一行，全为 0 时取第一行。"""
    rows: List[Tuple[str, float]] = []
    for row in body.get("balance_infos") or []:
        if not isinstance(row, dict):
            continue
        amount = _number(row.get("total_balance"))
        currency = str(row.get("currency") or "").strip().upper()
        if amount is not None and currency:
            rows.append((currency, amount))
    if not rows:
        return None
    currency, amount = max(rows, key=lambda item: item[1]) if any(a > 0 for _, a in rows) else rows[0]
    return _balance(amount, currency)


async def fetch_deepseek(get_json: GetJson, base_url: str) -> Optional[PlatformQuota]:
    window = parse_deepseek_balance(_dict(await get_json(_origin(base_url) + "/user/balance")))
    return PlatformQuota(plan_label="Pay-as-you-go", windows=[window]) if window else None


# ---------------------------------------------------------------------------
# OpenRouter：Key 限额 + 账户 Credits
# ---------------------------------------------------------------------------

_OPENROUTER_RESET_LABELS = {"daily": "Daily limit", "weekly": "Weekly limit", "monthly": "Monthly limit"}


def parse_openrouter_key_limit(body: Dict[str, Any]) -> Optional[QuotaWindow]:
    """Key 设置了消费上限时给出限额窗口；limit_remaining 是平台自己的口径，优先采用。"""
    data = _dict(body.get("data"))
    limit = _number(data.get("limit"))
    remaining = _number(data.get("limit_remaining"))
    if limit is None or limit <= 0 or remaining is None:
        return None
    reset = str(data.get("limit_reset") or "").strip().lower()
    return QuotaWindow(
        kind="balance",
        label=_OPENROUTER_RESET_LABELS.get(reset, "Key limit"),
        used_percent=_clamp_percent((limit - remaining) / limit * 100),
        amount=max(0.0, remaining),
        currency="USD",
    )


def parse_openrouter_credits(body: Dict[str, Any]) -> Optional[QuotaWindow]:
    data = _dict(body.get("data"))
    total = _number(data.get("total_credits"))
    used = _number(data.get("total_usage"))
    if total is None or used is None or total < 0 or used < 0:
        return None
    return QuotaWindow(
        kind="balance",
        label="Credits",
        used_percent=_clamp_percent(used / total * 100) if total > 0 else 100.0,
        amount=max(0.0, total - used),
        currency="USD",
    )


async def fetch_openrouter(get_json: GetJson, base_url: str) -> Optional[PlatformQuota]:
    """/key 与 /credits 并发查询，任一成功即可展示；两个都失败才算查询失败。"""
    origin = _origin(base_url)
    key_result, credits_result = await asyncio.gather(
        get_json(origin + "/api/v1/key"), get_json(origin + "/api/v1/credits"), return_exceptions=True
    )
    windows: List[QuotaWindow] = []
    plan_label = "Pay-as-you-go"
    for name, result, parser in (
        ("key", key_result, parse_openrouter_key_limit),
        ("credits", credits_result, parse_openrouter_credits),
    ):
        if isinstance(result, BaseException):
            logger.debug(f"OpenRouter /{name} 查询失败: {result}", exc=result)
            continue
        window = parser(_dict(result))
        if window is not None:
            windows.append(window)
        if name == "key" and _dict(_dict(result).get("data")).get("is_free_tier") is True:
            plan_label = "Free"
    if not windows and isinstance(key_result, BaseException) and isinstance(credits_result, BaseException):
        raise QuotaFetchError(f"OpenRouter /key 与 /credits 均查询失败: {key_result}")
    return PlatformQuota(plan_label=plan_label, windows=windows) if windows else None


# ---------------------------------------------------------------------------
# 智谱 / Z.ai：GLM Coding Plan 限额窗口
# ---------------------------------------------------------------------------

# unit 编码：5=分钟、3=小时、1=天、6=周
_ZHIPU_UNIT_MINUTES = {5: 1, 3: 60, 1: 24 * 60, 6: 7 * 24 * 60}


def _zhipu_used_percent(limit: Dict[str, Any]) -> Optional[float]:
    """优先用总量与剩余量 / 当前用量换算，缺数时退回接口给的百分比。"""
    total = _number(limit.get("usage"))
    remaining = _number(limit.get("remaining"))
    current = _number(limit.get("currentValue", limit.get("current_value")))
    if total is not None and total > 0:
        used = None
        if remaining is not None:
            used = total - remaining if current is None else max(total - remaining, current)
        elif current is not None:
            used = current
        if used is not None:
            return _clamp_percent(max(0.0, min(total, used)) / total * 100)
    percent = _number(limit.get("percentage"))
    return _clamp_percent(percent) if percent is not None else None


def parse_zhipu_quota(body: Dict[str, Any]) -> PlatformQuota:
    """TOKENS_LIMIT / CREDIT_LIMIT 是 token 窗口（按时长排序），TIME_LIMIT 是每月 MCP 调用次数。"""
    data = _dict(body.get("data"))
    token_windows: List[Tuple[float, QuotaWindow]] = []
    mcp_window: Optional[QuotaWindow] = None
    for limit in data.get("limits") or []:
        if not isinstance(limit, dict):
            continue
        used_percent = _zhipu_used_percent(limit)
        if used_percent is None:
            continue
        limit_type = str(limit.get("type") or "").strip().upper()
        resets_at_ms = _time_ms(limit.get("nextResetTime", limit.get("next_reset_time")))
        if limit_type in ("TOKENS_LIMIT", "CREDIT_LIMIT"):
            unit = _number(limit.get("unit"))
            number = _number(limit.get("number"))
            scale = _ZHIPU_UNIT_MINUTES.get(int(unit)) if unit is not None else None
            minutes = scale * number if scale is not None and number is not None and number > 0 else None
            window = QuotaWindow(
                kind="limit",
                label=_span_label(minutes, "Token limit"),
                used_percent=used_percent,
                resets_at_ms=resets_at_ms,
            )
            token_windows.append((minutes if minutes is not None else math.inf, window))
        elif limit_type == "TIME_LIMIT":
            # MCP 额度按月重置，但接口把窗口写成 1 分钟，这里不按时长命名
            mcp_window = QuotaWindow(kind="limit", label="MCP", used_percent=used_percent, resets_at_ms=resets_at_ms)

    windows = [window for _, window in sorted(token_windows, key=lambda item: item[0])]
    if mcp_window is not None:
        windows.append(mcp_window)
    level = str(data.get("level") or data.get("planName") or "").strip()
    plan_label = f"GLM Coding {level.title()}" if level else "GLM Coding Plan"
    return PlatformQuota(plan_label=plan_label, windows=windows)


async def fetch_zhipu(get_json: GetJson, base_url: str) -> Optional[PlatformQuota]:
    body = _dict(await get_json(_origin(base_url) + "/api/monitor/usage/quota/limit"))
    if body.get("success") is False:
        raise QuotaFetchError(f"智谱额度接口返回失败: {body.get('msg', '')}")
    quota = parse_zhipu_quota(body)
    return quota if quota.windows else None


# ---------------------------------------------------------------------------
# Kimi Code：会员编程套餐限额窗口
# ---------------------------------------------------------------------------

_KIMI_UNIT_MINUTES = (("MIN", 1), ("HOUR", 60), ("DAY", 24 * 60), ("WEEK", 7 * 24 * 60), ("MONTH", 30 * 24 * 60))


def _kimi_used_percent(detail: Dict[str, Any]) -> Optional[float]:
    used = _number(detail.get("used"))
    limit = _number(detail.get("limit"))
    remaining = _number(detail.get("remaining"))
    if limit is not None and limit > 0:
        if used is not None:
            return _clamp_percent(used / limit * 100)
        if remaining is not None:
            return _clamp_percent((limit - remaining) / limit * 100)
    return None


def _kimi_reset_ms(detail: Dict[str, Any]) -> Optional[int]:
    return _time_ms(detail.get("resetTime", detail.get("reset_time", detail.get("reset_at"))))


def parse_kimi_usages(body: Dict[str, Any]) -> List[QuotaWindow]:
    """顶层 usage 是周额度；limits[] 里是带时长的滚动窗口（如 300 分钟）；
    新版接口改为 usages 下的命名池（limit_5h / limit_month_total，比例 0-1）。"""
    if isinstance(body.get("data"), dict):
        body = body["data"]
    windows: List[QuotaWindow] = []
    seen: set[str] = set()

    def add(label: str, used_percent: Optional[float], resets_at_ms: Optional[int]) -> None:
        if used_percent is None or label in seen:
            return
        seen.add(label)
        windows.append(QuotaWindow(kind="limit", label=label, used_percent=used_percent, resets_at_ms=resets_at_ms))

    for entry in body.get("limits") or []:
        if not isinstance(entry, dict):
            continue
        detail = _dict(entry.get("detail")) or entry
        window = _dict(entry.get("window"))
        duration = _number(window.get("duration"))
        unit = str(window.get("timeUnit") or "").upper()
        scale = next((minutes for key, minutes in _KIMI_UNIT_MINUTES if key in unit), None)
        minutes = duration * scale if duration is not None and scale is not None else None
        add(_span_label(minutes, "5h"), _kimi_used_percent(detail), _kimi_reset_ms(detail))

    usage = _dict(body.get("usage"))
    add("Weekly", _kimi_used_percent(usage), _kimi_reset_ms(usage))

    pools = _dict(body.get("usages"))
    for key, label in (("limit_5h", "5h"), ("limit_month_total", "Monthly")):
        pool = _dict(pools.get(key))
        ratio = _number(pool.get("used_ratio", pool.get("usedRatio")))
        add(label, _clamp_percent(ratio * 100) if ratio is not None and ratio >= 0 else None, _kimi_reset_ms(pool))
    return windows


async def fetch_kimi_code(get_json: GetJson, base_url: str) -> Optional[PlatformQuota]:
    windows = parse_kimi_usages(_dict(await get_json(_origin(base_url) + "/coding/v1/usages")))
    return PlatformQuota(plan_label="Kimi Code", windows=windows) if windows else None


# ---------------------------------------------------------------------------
# Moonshot 开放平台 / SiliconFlow：账户余额
# ---------------------------------------------------------------------------


async def fetch_moonshot(get_json: GetJson, base_url: str) -> Optional[PlatformQuota]:
    data = _dict(_dict(await get_json(_origin(base_url) + "/v1/users/me/balance")).get("data"))
    amount = _number(data.get("available_balance"))
    if amount is None:
        return None
    currency = _china_currency(base_url, ("moonshot.cn",))
    return PlatformQuota(plan_label="Pay-as-you-go", windows=[_balance(amount, currency)])


async def fetch_siliconflow(get_json: GetJson, base_url: str) -> Optional[PlatformQuota]:
    data = _dict(_dict(await get_json(_origin(base_url) + "/v1/user/info")).get("data"))
    amount = _number(data.get("totalBalance"))
    if amount is None:
        return None
    currency = _china_currency(base_url, ("siliconflow.cn",))
    return PlatformQuota(plan_label="Pay-as-you-go", windows=[_balance(amount, currency)])


# ---------------------------------------------------------------------------
# NewAPI / OneAPI 类中转站：令牌剩余额度
# ---------------------------------------------------------------------------

_NEWAPI_DEFAULT_QUOTA_PER_UNIT = 500_000.0


def _newapi_root(base_url: str) -> str:
    """中转站可能挂在子路径下：去掉末尾的 /v1 得到站点根。"""
    root = base_url.rstrip("/")
    return root[:-3] if root.lower().endswith("/v1") else root


def parse_newapi_token_usage(body: Dict[str, Any], quota_per_unit: float) -> Optional[QuotaWindow]:
    """额度以内部点数计，按站点的 quota_per_unit 换算成美元；无限额度的令牌不展示。"""
    if body.get("success") is False or body.get("code") is False:
        return None
    data = _dict(body.get("data"))
    if data.get("unlimited_quota") is True:
        return None
    available = _number(data.get("total_available"))
    if available is None:
        return None
    used = _number(data.get("total_used"))
    total = available + used if used is not None else None
    return QuotaWindow(
        kind="balance",
        label="Token quota",
        used_percent=_clamp_percent(used / total * 100) if used is not None and total else None,
        amount=max(0.0, available) / quota_per_unit,
        currency="USD",
    )


async def fetch_newapi(get_json: GetJson, base_url: str) -> Optional[PlatformQuota]:
    root = _newapi_root(base_url)
    status_result, usage_body = await asyncio.gather(
        get_json(root + "/api/status"), get_json(root + "/api/usage/token/"), return_exceptions=True
    )
    if isinstance(usage_body, BaseException):
        raise QuotaFetchError(f"中转站令牌额度查询失败: {usage_body}") from usage_body
    quota_per_unit = _NEWAPI_DEFAULT_QUOTA_PER_UNIT
    if isinstance(status_result, BaseException):
        logger.debug(f"中转站 /api/status 查询失败，按默认额度单位换算: {status_result}", exc=status_result)
    else:
        configured = _number(_dict(_dict(status_result).get("data")).get("quota_per_unit"))
        if configured is not None and configured > 0:
            quota_per_unit = configured
    window = parse_newapi_token_usage(_dict(usage_body), quota_per_unit)
    return PlatformQuota(plan_label="API key", windows=[window]) if window else None


# ---------------------------------------------------------------------------
# 平台注册表与识别
# ---------------------------------------------------------------------------

QUOTA_PLATFORMS: Dict[str, QuotaPlatform] = {
    platform.name: platform
    for platform in (
        QuotaPlatform(
            "minimax", "MiniMax", ("minimaxi.com", "minimax.io", "minimax.cn", "minimaxi.chat"), fetch_minimax
        ),
        QuotaPlatform("deepseek", "DeepSeek", ("deepseek.com",), fetch_deepseek),
        QuotaPlatform("openrouter", "OpenRouter", ("openrouter.ai",), fetch_openrouter),
        QuotaPlatform("zhipu", "智谱 GLM", ("bigmodel.cn", "api.z.ai"), fetch_zhipu),
        QuotaPlatform("kimi_code", "Kimi Code", ("api.kimi.com",), fetch_kimi_code),
        QuotaPlatform("moonshot", "Moonshot", ("moonshot.cn", "moonshot.ai"), fetch_moonshot),
        QuotaPlatform("siliconflow", "SiliconFlow", ("siliconflow.cn", "siliconflow.com"), fetch_siliconflow),
        # 中转站域名千差万别，只能在 provider 配置里显式指定
        QuotaPlatform("newapi", "NewAPI", (), fetch_newapi),
    )
}


def resolve_platform(base_url: str, setting: str) -> Optional[QuotaPlatform]:
    """按 provider 的 quota_platform 设置决定查询哪个平台。

    auto 按 base_url 域名后缀识别（按标签边界匹配，``xz.ai`` 不会命中 ``z.ai``）；
    off 不查询；其余值直接取对应平台，用于反代域名或中转站。
    """
    if setting == "off":
        return None
    if setting != "auto":
        return QUOTA_PLATFORMS.get(setting)
    host = _host(base_url)
    if not host:
        return None
    for platform in QUOTA_PLATFORMS.values():
        if any(host == suffix or host.endswith("." + suffix) for suffix in platform.host_suffixes):
            return platform
    return None
