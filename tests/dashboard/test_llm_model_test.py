"""模型连通性测试服务与端点：按名解析已保存配置、探测请求形态、SDK 异常到失败原因的翻译、HTTP 契约"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
import pytest
from openai import (
    APITimeoutError,
    APIConnectionError,
    APIStatusError,
    RateLimitError,
)

import src.modules.dashboard.api.llm as llm_api
import src.modules.dashboard.services.model_test as model_test
from src.modules.dashboard.schemas.llm import LLMModelTestResponse

_BASE_URL = "https://api.example.com/v1"


class _FakeAsyncOpenAI:
    """替身客户端：记录构造参数，chat.completions.create 按预设结果返回或抛出"""

    #: 所有实例共享的构造参数记录（断言探测客户端形态用）
    constructor_kwargs: List[Dict[str, Any]] = []
    #: 所有已构造实例（断言用后关闭）
    instances: List["_FakeAsyncOpenAI"] = []
    #: 当前测试预设的结果（响应对象或异常）
    outcome: Optional[Any] = None

    def __init__(self, **kwargs: Any) -> None:
        self.constructor_kwargs.append(kwargs)
        type(self).instances.append(self)
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        outcome = type(self).outcome
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_fake(monkeypatch: pytest.MonkeyPatch):
    """每个用例重置替身状态并把服务模块的 AsyncOpenAI 换成替身。"""
    _FakeAsyncOpenAI.constructor_kwargs = []
    _FakeAsyncOpenAI.instances = []
    _FakeAsyncOpenAI.outcome = None
    monkeypatch.setattr(model_test, "AsyncOpenAI", _FakeAsyncOpenAI)


def _ok_response(model: str = "deepseek-chat", finish_reason: str = "stop") -> Any:
    return SimpleNamespace(model=model, choices=[SimpleNamespace(finish_reason=finish_reason)])


def _status_error(exc_cls: type, status_code: int, body: Any) -> APIStatusError:
    request = httpx.Request("POST", f"{_BASE_URL}/chat/completions")
    return exc_cls("服务端拒绝", response=httpx.Response(status_code, request=request), body=body)


async def test_success_reports_latency_and_model_echo() -> None:
    _FakeAsyncOpenAI.outcome = _ok_response()
    result = await model_test.test_model_connectivity(
        {"base_url": _BASE_URL, "api_key": "sk-test"}, "deepseek-chat", 15_000
    )
    assert result.ok is True
    assert isinstance(result.latency_ms, int) and result.latency_ms >= 0
    assert result.model == "deepseek-chat"
    assert result.finish_reason == "stop"
    assert result.error is None
    # 探测客户端不重试（重试会让时延失真）、超时按请求值换算为秒
    kwargs = _FakeAsyncOpenAI.constructor_kwargs[-1]
    assert kwargs["max_retries"] == 0
    assert kwargs["timeout"] == 15.0
    assert kwargs["base_url"] == _BASE_URL
    # 一次性客户端用后即关，不驻留连接
    assert _FakeAsyncOpenAI.instances[-1].closed is True


async def test_probe_carriers_compat_auth_header() -> None:
    """非默认鉴权方式经 compat 适配落到 default_headers，与生产同一条路径。"""
    _FakeAsyncOpenAI.outcome = _ok_response()
    await model_test.test_model_connectivity(
        {"base_url": _BASE_URL, "api_key": "sk-x", "auth_type": "header", "auth_header_name": "X-Key"},
        "m",
        15_000,
    )
    kwargs = _FakeAsyncOpenAI.constructor_kwargs[-1]
    assert kwargs["default_headers"]["X-Key"] == "Bearer sk-x"


async def test_empty_base_url_fails_without_client() -> None:
    result = await model_test.test_model_connectivity({"api_key": "k"}, "m", 15_000)
    assert result.ok is False
    assert result.error is not None and "base_url" in result.error
    assert _FakeAsyncOpenAI.constructor_kwargs == []


async def test_auth_failure_names_the_key() -> None:
    _FakeAsyncOpenAI.outcome = _status_error(APIStatusError, 401, {"error": "invalid api key"})
    result = await model_test.test_model_connectivity({"base_url": _BASE_URL}, "m", 15_000)
    assert result.ok is False
    assert result.error is not None
    assert "401" in result.error and "鉴权" in result.error
    assert "invalid api key" in result.error


async def test_missing_model_reports_404() -> None:
    _FakeAsyncOpenAI.outcome = _status_error(APIStatusError, 404, {"error": "model not found"})
    result = await model_test.test_model_connectivity({"base_url": _BASE_URL}, "nope", 15_000)
    assert result.ok is False
    assert result.error is not None
    assert "404" in result.error and "model not found" in result.error


async def test_rate_limit_carries_status() -> None:
    _FakeAsyncOpenAI.outcome = _status_error(RateLimitError, 429, {"error": "quota exceeded"})
    result = await model_test.test_model_connectivity({"base_url": _BASE_URL}, "m", 15_000)
    assert result.ok is False
    assert result.error is not None
    assert "429" in result.error and "quota exceeded" in result.error


async def test_timeout_reports_unreachable() -> None:
    request = httpx.Request("POST", f"{_BASE_URL}/chat/completions")
    _FakeAsyncOpenAI.outcome = APITimeoutError(request=request)
    result = await model_test.test_model_connectivity({"base_url": _BASE_URL}, "m", 15_000)
    assert result.ok is False
    assert result.error is not None and "超时" in result.error


async def test_connection_error_reports_network() -> None:
    request = httpx.Request("POST", f"{_BASE_URL}/chat/completions")
    _FakeAsyncOpenAI.outcome = APIConnectionError(request=request)
    result = await model_test.test_model_connectivity({"base_url": _BASE_URL}, "m", 15_000)
    assert result.ok is False
    assert result.error is not None and "网络连接失败" in result.error


async def test_unicode_encode_error_reports_non_ascii_hint() -> None:
    """落盘密钥含非 ASCII 字符（如误把占位文案当密钥存盘）时给可懂的失败原因。"""
    _FakeAsyncOpenAI.outcome = UnicodeEncodeError("ascii", "Bearer 已设置", 7, 10, "ordinal not in range(128)")
    result = await model_test.test_model_connectivity({"base_url": _BASE_URL, "api_key": "已设置"}, "m", 15_000)
    assert result.ok is False
    assert result.error is not None and "非 ASCII" in result.error


async def test_unexpected_error_is_caught_and_reported() -> None:
    class _Boom(Exception):
        pass

    _FakeAsyncOpenAI.outcome = _Boom("磁盘着火")
    result = await model_test.test_model_connectivity({"base_url": _BASE_URL}, "m", 15_000)
    assert result.ok is False
    assert result.error is not None and "_Boom" in result.error


class _FakeConfigService:
    """替身配置服务：只支撑 get_file_section("model") 的提供商段读取。"""

    def __init__(self, providers: List[Dict[str, Any]]) -> None:
        self._providers = providers

    def get_file_section(self, file_stem: str, default: Any = None) -> Dict[str, Any]:
        return {"llm_providers": self._providers} if file_stem == "model" else ({} if default is None else default)


_SAVED_PROVIDERS = [{"name": "sheep", "base_url": _BASE_URL, "api_key": "sk-real"}]


def test_find_saved_provider_matches_by_name_stripped() -> None:
    config_service = _FakeConfigService(_SAVED_PROVIDERS)
    found = model_test.find_saved_provider(config_service, " sheep ")
    assert found is not None and found["api_key"] == "sk-real"


def test_find_saved_provider_returns_none_without_match() -> None:
    # 未注入配置服务 / 名字为空 / 落盘无同名提供商：一律 None（端点据此提示先保存）
    assert model_test.find_saved_provider(None, "sheep") is None
    assert model_test.find_saved_provider(_FakeConfigService(_SAVED_PROVIDERS), "") is None
    assert model_test.find_saved_provider(_FakeConfigService(_SAVED_PROVIDERS), "ghost") is None


async def test_endpoint_resolves_provider_from_saved_config(api, monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端：请求只报名字，探测拿到的连接参数来自已保存配置（真密钥）。"""
    captured: Dict[str, Any] = {}

    async def fake_test(provider_cfg: Dict[str, Any], model: str, timeout_ms: int) -> LLMModelTestResponse:
        captured.update({"provider_cfg": provider_cfg, "model": model})
        return LLMModelTestResponse(ok=True, latency_ms=42, model="echo-model", finish_reason="stop")

    monkeypatch.setattr(llm_api, "test_model_connectivity", fake_test)
    client = await api(config_service=_FakeConfigService(_SAVED_PROVIDERS))
    resp = await client.post(
        "/api/v1/llm/models/test",
        json={"provider": "sheep", "model": "m", "timeout_ms": 20_000},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "latency_ms": 42,
        "model": "echo-model",
        "finish_reason": "stop",
        "error": None,
    }
    # 连接参数是落盘整条目（含真密钥），前端传的名字不进探测
    assert captured["provider_cfg"] == _SAVED_PROVIDERS[0]
    assert captured["model"] == "m"


async def test_endpoint_reports_unsaved_provider(api, monkeypatch: pytest.MonkeyPatch) -> None:
    """提供商未保存：业务级失败 + 明确提示，不发起探测。"""

    async def fail_test(provider_cfg: Dict[str, Any], model: str, timeout_ms: int) -> LLMModelTestResponse:
        raise AssertionError("不应发起探测请求")

    monkeypatch.setattr(llm_api, "test_model_connectivity", fail_test)
    client = await api(config_service=_FakeConfigService(_SAVED_PROVIDERS))
    resp = await client.post("/api/v1/llm/models/test", json={"provider": "ghost", "model": "m"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "尚未保存" in body["error"]


async def test_endpoint_validates_model_identifier(api) -> None:
    client = await api(config_service=_FakeConfigService(_SAVED_PROVIDERS))
    resp = await client.post("/api/v1/llm/models/test", json={"provider": "sheep", "model": ""})
    assert resp.status_code == 422
