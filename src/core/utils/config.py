import os
import sys
import shutil
from typing import Dict, Any, Optional, Tuple
from packaging import version

# 尝试导入 tomllib (Python 3.11+), 否则使用 toml
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import toml as tomllib  # type: ignore
    except ModuleNotFoundError:
        # 这个错误理论上不应该在这里触发，因为主程序入口会先检查
        # 但为了模块的独立性，保留一个基本的提示
        print("错误：TOML 解析库缺失。请在主程序环境中安装 'toml'。", file=sys.stderr)
        # 此处不直接 sys.exit(1)，让调用方处理初始化失败
        raise

from src.core.utils.logger import get_logger  # 假设 logger 初始化已在外部完成或以其他方式处理

logger = get_logger("ConfigManager")

# 获取此文件所在的目录，用于相对路径计算
_CONFIG_MANAGER_DIR = os.path.dirname(os.path.abspath(__file__))
# 项目根目录通常是 src 的上一级
_BASE_DIR = os.path.dirname(os.path.dirname(_CONFIG_MANAGER_DIR))


def load_config(config_filename: str = "config.toml", base_dir: str = _BASE_DIR) -> dict:
    """加载位于指定基础目录下的 TOML 配置文件。"""
    config_path = os.path.join(base_dir, config_filename)
    logger.debug(f"尝试加载配置文件: {config_path}")
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
            logger.info(f"成功加载配置文件: {config_path}")
            return config
    except FileNotFoundError:
        logger.error(f"错误：配置文件 '{config_path}' 未找到。")
        # 将异常向上抛出，让调用者决定如何处理
        raise
    except tomllib.TOMLDecodeError as e:
        logger.error(f"错误：配置文件 '{config_path}' 格式无效: {e}")
        raise
    except Exception as e:
        logger.error(f"加载配置文件 '{config_path}' 时发生未知错误: {e}", exc_info=True)
        raise


def _ensure_component_configs(component_base_dir: str, component_type_name: str) -> bool:
    """
    通用函数：检查指定类型组件目录中的 config.toml。
    如果不存在但存在 config-template.toml，则复制模板。
    返回 True 如果有任何文件被复制，否则返回 False。
    """
    config_copied = False
    logger.info(f"开始检查{component_type_name}配置文件于 '{component_base_dir}'...")
    try:
        if not os.path.isdir(component_base_dir):
            logger.warning(f"指定的{component_type_name}目录 '{component_base_dir}' 不存在或不是一个目录。")
            return False

        for item_name in os.listdir(component_base_dir):
            component_item_path = os.path.join(component_base_dir, item_name)

            if os.path.isdir(component_item_path) and not item_name.startswith("__"):
                config_path = os.path.join(component_item_path, "config.toml")
                template_path = os.path.join(component_item_path, "config-template.toml")

                logger.debug(f"检查{component_type_name}目录: {item_name}")

                template_exists = os.path.exists(template_path)
                config_exists = os.path.exists(config_path)

                if template_exists and not config_exists:
                    try:
                        shutil.copy2(template_path, config_path)
                        logger.info(
                            f"在{component_type_name} '{item_name}' 中: config.toml 不存在，已从 config-template.toml 复制。"
                        )
                        config_copied = True
                    except Exception as e:
                        logger.error(f"在{component_type_name} '{item_name}' 中: 从模板复制配置文件失败: {e}")
                        # 考虑是否应该在这里抛出异常，或者让上层决定
                elif not template_exists and not config_exists:
                    logger.debug(
                        f"在{component_type_name} '{item_name}' 中: 未找到 config.toml 或 config-template.toml。"
                    )
                elif template_exists and config_exists:
                    logger.debug(f"在{component_type_name} '{item_name}' 中: config.toml 已存在。")

        return config_copied

    except Exception as e:
        logger.error(f"检查{component_type_name}配置文件时出错: {e}", exc_info=True)
        return False


def check_and_setup_plugin_configs(plugin_base_dir_name: str = "src/plugins") -> bool:
    """
    检查插件目录中的 config.toml。如果不存在但存在 config-template.toml，则复制模板。
    plugin_base_dir_name 是相对于项目根目录的路径。
    返回 True 如果有任何文件被复制，否则返回 False。
    """
    abs_plugin_base_dir = os.path.join(_BASE_DIR, plugin_base_dir_name)
    return _ensure_component_configs(abs_plugin_base_dir, "插件")


