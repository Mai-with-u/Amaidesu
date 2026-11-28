"""MaicraftMcpProvider 幂等只读查询白名单与会话回收无感重试集成测试。"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.agents.minecraft.tools import MaicraftMcpProvider, is_idempotent_maicraft_call
from src.modules.mcp.client import McpClient
from src.modules.tools.models import ToolInvocation
from tests.modules.mcp.fakes import AnyConfig, FakeContent, FakeResult, FakeTool


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    [
        ("observe", {"what": "self"}, True),
        ("maicraft_observe", {"what": "scene"}, True),
        ("lookup", {"topic": "knowledge"}, True),
        ("events", {"wait_ms": 0}, True),
        ("goal", {"operation": "get", "goal_id": 1}, True),
        ("goal", {"operation": "list"}, True),
        ("maicraft_goal", {"operation": "GET", "goal_id": 2}, True),
        # 非幂等写操作与控制动作绝不自动重放
        ("execute", {"goal": {"ability": "build"}}, False),
        ("maicraft_execute", {"goal": {"ability": "chat", "parameters": {"message": "hi"}}}, False),
        ("goal", {"operation": "cancel", "goal_id": 1}, False),
        ("goal", {"operation": "answer", "goal_id": -1, "answer": "respawn"}, False),
        ("goal", {"operation": "pause", "goal_id": 1}, False),
        ("goal", {"operation": "resume", "goal_id": 1}, False),
        ("goal", {}, False),
        ("unknown_tool", {}, False),
    ],
)
def test_is_idempotent_maicraft_call_whitelist(
    tool_name: str,
    arguments: Dict[str, Any],
    expected: bool,
) -> None:
    """只放行 observe / lookup / events 与 goal(get|list)；写操作一律返回 False。"""
    assert is_idempotent_maicraft_call(tool_name, arguments) is expected


async def test_maicraft_provider_retries_readonly_on_session_terminated_but_never_replays_writes() -> None:
    """MaicraftMcpProvider：只读查询遇 Session terminated 无感重连重试 1 次成功；execute 与 goal(cancel) 绝不重放。"""
    # 可选重型依赖 mcp 仅在模拟会话终止协议异常时延迟加载
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    sessions: List[int] = []
    call_log: List[tuple[int, str, Dict[str, Any]]] = []
    fail_first_for_session: Dict[int, bool] = {}

    class _SessionEndpoint:
        def __init__(self, session_idx: int) -> None:
            self.session_idx = session_idx

        async def __aenter__(self) -> "_SessionEndpoint":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def list_tools(self) -> List[Any]:
            return [FakeTool("observe"), FakeTool("lookup"), FakeTool("goal"), FakeTool("execute")]

        async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
            call_log.append((self.session_idx, name, dict(arguments)))
            if fail_first_for_session.get(self.session_idx, False):
                fail_first_for_session[self.session_idx] = False
                raise McpError(ErrorData(code=32600, message="Session terminated"))
            return FakeResult(
                content=[FakeContent("ok")],
                structured={"ok": True, "data": {"tool": name, "session": self.session_idx}},
            )

    def _factory(transport: Any, message_handler: Any = None, init_timeout: float | None = None) -> _SessionEndpoint:
        del transport, message_handler, init_timeout
        idx = len(sessions) + 1
        sessions.append(idx)
        return _SessionEndpoint(idx)

    client = McpClient(
        name="maicraft",
        config=AnyConfig(),
        transport_factory=lambda: object(),
        client_factory=_factory,
    )

    async def _dummy_goal_lookup(_name: str, _args: Dict[str, Any]) -> Any:
        raise AssertionError("本测试不走 query_task")

    provider = MaicraftMcpProvider(
        client=client,
        server_name="maicraft",
        provider="maicraft",
        goal_lookup=_dummy_goal_lookup,
    )
    assert await provider.setup() == 4
    assert sessions == [1]

    # 1. 只读 observe 遇 Session terminated：自动重连到 session 2 并无感重试 1 次成功
    fail_first_for_session[1] = True
    obs_res = await provider.invoke(ToolInvocation(tool_name="maicraft_observe", arguments={"what": "self"}))
    assert obs_res.success is True
    assert obs_res.structured_content["data"] == {"tool": "observe", "session": 2}
    assert sessions == [1, 2]

    # 2. 只读 goal(get) 遇 Session terminated：自动重连到 session 3 并无感重试 1 次成功
    fail_first_for_session[2] = True
    get_res = await provider.invoke(
        ToolInvocation(tool_name="maicraft_goal", arguments={"operation": "get", "goal_id": 7})
    )
    assert get_res.success is True
    assert get_res.structured_content["data"] == {"tool": "goal", "session": 3}
    assert sessions == [1, 2, 3]

    # 3. 写操作 execute 遇 Session terminated：绝不自动重放，直接失败并标记断连
    fail_first_for_session[3] = True
    exec_calls_before = len([c for c in call_log if c[1] == "execute"])
    exec_res = await provider.invoke(
        ToolInvocation(tool_name="maicraft_execute", arguments={"goal": {"ability": "build"}})
    )
    assert exec_res.success is False
    assert client.connected is False
    assert len([c for c in call_log if c[1] == "execute"]) == exec_calls_before + 1
    assert sessions == [1, 2, 3]

    # 重连回 session 4，再验证 goal(cancel) 遇 Session terminated 同样绝不重放
    assert await client.connect() is True
    assert sessions == [1, 2, 3, 4]
    fail_first_for_session[4] = True
    cancel_calls_before = len([c for c in call_log if c[1] == "goal" and c[2].get("operation") == "cancel"])
    cancel_res = await provider.invoke(
        ToolInvocation(tool_name="maicraft_goal", arguments={"operation": "cancel", "goal_id": 7})
    )
    assert cancel_res.success is False
    assert client.connected is False
    assert len([c for c in call_log if c[1] == "goal" and c[2].get("operation") == "cancel"]) == cancel_calls_before + 1
    assert sessions == [1, 2, 3, 4]
    await provider.close()
