"""
配置文件生成器 - 从 Pydantic Schemas 自动生成 config.toml

此模块提供从 Pydantic Schema 类自动生成 TOML 配置文件的功能。

主要功能:
- generate_toml(): 从 Schema 类生成 TOML 配置内容
- ensure_provider_config(): 确保 Provider 配置文件存在

使用示例:
    from src.modules.config.schemas.schemas.generator import generate_toml, ensure_provider_config
    from pydantic import BaseModel

    class MyProviderConfig(BaseModel):
        host: str = "localhost"
        port: int = 8080
        enabled: bool = True

    # 生成 TOML 内容
    toml_content = generate_toml(MyProviderConfig, "my_provider")

    # 确保配置文件存在
    ensure_provider_config(
        provider_name="my_provider",
        provider_layer="input",
        schema_class=MyProviderConfig,
        base_dir="/path/to/project"
    )
"""

import os
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Type

import tomli_w
from pydantic import BaseModel

from src.modules.logging import get_logger

logger = get_logger("ConfigGenerator")


def generate_toml(
    schema_class: Type[BaseModel],
    section_name: str,
    include_comments: bool = True,
    include_docstring: bool = True,
) -> str:
    """
    从 Pydantic Schema 类生成 TOML 配置内容

    Args:
        schema_class: Pydantic BaseModel 子类
        section_name: TOML 配置节名称（如 "console_input", "tts"）
        include_comments: 是否包含字段注释（从 Field 描述生成）
        include_docstring: 是否包含类的文档字符串作为文件头

    Returns:
        TOML 格式的配置内容字符串

    Raises:
        ValueError: 如果 schema_class 不是 BaseModel 的子类

    Example:
        >>> from pydantic import BaseModel, Field
        >>> class MyConfig(BaseModel):
        ...     host: str = Field(default="localhost", description="服务器主机地址")
        ...     port: int = 8080
        >>> toml_str = generate_toml(MyConfig, "my_provider")
        >>> print(toml_str)
    """
    if not issubclass(schema_class, BaseModel):
        raise ValueError(f"schema_class 必须是 BaseModel 的子类，获得: {type(schema_class)}")

    logger.debug(f"生成 TOML 配置: {section_name}")

    # 创建 Schema 实例以获取默认值
    schema_instance = schema_class()
    config_dict = schema_instance.model_dump(exclude_unset=False)

    # 构建带注释的 TOML 内容
    lines = []

    # 添加文档字符串作为文件头注释
    if include_docstring and schema_class.__doc__:
        docstring = schema_class.__doc__.strip()
        lines.append(f"# {docstring}")
        lines.append("")

    # 添加配置节
    lines.append(f"[{section_name}]")

    # 添加字段注释和值
    for field_name, field_info in schema_class.model_fields.items():
        # 获取字段值
        field_value = config_dict.get(field_name)

        # 添加字段注释
        if include_comments and field_info.description:
            lines.append(f"# {field_info.description}")

        # 格式化字段值（转为 TOML 格式）
        toml_value = _format_toml_value(field_value)
        lines.append(f"{field_name} = {toml_value}")

    return "\n".join(lines)


def _format_toml_value(value: Any) -> str:
    """
    将 Python 值转换为 TOML 字面量字符串

    Args:
        value: Python 值

    Returns:
        TOML 格式的字符串

    Note:
        此函数用于生成可直接写入 TOML 文件的字符串字面量
        与 tomli_w 配合使用时，tomli_w 会自动处理大部分格式化
        此函数主要用于手动构建包含注释的 TOML 内容
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, str):
        # 转义特殊字符
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    elif isinstance(value, (int, float)):
        return str(value)
    elif isinstance(value, list):
        items = [_format_toml_item(v) for v in value]
        return f"[{', '.join(items)}]"
    elif isinstance(value, dict):
        # 嵌套字典使用内联表格格式
        pairs = [f"{k} = {_format_toml_item(v)}" for k, v in value.items()]
        return f"{{{', '.join(pairs)}}}"
    elif value is None:
        return '""'
    else:
        # 其他类型转为字符串
        return f'"{str(value)}"'


def _format_toml_item(value: Any) -> str:
    """
    格式化 TOML 数组项（简化版，不处理嵌套结构）

    Args:
        value: 数组项值

    Returns:
        TOML 格式的字符串
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    elif isinstance(value, (int, float)):
        return str(value)
    elif value is None:
        return '""'
    else:
        return f'"{str(value)}"'