def check_and_setup_pipeline_configs(pipeline_base_dir_name: str = "src/domains/input/pipelines") -> bool:
    """
    检查管道目录中的 config.toml。如果不存在但存在 config-template.toml，则复制模板。
    pipeline_base_dir_name 是相对于项目根目录的路径。
    返回 True 如果有任何文件被复制，否则返回 False。
    """
    abs_pipeline_base_dir = os.path.join(_BASE_DIR, pipeline_base_dir_name)
    return _ensure_component_configs(abs_pipeline_base_dir, "管道")


def check_and_setup_main_config(
    config_filename: str = "config.toml", template_filename: str = "config-template.toml", base_dir: str = _BASE_DIR
) -> bool:
    """
    检查主 config.toml。如果不存在但存在 config-template.toml，则复制模板。
    返回 True 如果文件被复制，否则返回 False。失败则抛出异常。
    """
    config_path = os.path.join(base_dir, config_filename)
    template_path = os.path.join(base_dir, template_filename)
    config_copied = False

    logger.info(f"开始检查主配置文件 '{config_path}'...")

    template_exists = os.path.exists(template_path)
    config_exists = os.path.exists(config_path)

    if template_exists and not config_exists:
        try:
            shutil.copy2(template_path, config_path)
            logger.info(f"主配置文件 '{config_filename}' 不存在，已从 '{template_filename}' 复制。")
            config_copied = True
        except Exception as e:
            logger.error(f"从模板 '{template_filename}' 复制主配置文件 '{config_filename}' 失败: {e}")
            raise IOError(f"无法复制主配置文件 {config_filename} 从模板 {template_filename}: {e}") from e
    elif not config_exists and not template_exists:
        logger.error(f"主配置文件 '{config_filename}' 和模板 '{template_filename}' 均未找到于 '{base_dir}'。")
        # 这个错误会在 load_config 中被 FileNotFoundError 捕获，但提前记录更清晰
        # 不在此处 raise，让 load_config 统一处理
    elif not config_exists and template_exists:  # 这种情况被上面 if template_exists and not config_exists 覆盖
        pass
    else:  # config_exists is True
        logger.debug(f"主配置文件 '{config_filename}' 已存在于 '{base_dir}'.")

    logger.info("主配置文件检查完成。")
    return config_copied


def load_component_specific_config(
    component_dir_path: str, component_name: str, component_type_name: str = "组件"
) -> Dict[str, Any]:
    """
    加载组件自身目录下的 config.toml。

    Args:
        component_dir_path: 组件包的绝对路径 (例如 /path/to/src/domains/{domain}/providers/{provider})
        component_name: 组件的名称 (例如 my_plugin)
        component_type_name: 组件类型名称，用于日志 (例如 "插件", "管道")

    Returns:
        配置字典，若配置文件不存在或加载失败则返回空字典
    """
    config_path = os.path.join(component_dir_path, "config.toml")
    component_config_data = {}

    # tomllib 应该在文件顶部被导入和检查
    if tomllib and os.path.exists(config_path):
        try:
            with open(config_path, "rb") as f:
                loaded_data = tomllib.load(f)
                # 检查组件自身的配置文件是否包含一个与组件名同名的配置段
                # (例如 [bili_danmaku] 在 bili_danmaku/config.toml 中)
                # 如果是，则使用该配置段作为插件的独立配置。
                # 否则，假设整个文件内容都是该插件的配置（例如，根级别就是键值对）。
                if isinstance(loaded_data.get(component_name), dict):
                    component_config_data = loaded_data.get(component_name, {}).copy()
                    logger.debug(
                        f"从 '{config_path}' 加载了{component_type_name} '{component_name}' 的 '{component_name}' 特定配置段。"
                    )
                elif isinstance(loaded_data, dict):  # 允许配置文件根就是配置
                    component_config_data = loaded_data.copy()
                    logger.debug(
                        f"从 '{config_path}' 加载了{component_type_name} '{component_name}' 的根配置 (未找到名为 '{component_name}' 的特定配置段)."
                    )
                else:
                    logger.warning(
                        f"{component_type_name} '{component_name}' 的配置文件 '{config_path}' 内容不是预期的字典格式。"
                    )
        except Exception as e:
            logger.error(
                f"加载{component_type_name} '{component_name}' 的独立配置文件 '{config_path}' 失败: {e}", exc_info=True
            )
    elif not tomllib:
        logger.warning(
            f"TOML库不可用，无法加载{component_type_name} '{component_name}' 的独立配置文件 '{config_path}'。"
        )
    else:  # tomllib is available but config_path does not exist
        logger.debug(f"{component_type_name} '{component_name}' 无独立配置文件 '{config_path}'。")
    return component_config_data


