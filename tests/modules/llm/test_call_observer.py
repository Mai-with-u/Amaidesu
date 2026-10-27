"""CallObserver 直接单测：不跑完整 generate 流程，直达观测出库形状契约。

覆盖：请求参数快照 → 两账落库（llm_usage/llm_requests）→ 诊断正文出库
（payload 逐键形状）→ 每模型上下文水位快照；失败路径只写请求明细。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional

from src.modules.llm.call_observer import CallObserver
from src.modules.llm.context_meter import ContextBreakdown
from src.modules.llm.engine import LLMManager
from src.modules.llm.payload import GenerateRequest, Message, Response, TextPart, ToolCall, ToolSpec, Usage
from src.modules.storage.connection import SQLiteConnectionManager
from src.modules.storage.repos.llm import LLMRepo
from src.modules.storage.schema import build_schema_sql


class _FakeDiagnosticsWriter:
    """诊断写侧替身：直接缓存 append 记录与快照，无后台线程，测试可同步断言。"""

    def __init__(self) -> None:
        self.appended: List[Dict[str, Any]] = []
        self.snapshots: Dict[str, Dict[str, Any]] = {}

    def append(self, kind: str, record: Mapping[str, Any]) -> None:
        self.appended.append({"kind": kind, **dict(record)})

    def read_snapshot(self, kind: str, file_name: str) -> Dict[str, Any]:
        return self.snapshots.get(f"{kind}/{file_name}", {})

    def write_snapshot(self, kind: str, file_name: str, payload: Mapping[str, Any]) -> None:
        self.snapshots[f"{kind}/{file_name}"] = dict(payload)


def _make_observer(
    conn: SQLiteConnectionManager,
    writer: Optional[_FakeDiagnosticsWriter],
) -> CallObserver:
    return CallObserver(
        llm_repo=LLMRepo(conn),
        live_session_resolver=lambda: 7,
        diagnostics_writer=writer,
        model_index={"m1": ({"name": "m1", "model_identifier": "glm-x"}, "fake-provider")},
        model_prices={"glm-x": {"price_in": 1.0, "price_out": 2.0, "cache_price_in": 0.0, "cache": "none"}},
    )


def _make_request() -> GenerateRequest:
    return GenerateRequest(
        messages=[Message(role="user", parts=[TextPart(text="你好")])],
        system="你是直播助手",
        tools=[ToolSpec(name="wave", description="挥手", parameters={"type": "object"})],
        temperature=0.5,
    )


async def test_success_call_writes_usage_row_diagnostics_payload_and_snapshot(tmp_path) -> None:
    """成功调用一次出库：聚合账 + 请求明细同事务；诊断正文逐键形状；水位快照按模型覆写。"""
    conn = SQLiteConnectionManager(tmp_path / "t.db")
    conn.connection().executescript(build_schema_sql())
    writer = _FakeDiagnosticsWriter()
    observer = _make_observer(conn, writer)
    request = _make_request()
    result = Response(
        success=True,
        content="好的",
        model="upstream-alias-n",
        request_id="rid-1",
        usage=Usage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
        tool_calls=[ToolCall(id="t1", name="wave", arguments={})],
    )

    await observer.record_call(
        request_id="rid-1",
        profile_name="planner",
        model_name="m1",
        request_type="generate",
        result=result,
        kwargs={"request": request, "temperature": 0.5},
        start_time=time.time(),
    )

    # 两账：费用按引用名 m1 → 标识符 glm-x 查价表；场次归属取解析器值 7
    with conn.transaction() as db:
        usage_rows = [
            tuple(r)
            for r in db.execute(
                "SELECT model_name, total_tokens, live_session_id, provider_name FROM llm_usage"
            ).fetchall()
        ]
        detail_rows = [
            tuple(r) for r in db.execute("SELECT request_id, success, model_name FROM llm_requests").fetchall()
        ]
    assert usage_rows == [("m1", 120, 7, "fake-provider")]
    assert detail_rows == [("rid-1", 1, "m1")]

    # 诊断正文逐键形状：tool_calls 是 OpenAI 嵌套落盘契约，messages 是中立 dump（parts）
    assert len(writer.appended) == 1
    payload = writer.appended[0]
    assert payload["kind"] == "llm_requests"
    assert payload["request_id"] == "rid-1"
    assert payload["profile_name"] == "planner"
    assert payload["model_name"] == "m1"
    assert payload["success"] is True
    assert payload["response_content"] == "好的"
    assert payload["tool_calls"] == [{"id": "t1", "type": "function", "function": {"name": "wave", "arguments": {}}}]
    assert isinstance(payload["timestamp_ms"], int)
    assert payload["request_params"]["system"] == "你是直播助手"
    assert payload["request_params"]["temperature"] == 0.5
    assert payload["request_params"]["tools"][0]["name"] == "wave"
    for message in payload["request_params"]["messages"]:
        assert "content" not in message
        assert "parts" in message

    # 成功路径覆写每模型上下文水位快照（快照键 = 分段的模型引用名）
    snapshot = writer.snapshots["llm_context/latest.json"]
    assert set(snapshot["models"]) == {"m1"}


async def test_failed_call_writes_detail_only_and_diagnostics(tmp_path) -> None:
    """失败调用：只写请求明细行（success=0），llm_usage 无行；诊断正文照写（排障第一手材料）。"""
    conn = SQLiteConnectionManager(tmp_path / "t.db")
    conn.connection().executescript(build_schema_sql())
    writer = _FakeDiagnosticsWriter()
    observer = _make_observer(conn, writer)

    await observer.record_call(
        request_id="rid-2",
        profile_name="planner",
        model_name="",
        request_type="generate",
        result=Response(success=False, content=None, error="全部模型失败", request_id="rid-2"),
        kwargs={},
        start_time=time.time(),
    )

    with conn.transaction() as db:
        usage_rows = db.execute("SELECT * FROM llm_usage").fetchall()
        detail_rows = [
            tuple(r) for r in db.execute("SELECT request_id, success, model_name FROM llm_requests").fetchall()
        ]
    assert usage_rows == []
    assert detail_rows == [("rid-2", 0, "unknown")]
    # 失败无 usage：不估算分段，诊断正文仍落一条且 breakdown 为 None
    assert len(writer.appended) == 1
    assert writer.appended[0]["success"] is False
    assert writer.appended[0]["request_params"] == {}
    assert writer.appended[0]["breakdown"] is None
    assert "llm_context/latest.json" not in writer.snapshots


def test_context_breakdown_estimated_from_request_kwargs() -> None:
    """分段估算直入观测器：kwargs 带 request 时产出带 request_id/profile/model 的分段。"""
    observer = CallObserver(
        llm_repo=None,
        live_session_resolver=None,
        diagnostics_writer=None,
        model_index={},
        model_prices={},
    )
    result = Response(
        success=True,
        content="好的",
        model="m1",
        request_id="rid-3",
        usage=Usage(prompt_tokens=50, completion_tokens=10, total_tokens=60),
    )
    breakdown: Optional[ContextBreakdown] = observer._build_context_breakdown(
        request_id="rid-3",
        profile_name="planner",
        model_name="m1",
        result=result,
        kwargs={"request": _make_request()},
    )
    assert breakdown is not None
    assert breakdown.request_id == "rid-3"
    assert breakdown.profile_name == "planner"
    assert breakdown.model_name == "m1"


async def test_observer_price_table_stays_shared_across_setup() -> None:
    """观测器按引用持有价格表：setup（含重复调用）后与 manager 同表且内容跟随刷新。

    回归钉：setup 重置块曾把 ``_model_prices`` 重绑为新 dict，观测器持有的
    旧引用从此是永久空表，费用计算恒 0（与落账模型名污染同类的计价失准）。
    """
    config: Dict[str, Any] = {
        "llm_providers": [
            {"name": "p", "client_type": "openai", "base_url": "https://api.example.com/v1", "api_key": "test"}
        ],
        "llm_models": [
            {"name": "m", "model_identifier": "test-model", "api_provider": "p", "price_in": 1.0, "price_out": 2.0}
        ],
        "llm_profiles": {"planner": {"model_list": ["m"]}},
    }
    manager = LLMManager()
    try:
        await manager.setup(config)
        assert manager._observer._model_prices is manager._model_prices
        assert manager._observer._model_prices["test-model"]["price_in"] == 1.0

        config["llm_models"][0]["price_in"] = 3.0
        await manager.setup(config)
        assert manager._observer._model_prices is manager._model_prices
        assert manager._observer._model_prices["test-model"]["price_in"] == 3.0
    finally:
        await manager.cleanup()
