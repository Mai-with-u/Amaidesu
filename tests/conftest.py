"""
Pytest 全局共享 fixtures

这个文件定义了跨多个测试模块共享的 fixtures。
如果某个 fixture 只在特定 domain 使用，应该放在该 domain 的 conftest.py 中。
"""

import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator, List, Optional

import pytest
from loguru import logger as _loguru_logger

from src.modules.config.service import ConfigService
from src.modules.events.event_bus import EventBus
from src.modules.llm.engine import LLMManager


class _LoguruCapture:
    """内存里捕获 loguru 日志记录（项目用 loguru，pytest caplog 不适用）。

    每个测试用 fixture 实例化一次；测试结束后清理 sink，避免污染其它用例。
    """

    def __init__(self) -> None:
        self.records: List[dict] = []
        self._sink_id: Optional[int] = None

    def __enter__(self) -> "_LoguruCapture":
        def _sink(message) -> None:
            record = message.record
            self.records.append(
                {
                    "level": record["level"].name,
                    "message": record["message"],
                    "module": record["name"],
                }
            )

        self._sink_id = _loguru_logger.add(_sink, level="DEBUG")
        return self

    def __exit__(self, *exc_info) -> None:
        if self._sink_id is not None:
            _loguru_logger.remove(self._sink_id)
            self._sink_id = None


@pytest.fixture
def loguru_capture():
    """提供 _LoguruCapture 实例，自动管理 sink 生命周期。

    断言"某条日志是否/是否只出现一次"的测试用它——日志级别与去重都是
    契约的一部分，不测就等于没守住。

    注意：fixture 本身已经进入捕获，测试里直接读 ``loguru_capture.records``
    即可；再写 ``with loguru_capture as cap`` 会把同一个 sink 挂第二次，
    每条日志被记两遍，按条数断言就会失真。
    """
    cap = _LoguruCapture()
    with cap:
        yield cap


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
# 例如：tests/modules/collectors/conftest.py 提供 Collector 相关 fixtures
# （v2 后 src/stages/ 已删除，stages 路径不再存在）