def merge_component_configs(
    specific_config: Dict[str, Any],
    global_override_config: Dict[str, Any],
    component_name: str,
    component_type_name: str = "组件",
) -> Dict[str, Any]:
    """
    合并组件的特定配置和全局覆盖配置。全局配置优先。
    这样做是为了提供一个集中的控制点。主配置文件 (config.toml) 中的设置
    应具有最高优先级（文件配置层面），允许用户或部署环境方便地覆盖
    插件自带的默认配置，而无需修改插件目录内的文件。
    这使得全局调整和环境特定配置更加直接和可管理。

    Args:
        specific_config: 组件自身的配置。
        global_override_config: 从主配置文件中提取的、针对该组件的全局覆盖配置。
        component_name: 组件的名称，用于日志。
        component_type_name: 组件类型名称，用于日志。

    Returns:
        合并后的配置字典。
    """
    final_config = specific_config.copy()
    final_config.update(global_override_config)
    # 日志可以更简洁一些，或者在调用处记录更详细的信息
    logger.debug(f"{component_type_name} '{component_name}' 合并后配置: {final_config}")
    return final_config


# 可以在此添加一个统一的设置函数，如果需要的话
def initialize_configurations(
    base_dir: str = _BASE_DIR,
    main_cfg_name: str = "config.toml",
    main_template_name: str = "config-template.toml",
    plugin_dir_name: str = "src/plugins",
    pipeline_dir_name: str = "src/domains/input/pipelines",
) -> tuple[dict, bool, bool, bool]:
    """
    执行所有配置检查和加载步骤。
    1. 检查并设置主配置。
    2. 加载主配置。
    3. 检查并设置插件配置。
    4. 检查并设置管道配置。
    返回 (loaded_main_config, main_config_copied, plugin_configs_copied, pipeline_configs_copied)
    如果主配置加载失败，则会抛出异常。
    """
    logger.info("开始初始化所有配置文件...")

    # 1. 处理主配置
    # _BASE_DIR 是脚本 main.py 所在的目录，现在是 config_manager.py 所在的目录的父目录的父目录
    # 我们需要确保这里的 base_dir 是正确的项目根目录
    # 传入的 base_dir 参数应为项目根目录

    main_config_copied = check_and_setup_main_config(
        config_filename=main_cfg_name, template_filename=main_template_name, base_dir=base_dir
    )

    # 2. 加载主配置 (如果 check_and_setup_main_config 失败会抛异常, 不会执行到这里)
    # 如果主配置文件不存在且无法从模板创建，load_config 会抛出 FileNotFoundError
    try:
        main_config = load_config(config_filename=main_cfg_name, base_dir=base_dir)
    except FileNotFoundError:
        logger.critical(f"主配置文件 '{main_cfg_name}' 在 '{base_dir}' 中未找到且无法从模板创建。程序无法继续。")
        sys.exit(1)  # 或者向上抛出自定义的关键错误
    except Exception as e:  # 其他 load_config 可能抛出的异常
        logger.critical(f"加载主配置文件 '{main_cfg_name}' 时发生致命错误: {e}")
        sys.exit(1)

    # 3. 处理插件配置
    # plugin_dir_name 和 pipeline_dir_name 应该是相对于 base_dir 的路径
    # 例如 "src/plugins" or "plugins" if base_dir is already "src"
    # 当前设计是相对于项目根目录
    plugin_configs_copied = check_and_setup_plugin_configs(
        plugin_base_dir_name=os.path.join(base_dir, plugin_dir_name)
        if not os.path.isabs(plugin_dir_name)
        else plugin_dir_name
    )

    # 4. 处理管道配置
    pipeline_configs_copied = check_and_setup_pipeline_configs(
        pipeline_base_dir_name=os.path.join(base_dir, pipeline_dir_name)
        if not os.path.isabs(pipeline_dir_name)
        else pipeline_dir_name
    )

    logger.info("所有配置文件初始化完成。")
    return main_config, main_config_copied, plugin_configs_copied, pipeline_configs_copied


