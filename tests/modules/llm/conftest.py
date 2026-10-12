"""
LLM 测试专用 fixtures

所有 LLM 相关测试自动禁用请求历史记录写入，防止测试数据
污染本地历史文件（src/modules/llm/history/*.json）。

新架构说明：
- LLMManager 使用 provider-reference 配置格式（llm_providers + role.provider）
- OpenAIClient 等客户端经 src.modules.llm.clients._CLIENT_DISPATCH 显式调度表查找
- 因此 patch("src.modules.llm.clients.openai.client.OpenAIClient") 不能拦截
  实际查询（调度表持有的是类对象引用，不是模块属性）
- 必须 patch _CLIENT_DISPATCH 字典中的 "openai" 键才能拦截 get_client_impl()
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


@pytest.fixture
def patch_client_registry():
    """
    返回一个 context manager 工厂，用于将 mock 类注入 _CLIENT_DISPATCH 调度表。

    使用示例：
        def test_something(patch_client_registry):
            mock_class = MagicMock(return_value=mock_backend)
            with patch_client_registry("openai", mock_class):
                # ... 触发 manager.setup() 时会使用 mock_class
                ...

    替代旧的 patch("src.modules.llm.clients.openai_client.OpenAIClient")
    模式（旧的 patch 不会拦截 registry 中的类引用）。
    """
    from contextlib import contextmanager

    from src.modules.llm.clients import _CLIENT_DISPATCH as _CLIENT_DISPATCH

    @contextmanager
    def _patcher(client_type: str, mock_class):
        # 使用 patch.dict 而非直接修改 _CLIENT_DISPATCH，确保 teardown 时自动还原
        from unittest.mock import patch

        with patch.dict(_CLIENT_DISPATCH, {client_type: mock_class}):
            yield

    return _patcher
