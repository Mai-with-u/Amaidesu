"""工具循环内回传思考：按模型开关附带、只发给产出它的模型、按收到时的格式写回。

MiniMax、Kimi、智谱、千问的文档要求多步工具调用时把思考带回；回传开关挂在
``[[llm_models]]`` 条目上，换到备用模型时别的模型的思考不发。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
import tomlkit

from src.modules.config.model_schemas import LLMModelConfig
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.context.tokens import TokenCounter
from src.modules.llm.client import BaseLLMClient
from src.modules.llm.clients import _CLIENT_DISPATCH
from src.modules.llm.clients.openai.client import OpenAIClient
from src.modules.llm.engine import LLMManager
from src.modules.llm.errors import FatalError
from src.modules.llm.payload import GenerateRequest, Message, Reasoning, Response, ToolCall
from src.modules.llm.reasoning import ReasoningParseMode, split_reasoning
from tests.config.helpers import tail_version
from tests.modules.llm.fakes import make_openai_client, sdk_reply


class ScriptedVendor(BaseLLMClient):
    """按模型标识回放响应并记下收到的请求；标识在 ``failing`` 里的直接报不可重试错误。"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.calls: List[tuple[str, GenerateRequest]] = []
        self.failing: set[str] = set()
        self.reply = Response(success=True, content="好")

    async def generate(
        self,
        request: GenerateRequest,
        *,
        model: str,
        temperature: Optional[float] = None,
        reasoning_effort: Optional[str] = None,
        on_delta: Any = None,
        interrupt_flag: Any = None,
    ) -> Response:
        self.calls.append((model, request))
        if model in self.failing:
            raise FatalError("模拟模型不可用")
        return self.reply

    async def generate_vision(self, request: GenerateRequest, images: List[Any], **kwargs: Any) -> Response:
        return Response(success=True, content="")


def _config(*, echo_m1: bool = True, echo_m2: bool = True) -> Dict[str, Any]:
    models = [
        {"name": "m1", "model_identifier": "id-1", "api_provider": "v", "reasoning_echo": echo_m1},
        {"name": "m2", "model_identifier": "id-2", "api_provider": "v", "reasoning_echo": echo_m2},
    ]
    return {
        "llm_providers": [{"name": "v", "client_type": "scripted", "base_url": "fake://", "api_key": "k"}],
        "llm_models": models,
        "llm_profiles": {
            "planner": {"model_list": ["m1"]},
            "minecraft": {"model_list": ["m2"]},
            "summary": {"model_list": ["m1", "m2"]},
        },
    }


async def _engine(**echo: bool) -> tuple[LLMManager, ScriptedVendor]:
    with patch.dict(_CLIENT_DISPATCH, {"scripted": ScriptedVendor}):
        manager = LLMManager()
        await manager.setup(_config(**echo))
    return manager, manager._provider_clients["v"]


def _history(reasoning: Any) -> List[Dict[str, Any]]:
    """一步工具调用的历史：assistant 发起调用（带上一步思考）+ 工具回执。"""
    call = {"id": "c1", "type": "function", "function": {"name": "look", "arguments": "{}"}}
    return [
        {"role": "user", "content": "看看周围"},
        {"role": "assistant", "content": "", "tool_calls": [call], "reasoning": reasoning},
        {"role": "tool", "tool_call_id": "c1", "content": "一片草地"},
    ]


def _sent_reasoning(request: GenerateRequest) -> Optional[Reasoning]:
    return next(m.reasoning for m in request.messages if m.role == "assistant")


@pytest.mark.parametrize(
    ("content", "native", "mode", "expected"),
    [
        ("答复", "想一想", ReasoningParseMode.AUTO, ("答复", "想一想", "field")),
        ("<think>想一想</think>答复", None, ReasoningParseMode.AUTO, ("答复", "想一想", "think_tag")),
        ("<think>想一想</think>答复", None, ReasoningParseMode.THINK_TAG, ("答复", "想一想", "think_tag")),
        ("答复", None, ReasoningParseMode.NATIVE, ("答复", None, None)),
        ("<think>想一想</think>答复", "想一想", ReasoningParseMode.NONE, ("<think>想一想</think>答复", None, None)),
    ],
)
def test_split_reasoning_records_where_the_thinking_came_from(content, native, mode, expected) -> None:
    """格式随思考一起记下，写回时才能还原成模型当初输出的样子。"""
    assert split_reasoning(content, native, mode) == expected


@pytest.mark.parametrize(
    ("reasoning", "content", "expected"),
    [
        (Reasoning(text="想", format="field"), "答复", {"content": "答复", "reasoning_content": "想"}),
        (Reasoning(text="想", format="think_tag"), "答复", {"content": "<think>想</think>答复"}),
        (Reasoning(text="想", format="think_tag"), None, {"content": "<think>想</think>"}),
    ],
)
def test_client_writes_reasoning_back_in_the_format_it_arrived(reasoning, content, expected) -> None:
    """独立字段写回 reasoning_content；从 <think> 标签切出来的包回正文开头。"""
    message = Message(
        role="assistant",
        parts=[content] if content else [],
        tool_calls=[ToolCall(id="c1", name="look")],
        reasoning=reasoning,
    )
    wire = OpenAIClient._message_to_openai(message)[0]
    assert {key: wire[key] for key in ("content", "reasoning_content") if key in wire} == expected
    assert wire["tool_calls"][0]["id"] == "c1"


