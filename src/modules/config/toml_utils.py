"""TOML文件处理工具模块

使用 tomlkit 保留注释和格式
"""

import copy
import os
import shutil
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import tomllib
except ImportError:
    tomllib = None

try:
    import tomlkit
except ImportError as e:
    raise ImportError("tomlkit is required for config updates. Install it with: uv add tomlkit") from e

from src.modules.logging import get_logger

logger = get_logger("TomlUtils")


# ========== 枚举定义 ==========


class ArrayMergeStrategy(str, Enum):
    """数组合并策略"""

    USER = "user"  # 使用用户配置的数组
    TEMPLATE = "template"  # 使用模板的数组
    UNION = "union"  # 合并两个数组（去重）


def load_toml_with_comments(toml_path: str) -> tomlkit.TOMLDocument:
    """加载TOML文件并保留注释和格式

    Args:
        toml_path: TOML文件路径

    Returns:
        tomlkit.TOMLDocument 对象
    """
    with open(toml_path, "r", encoding="utf-8") as f:
        return tomlkit.load(f)


def save_toml_with_comments(data: tomlkit.TOMLDocument, toml_path: str) -> None:
    """保存TOML文件，保留注释和格式

    Args:
        data: tomlkit.TOMLDocument 对象
        toml_path: 保存路径
    """
    with open(toml_path, "w", encoding="utf-8") as f:
        f.write(tomlkit.dumps(data))


def _merge_toml_documents(template: tomlkit.TOMLDocument, existing: tomlkit.TOMLDocument) -> tomlkit.TOMLDocument:
    """合并两个TOML文档，模板优先但保留用户自定义值"""
    result = tomlkit.document()

    for key in template:
        result[key] = template[key]

    for key in existing:
        if key in result:
            if isinstance(result[key], dict) and isinstance(existing[key], dict):
                result[key] = _merge_tables(result[key], existing[key], key)
        else:
            result[key] = existing[key]

    return result


def _merge_tables(template_table: Dict[str, Any], existing_table: Dict[str, Any], table_name: str) -> Dict[str, Any]:
    """合并两个TOML表"""
    result = {}

    for key, value in template_table.items():
        result[key] = value

    for key, existing_value in existing_table.items():
        if key in template_table:
            template_value = template_table[key]

            if key == "version" and table_name in ("meta", "inner"):
                continue

            if existing_value != template_value:
                if not _is_placeholder_value(existing_value):
                    result[key] = existing_value

    return result


def _is_placeholder_value(value: Any) -> bool:
    """检查值是否是占位符"""
    if isinstance(value, str):
        placeholders = [
            "your-api-key",
            "your_token_here",
            "placeholder",
            "TODO",
            "CHANGE_ME",
        ]
        value_lower = value.lower().strip()
        return any(p in value_lower for p in placeholders)
    return False


def update_config_key(config_path: str, section: str, key: str, value: Any) -> Tuple[bool, str]:
    """更新配置文件中的特定键值"""
    try:
        data = load_toml_with_comments(config_path)

        if section not in data:
            data[section] = tomlkit.table()

        data[section][key] = value
        save_toml_with_comments(data, config_path)

        logger.info(f"[TOML更新] 已更新 {section}.{key} = {value}")
        return True, f"已更新 {section}.{key}"

    except Exception as e:
        logger.error(f"[TOML更新] 更新键值失败: {e}")
        return False, f"更新失败: {e}"


def get_config_value(config_path: str, section: str, key: str, default: Any = None) -> Any:
    """获取配置文件中的特定键值"""
    try:
        data = load_toml_with_comments(config_path)
        return data.get(section, {}).get(key, default)
    except Exception as e:
        logger.warning(f"[TOML读取] 读取键值失败: {e}")
        return default


# ==================== 版本管理所需函数 ====================


def read_toml_preserve(file_path: str) -> tomlkit.TOMLDocument:
    return load_toml_with_comments(file_path)


def read_toml_fast(file_path: str) -> Dict[str, Any]:
    """使用 tomllib 快速读取 TOML 文件（不保留注释）

    Args:
        file_path: TOML 文件路径

    Returns:
        解析后的字典
    """
    if tomllib is not None:
        with open(file_path, "rb") as f:
            return tomllib.load(f)
    # 回退到 tomlkit
    return load_toml_with_comments(file_path)


