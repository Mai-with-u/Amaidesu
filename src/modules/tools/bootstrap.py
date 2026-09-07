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
``[tools.output.config].enabled`` 列表中的 ``"subtitle"`` 条目会被静默忽略
（字幕不由本模块装配；白名单无对应映射项）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from src.modules.logging import get_logger
from src.modules.tools.registry import ToolRegistry

logger = get_logger("ToolBootstrap")


# (成员身份, 描述, 注册函数加载器)
# 成员身份 = (分类段, 提供者键)：分类段开关（avatar.vts 等）驱动装配。
# 注册函数加载器：返回 ``Callable[[registry, config], provider]``，
# 延迟 import 避免本模块被加载时拖入整条 avatar/studio 依赖链。
_EntrySpec = Tuple[Tuple[str, str], str, Callable[[], Callable[..., Any]]]


def _load_vts() -> Callable[..., Any]:
    from src.modules.avatar.vts.vts_provider import register_vts_tools

    return register_vts_tools


def _load_vrchat() -> Callable[..., Any]:
    from src.modules.avatar.vrchat.vrchat_provider import register_vrchat_tools

    return register_vrchat_tools


def _load_warudo() -> Callable[..., Any]:
    from src.modules.avatar.warudo.warudo_provider import register_warudo_tools

    return register_warudo_tools


def _load_obs() -> Callable[..., Any]:
    from src.modules.studio.obs.obs_provider import register_obs_tools

    return register_obs_tools


# 工具分类成员表：每个提供者绑定其分类段（avatar.vts / avatar.warudo / studio.obs）。
# 分类段缺失或 enabled=false 时不装配（开关控制权归属人类：配置 + Web UI）。
_DOMAIN_MEMBERS: List[_EntrySpec] = [
    (("avatar", "vts"), "VTubeStudio 控制", _load_vts),
    (("avatar", "vrchat"), "VRChat OSC 桥接", _load_vrchat),
    (("avatar", "warudo"), "Warudo 控制", _load_warudo),
    (("studio", "obs"), "OBS Studio 控制", _load_obs),
]

# 对外保留的 _NON_TTS_PACKAGES / _CORE_PACKAGES 兼容名（指代全部可控分类包）。
# 运行时不使用，仅供外部静态分析 / 类型检查引用；实际装配由分类段开关门控。
_NON_TTS_PACKAGES: List[_EntrySpec] = list(_DOMAIN_MEMBERS)
_CORE_PACKAGES: List[_EntrySpec] = list(_DOMAIN_MEMBERS)


def _resolve_domain_config(tools_cfg: Dict[str, Any], domain: str, key: str) -> Dict[str, Any]:
    """从 ``[tools]`` 顶层配置读出 ``domain.key`` 子段开关配置。

    返回该分类的 ``config`` 字典（provider 具体配置）：
    - ``[tools.avatar.vts] {enabled: true, config: {...}}`` → ``{...}``
    - 分类段缺失 / 非 dict → {}（下游 register 走默认 / 抛错兜底）

    开关语义：分类段不存在于配置即视为未启用（不装配），避免隐式行为漂移。
    """
    if not isinstance(tools_cfg, dict):
        return {}
    domain_cfg = tools_cfg.get(domain)
    if not isinstance(domain_cfg, dict):
        return {}
    member_cfg = domain_cfg.get(key)
    if not isinstance(member_cfg, dict):
        return {}
    cfg = member_cfg.get("config")
    return dict(cfg) if isinstance(cfg, dict) else {}


def _domain_enabled(tools_cfg: Dict[str, Any], domain: str, key: str) -> bool:
    """分类开关：``[tools.<domain>.<key>].enabled``，默认 False（缺省不装配）。"""
    if not isinstance(tools_cfg, dict):
        return False
    domain_cfg = tools_cfg.get(domain)
    if not isinstance(domain_cfg, dict):
        return False
    member_cfg = domain_cfg.get(key)
    if not isinstance(member_cfg, dict):
        return False
    return bool(member_cfg.get("enabled", False))


def bind_core_tools(
    registry: ToolRegistry,
    config: Dict[str, Any] | None = None,
) -> Dict[str, int]:
    """绑定 Amaidesu 核心分类工具包到 ``registry``。

    装配规则：

    - **按分类开关装配**——``[tools.avatar.vts].enabled`` 等分类段为 true 时
      装配该提供者（avatar 分类 / studio 分类）；false 或段缺失则不装配
    - TTS / 字幕为基础设施（core.toml 驱动），不在本 bootstrap 范围

    Args:
        registry: 目标注册器（由调用方构造并持有）
        config: ``[tools]`` 段（分类开关容器），键名见 ``_DOMAIN_MEMBERS``；
            传 ``None`` 表示所有分类走"空配置"，一律不装配

    Returns:
        ``{member_key: new_tool_count}`` 报告。失败 / 跳过成员
        ``count=0``。
    """
    if not isinstance(registry, ToolRegistry):
        raise TypeError(f"bind_core_tools: registry 必须是 ToolRegistry 实例，得到 {type(registry).__name__}")

    tools_cfg: Dict[str, Any] = config if isinstance(config, dict) else {}

    report: Dict[str, int] = {}

    # --- 按分类开关装配 ---
    for (domain, key), description, loader in _DOMAIN_MEMBERS:
        if not _domain_enabled(tools_cfg, domain, key):
            # 分类未启用：跳过（不记 ERROR，预期行为）
            report[key] = 0
            continue

        provider_config = _resolve_domain_config(tools_cfg, domain, key)
        before_count = len(registry)

        try:
            register_fn = loader()
        except Exception:  # noqa: BLE001 - 单成员隔离边界
            logger.error(
                f"bind_core_tools: 加载 '{key}' 的 register 函数失败（{description}）",
                exc_info=True,
            )
            report[key] = 0
            continue

        try:
            register_fn(registry=registry, config=provider_config)
        except Exception as exc:  # noqa: BLE001 - 单成员隔离边界
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
    "_DOMAIN_MEMBERS",
]
