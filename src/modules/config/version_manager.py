"""
ConfigVersionManager - 配置版本管理器

集中式管理所有配置文件的版本检查和更新。
职责:
- 主配置版本检查和更新
- Provider 配置版本注册表
- 批量更新命令支持
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.modules.logging import get_logger
from src.modules.config.toml_utils import (
    read_toml_preserve,
    read_toml_fast,
    write_toml_preserve,
    get_version,
    merge_toml_documents,
)

logger = get_logger("ConfigVersionManager")


@dataclass
class ProviderConfigInfo:
    """Provider 配置信息"""

    domain: str  # input/decision/output
    provider_name: str
    config_path: str
    template_path: Optional[str] = None  # 如果为 None，无版本管理
    current_version: Optional[str] = None


class ConfigVersionManager:
    """
    配置版本管理器

    管理主配置和 Provider 配置的版本检查和更新。
    """

    def __init__(self, base_dir: str):
        """
        初始化版本管理器

        Args:
            base_dir: 项目根目录
        """
        self.base_dir = Path(base_dir)
        self._provider_configs: Dict[str, ProviderConfigInfo] = {}
        self.logger = logger

    # ==================== 主配置管理 ====================

    def check_main_config(self) -> Tuple[bool, str]:
        """
        检查主配置版本

        Returns:
            (needs_update, message)
        """
        config_path = self.base_dir / "config.toml"
        template_path = self.base_dir / "config-template.toml"

        if not config_path.exists():
            return False, "主配置不存在，跳过版本检查"

        if not template_path.exists():
            return False, "模板不存在，跳过版本检查"

        try:
            template = read_toml_fast(str(template_path))
            config = read_toml_fast(str(config_path))

            template_version = template.get("meta", {}).get("version")
            config_version = config.get("meta", {}).get("version")

            if not template_version:
                return False, "模板无版本信息"

            if not config_version:
                return True, f"需要初始化版本到 {template_version}"

            from packaging.version import Version

            if Version(template_version) > Version(config_version):
                return True, f"需要更新: {config_version} -> {template_version}"

            return False, "配置已是最新版本"

        except Exception as e:
            self.logger.warning(f"版本检查失败: {e}")
            return False, f"版本检查失败: {e}"

    def update_main_config(self) -> Tuple[bool, str]:
        """
        更新主配置文件

        Returns:
            (updated, message)
        """
        config_path = self.base_dir / "config.toml"
        template_path = self.base_dir / "config-template.toml"

        try:
            template = read_toml_preserve(str(template_path))
            user_config = read_toml_preserve(str(config_path))

            # 合并配置
            merged = merge_toml_documents(template, user_config)

            # 写入
            success, message = write_toml_preserve(str(config_path), merged)

            if success:
                new_version = get_version(merged)
                return True, f"主配置已更新到版本 {new_version}"
            else:
                return False, f"更新失败: {message}"

        except Exception as e:
            return False, f"更新失败: {e}"

    # ==================== Provider 配置管理 ====================

    def scan_provider_configs(self) -> None:
        """
        扫描所有 Provider 配置

        填充 _provider_configs 字典。
        注意：此操作较慢，不应在启动时自动执行。
        """
        for domain in ["input", "decision", "output"]:
            domain_path = self.base_dir / "src" / "domains" / domain / "providers"
            if not domain_path.exists():
                continue

            for provider_dir in domain_path.iterdir():
                if not provider_dir.is_dir() or provider_dir.name.startswith("__"):
                    continue

                config_path = provider_dir / "config.toml"
                template_path = provider_dir / "config-template.toml"

                if not config_path.exists():
                    continue

                # 获取当前版本
                current_version = None
                if config_path.exists():
                    try:
                        config = read_toml_fast(str(config_path))
                        current_version = config.get("meta", {}).get("version")
                    except Exception:
                        self.logger.error(f"读取配置文件 '{config_path}' 失败", exc_info=True)

                info = ProviderConfigInfo(
                    domain=domain,
                    provider_name=provider_dir.name,
                    config_path=str(config_path),
                    template_path=str(template_path) if template_path.exists() else None,
                    current_version=current_version,
                )

                key = f"{domain}.{provider_dir.name}"
                self._provider_configs[key] = info

        self.logger.info(f"扫描到 {len(self._provider_configs)} 个 Provider 配置")

    def check_provider_config(self, domain: str, provider_name: str) -> Tuple[bool, str]:
        """
        检查单个 Provider 配置版本

        在 Provider 初始化时调用。

        Args:
            domain: Provider 域 (input/decision/output)
            provider_name: Provider 名称

        Returns:
            (needs_update, message)
        """
        key = f"{domain}.{provider_name}"

        # 如果未扫描，先检查此 Provider
        if key not in self._provider_configs:
            self._register_provider(domain, provider_name)

        info = self._provider_configs.get(key)
        if not info or not info.template_path:
            return False, "Provider 无版本管理"

        try:
            template = read_toml_fast(info.template_path)
            template_version = template.get("meta", {}).get("version")

            if not template_version:
                return False, "模板无版本信息"

            if not info.current_version:
                return True, f"需要初始化版本到 {template_version}"

            from packaging.version import Version

            if Version(template_version) > Version(info.current_version):
                return True, f"需要更新: {info.current_version} -> {template_version}"

            return False, "配置已是最新版本"

        except Exception as e:
            self.logger.warning(f"Provider 配置版本检查失败 ({key}): {e}")
            return False, f"版本检查失败: {e}"

    def update_provider_config(self, domain: str, provider_name: str) -> Tuple[bool, str]:
        """
        更新单个 Provider 配置

        Args:
            domain: Provider 域
            provider_name: Provider 名称

        Returns:
            (updated, message)
        """
        key = f"{domain}.{provider_name}"

        if key not in self._provider_configs:
            return False, "Provider 未注册"

        info = self._provider_configs[key]

        if not info.template_path:
            return False, "Provider 无版本管理"

        try:
            template = read_toml_preserve(info.template_path)
            user_config = read_toml_preserve(info.config_path)

            # 合并
            merged = merge_toml_documents(template, user_config)

            # 写入
            success, message = write_toml_preserve(info.config_path, merged)

            if success:
                new_version = get_version(merged)
                info.current_version = new_version
                return True, f"Provider 配置已更新到版本 {new_version}"
            else:
                return False, f"更新失败: {message}"

        except Exception as e:
            self.logger.error(f"Provider 配置更新失败 ({key}): {e}")
            return False, f"更新失败: {e}"

    def update_all_provider_configs(self) -> List[Tuple[str, bool, str]]:
        """
        更新所有 Provider 配置

        Returns:
            [(provider_key, updated, message), ...]
        """
        results = []

        for key, info in self._provider_configs.items():
            if not info.template_path:
                results.append((key, False, "无版本管理"))
                continue

            updated, message = self.update_provider_config(info.domain, info.provider_name)
            results.append((key, updated, message))

        return results

    # ==================== 辅助方法 ====================

    def _register_provider(self, domain: str, provider_name: str) -> None:
        """注册单个 Provider 配置"""
        provider_path = self.base_dir / "src" / "domains" / domain / "providers" / provider_name
        config_path = provider_path / "config.toml"
        template_path = provider_path / "config-template.toml"

        if not config_path.exists():
            return

        current_version = None
        if config_path.exists():
            try:
                config = read_toml_fast(str(config_path))
                current_version = config.get("meta", {}).get("version")
            except Exception:
                self.logger.error(f"读取配置文件 '{config_path}' 失败", exc_info=True)
                # 返回 None，让调用者处理

        info = ProviderConfigInfo(
            domain=domain,
            provider_name=provider_name,
            config_path=str(config_path),
            template_path=str(template_path) if template_path.exists() else None,
            current_version=current_version,
        )

        key = f"{domain}.{provider_name}"
        self._provider_configs[key] = info
