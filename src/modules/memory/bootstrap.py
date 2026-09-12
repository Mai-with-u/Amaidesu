"""
记忆层组合根装配入口

装配原则：
- **显式注入**——``config`` 由调用方传入；``SQLiteStore`` / ``SimpleMemory``
  / ``ToolRegistry`` 全部由本模块在调用方提供的 config 上构造
- **类型检查 + fail-fast**：registry 必须是 ``ToolRegistry``；backend 必须是
  ``"simple"``；遇到未知值（含 ``"amemorix"``）直接 ``raise ValueError``
- **零全局单例**：本模块**不**接触 ``sqlite_store()`` 默认单例；注册表由
  组合根构造并经参数传入（``bind_memory_tools``）

## 调用示例（main.py 装配阶段）

```python
from src.modules.memory.bootstrap import build_memory_stack, bind_memory_tools
from src.modules.tools import ToolRegistry

store, memory = await build_memory_stack(config)  # 注入 [memory] / [sqlite]
registry = ToolRegistry()
bind_memory_tools(registry, memory)  # 注册 query_memory 工具
```

## 当前支持的 backend

| backend | 说明 | 路径 |
|---|---|---|
| ``"simple"`` | SQLite + 关键词召回（本模块） | → ``SimpleMemory(store)`` |

``"amemorix"`` / 其它值：``raise ValueError("...当前仅支持 backend='simple'")``，
fail-fast 由组合根捕获并退出（避免启动后才发现 memory 缺失）。

## 时间单位约定
- SQLiteStore.timeout 单位是**秒**（sqlite3.connect timeout）
- 本模块将 config ``busy_timeout_ms``（毫秒 int）按 ``/1000`` 转换为秒再传入
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from src.modules.logging import get_logger
from src.modules.memory.provider import MemoryProvider
from src.modules.memory.query_tool import build_query_memory_tool
from src.modules.memory.simple_memory import SimpleMemory
from src.modules.storage._default_path import DEFAULT_DB_PATH
from src.modules.storage.sqlite_store import SQLiteStore
from src.modules.tools.registry import ToolRegistry

logger = get_logger("MemoryBootstrap")


# 当前支持的 backend 集合（fail-fast 校验用；扩展时同步更新）
SUPPORTED_BACKENDS: Tuple[str, ...] = ("simple",)


def _resolve_db_path(sqlite_cfg: Dict[str, Any]) -> Path:
    """从 ``config['sqlite']``（storage.toml 顶层段）取出 db_path 并解析为绝对路径。

    - 缺失 / 非 str：回退到 ``DEFAULT_DB_PATH``（项目根 ``data/amaidesu.db``）
    - 相对路径：相对于**项目根**（``DEFAULT_DB_PATH.parent.parent``，即 data 之上）
      —— 不依赖 cwd，避免组合根在不同目录下启动时路径漂移
    """
    raw = sqlite_cfg.get("db_path") if isinstance(sqlite_cfg, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return DEFAULT_DB_PATH
    p = Path(raw)
    if p.is_absolute():
        return p
    # 相对路径 → 项目根；DEFAULT_DB_PATH = <ROOT>/data/amaidesu.db → ROOT = .parent.parent
    project_root = DEFAULT_DB_PATH.parent.parent
    return (project_root / raw).resolve()


def _resolve_busy_timeout_seconds(sqlite_cfg: Dict[str, Any]) -> float:
    """从 ``sqlite_cfg`` 中读 ``busy_timeout_ms``（毫秒 int）并转换为秒。

    SQLiteStore.timeout 单位是秒。schema 默认 5000ms → 5s。缺省/非 int 时
    走 5s 默认（与 schema 与 PRAGMA 对齐——旧注释"30s"是错的）。
    """
    raw = sqlite_cfg.get("busy_timeout_ms") if isinstance(sqlite_cfg, dict) else None
    if not isinstance(raw, (int, float)):
        return 5.0
    ms = int(raw)
    if ms < 0:
        return 5.0
    # 至少 1 秒（避免 0 秒导致繁忙等待死循环）；上限 300 秒防御异常配置
    seconds = max(1, min(ms // 1000, 300))
    return float(seconds)


async def build_memory_stack(config: Dict[str, Any]) -> Tuple[SQLiteStore, SimpleMemory]:
    """构造 SQLiteStore + SimpleMemory 记忆栈。

    Args:
        config: 扁平化后的 ConfigService dict；形如
            ``{"memory": {"backend": "simple", ...},
              "sqlite": {"db_path": "...", "busy_timeout_ms": 5000, ...}}``
            其中 ``memory.simple`` / ``memory.amemorix`` 可能是 ``None`` 不是 ``{}``，
            本函数对两者都安全（不读其内容——SimpleMemory 内部无配置项）。

    Returns:
        ``(store, memory)`` 元组：
        - ``store``：已 ``initialize()`` 的 SQLiteStore
        - ``memory``：已 ``initialize()`` 的 SimpleMemory（私有表已就绪）

    Raises:
        ValueError: ``config['memory']['backend']`` 不是 ``"simple"`` 时
        OSError: 数据库目录创建失败时
    """
    if not isinstance(config, dict):
        raise TypeError(f"build_memory_stack: config 必须是 dict，得到 {type(config).__name__}")

    # backend 校验（fail-fast；不通过不构造 store）
    raw_memory_cfg = config.get("memory")
    memory_cfg = raw_memory_cfg if isinstance(raw_memory_cfg, dict) else {}
    backend = memory_cfg.get("backend", "simple")
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(
            f"build_memory_stack: memory backend={backend!r} 不受支持；"
            f"当前仅支持 {SUPPORTED_BACKENDS}。"
            f"（A_Memorix 接入尚未完成）"
        )

    # SQLiteStore 配置解析 + 构造
    sqlite_cfg_raw = config.get("sqlite")
    sqlite_cfg = sqlite_cfg_raw if isinstance(sqlite_cfg_raw, dict) else {}

    db_path = _resolve_db_path(sqlite_cfg)
    busy_timeout_s = _resolve_busy_timeout_seconds(sqlite_cfg)

    store = SQLiteStore(db_path=db_path, timeout=busy_timeout_s)
    await store.initialize()
    logger.info(f"记忆栈：SQLiteStore 已初始化 path={db_path} timeout={busy_timeout_s}s")

    # SimpleMemory 装配 + 私有表初始化
    memory = SimpleMemory(store)
    await memory.initialize()
    logger.info("记忆栈：SimpleMemory 私有表（_memory_facts / _memory_profiles）已就绪")

    return store, memory


def bind_memory_tools(registry: ToolRegistry, memory: MemoryProvider) -> int:
    """把 ``query_memory`` 工具注册到 ``registry``。

    经 ``build_query_memory_tool(memory)`` 构造 Provider（简单工具正典路径
    样板，见 ``query_tool.py``）并 ``registry.register_provider(provider)``；
    返回新增工具数。

    **禁止**全局单例路径——生产装配必须由组合根构造注册表并传入，
    本函数把 provider 注入到那个注册表。

    Args:
        registry: 调用方构造的 ``ToolRegistry``（必须非 None）
        memory: ``MemoryProvider`` 实例（``SimpleMemory`` 或未来 ``AMemorixProvider``）

    Returns:
        本次新注册的工具数（重复注册则返回 0）。
    """
    if not isinstance(registry, ToolRegistry):
        raise TypeError(f"bind_memory_tools: registry 必须是 ToolRegistry 实例，得到 {type(registry).__name__}")
    if memory is None:
        raise ValueError("bind_memory_tools: memory 不能为 None；请先调用 build_memory_stack")

    provider = build_query_memory_tool(memory)
    new_count = registry.register_provider(provider)
    if new_count > 0:
        logger.info(f"记忆工具已绑定: provider='{provider.name}' 新增 {new_count} 个工具（query_memory）")
    else:
        # 重复注册不报错
        logger.debug(f"记忆工具 '{provider.name}' 注册未新增（可能已注册或 spec name 冲突）")
    return new_count


__all__ = ["build_memory_stack", "bind_memory_tools", "SUPPORTED_BACKENDS"]
