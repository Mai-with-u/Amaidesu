"""web 域搜索引擎层：能力协议 + 引擎注册表

search 侧（搜索结果从哪来）与 fetch 侧（网页正文怎么读）是两个
正交能力，各自独立登记与选择。新增引擎 = 新增实现类文件（实现
自身能力 + ``from_config`` 声明消费的配置字段）+ 在下方对应表登记一行。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Type, Union

from src.modules.web.engines.base import EngineConfig, EngineHttpError, FetchEngine, SearchEngine, SearchHit
from src.modules.web.engines.bing import BingSearchEngine
from src.modules.web.engines.deepseek import DeepSeekApiEngine
from src.modules.web.engines.local_fetch import LocalFetchEngine

if TYPE_CHECKING:
    # 仅类型标注用，运行期零依赖，避免引擎层反向 import 工具层
    from src.modules.web.search_provider import WebSearchProvider

# 全部在册引擎类（两侧共用）
_AnyEngineClass = Type[Union[BingSearchEngine, LocalFetchEngine, DeepSeekApiEngine]]

# search 侧注册表：配置键 → 引擎类
SEARCH_ENGINES: Dict[str, _AnyEngineClass] = {
    "bing": BingSearchEngine,
    "deepseek": DeepSeekApiEngine,
}

# fetch 侧注册表：配置键 → 引擎类
FETCH_ENGINES: Dict[str, _AnyEngineClass] = {
    "local": LocalFetchEngine,
    "deepseek": DeepSeekApiEngine,
}


def _build_engine(
    table: Dict[str, _AnyEngineClass],
    name: str,
    config: "WebSearchProvider.ConfigSchema",
) -> Union[BingSearchEngine, LocalFetchEngine, DeepSeekApiEngine]:
    cls = table.get(name)
    if cls is None:
        raise ValueError(f"未知引擎 '{name}'，可选：{sorted(table)}")
    return cls.from_config(config)


def build_search_engine(name: str, config: "WebSearchProvider.ConfigSchema") -> SearchEngine:
    """按配置键构造 search 侧引擎实例"""
    return _build_engine(SEARCH_ENGINES, name, config)


def build_fetch_engine(name: str, config: "WebSearchProvider.ConfigSchema") -> FetchEngine:
    """按配置键构造 fetch 侧引擎实例"""
    return _build_engine(FETCH_ENGINES, name, config)


__all__ = [
    "EngineConfig",
    "EngineHttpError",
    "FetchEngine",
    "SearchEngine",
    "SearchHit",
    "BingSearchEngine",
    "DeepSeekApiEngine",
    "LocalFetchEngine",
    "SEARCH_ENGINES",
    "FETCH_ENGINES",
    "build_search_engine",
    "build_fetch_engine",
]