def write_toml_preserve(
    file_path: str,
    data: tomlkit.TOMLDocument,
    create_backup: bool = True,
) -> Tuple[bool, str]:
    """使用 tomlkit 写入 TOML 文件（保留注释和格式）

    使用原子写入机制：先写入临时文件，验证后重命名。

    Args:
        file_path: 目标文件路径
        data: tomlkit 文档对象
        create_backup: 是否创建备份

    Returns:
        (success, message) - 是否成功及消息
    """
    file_path = Path(file_path)
    temp_path = file_path.with_suffix(".tmp")

    try:
        # 1. 创建备份
        if create_backup and file_path.exists():
            backup_path = get_backup_path(str(file_path))
            shutil.copy2(file_path, backup_path)
            logger.debug(f"已创建备份: {backup_path}")

        # 2. 写入临时文件
        with open(temp_path, "w", encoding="utf-8") as f:
            tomlkit.dump(data, f)

        # 3. 验证 TOML 格式
        if tomllib is not None:
            try:
                with open(temp_path, "rb") as f:
                    tomllib.load(f)
            except Exception as e:
                temp_path.unlink(missing_ok=True)
                return False, f"TOML 验证失败: {e}"

        # 4. 原子重命名
        if file_path.exists():
            file_path.unlink()
        temp_path.rename(file_path)

        return True, "写入成功"

    except Exception as e:
        temp_path.unlink(missing_ok=True)
        logger.error(f"写入 TOML 文件失败: {file_path} - {e}")
        return False, f"写入失败: {e}"


def get_version(document: tomlkit.TOMLDocument) -> Optional[str]:
    if "meta" in document and "version" in document["meta"]:
        return str(document["meta"]["version"])
    if "inner" in document and "version" in document["inner"]:
        return str(document["inner"]["version"])
    return None


def set_version(document: tomlkit.TOMLDocument, version: str) -> None:
    if "meta" not in document:
        document["meta"] = tomlkit.table()
    document["meta"]["version"] = version


def merge_toml_documents(
    template: tomlkit.TOMLDocument,
    user_config: tomlkit.TOMLDocument,
    array_merge_config: Optional[Dict[str, str]] = None,
    deleted_keys: Optional[Dict[str, set]] = None,
) -> tomlkit.TOMLDocument:
    """合并两个 TOML 文档，保留用户修改和注释

    策略:
    - 模板新增键 → 添加
    - 用户修改的值 → 保留
    - 用户删除的键 → 保持删除
    - 用户自定义字段 → 保留
    - 数组 → 按策略配置合并

    Args:
        template: 模板文档（tomlkit 对象）
        user_config: 用户配置文档（tomlkit 对象）
        array_merge_config: 数组合并策略配置
        deleted_keys: 用户删除的键记录

    Returns:
        合并后的 tomlkit 文档
    """
    array_merge_config = array_merge_config or {}
    deleted_keys = deleted_keys or {}

    # 深拷贝模板作为基础
    result = copy.deepcopy(template)

    # 递归合并
    result = _merge_dicts(
        result,
        user_config,
        "",
        array_merge_config,
        deleted_keys,
    )

    return result


def _merge_dicts(
    template: Dict[str, Any],
    user: Dict[str, Any],
    path: str,
    array_merge_config: Dict[str, str],
    deleted_keys: Dict[str, set],
) -> Dict[str, Any]:
    """递归合并字典

    Args:
        template: 模板字典
        user: 用户配置字典
        path: 当前路径（点分隔）
        array_merge_config: 数组合并策略
        deleted_keys: 删除键记录

    Returns:
        合并后的字典
    """
    result = {}

    # 1. 处理模板中的键
    for key, template_value in template.items():
        current_path = f"{path}.{key}" if path else key

        # 检查是否被用户删除
        if current_path in deleted_keys.get(path, set()):
            continue

        if key not in user:
            # 新增键
            result[key] = template_value
        else:
            user_value = user[key]

            # 判断类型并合并
            if isinstance(template_value, dict) and isinstance(user_value, dict):
                # 递归合并字典
                result[key] = _merge_dicts(
                    template_value,
                    user_value,
                    current_path,
                    array_merge_config,
                    deleted_keys,
                )
            elif isinstance(template_value, list) and isinstance(user_value, list):
                # 按策略合并数组
                strategy = array_merge_config.get(current_path, "USER")
                result[key] = _merge_arrays(
                    template_value,
                    user_value,
                    strategy,
                )
            else:
                # 使用用户值
                result[key] = user_value

    # 2. 保留用户自定义字段
    for key in user:
        if key not in template:
            result[key] = user[key]

    return result


