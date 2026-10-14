"""LLM 测试共用 fixtures。"""

import pytest

from src.modules.llm.request_history_manager import get_global_request_history_manager


@pytest.fixture(autouse=True, scope="session")
def disable_request_history():
    """禁用全局请求历史写入：mock 调用也会被记录到磁盘，污染本地历史文件。"""
    manager = get_global_request_history_manager()
    manager.enabled = False
    yield
    manager.enabled = True