async def test_client_reports_the_format_of_the_reasoning_it_parsed() -> None:
    """非流式响应：原生字段记为 field，正文 <think> 标签记为 think_tag，没有思考记为 None。"""
    request = GenerateRequest(messages=[Message(role="user", parts=["hi"])])
    for reply, expected in (
        (sdk_reply(content="答复", reasoning_content="想"), "field"),
        (sdk_reply(content="<think>想</think>答复"), "think_tag"),
        (sdk_reply(content="答复"), None),
    ):
        client, sdk = make_openai_client()
        sdk.chat.completions.create.return_value = reply
        response = await client.generate(request, model="test-model")
        assert response.reasoning_format == expected


async def test_engine_attaches_echo_only_when_the_producing_model_has_the_switch_on() -> None:
    """开关关闭时不附带，Agent 历史里就不会出现思考；格式未知同样不附带。"""
    manager, vendor = await _engine(echo_m2=False)
    vendor.reply = Response(success=True, content="好", reasoning_content="想", reasoning_format="field")

    on = await manager.generate("看看", profile="planner")
    off = await manager.generate("看看", profile="minecraft")
    vendor.reply = Response(success=True, content="好", reasoning_content="想")
    unknown = await manager.generate("看看", profile="planner")

    assert on.reasoning_echo == Reasoning(text="想", format="field", model="m1")
    assert off.reasoning_echo is None and off.reasoning_content == "想"
    assert unknown.reasoning_echo is None


async def test_history_reasoning_reaches_only_the_model_that_produced_it() -> None:
    """同一模型且开着开关才发；别的模型产出的、或当前模型关着开关，都不发。"""
    own = Reasoning(text="想", format="field", model="m1")
    manager, vendor = await _engine(echo_m2=True)
    await manager.generate(_history(own.model_dump()), profile="planner")
    await manager.generate(_history(own.model_dump()), profile="minecraft")
    assert _sent_reasoning(vendor.calls[0][1]) == own
    assert _sent_reasoning(vendor.calls[1][1]) is None

    manager, vendor = await _engine(echo_m1=False)
    await manager.generate(_history(own.model_dump()), profile="planner")
    assert _sent_reasoning(vendor.calls[0][1]) is None


async def test_failover_drops_foreign_reasoning_for_the_backup_model_only() -> None:
    """首选模型失败切到备用模型：首选那次照常带思考，备用那次不带，调用方的历史不被改动。"""
    manager, vendor = await _engine()
    vendor.failing = {"id-1"}
    history = _history(Reasoning(text="想", model="m1").model_dump())

    response = await manager.generate(history, profile="summary")

    assert response.success
    assert [(model, _sent_reasoning(request) is not None) for model, request in vendor.calls] == [
        ("id-1", True),
        ("id-2", False),
    ]
    assert history[1]["reasoning"]["text"] == "想"


async def test_malformed_history_reasoning_is_dropped_instead_of_failing_the_request() -> None:
    manager, vendor = await _engine()
    response = await manager.generate(_history({"format": "unknown"}), profile="planner")
    assert response.success and _sent_reasoning(vendor.calls[0][1]) is None


def test_token_counter_counts_reasoning_kept_in_history() -> None:
    """存进历史的思考会随请求发出，上下文预算要照实算上。"""
    counter = TokenCounter()
    bare = {"role": "assistant", "content": "好"}
    with_reasoning = {**bare, "reasoning": Reasoning(text="先查背包再去仓库拿铁锭" * 20, model="m1").model_dump()}
    assert counter.message(with_reasoning) > counter.message(bare)


def test_switch_defaults_on_and_old_installs_get_it_written_back(tmp_path: Path) -> None:
    """纯新增字段：旧安装补默认 true 不升版本，用户显式关闭的值保留。"""
    assert LLMModelConfig().reasoning_echo is True
    config_dir = tmp_path / "config"
    generate_default_configs(config_dir)
    path = config_dir / "model.toml"

    def _models() -> Any:
        return tomlkit.parse(path.read_text(encoding="utf-8-sig"))

    doc = _models()
    for model in doc["llm_models"]:
        model.pop("reasoning_echo", None)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8-sig")
    _, report = load_config_dir(config_dir)
    written = _models()
    assert all(model.get("reasoning_echo") is True for model in written["llm_models"])
    assert written["meta"]["version"] == tail_version("model.toml") and not report.has_drift

    written["llm_models"][0]["reasoning_echo"] = False
    path.write_text(tomlkit.dumps(written), encoding="utf-8-sig")
    load_config_dir(config_dir)
    assert _models()["llm_models"][0]["reasoning_echo"] is False
