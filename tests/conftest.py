"""
Pytest 全局共享 fixtures

这个文件定义了跨多个测试模块共享的 fixtures。
如果某个 fixture 只在特定 domain 使用，应该放在该 domain 的 conftest.py 中。
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from typing import AsyncGenerator, Generator

from src.core.event_bus import EventBus
from src.services.config.service import ConfigService
from src.services.llm.manager import LLMManager


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

    Yields:
        EventBus: 新的事件总线实例
    """
    bus = EventBus()
    await bus.initialize()
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

    Returns:
        LLMManager: LLM 管理器实例
    """
    return LLMManager()


# Domain 特定的 fixtures 通过各 domain 的 conftest.py 提供
# 例如：tests/domains/input/conftest.py 提供 InputProvider 相关 fixtures