def ensure_provider_config(
    provider_name: str,
    provider_layer: Literal["input", "output", "decision"],
    schema_class: Optional[Type[BaseModel]] = None,
    base_dir: Optional[str] = None,
    config_filename: str = "config.toml",
    force_regenerate: bool = False,
) -> str:
    """
    确保 Provider 配置文件存在

    如果配置文件不存在，则从 Schema 类生成默认配置文件。
    如果配置文件已存在且 force_regenerate=False，则跳过生成。

    Args:
        provider_name: Provider 名称（如 "console_input", "tts"）
        provider_layer: Provider 层级（input/output/decision）
        schema_class: Pydantic Schema 类（用于生成默认配置）
        base_dir: 项目根目录（如果为 None 则自动检测）
        config_filename: 配置文件名（默认 "config-defaults.toml"）
        force_regenerate: 是否强制重新生成（覆盖已存在的配置）

    Returns:
        配置文件的绝对路径

    Raises:
        FileNotFoundError: 如果无法定位 Provider 目录
        ValueError: 如果 schema_class 未提供且配置文件不存在

    Example:
        >>> # 从 Schema 生成配置文件
        >>> ensure_provider_config(
        ...     provider_name="console_input",
        ...     provider_layer="input",
        ...     schema_class=ConsoleInputConfig,
        ...     base_dir="/path/to/project",
        ... )

        >>> # 检查配置文件是否存在（不生成）
        >>> config_path = ensure_provider_config(provider_name="tts", provider_layer="output")
    """
    # 确定项目根目录
    if base_dir is None:
        base_dir = _detect_project_root()

    # 构建 Provider 目录路径
    if provider_layer == "input":
        provider_dir = os.path.join(base_dir, "src", "domains", "input", "providers", provider_name)
    elif provider_layer == "output":
        provider_dir = os.path.join(base_dir, "src", "domains", "output", "providers", provider_name)
    elif provider_layer == "decision":
        provider_dir = os.path.join(base_dir, "src", "domains", "decision", "providers", provider_name)
    else:
        raise ValueError(f"未知的 Provider 层级: {provider_layer}")

    # 检查 Provider 目录是否存在
    if not os.path.isdir(provider_dir):
        raise FileNotFoundError(f"Provider 目录不存在: {provider_dir}")

    # 配置文件路径
    config_path = os.path.join(provider_dir, config_filename)

    # 如果配置文件已存在且不强制重新生成，则返回路径
    if os.path.exists(config_path) and not force_regenerate:
        logger.debug(f"配置文件已存在，跳过生成: {config_path}")
        return config_path

    # 如果配置文件不存在，但也没有提供 Schema 类，则报错
    if not os.path.exists(config_path) and schema_class is None:
        raise ValueError(
            f"配置文件不存在且未提供 schema_class: {config_path}\n"
            f"请提供 schema_class 以生成默认配置，或手动创建配置文件。"
        )

    # 生成配置文件
    if schema_class is not None:
        logger.info(f"生成配置文件: {config_path}")
        toml_content = generate_toml(schema_class, provider_name)

        # 写入配置文件
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(toml_content)

        logger.info(f"配置文件已生成: {config_path}")
        return config_path

    return config_path


def generate_config_dict(
    schema_class: Type[BaseModel],
    provider_name: str,
) -> Dict[str, Any]:
    """
    从 Pydantic Schema 类生成配置字典

    Args:
        schema_class: Pydantic BaseModel 子类
        provider_name: Provider 名称（用作配置节键）

    Returns:
        配置字典，格式为 {provider_name: {...}}

    Example:
        >>> config_dict = generate_config_dict(MyProviderConfig, "my_provider")
        >>> print(config_dict)
        {'my_provider': {'host': 'localhost', 'port': 8080}}
    """
    if not issubclass(schema_class, BaseModel):
        raise ValueError(f"schema_class 必须是 BaseModel 的子类，获得: {type(schema_class)}")

    schema_instance = schema_class()
    config_values = schema_instance.model_dump(exclude_unset=False)

    return {provider_name: config_values}