"""
# Notes for the new config_manager.py:
# - Imports: os, sys, shutil, tomllib (or toml), get_logger.
# - _BASE_DIR: Adjusted to be calculated from config_manager.py's location, assuming it's in src/core.
#   This needs to point to the project root.
# - load_config:
#   - Takes an optional `base_dir` argument, defaulting to the project root.
#   - Propagates exceptions upwards instead of sys.exit(1).
# - _ensure_component_configs:
#   - Takes `component_base_dir` as an absolute path.
#   - Logs a warning if `component_base_dir` doesn't exist, instead of an error.
# - check_and_setup_plugin_configs & check_and_setup_pipeline_configs:
#   - Construct absolute paths for plugin/pipeline base dirs using `_BASE_DIR` and the provided relative path.
# - check_and_setup_main_config:
#   - Takes an optional `base_dir`.
#   - If copying from template fails, raises an IOError.
#   - If config and template are missing, logs an error but doesn't exit (load_config will handle it).
# - initialize_configurations:
#   - New orchestrator function.
#   - Takes `base_dir` for paths.
#   - Handles paths for plugin/pipeline dirs to be relative to `base_dir`.
#   - If main config loading fails critically, it calls sys.exit(1). This might be better handled by raising an exception for main.py to catch.
# - Logging: Uses a logger named "ConfigManager".
# - Error Handling: More emphasis on raising exceptions for `load_config` and critical failures in `check_and_setup_main_config`, allowing the caller (`main.py`) to decide on termination.
# - Path handling for plugin/pipeline directories in `initialize_configurations` assumes they are relative to the project root if not absolute.
"""


# ==================== 版本检查和自动更新功能 ====================

def _get_version_from_toml(toml_path: str) -> Optional[str]:
    """从TOML文件中获取版本号"""
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("inner", {}).get("version")
    except Exception:
        return None


def _compare_versions(template_version: str, config_version: str) -> bool:
    """比较版本号，返回True表示模板版本更高"""
    try:
        return version.parse(template_version) > version.parse(config_version)
    except Exception:
        # 如果版本号格式不正确，认为需要更新
        return True


def _parse_toml_with_comments(file_path: str) -> Tuple[Dict[str, Any], str]:
    """解析TOML文件并保留注释"""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 使用tomllib解析数据
    with open(file_path, "rb") as f:
        data = tomllib.load(f)

    return data, content


