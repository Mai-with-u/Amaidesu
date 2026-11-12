"""LLMManager：三层配置装配与校验、单模型重试、选模策略、跨模型故障切换、清理与档位能力出口

客户端实现登记在 _CLIENT_DISPATCH 调度表（持有类引用），替身必须 patch 调度表本身。
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.config.model_schemas import LLMProfilesConfig
from src.modules.llm.bootstrap import KNOWN_PROFILE_NAMES, ProfileNames, validate_profile_binding
from src.modules.events.event_bus import EventBus
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.core import CoreErrorPayload
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import ERROR_ALERT_THROTTLE_MS, LLMManager, RetryConfig
from src.modules.llm.errors import FatalError
from src.modules.llm.payload import Response, Usage


def _ok(content: str = "Test response", model: str = "gpt-4o-mini", **kwargs: Any) -> Response:
    usage = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return Response(success=True, content=content, model=model, usage=usage, **kwargs)


def _backend(generate: Any = None) -> MagicMock:
    backend = MagicMock()
    backend.generate = generate or AsyncMock(return_value=_ok())
    backend.cleanup = AsyncMock()
    return backend


def _config(models: Dict[str, Any], profiles: Dict[str, Any], providers=("p1",), **provider_extra: Any) -> dict:
    """models: 名称 → model_identifier 或完整模型字段（api_provider 缺省为首个 provider）。"""
    model_list = []
    for name, spec in models.items():
        fields = {"model_identifier": spec} if isinstance(spec, str) else dict(spec)
        model_list.append({"name": name, "api_provider": providers[0], **fields})
    return {
        "llm_providers": [{"name": p, "client_type": "openai", **provider_extra} for p in providers],
        "llm_models": model_list,
        "llm_profiles": {
            name: profile if isinstance(profile, dict) else {"model_list": profile}
            for name, profile in profiles.items()
        },
    }


async def _setup(config: dict, *backends: MagicMock, retry: RetryConfig | None = None) -> LLMManager:
    manager = LLMManager()
    if retry is not None:
        manager._retry_config = retry
    factory = MagicMock(side_effect=list(backends)) if len(backends) > 1 else MagicMock(return_value=backends[0])
    with patch.dict(_CLIENT_DISPATCH, {"openai": factory}):
        await manager.setup(config)
    return manager


async def test_setup_builds_one_client_per_provider_and_price_table() -> None:
    config = _config(
        {
            "free": "free-model-id",
            "paid": {"model_identifier": "paid-model-id", "price_in": 1.0, "price_out": 2.0},
        },
        {"planner": ["paid"], "replyer": ["free"], "vision": ["free"]},
        api_key="custom-key",
        base_url="https://custom.api.com/v1",
    )
    factory = MagicMock(return_value=_backend())
    manager = LLMManager()
    with patch.dict(_CLIENT_DISPATCH, {"openai": factory}):
        await manager.setup(config)

    # profile 维度不再各自构造 client；client 只拿 provider 配置，model 由调用方每次传入
    assert factory.call_count == 1
    client_config = factory.call_args[0][0]
    assert (client_config["api_key"], client_config["base_url"]) == ("custom-key", "https://custom.api.com/v1")
    assert "model" not in client_config
    assert list(manager._providers) == ["p1"] and len(manager._models) == 2
    assert set(manager._profiles) == {"planner", "replyer", "vision"}
    assert set(manager._model_prices) == {"paid-model-id"}
    assert manager._model_prices["paid-model-id"]["price_in"] == 1.0


@pytest.mark.parametrize(
    ("config", "match"),
    [
        (_config({"m1": "id1"}, {"planner": ["m1"]}) | {"llm_providers": []}, "llm_providers"),
        (
            _config({"m1": "id1"}, {"planner": ["m1"]})
            | {"llm_providers": [{"name": "test", "client_type": "unknown_backend"}]},
            "未注册的客户端类型",
        ),
        (
            _config({"m1": {"model_identifier": "id1", "api_provider": "nonexistent"}}, {"planner": ["m1"]}),
            "llm_models",
        ),
        (_config({"m1": "id1"}, {"planner": ["m1"]}, providers=("p1", "p1")), "重复的 provider name"),
        (_config({"m1": "id1"}, {"planner": ["nonexistent"]}), "引用未知 model"),
        (_config({"m1": "id1"}, {"planner": []}), "model_list 为空"),
        (
            _config({"m1": "id1"}, {"planner": {"model_list": ["m1"], "selection_strategy": {"name": "bogus"}}}),
            "selection_strategy",
        ),
    ],
)
async def test_setup_fails_fast_on_invalid_config(config: dict, match: str) -> None:
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=_backend())}):
        with pytest.raises(ValueError, match=match):
            await LLMManager().setup(config)


async def test_generate_with_unknown_profile_raises() -> None:
    manager = await _setup(_config({"m1": "id1"}, {"planner": ["m1"]}), _backend())
    with pytest.raises(ValueError, match="未配置"):
        await manager.generate("Test", profile="unknown_profile")


async def test_single_model_retries_until_success_or_exhaustion() -> None:
    calls = 0

    async def flaky(**kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise Exception("API Error")
        return _ok("Success after retries")

    config = _config({"m1": "id1"}, {"planner": ["m1"]})
    # 默认 max_retries=3：失败 2 次后第 3 次成功
    manager = await _setup(config, _backend(flaky), retry=RetryConfig(base_delay=0.01))
    assert (await manager.generate("Test", profile="planner")).content == "Success after retries"
    assert calls == 3

    calls = 0

    async def always_fail(**kwargs):
        nonlocal calls
        calls += 1
        raise Exception("Persistent API Error")

    manager = await _setup(config, _backend(always_fail), retry=RetryConfig(max_retries=2, base_delay=0.01))
    response = await manager.generate("Test", profile="planner")
    assert response.success is False and "Persistent API Error" in response.error
    assert calls == 2  # 首次 + 1 次重试


async def test_sequential_and_balance_selection() -> None:
    backend = _backend()
    config = _config(
        {"m1": "id-m1", "m2": "id-m2"},
        {
            "planner": {"model_list": ["m1", "m2"], "selection_strategy": {"name": "sequential"}},
            "replyer": {"model_list": ["m1", "m2"], "selection_strategy": {"name": "balance"}},
        },
    )
    manager = await _setup(config, backend)
    for _ in range(3):
        await manager.generate("Test", profile="planner")
    for _ in range(2):
        await manager.generate("Test", profile="replyer")

    used = [c.kwargs["model"] for c in backend.generate.call_args_list]
    # sequential 始终取首位；balance 取调用最少者，并列时取靠前的
    assert used == ["id-m1"] * 3 + ["id-m1", "id-m2"]


async def test_random_selection_is_reproducible_with_seed() -> None:
    config = _config(
        {"m1": "id-m1", "m2": "id-m2", "m3": "id-m3"},
        {"planner": {"model_list": ["m1", "m2", "m3"], "selection_strategy": {"name": "random", "seed": 42}}},
    )
    chosen = []
    for _ in range(2):
        backend = _backend()
        await (await _setup(config, backend)).generate("Test", profile="planner")
        chosen.append(backend.generate.call_args.kwargs["model"])
    assert chosen[0] == chosen[1]


async def test_failover_advances_to_next_model_and_reports_all_failures() -> None:
    config = _config({"m1": "id-m1", "m2": "id-m2"}, {"planner": ["m1", "m2"]}, max_retries=0)
    calls: Dict[str, int] = {}

    async def first_fails(**kwargs):
        model = kwargs["model"]
        calls[model] = calls.get(model, 0) + 1
        if model == "id-m1":
            raise Exception("upstream error")
        return _ok("success from m2", model="id-m2")

    response = await (await _setup(config, _backend(first_fails))).generate("Test", profile="planner")
    # 引擎把响应 model 归一为引用名（m2），上游自报的标识符（id-m2）不出引擎
    assert (response.content, response.model) == ("success from m2", "m2")
    assert calls == {"id-m1": 1, "id-m2": 1}

    async def always_fail(**kwargs):
        raise Exception(f"fail-{kwargs['model']}")

    response = await (await _setup(config, _backend(always_fail))).generate("Test", profile="planner")
    assert response.success is False
    assert "全部模型失败" in response.error and "m1" in response.error and "m2" in response.error


async def test_cleanup_dedups_shared_clients_and_isolates_errors() -> None:
    shared = _backend()
    manager = await _setup(_config({"m1": "id1"}, {"planner": ["m1"], "replyer": ["m1"]}), shared)
    assert manager._provider_clients["p1"] is shared
    await manager.cleanup()
    assert shared.cleanup.await_count == 1

    failing, ok = _backend(), _backend()
    failing.cleanup = AsyncMock(side_effect=Exception("Cleanup error"))
    config = _config(
        {"m1": "id1", "m2": {"model_identifier": "id2", "api_provider": "p2"}},
        {"planner": ["m1"], "replyer": ["m2"]},
        providers=("p1", "p2"),
    )
    manager = await _setup(config, failing, ok)
    assert manager._provider_clients == {"p1": failing, "p2": ok}
    await manager.cleanup()
    ok.cleanup.assert_awaited_once()
    assert manager._provider_clients == {} and manager._providers == {}


def test_profile_names_closed_set() -> None:
    assert KNOWN_PROFILE_NAMES == {
        ProfileNames.PLANNER,
        ProfileNames.REPLYER,
        ProfileNames.SUMMARY,
        ProfileNames.SETTLE,
        ProfileNames.MINECRAFT,
        ProfileNames.MINECRAFT_BUILDER,
        ProfileNames.VISION,
        ProfileNames.TTS,
        ProfileNames.SIMULATOR,
    }
    assert validate_profile_binding("planner") is None
    with pytest.raises(ValueError, match="unknown"):
        validate_profile_binding("unknown")


def test_profile_names_constants_match_config_schema_fields() -> None:
    """ProfileNames 常量与配置 schema 字段双向钉死。

    常量类是手抄字面量：schema 改名/增删字段而常量没跟上，或常量抄错，
    任一方向漂移都会在此暴露（封闭集合的成员权威是 ``LLMProfilesConfig``）。
    """
    constants = {
        value for key, value in vars(ProfileNames).items() if not key.startswith("_") and isinstance(value, str)
    }
    assert constants == set(LLMProfilesConfig.model_fields)
    assert constants == KNOWN_PROFILE_NAMES


async def test_generate_audio_dispatches_with_voice_and_format() -> None:
    backend = _backend()
    audio = _ok("你好", model="qwen3-omni", audio_base64="QUJD", audio_format="wav")
    backend.generate_audio = AsyncMock(return_value=audio)
    manager = await _setup(_config({"omni": "qwen3-omni"}, {"tts": ["omni"], "summary": ["omni"]}), backend)

    # 缺省走 tts 档位；音频字段经记账往返无损
    result = await manager.generate_audio("你好", voice="Chelsie")
    assert (result.audio_base64, result.audio_format, result.content) == ("QUJD", "wav", "你好")
    kwargs = backend.generate_audio.await_args.kwargs
    assert (kwargs["model"], kwargs["voice"], kwargs["audio_format"]) == ("qwen3-omni", "Chelsie", "wav")

    assert (await manager.generate_audio("x", profile="summary", voice="Chelsie")).success is True
    # 音色缺失在进入 failover 前快速失败
    backend.generate_audio.reset_mock()
    with pytest.raises(ValueError, match="voice"):
        await manager.generate_audio("你好", voice="")
    backend.generate_audio.assert_not_awaited()


async def test_profile_capabilities_visual_and_context_window() -> None:
    """visual 取 failover 首位模型；上下文窗口取名单内已配置窗口的最小值（故障切换可能落到任一模型）。"""
    config = _config(
        {
            "vision-model": {"model_identifier": "vision-id", "visual": True, "context_window": 1_000_000},
            "small": {"model_identifier": "small-id", "context_window": 128_000},
            "unknown": "unknown-id",
        },
        {
            "planner": ["vision-model", "small", "unknown"],
            "replyer": ["unknown", "vision-model"],
            "summary": ["unknown"],
        },
    )
    manager = await _setup(config, _backend())

    assert [manager.is_profile_visual(p) for p in ("planner", "replyer", "no-such")] == [True, False, False]
    windows = [manager.get_profile_context_window(p) for p in ("planner", "replyer", "summary", "no-such")]
    assert windows == [128_000, 1_000_000, 0, 0]


# ---------------- 运行告警：全部模型失败旁路发布 core.error ----------------


def _capture_core_error(bus: EventBus) -> list[CoreErrorPayload]:
    """收集 core.error 告警（后台扇出，断言前先 wait_until_drained）。"""
    alerts: list[CoreErrorPayload] = []

    async def _capture(event_name: str, payload: CoreErrorPayload, source: str) -> None:
        alerts.append(payload)

    bus.on(CoreEvents.CORE_ERROR, _capture, model_class=CoreErrorPayload)
    return alerts


async def _setup_with_alert_channel(config: dict, backend: MagicMock, bus: EventBus, clock: Any) -> LLMManager:
    """装配接了告警通道（event_bus + 注入时钟）的引擎。"""
    manager = LLMManager(event_bus=bus, clock=clock.now_ms)
    manager._retry_config = RetryConfig(max_retries=0)
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=backend)}):
        await manager.setup(config)
    return manager


async def test_all_models_failed_emits_core_error_alert(event_bus: EventBus, fake_clock: Any) -> None:
    """全部模型失败：发布一条 core.error，message 写清 profile、尝试过的模型与原因。"""
    config = _config({"m1": "id-m1"}, {"planner": ["m1"]})
    alerts = _capture_core_error(event_bus)

    async def always_fail(**kwargs):
        raise Exception("HTTP 402 insufficient balance")

    manager = await _setup_with_alert_channel(config, _backend(always_fail), event_bus, fake_clock)
    response = await manager.generate("Test", profile="planner")
    await event_bus.wait_until_drained()

    assert response.success is False
    assert len(alerts) == 1
    message = alerts[0].message or ""
    assert "全部模型失败" in message and "planner" in message and "m1" in message and "402" in message


async def test_all_models_failed_alert_throttled_within_window(event_bus: EventBus, fake_clock: Any) -> None:
    """同类告警 5 分钟窗口内只发布第一条；拨过窗口后再次全部失败才发第二条。"""
    config = _config({"m1": "id-m1"}, {"planner": ["m1"]})
    alerts = _capture_core_error(event_bus)

    async def always_fail(**kwargs):
        raise Exception("upstream down")

    manager = await _setup_with_alert_channel(config, _backend(always_fail), event_bus, fake_clock)
    assert (await manager.generate("Test", profile="planner")).success is False
    assert (await manager.generate("Test", profile="planner")).success is False
    await event_bus.wait_until_drained()
    assert len(alerts) == 1

    fake_clock.tick(ERROR_ALERT_THROTTLE_MS)
    assert (await manager.generate("Test", profile="planner")).success is False
    await event_bus.wait_until_drained()
    assert len(alerts) == 2


async def test_failover_backup_success_does_not_alert(event_bus: EventBus, fake_clock: Any) -> None:
    """单模型失败但备胎接住：不发 core.error（只有全部模型失败才告警）。"""
    config = _config({"m1": "id-m1", "m2": "id-m2"}, {"planner": ["m1", "m2"]})
    alerts = _capture_core_error(event_bus)

    async def first_fails(**kwargs):
        if kwargs["model"] == "id-m1":
            raise Exception("upstream error")
        return _ok("success from m2", model="id-m2")

    manager = await _setup_with_alert_channel(config, _backend(first_fails), event_bus, fake_clock)
    response = await manager.generate("Test", profile="planner")
    await event_bus.wait_until_drained()

    assert response.success is True and response.model == "m2"
    assert alerts == []


# ---------------- 运行告警：账户级错误切换成功旁路发布 core.error ----------------


@pytest.mark.parametrize("status_code", [401, 402, 403])
async def test_account_error_failover_emits_core_error_alert(
    event_bus: EventBus, fake_clock: Any, status_code: int
) -> None:
    """账户级错误切换到备胎成功：发布一条 core.error，文案含状态码、失败模型与切换目标。"""
    config = _config({"m1": "id-m1", "m2": "id-m2"}, {"planner": ["m1", "m2"]})
    alerts = _capture_core_error(event_bus)

    async def m1_account_error(**kwargs):
        if kwargs["model"] == "id-m1":
            raise FatalError("upstream rejected", status_code=status_code)
        return _ok("success from m2", model="id-m2")

    manager = await _setup_with_alert_channel(config, _backend(m1_account_error), event_bus, fake_clock)
    response = await manager.generate("Test", profile="planner")
    await event_bus.wait_until_drained()

    assert response.success is True and response.model == "m2"
    assert len(alerts) == 1
    message = alerts[0].message or ""
    assert f"HTTP {status_code}" in message
    assert "m1" in message and "已切换 m2" in message
    assert "充值" in message


async def test_fatal_400_failover_does_not_alert(event_bus: EventBus, fake_clock: Any) -> None:
    """400 参数错误切换不发账户级告警：它救不回来，最终由"全部模型失败"告警覆盖。"""
    config = _config({"m1": "id-m1", "m2": "id-m2"}, {"planner": ["m1", "m2"]})
    alerts = _capture_core_error(event_bus)

    async def m1_bad_request(**kwargs):
        if kwargs["model"] == "id-m1":
            raise FatalError("invalid parameter", status_code=400)
        return _ok("success from m2", model="id-m2")

    manager = await _setup_with_alert_channel(config, _backend(m1_bad_request), event_bus, fake_clock)
    response = await manager.generate("Test", profile="planner")
    await event_bus.wait_until_drained()

    assert response.success is True
    assert alerts == []


async def test_account_error_alert_throttled_within_window(event_bus: EventBus, fake_clock: Any) -> None:
    """账户级切换告警 5 分钟窗口内只发第一条；拨过窗口后才发下一条。"""
    config = _config({"m1": "id-m1", "m2": "id-m2"}, {"planner": ["m1", "m2"]})
    alerts = _capture_core_error(event_bus)

    async def m1_account_error(**kwargs):
        if kwargs["model"] == "id-m1":
            raise FatalError("insufficient balance", status_code=402)
        return _ok("success from m2", model="id-m2")

    manager = await _setup_with_alert_channel(config, _backend(m1_account_error), event_bus, fake_clock)
    await manager.generate("Test", profile="planner")
    # 402 后每笔请求都会再 402 再切换：窗口内重复发生只发第一条
    await manager.generate("Test", profile="planner")
    await event_bus.wait_until_drained()
    assert len(alerts) == 1

    fake_clock.tick(ERROR_ALERT_THROTTLE_MS)
    await manager.generate("Test", profile="planner")
    await event_bus.wait_until_drained()
    assert len(alerts) == 2


async def test_account_error_alert_without_event_bus(fake_clock: Any) -> None:
    """事件总线未挂载：账户级告警只记日志，切换照常成功不炸。"""
    config = _config({"m1": "id-m1", "m2": "id-m2"}, {"planner": ["m1", "m2"]})

    async def m1_account_error(**kwargs):
        if kwargs["model"] == "id-m1":
            raise FatalError("insufficient balance", status_code=402)
        return _ok("success from m2", model="id-m2")

    manager = LLMManager(clock=fake_clock.now_ms)
    manager._retry_config = RetryConfig(max_retries=0)
    with patch.dict(_CLIENT_DISPATCH, {"openai": MagicMock(return_value=_backend(m1_account_error))}):
        await manager.setup(config)
    response = await manager.generate("Test", profile="planner")
    assert response.success is True and response.model == "m2"
