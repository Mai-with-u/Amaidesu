"""
工具层组合根装配入口

集中装配 Amaidesu 框架自带的"核心"工具包到指定 ``ToolRegistry``，
并把 ``@tool`` 装饰器挂起的 pending 工具刷入同一 registry。

## 设计要点
- **显式注入**——registry 由调用方构造并传入，本模块**不**触碰任何全局单例
  （``default_tool_registry()``）。生产代码请使用本入口；测试可通过
  ``bind_pending_tools`` 单独验证装饰器 pending 路径
- **按包隔离**——每个 ``register_*_tools`` 调用都被 ``try/except`` 包裹，
  单包失败（缺配置 / 缺 endpoint / 缺依赖）只记 ERROR 日志 + 报告里
  ``count=0``，不阻断其它包
- **无目录扫描**——绑定关系全部写死在下方 ``_CORE_PACKAGES`` 中，
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
report = bind_core_tools(registry, config={"edge_tts": {...}, "vts": {...}, ...})
bind_pending_tools(registry)  # 刷入 @tool pending
```

## 当前覆盖的核心工具包

| 包 | register 函数 | config 子键 |
|---|---|---|
| ``output.tts.edge_tts`` | ``register_edge_tts_tools`` | ``"edge_tts"`` |
| ``output.tts.gptsovits`` | ``register_gptsovits_tools`` | ``"gptsovits"`` |
| ``output.tts.omni_tts`` | ``register_omni_tts_tools`` | ``"omni_tts"`` |
| ``output.tts.voicebox`` | ``register_voicebox_tools`` | ``"voicebox"`` |
| ``output.vts`` | ``register_vts_tools`` | ``"vts"`` |
| ``output.vts`` | ``register_vrchat_tools`` | ``"vrchat"`` |
| ``output.warudo`` | ``register_warudo_tools`` | ``"warudo"`` |
| ``output.obs`` | ``register_obs_tools`` | ``"obs"`` |
| ``output.subtitle`` | ``register_subtitle_tools`` | ``"subtitle"`` |

注意：
- ``perception`` / ``content_engine`` 是 L2 DI 工具（需 ``ScreenCapture`` /
  ``ContentEngine`` 注入），无 ``register_*_tools`` 入口，由组合根在
  知道具体依赖后再 ``registry.register_provider(...)`` 注入——**不在本
  bootstrap 范围**（架构红线：工具不感知 Agent 层）
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


def _load_edge_tts() -> Callable[..., Any]:
    from src.modules.tools.output.tts.edge_tts_tool import register_edge_tts_tools

    return register_edge_tts_tools


def _load_gptsovits() -> Callable[..., Any]:
    from src.modules.tools.output.tts.gptsovits_tool import register_gptsovits_tools

    return register_gptsovits_tools


def _load_omni_tts() -> Callable[..., Any]:
    from src.modules.tools.output.tts.omni_tts_tool import register_omni_tts_tools

    return register_omni_tts_tools


def _load_voicebox() -> Callable[..., Any]:
    from src.modules.tools.output.tts.voicebox_tool import register_voicebox_tools

    return register_voicebox_tools


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


def _load_subtitle() -> Callable[..., Any]:
    from src.modules.tools.output.subtitle.subtitle_provider import register_subtitle_tools

    return register_subtitle_tools


_CORE_PACKAGES: List[_EntrySpec] = [
    # (config_key, description, loader)
    ("edge_tts", "Edge TTS 语音合成", _load_edge_tts),
    ("gptsovits", "GPT-SoVITS 本地 TTS", _load_gptsovits),
    ("omni_tts", "Omni TTS", _load_omni_tts),
    ("voicebox", "Voicebox TTS", _load_voicebox),
    ("vts", "VTubeStudio 控制", _load_vts),
    ("vrchat", "VRChat OSC 桥接", _load_vrchat),
    ("warudo", "Warudo 控制", _load_warudo),
    ("obs", "OBS Studio 控制", _load_obs),
    ("subtitle", "字幕 GUI 服务", _load_subtitle),
]


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


def bind_core_tools(
    registry: ToolRegistry,
    config: Dict[str, Any] | None = None,
) -> Dict[str, int]:
    """绑定 Amaidesu 核心工具包到 ``registry``。

    按 ``_CORE_PACKAGES`` 顺序逐个调用 ``register_<x>_tools(registry, config)``：
    - 成功：从 ``len(registry)`` 前后差得到新注册工具数
    - 失败（缺 config / 缺依赖 / provider 构造异常）：记 ERROR 日志，
      计入报告 ``{pkg: 0}``，继续处理下一个包
    - 未识别 / 任何代码 bug：同上

    Args:
        registry: 目标注册器（由调用方构造并持有）
        config: 每包子配置，键名见 ``_CORE_PACKAGES``；传 ``None`` 表示
                所有包走"空配置"，多数包将失败并被记录

    Returns:
        ``{package_name: new_tool_count}`` 报告。失败包 ``count=0``。
    """
    if not isinstance(registry, ToolRegistry):
        raise TypeError(f"bind_core_tools: registry 必须是 ToolRegistry 实例，得到 {type(registry).__name__}")

    raw_config: Dict[str, Any] = config if isinstance(config, dict) else {}

    report: Dict[str, int] = {}
    for key, description, loader in _CORE_PACKAGES:
        provider_config = _resolve_provider_config(raw_config, key)
        before_count = len(registry)

        # 1) 加载 register 函数（自身可能因缺依赖 / import 错误失败）
        try:
            register_fn = loader()
        except Exception as exc:  # noqa: BLE001 - 单包隔离边界
            logger.error(
                f"bind_core_tools: 加载 '{key}' 的 register 函数失败（{description}）: {type(exc).__name__}: {exc}",
                exc_info=True,
            )
            report[key] = 0
            continue

        # 2) 调用 register 函数（构造 provider / setup / register 都可能失败）
        try:
            register_fn(registry=registry, config=provider_config)
        except Exception as exc:  # noqa: BLE001 - 单包隔离边界
            logger.error(
                f"bind_core_tools: 绑定 '{key}' 失败（{description}）: {type(exc).__name__}: {exc}",
                exc_info=True,
            )
            report[key] = 0
            continue

        # 3) 计算本次新注册的工具数
        after_count = len(registry)
        new_count = after_count - before_count
        report[key] = new_count
        if new_count > 0:
            logger.info(f"bind_core_tools: '{key}' 已绑定，新增 {new_count} 个工具（{description}）")
        else:
            # register 函数没抛异常但也没新增工具——可能是空配置 / 全部 name 冲突
            logger.warning(f"bind_core_tools: '{key}' 调用成功但未新增任何工具（{description}）")

    return report


__all__ = ["bind_core_tools"]