def _format_toml_value(value: Any) -> str:
    """将Python值转换为TOML字面量字符串，确保布尔为小写true/false。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return '""'
    if isinstance(value, list):
        items = ", ".join(_format_toml_value(v) for v in value)
        return f"[ {items} ]"
    return f'"{str(value)}"'


def _extract_section_comment(content: str, section: str) -> str:
    """从内容中提取section的注释"""
    lines = content.split("\n")
    section_line = f"[{section}]"

    for i, line in enumerate(lines):
        if line.strip() == section_line:
            # 查找section前的注释
            for j in range(i - 1, -1, -1):
                comment_line = lines[j].strip()
                if comment_line.startswith("#"):
                    return comment_line
                elif comment_line == "":
                    continue
                else:
                    break
            break

    return ""


def _extract_key_comment(content: str, section: str, key: str) -> str:
    """从内容中提取key的注释"""
    lines = content.split("\n")
    in_section = False

    for i, line in enumerate(lines):
        line_stripped = line.strip()

        # 检查是否进入目标section
        if line_stripped == f"[{section}]":
            in_section = True
            continue

        # 如果遇到新的section，退出当前section
        if in_section and line_stripped.startswith("[") and line_stripped.endswith("]"):
            break

        # 在目标section中查找key
        if in_section and line_stripped.startswith(f"{key} ="):
            # 查找key前的注释
            for j in range(i - 1, -1, -1):
                comment_line = lines[j].strip()
                if comment_line.startswith("#"):
                    return comment_line
                elif comment_line == "":
                    continue
                else:
                    break
            break

    return ""


def _generate_config_content_with_comments(
    merged_data: Dict[str, Any], template_content: str, existing_content: str
) -> str:
    """生成包含注释的配置文件内容"""
    lines = []

    # 添加文件头注释
    lines.append("# Amaidesu 配置文件")
    lines.append("# 此文件从 config-template.toml 自动生成")
    lines.append("")

    # 为每个section生成内容
    for section, values in merged_data.items():
        # 添加section注释（从模板中提取）
        section_comment = _extract_section_comment(template_content, section)
        if section_comment:
            lines.append(section_comment)

        lines.append(f"[{section}]")

        if isinstance(values, dict):
            for key, value in values.items():
                # 添加key的注释（从模板中提取）
                key_comment = _extract_key_comment(template_content, section, key)
                if key_comment:
                    lines.append(key_comment)

                # 添加配置项
                lines.append(f"{key} = {_format_toml_value(value)}")
        else:
            lines.append(f"value = {_format_toml_value(values)}")

        lines.append("")

    return "\n".join(lines)


def _update_config_from_template(config_path: str, template_path: str) -> None:
    """从模板更新配置文件，保留用户的自定义设置和注释"""
    logger.info(f"[配置更新] 开始从模板更新配置文件: {config_path}")

    try:
        # 读取模板和现有配置
        template_data, template_content = _parse_toml_with_comments(template_path)

        # 如果配置文件存在，读取现有配置
        existing_data = {}
        existing_content = ""
        if os.path.exists(config_path):
            existing_data, existing_content = _parse_toml_with_comments(config_path)

        # 创建备份
        if os.path.exists(config_path):
            backup_path = config_path + ".backup"
            shutil.copy2(config_path, backup_path)
            logger.info(f"[配置更新] 已创建备份文件: {backup_path}")

        # 合并配置：以模板为基础，保留用户的自定义值
        merged_data = {}

        # 首先添加模板中的所有配置项
        for section, values in template_data.items():
            if isinstance(values, dict):
                merged_data[section] = {}
                for key, template_value in values.items():
                    # 如果现有配置中有相同的键，且不是默认值，则保留用户设置
                    if (
                        section in existing_data
                        and key in existing_data[section]
                        and existing_data[section][key] != template_value
                        and not (key == "api_key" and existing_data[section][key] == "your-api-key")
                        and not (section == "inner" and key == "version")
                    ):
                        merged_data[section][key] = existing_data[section][key]
                        logger.info(f"[配置更新] 保留用户设置: {section}.{key} = {existing_data[section][key]}")
                    else:
                        merged_data[section][key] = template_value
            else:
                merged_data[section] = values

        # 生成新的配置文件内容，保留注释
        new_content = _generate_config_content_with_comments(merged_data, template_content, existing_content)

        # 写入更新后的配置
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        logger.info(f"[配置更新] 配置文件已更新: {config_path}")

    except Exception as e:
        logger.error(f"[配置更新] 更新配置文件失败: {e}")
        raise


def check_and_update_config_with_version(
    config_path: str, template_path: str
) -> bool:
    """检查配置文件版本，如果需要则从模板更新

    Args:
        config_path: 配置文件路径
        template_path: 模板文件路径

    Returns:
        True 如果配置文件被更新，否则 False
    """
    if not os.path.exists(config_path):
        logger.debug(f"[配置检查] 配置文件不存在，跳过版本检查: {config_path}")
        return False

    if not os.path.exists(template_path):
        logger.warning(f"[配置检查] 模板文件不存在，跳过版本检查: {template_path}")
        return False

    try:
        template_version = _get_version_from_toml(template_path)
        config_version = _get_version_from_toml(config_path)

        if template_version and config_version:
            if _compare_versions(template_version, config_version):
                logger.info(f"[配置更新] 检测到模板版本更新: {config_version} -> {template_version}")
                _update_config_from_template(config_path, template_path)
                return True
            else:
                logger.debug(f"[配置检查] 配置文件版本已是最新: {config_version}")
        else:
            logger.warning("[配置检查] 无法获取版本信息，跳过版本检查")

        return False

    except Exception as e:
        logger.warning(f"[配置检查] 版本检查失败: {e}")
        return False
