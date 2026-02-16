"""
Provider 辅助函数

提供 Provider 状态查询和信息提取的工具函数。
适配三种 Manager 的 API 差异。
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.modules.dashboard.schemas.provider import ProviderSummary

if TYPE_CHECKING:
    from src.domains.decision.provider_manager import DecisionProviderManager
    from src.domains.input.provider_manager import InputProviderManager
    from src.domains.output.provider_manager import OutputProviderManager


def get_input_provider_summaries(manager: Optional["InputProviderManager"]) -> List[ProviderSummary]:
    """从 InputProviderManager 提取 Provider 信息"""
    if not manager:
        return []

    summaries = []
    providers = getattr(manager, "_providers", [])

    for provider in providers:
        # 获取 provider 信息
        info = provider.get_info() if hasattr(provider, "get_info") else {}
        name = info.get("name", provider.__class__.__name__)
        provider_type = info.get("type", "unknown")
        is_started = getattr(provider, "is_started", True)

        summaries.append(
            ProviderSummary(
                name=name,
                domain="input",
                type=provider_type,
                is_started=is_started,
                is_enabled=True,  # 在 _providers 中说明已启用
            )
        )

    return summaries


def get_decision_provider_summaries(manager: Optional["DecisionProviderManager"]) -> List[ProviderSummary]:
    """从 DecisionProviderManager 提取 Provider 信息"""
    if not manager:
        return []

    summaries = []
    current_name = None
    available = []

    if hasattr(manager, "get_current_provider_name"):
        current_name = manager.get_current_provider_name()

    if hasattr(manager, "get_available_providers"):
        available = manager.get_available_providers()

    for name in available:
        summaries.append(
            ProviderSummary(
                name=name,
                domain="decision",
                type="decision",
                is_started=(name == current_name),
                is_enabled=(name == current_name),
            )
        )

    return summaries


def get_output_provider_summaries(manager: Optional["OutputProviderManager"]) -> List[ProviderSummary]:
    """从 OutputProviderManager 提取 Provider 信息"""
    if not manager:
        return []

    summaries = []
    names = []

    if hasattr(manager, "get_provider_names"):
        names = manager.get_provider_names()

    for name in names:
        provider = None
        if hasattr(manager, "get_provider_by_name"):
            provider = manager.get_provider_by_name(name)

        is_started = getattr(provider, "is_started", False) if provider else False

        summaries.append(
            ProviderSummary(
                name=name,
                domain="output",
                type="output",
                is_started=is_started,
                is_enabled=True,
            )
        )

    return summaries


def get_provider_detail(
    domain: str,
    name: str,
    input_manager: Optional["InputProviderManager"],
    decision_manager: Optional["DecisionProviderManager"],
    output_manager: Optional["OutputProviderManager"],
) -> Optional[Dict[str, Any]]:
    """获取单个 Provider 详情"""
    if domain == "input":
        return _get_input_provider_detail(name, input_manager)
    elif domain == "decision":
        return _get_decision_provider_detail(name, decision_manager)
    elif domain == "output":
        return _get_output_provider_detail(name, output_manager)
    return None


def _get_input_provider_detail(name: str, manager: Optional["InputProviderManager"]) -> Optional[Dict[str, Any]]:
    """获取 InputProvider 详情"""
    if not manager:
        return None

    for provider in getattr(manager, "_providers", []):
        info = provider.get_info() if hasattr(provider, "get_info") else {}
        provider_name = info.get("name", provider.__class__.__name__)
        if provider_name == name:
            return {
                "name": name,
                "domain": "input",
                "type": info.get("type", "unknown"),
                "is_started": getattr(provider, "is_started", True),
                "is_enabled": True,
                "config": getattr(provider, "config", None),
            }
    return None


def _get_decision_provider_detail(name: str, manager: Optional["DecisionProviderManager"]) -> Optional[Dict[str, Any]]:
    """获取 DecisionProvider 详情"""
    if not manager:
        return None

    available = manager.get_available_providers() if hasattr(manager, "get_available_providers") else []
    current_name = manager.get_current_provider_name() if hasattr(manager, "get_current_provider_name") else None

    if name in available:
        return {
            "name": name,
            "domain": "decision",
            "type": "decision",
            "is_started": (name == current_name),
            "is_enabled": (name == current_name),
        }
    return None


def _get_output_provider_detail(name: str, manager: Optional["OutputProviderManager"]) -> Optional[Dict[str, Any]]:
    """获取 OutputProvider 详情"""
    if not manager:
        return None

    names = manager.get_provider_names() if hasattr(manager, "get_provider_names") else []
    if name not in names:
        return None

    provider = manager.get_provider_by_name(name) if hasattr(manager, "get_provider_by_name") else None
    is_started = getattr(provider, "is_started", False) if provider else False

    return {
        "name": name,
        "domain": "output",
        "type": "output",
        "is_started": is_started,
        "is_enabled": True,
        "config": getattr(provider, "config", None) if provider else None,
    }
