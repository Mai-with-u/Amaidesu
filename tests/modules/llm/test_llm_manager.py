"""LLMManager：三层配置装配与校验、单模型重试、选模策略、跨模型故障切换、清理与档位能力出口

客户端实现登记在 _CLIENT_DISPATCH 调度表（持有类引用），替身必须 patch 调度表本身。
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.llm.bootstrap import KNOWN_PROFILE_NAMES, ProfileNames, validate_profile_binding
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.engine import LLMManager, RetryConfig
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
        ProfileNames.MINECRAFT,
        ProfileNames.MINECRAFT_BUILDER,
        ProfileNames.VISION,
        ProfileNames.TTS,
        ProfileNames.SIMULATOR,
    }
    assert validate_profile_binding("planner") is None
    with pytest.raises(ValueError, match="unknown"):
        validate_profile_binding("unknown")


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
