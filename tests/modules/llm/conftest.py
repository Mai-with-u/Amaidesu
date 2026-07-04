"""
LLM 测试专用 fixtures

所有 LLM 相关测试自动禁用请求历史记录写入，防止测试数据
污染本地历史文件（src/modules/llm/history/*.json）。
"""

import pytest


@pytest.fixture(autouse=True, scope="session")
def disable_request_history():
    """禁用 LLM 请求历史记录写入

    每次 LLM 调用（包括 mock 调用）都会被 request_history_manager 记录到磁盘。
    测试中产生的大量测试数据（Test/Hello/Describe this 等）会污染历史文件，
    且 JSON 写入在测试密集时有 I/O 竞争风险。
    """
    from src.modules.llm.request_history_manager import get_global_request_history_manager

    manager = get_global_request_history_manager()
    manager.enabled = False
    yield
    manager.enabled = True
