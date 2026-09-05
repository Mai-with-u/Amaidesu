"""
工具层组合根装配入口

集中装配 Amaidesu 框架自带的"核心"工具包到指定 ``ToolRegistry``，
并把 ``@tool`` 装饰器挂起的 pending 工具刷入同一 registry。

## 设计要点
- **显式注入**——registry 由调用方构造并传入，本模块**不**触碰任何全局单例
  （``default_tool_registry()``）。生产代码请使用本入口；测试可通过
  ``bind_pending_tools`` 单独验证装饰器 pending 路径
- **非 TTS 包白名单**——``[tools.output.config].enabled`` 列表驱动非 TTS
  包（subtitle / vts / vrchat / warudo / obs）的装配；列表外的包一律不
  装配（避免"配置里没启用但工具进入 registry"的隐式行为）
- **按包隔离**——每个 ``register_<x>_tools`` 调用都被 ``try/except`` 包裹，
  单包失败（缺配置 / 缺 endpoint / 缺依赖）只记 ERROR 日志 + 报告里
  ``count=0``，不阻断其它包
- **无目录扫描**——绑定关系全部写死在下方 ``_NON_TTS_PACKAGES`` 中，
  避免动态 import / 文件系统扫描引入隐式耦合
- **与 L2 Provider 注册语义对齐**——输出包内已有 ``register_<x>_tools``
  函数，统一调用入口（不改 provider 内部）
- **不感知 Agent 层**——bootstrap 只调 provider 的 register 函数，
  不引入对 ``src/agents/**`` 的依赖（架构红线：工具不感知 Agent 层）

## 调用示例（main.py 装配阶段）

```python
from src.modules.tools import ToolRegistry
from src.modules.tools.bootstrap import bind_core_tools
from src.modules.tools.decorator import bind_pending_tools

registry = ToolRegistry()
report = bind_core_tools(registry, config=output_config_dict)
bind_pending_tools(registry)
```

## 当前覆盖的核心工具包

### 非 TTS 输出包（按 ``[tools.output.config].enabled`` 白名单）
| 配置键 | register 函数 | 描述 |
|---|---|---|
| ``"vts"`` | ``register_vts_tools`` | VTubeStudio 控制 |
| ``"vrchat"`` | ``register_vrchat_tools`` | VRChat OSC 桥接 |
| ``"warudo"`` | ``register_warudo_tools`` | Warudo 控制 |
| ``"obs"`` | ``register_obs_tools`` | OBS Studio 控制 |

注意：

- ``perception`` / ``content_engine`` 是 L2 DI 工具（需 ``ScreenCapture`` /
  ``ContentEngine`` 注入），无 ``register_*_tools`` 入口，由组合根在
  知道具体依赖后再 ``registry.register_provider(...)`` 注入——**不在本
  bootstrap 范围**（架构红线：工具不感知 Agent 层）

## TTS 与字幕装配说明

TTS 与字幕均为基础设施而非工具：

- TTS 由 ``src/modules/tts/build_tts_infrastructure`` 按核心 ``[tts]``
  段构造引擎实例并由 ``StreamerAgent`` 直接持有调用
- 字幕由 ``src/modules/subtitle/build_subtitle_infrastructure`` 按
  ``[tools.output.config.subtitle]`` 段构造 ``SubtitleService`` 实例并
  注入 ``StreamerAgent`` 直接调用

两者均不经 ``ToolRegistry``。本模块不介入 TTS / 字幕装配。
``[tools.output.config].enabled`` 列表中若残留 ``"subtitle"`` 旧条目，
将被静默忽略（白名单已无对应映射项；迁移期间保持冷启动不报错）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from src.modules.logging import get_logger
from src.modules.tools.registry import ToolRegistry

logger = get_logger("ToolBootstrap")


# (配置子键, 描述, 注册函数加载器)
# 注册函数加载器：返回 ``Callable[[registry, config], provider]``，
# 延迟 import 避免本模块被加载时拖入整条 output/* 依赖链。
_EntrySpec = Tuple[str, str, Callable[[], Callable[..., Any]]]


def _load_vts() -> Callable[..., Any]:
    from src.modules.tools.output.vts.vts_provider import register_vts_tools

    return register_vts_tools


def _load_vrchat() -> Callable[..., Any]:
    from src.modules.tools.output.vts.vrchat_provider import register_vrchat_tools

    return register_vrchat_tools


def _load_warudo() -> Callable[..., Any]:
    from src.modules.tools.output.warudo.warudo_provider import register_warudo_tools

    return register_warudo_tools


def _load_obs() -> Callable[..., Any]:
    from src.modules.tools.output.obs.obs_provider import register_obs_tools

    return register_obs_tools


# 非 TTS 输出包表（bootstrap 装配时按 [tools.output.config].enabled 白名单选择）
_NON_TTS_PACKAGES: List[_EntrySpec] = [
    ("vts", "VTubeStudio 控制", _load_vts),
    ("vrchat", "VRChat OSC 桥接", _load_vrchat),
    ("warudo", "Warudo 控制", _load_warudo),
    ("obs", "OBS Studio 控制", _load_obs),
]

# 对外保留的 _CORE_PACKAGES 兼容名（指代当前实现会装配的全部非 TTS 包）。
# 实际装配不再无条件遍历——由 enabled 列表门控；这里保留符号供外部
# 静态分析 / 类型检查不被打脸。运行时不再使用。
_CORE_PACKAGES: List[_EntrySpec] = list(_NON_TTS_PACKAGES)


def _resolve_provider_config(raw_config: Dict[str, Any], key: str) -> Dict[str, Any]:
    """从 ``bind_core_tools`` 顶层 config 中取出 ``key`` 子配置。

    若 ``raw_config`` 为空 / 缺失 ``key``，返回空 dict（下游 register 函数
    会按各自 ConfigSchema 走默认 / 抛错——抛错由调用方 try/except 兜底）。
    """
    if not raw_config:
        return {}
    sub = raw_config.get(key)
    if sub is None:
        return {}
    if not isinstance(sub, dict):
        logger.warning(f"bind_core_tools: config['{key}'] 不是 dict（type={type(sub).__name__}），忽略")
        return {}
    return dict(sub)


def _resolve_enabled_list(raw_config: Dict[str, Any]) -> List[str]:
    """从 ``[tools.output.config].enabled`` 列表读出非 TTS 包白名单。

    缺失或非列表时返回空列表（视作"什么也不装配"，与新契约一致——避免隐式
    行为漂移）。
    """
    enabled = raw_config.get("enabled") if isinstance(raw_config, dict) else None
    if not isinstance(enabled, list):
        return []
    return [str(x) for x in enabled if isinstance(x, (str,))]


def bind_core_tools(
    registry: ToolRegistry,
    config: Dict[str, Any] | None = None,
) -> Dict[str, int]:
    """绑定 Amaidesu 核心非 TTS 工具包到 ``registry``。

    装配规则：

    - **非 TTS 输出包**——按 ``config["enabled"]`` 白名单装配；非 TTS
      键未在列表中则不装配。TTS 引擎由核心 ``[tts]`` 段驱动装配，
      经 ``src.modules.tts.build_tts_infrastructure`` 注入到 Agent，
      不在本 bootstrap 范围。

    Args:
        registry: 目标注册器（由调用方构造并持有）
        config: ``[tools.output.config]`` 子配置，键名见
            ``_NON_TTS_PACKAGES``；传 ``None`` 表示所有非 TTS 包走"空配置"，
            由于新契约下白名单为空，将一律不装配

    Returns:
        ``{package_name: new_tool_count}`` 报告。失败 / 跳过包
        ``count=0``。
    """
    if not isinstance(registry, ToolRegistry):
        raise TypeError(f"bind_core_tools: registry 必须是 ToolRegistry 实例，得到 {type(registry).__name__}")

    raw_config: Dict[str, Any] = config if isinstance(config, dict) else {}

    report: Dict[str, int] = {}

    # --- 非 TTS 包按 enabled 白名单装配 ---
    enabled_list = _resolve_enabled_list(raw_config)

    for key, description, loader in _NON_TTS_PACKAGES:
        if key not in enabled_list:
            # 未在白名单里：跳过（不记 ERROR，预期行为）
            report[key] = 0
            continue

        provider_config = _resolve_provider_config(raw_config, key)
        before_count = len(registry)

        try:
            register_fn = loader()
        except Exception:  # noqa: BLE001 - 单包隔离边界
            logger.error(
                f"bind_core_tools: 加载 '{key}' 的 register 函数失败（{description}）",
                exc_info=True,
            )
            report[key] = 0
            continue

        try:
            register_fn(registry=registry, config=provider_config)
        except Exception as exc:  # noqa: BLE001 - 单包隔离边界
            logger.error(
                f"bind_core_tools: 绑定 '{key}' 失败（{description}）: {type(exc).__name__}: {exc}",
                exc_info=True,
            )
            report[key] = 0
            continue

        new_count = len(registry) - before_count
        report[key] = new_count
        if new_count > 0:
            logger.info(f"bind_core_tools: '{key}' 已绑定，新增 {new_count} 个工具（{description}）")
        else:
            logger.warning(f"bind_core_tools: '{key}' 调用成功但未新增任何工具（{description}）")

    return report


__all__ = [
    "bind_core_tools",
    "_NON_TTS_PACKAGES",
]