def _merge_arrays(
    template_array: list,
    user_array: list,
    strategy: str,
) -> list:
    """按策略合并数组

    Args:
        template_array: 模板数组
        user_array: 用户数组
        strategy: 合并策略 (USER/TEMPLATE/UNION)

    Returns:
        合并后的数组
    """
    if strategy == "USER":
        return user_array if user_array else template_array
    elif strategy == "TEMPLATE":
        return template_array
    elif strategy == "UNION":
        result = list(template_array or [])
        for item in user_array or []:
            if item not in result:
                result.append(item)
        return result
    else:
        # 默认用户优先
        return user_array if user_array else template_array


# ==================== 辅助函数 ====================


def get_backup_path(file_path: str) -> str:
    """生成备份文件路径

    策略:
    1. 如果 config.toml.backup 不存在，直接使用
    2. 如果存在，添加时间戳

    Args:
        file_path: 原文件路径

    Returns:
        备份文件路径
    """
    base_backup = f"{file_path}.backup"
    if not Path(base_backup).exists():
        return base_backup

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base_backup}.{timestamp}"


def ensure_meta_section(document: tomlkit.TOMLDocument) -> None:
    """确保 TOML 文档包含 [meta] 节

    Args:
        document: tomlkit 文档对象
    """
    if "meta" not in document:
        document.add("meta", tomlkit.table())
        # 添加注释
        document["meta"].add(tomlkit.comment("配置文件元信息"))


def get_deleted_keys(document: tomlkit.TOMLDocument) -> Dict[str, list]:
    """获取用户删除的键记录"""
    if "meta" in document and "deleted_keys" in document["meta"]:
        return document["meta"]["deleted_keys"]
    return {}


def set_deleted_keys(document: tomlkit.TOMLDocument, deleted_keys: Dict[str, list]) -> None:
    """设置用户删除的键记录"""
    ensure_meta_section(document)
    document["meta"]["deleted_keys"] = deleted_keys


def mark_key_as_deleted(document: tomlkit.TOMLDocument, section: str, key: str) -> None:
    """标记某个键为已删除"""
    ensure_meta_section(document)
    if "deleted_keys" not in document["meta"]:
        document["meta"]["deleted_keys"] = tomlkit.table()
    if section not in document["meta"]["deleted_keys"]:
        document["meta"]["deleted_keys"][section] = tomlkit.array()
    if key not in document["meta"]["deleted_keys"][section]:
        document["meta"]["deleted_keys"][section].append(key)


def compare_versions(template_version: str, config_version: str) -> bool:
    """比较版本号，返回 True 表示模板版本更高"""
    try:
        from packaging import version as pkg_version

        return pkg_version.parse(template_version) > pkg_version.parse(config_version)
    except Exception:
        return True


def is_tomlkit_document(value: Any) -> bool:
    """检查值是否为 tomlkit.TOMLDocument"""
    return isinstance(value, tomlkit.TOMLDocument)


def merge_toml_with_template(config_path: str, template_path: str) -> Tuple[bool, str]:
    """从模板更新配置文件，保留用户的自定义设置和注释"""
    logger.info(f"[TOML更新] 开始从模板更新配置文件: {config_path}")
    try:
        template_data = load_toml_with_comments(template_path)
        if os.path.exists(config_path):
            existing_data = load_toml_with_comments(config_path)
        else:
            existing_data = tomlkit.document()
        if os.path.exists(config_path):
            backup_path = get_backup_path(config_path)
            shutil.copy2(config_path, backup_path)
            logger.info(f"[TOML更新] 已创建备份文件: {backup_path}")
        merged_data = _merge_toml_documents(template_data, existing_data)
        save_toml_with_comments(merged_data, config_path)
        logger.info(f"[TOML更新] 配置文件已更新: {config_path}")
        return True, "配置文件已从模板更新"
    except Exception as e:
        logger.error(f"[TOML更新] 更新配置文件失败: {e}")
        return False, f"更新失败: {e}"
