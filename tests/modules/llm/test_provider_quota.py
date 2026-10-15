"""平台额度查询测试：各平台响应解析、按域名识别平台、服务层缓存与配置取值契约。

解析用例的响应体取自平台真实返回（MiniMax 为本机实测）或参考实现中的已验证结构；
查询流程用假的 get_json 回调按 URL 返回预置响应，不发真实网络请求。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, get_args

import pytest

from src.modules.config.model_schemas import LLMProviderConfig, QuotaPlatformName
from src.modules.llm.quota import QUOTA_PLATFORMS, ProviderQuotaService, resolve_platform
from src.modules.llm.quota.platforms import (
    PlatformQuota,
    QuotaFetchError,
    fetch_minimax,
    fetch_newapi,
    fetch_openrouter,
    parse_deepseek_balance,
    parse_kimi_usages,
    parse_minimax_remains,
    parse_zhipu_quota,
)

MINIMAX_REMAINS = {
    "model_remains": [
        {
            "start_time": 1791979200000,
            "end_time": 1791993600000,
            "model_name": "general",
            "weekly_end_time": 1792339200000,
            "current_interval_status": 1,
            "current_interval_remaining_percent": 91,
            "current_weekly_status": 3,
            "current_weekly_remaining_percent": 100,
        },
        {
            "model_name": "video",
            "end_time": 1791993600000,
            "current_interval_status": 1,
            "current_interval_remaining_percent": 40,
        },
    ],
    "base_resp": {"status_code": 0, "status_msg": "success"},
}


def _fake_get_json(responses: Dict[str, Any]):
    """按 URL 返回预置响应；值为异常时抛出，模拟 HTTP 失败。未预置的 URL 视为 404。"""
    calls = []

    async def get_json(url: str) -> Any:
        calls.append(url)
        value = responses.get(url, QuotaFetchError(f"{url} 返回 HTTP 404"))
        if isinstance(value, Exception):
            raise value
        return value

    return get_json, calls


# === MiniMax ===


def test_minimax_remains_reads_general_lane_and_hides_placeholder() -> None:
    """只取 general 行；周窗口 status=3 且剩余 100% 是占位，不展示；剩余比例换算为已用比例。"""
    windows = parse_minimax_remains(MINIMAX_REMAINS)
    assert [w.label for w in windows] == ["Session"]
    assert windows[0].used_percent == pytest.approx(9.0)
    assert windows[0].resets_at_ms == 1791993600000


def test_minimax_remains_accepts_nested_data_and_string_percent() -> None:
    body = {
        "data": {
            "model_remains": [
                {
                    "model_name": "general",
                    "current_interval_remaining_percent": "75",
                    "current_weekly_status": 1,
                    "current_weekly_remaining_percent": "60",
                    "weekly_end_time": 1792339200,
                }
            ]
        }
    }
    windows = parse_minimax_remains(body)
    assert [(w.label, w.used_percent) for w in windows] == [("Session", 25.0), ("Weekly", 40.0)]
    # 秒级时间戳统一换算为毫秒
    assert windows[1].resets_at_ms == 1792339200000


async def test_minimax_token_plan_key_stops_at_first_endpoint() -> None:
    get_json, calls = _fake_get_json({"https://api.minimax.cn/v1/token_plan/remains": MINIMAX_REMAINS})
    quota = await fetch_minimax(get_json, "https://api.minimax.cn/v1")
    assert quota is not None and quota.plan_label == "Token Plan"
    assert calls == ["https://api.minimax.cn/v1/token_plan/remains"]


async def test_minimax_pay_as_you_go_falls_back_to_balance() -> None:
    """套餐接口拒绝（业务码 1004）或无窗口时转查余额；国内域名按人民币计价。"""
    origin = "https://api.minimaxi.com"
    get_json, calls = _fake_get_json(
        {
            f"{origin}/v1/token_plan/remains": {"base_resp": {"status_code": 1004, "status_msg": "login fail"}},
            f"{origin}/v1/api/openplatform/coding_plan/remains": {"model_remains": []},
            f"{origin}/account/query_balance": {"available_amount": "12.50", "base_resp": {"status_code": 0}},
        }
    )
    quota = await fetch_minimax(get_json, f"{origin}/v1")
    assert quota is not None and quota.plan_label == "Pay-as-you-go"
    assert [(w.kind, w.amount, w.currency) for w in quota.windows] == [("balance", 12.5, "CNY")]
    assert calls[-1] == f"{origin}/account/query_balance"


async def test_minimax_balance_business_error_raises() -> None:
    origin = "https://api.minimax.io"
    get_json, _ = _fake_get_json({f"{origin}/account/query_balance": {"base_resp": {"status_code": 1004}}})
    with pytest.raises(QuotaFetchError):
        await fetch_minimax(get_json, f"{origin}/v1")


# === DeepSeek / OpenRouter ===


def test_deepseek_picks_funded_currency_row() -> None:
    body = {
        "is_available": True,
        "balance_infos": [
            {"currency": "USD", "total_balance": "0.00"},
            {"currency": "CNY", "total_balance": "110.50"},
        ],
    }
    window = parse_deepseek_balance(body)
    assert window is not None and (window.amount, window.currency) == (110.5, "CNY")
    assert parse_deepseek_balance({"balance_infos": []}) is None


async def test_openrouter_combines_key_limit_and_credits() -> None:
    get_json, _ = _fake_get_json(
        {
            "https://openrouter.ai/api/v1/key": {
                "data": {"limit": 10, "limit_remaining": 7.5, "limit_reset": "weekly", "is_free_tier": False}
            },
            "https://openrouter.ai/api/v1/credits": {"data": {"total_credits": 20, "total_usage": 5}},
        }
    )
    quota = await fetch_openrouter(get_json, "https://openrouter.ai/api/v1")
    assert quota is not None
    assert [(w.label, w.used_percent, w.amount) for w in quota.windows] == [
        ("Weekly limit", 25.0, 7.5),
        ("Credits", 25.0, 15.0),
    ]


async def test_openrouter_both_endpoints_failing_raises() -> None:
    get_json, _ = _fake_get_json({})
    with pytest.raises(QuotaFetchError):
        await fetch_openrouter(get_json, "https://openrouter.ai/api/v1")


# === 智谱 / Kimi Code / NewAPI ===


def test_zhipu_orders_token_windows_and_appends_mcp() -> None:
    body = {
        "code": 200,
        "success": True,
        "data": {
            "level": "pro",
            "limits": [
                {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 30, "nextResetTime": 1792339200000},
                {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "usage": 100, "remaining": 80},
                {"type": "TIME_LIMIT", "unit": 5, "number": 1, "usage": 1000, "currentValue": 250},
            ],
        },
    }
    quota = parse_zhipu_quota(body)
    assert quota.plan_label == "GLM Coding Pro"
    assert [(w.label, w.used_percent) for w in quota.windows] == [("5h", 20.0), ("Weekly", 30.0), ("MCP", 25.0)]


def test_kimi_reads_rolling_limit_and_weekly_usage() -> None:
    body = {
        "usage": {"limit": "100", "remaining": "70", "resetTime": "2026-10-20T00:00:00Z"},
        "limits": [
            {
                "window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                "detail": {"limit": "50", "used": "10"},
            }
        ],
    }
    windows = parse_kimi_usages(body)
    assert [(w.label, w.used_percent) for w in windows] == [("5h", 20.0), ("Weekly", 30.0)]
    assert windows[1].resets_at_ms == 1792454400000


async def test_newapi_converts_quota_with_site_unit_and_strips_v1() -> None:
    root = "https://relay.example.com/proxy"
    get_json, calls = _fake_get_json(
        {
            f"{root}/api/status": {"success": True, "data": {"quota_per_unit": 1000}},
            f"{root}/api/usage/token/": {"code": True, "data": {"total_available": 3000, "total_used": 1000}},
        }
    )
    quota = await fetch_newapi(get_json, f"{root}/v1")
    assert quota is not None
    assert [(w.amount, w.used_percent, w.currency) for w in quota.windows] == [(3.0, 25.0, "USD")]
    assert set(calls) == {f"{root}/api/status", f"{root}/api/usage/token/"}


# === 平台识别 ===


@pytest.mark.parametrize(
    ("base_url", "setting", "expected"),
    [
        ("https://api.minimax.cn/v1", "auto", "minimax"),
        ("https://api.deepseek.com", "auto", "deepseek"),
        ("https://open.bigmodel.cn/api/coding/paas/v4", "auto", "zhipu"),
        ("https://api.z.ai/api/paas/v4", "auto", "zhipu"),
        ("https://api.kimi.com/coding/v1", "auto", "kimi_code"),
        ("https://api.siliconflow.cn/v1", "auto", "siliconflow"),
        # 按域名标签边界匹配：xz.ai / fakedeepseek.com 不能误判
        ("https://api.xz.ai/v1", "auto", None),
        ("https://fakedeepseek.com/v1", "auto", None),
        ("http://localhost:11434/v1", "auto", None),
        ("https://api.deepseek.com", "off", None),
        ("https://my-proxy.example.com/v1", "newapi", "newapi"),
        ("https://my-proxy.example.com/v1", "minimax", "minimax"),
    ],
)
def test_resolve_platform(base_url: str, setting: str, expected: Optional[str]) -> None:
    platform = resolve_platform(base_url, setting)
    assert (platform.name if platform else None) == expected


def test_config_options_match_platform_registry() -> None:
    """provider 配置的 quota_platform 取值 = auto/off + 平台注册表，两处不能漂移。"""
    assert set(get_args(QuotaPlatformName)) == {"auto", "off", *QUOTA_PLATFORMS}
    assert LLMProviderConfig().quota_platform == "auto"


# === 服务层 ===


class _FakeLookup:
    def __init__(self, providers: Dict[str, Tuple[str, Dict[str, Any]]]) -> None:
        self._providers = providers

    def get_model_provider(self, model_identifier: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        return self._providers.get(model_identifier)


@pytest.fixture
def counting_platform(monkeypatch: pytest.MonkeyPatch) -> Dict[str, int]:
    """把 deepseek 的查询实现换成计数桩，验证缓存是否命中。"""
    counter = {"calls": 0}

    async def fake_fetch(get_json: Any, base_url: str) -> PlatformQuota:
        counter["calls"] += 1
        return PlatformQuota(plan_label="Pay-as-you-go", windows=parse_minimax_remains(MINIMAX_REMAINS))

    platform = QUOTA_PLATFORMS["deepseek"]
    monkeypatch.setitem(
        QUOTA_PLATFORMS, "deepseek", type(platform)(platform.name, platform.label, platform.host_suffixes, fake_fetch)
    )
    return counter


async def test_service_caches_within_ttl(counting_platform: Dict[str, int]) -> None:
    now = {"ms": 1_000_000}
    lookup = _FakeLookup({"deepseek-chat": ("ds", {"base_url": "https://api.deepseek.com", "api_key": "sk-test"})})
    service = ProviderQuotaService(lookup, ttl_ms=60_000, clock=lambda: now["ms"])

    first = await service.get_quota_for_model("deepseek-chat")
    second = await service.get_quota_for_model("deepseek-chat")
    assert first is not None and first.provider_name == "ds" and first.platform == "deepseek"
    assert second == first
    assert counting_platform["calls"] == 1

    now["ms"] += 60_001
    await service.get_quota_for_model("deepseek-chat")
    assert counting_platform["calls"] == 2


async def test_service_returns_none_without_platform_or_key(counting_platform: Dict[str, int]) -> None:
    lookup = _FakeLookup(
        {
            "local": ("ollama", {"base_url": "http://localhost:11434/v1", "api_key": "x"}),
            "nokey": ("ds", {"base_url": "https://api.deepseek.com", "api_key": "  "}),
            "off": ("ds2", {"base_url": "https://api.deepseek.com", "api_key": "sk", "quota_platform": "off"}),
        }
    )
    service = ProviderQuotaService(lookup)
    for model in ("unknown", "local", "nokey", "off"):
        assert await service.get_quota_for_model(model) is None
    assert counting_platform["calls"] == 0