def merge_config_with_tomli_w(
    base_config: Dict[str, Any],
    override_config: Dict[str, Any],
    output_path: str,
) -> None:
    """
    使用 tomli_w 合并配置并写入文件

    Args:
        base_config: 基础配置字典
        override_config: 覆盖配置字典（会合并到 base_config）
        output_path: 输出文件路径

    Example:
        >>> base = {"provider": {"host": "localhost", "port": 8080}}
        >>> override = {"provider": {"port": 9090}}
        >>> merge_config_with_tomli_w(base, override, "config.toml")
    """
    # 深度合并配置
    merged = _deep_merge_dicts(base_config, override_config)

    # 使用 tomli_w 写入文件
    with open(output_path, "wb") as f:
        tomli_w.dump(merged, f)

    logger.debug(f"配置文件已写入: {output_path}")


def _deep_merge_dicts(
    base: Dict[str, Any],
    override: Dict[str, Any],
) -> Dict[str, Any]:
    """
    深度合并两个字典

    Args:
        base: 基础字典
        override: 覆盖字典

    Returns:
        合并后的字典

    Note:
        - 基本类型: override 直接覆盖
        - 字典类型: 递归合并
        - 列表类型: override 完全替换
        - None: 跳过
    """
    result = base.copy()

    for key, value in override.items():
        if value is None:
            continue

        if key not in result:
            result[key] = value
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge_dicts(result[key], value)
        else:
            result[key] = value

    return result


def _detect_project_root() -> str:
    """
    自动检测项目根目录

    Returns:
        项目根目录的绝对路径

    Note:
        检测策略:
        1. 从当前文件向上查找，直到找到包含 pyproject.toml 的目录
        2. 如果找不到，则使用当前文件的 src 的父目录
    """
    # 当前文件所在目录: src/services/config/schemas/
    current_dir = Path(__file__).parent.absolute()

    # 向上查找项目根目录（查找 pyproject.toml）
    for parent in [
        current_dir,
        current_dir.parent,
        current_dir.parent.parent,
        current_dir.parent.parent.parent,
        current_dir.parent.parent.parent.parent,
    ]:
        if (parent / "pyproject.toml").exists():
            return str(parent)

    # 如果找不到，假设当前目录是 src/services/config/schemas，返回项目根目录
    return str(current_dir.parent.parent.parent.parent)


# 便捷函数：批量生成 Provider 配置
def batch_ensure_provider_configs(
    provider_configs: list[Dict[str, Any]],
    base_dir: Optional[str] = None,
    force_regenerate: bool = False,
) -> Dict[str, str]:
    """
    批量确保 Provider 配置文件存在

    Args:
        provider_configs: Provider 配置列表，每项包含:
            - provider_name: str
            - provider_layer: Literal["input", "output", "decision"]
            - schema_class: Type[BaseModel]
        base_dir: 项目根目录（如果为 None 则自动检测）
        force_regenerate: 是否强制重新生成

    Returns:
        字典，键为 provider_name，值为配置文件路径

    Example:
        >>> configs = [
        ...     {"provider_name": "console_input", "provider_layer": "input", "schema_class": ConsoleInputConfig},
        ...     {"provider_name": "tts", "provider_layer": "output", "schema_class": TTSConfig},
        ... ]
        >>> paths = batch_ensure_provider_configs(configs)
        >>> print(paths)
        {'console_input': '/path/to/console_input/config-defaults.toml', 'tts': '/path/to/tts/config-defaults.toml'}
    """
    results = {}

    for config in provider_configs:
        provider_name = config["provider_name"]
        provider_layer = config["provider_layer"]
        schema_class = config.get("schema_class")

        try:
            config_path = ensure_provider_config(
                provider_name=provider_name,
                provider_layer=provider_layer,
                schema_class=schema_class,
                base_dir=base_dir,
                force_regenerate=force_regenerate,
            )
            results[provider_name] = config_path
        except Exception as e:
            logger.error(f"生成配置文件失败: {provider_name} - {e}")
            results[provider_name] = None

    return results
