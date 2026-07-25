"""
Pytest 全局共享 fixtures

这个文件定义了跨多个测试模块共享的 fixtures。
如果某个 fixture 只在特定 domain 使用，应该放在该 domain 的 conftest.py 中。
"""

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest

from src.modules.config.service import ConfigService
from src.modules.events.event_bus import EventBus
from src.modules.llm.manager import LLMManager


@pytest.fixture
def temp_config_dir() -> Generator[Path, None, None]:
    """
    创建临时配置目录

    用于测试配置加载功能，避免污染实际配置文件。

    Yields:
        Path: 临时目录路径
    """
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    # 清理临时目录
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
async def event_bus() -> AsyncGenerator[EventBus, None]:
    """
    创建干净的 EventBus 实例

    每个测试获得独立的事件总线，避免测试间相互干扰。

    事件会在 Provider 订阅时自动注册，无需预先注册。

    Yields:
        EventBus: 新的事件总线实例
    """
    bus = EventBus()
    yield bus
    await bus.cleanup()


@pytest.fixture
def config_service(temp_config_dir: Path) -> ConfigService:
    """
    创建配置服务实例

    使用临时目录，避免影响实际配置。

    Args:
        temp_config_dir: 临时配置目录 fixture

    Returns:
        ConfigService: 配置服务实例
    """
    return ConfigService(base_dir=str(temp_config_dir))


@pytest.fixture
def llm_manager() -> LLMManager:
    """
    创建 LLM 管理器实例

    用于测试 LLM 相关功能，不连接真实后端。

    注意：使用前必须调用 await manager.setup(config)，其中 config 必须为
    新 provider-reference 格式：
        {
            "llm_providers": [{"name": "...", "client_type": "openai", ...}],
            "llm": {"provider": "...", "model": "..."},
            ...
        }

    Returns:
        LLMManager: 未初始化的 LLM 管理器实例
    """
    manager = LLMManager()
    manager._token_manager = None  # 显式标记未初始化，方便下游检查
    return manager


# Domain 特定的 fixtures 通过各 domain 的 conftest.py 提供
# 例如：tests/stages/input/conftest.py 提供 InputProvider 相关 fixtures
