# 同步config-template.toml文件和上级目录下的/src/plugins文件夹的小工具
import os
import re
import sys

# 尝试使用 Python 3.11+ 的标准库 tomllib，如果失败则回退到第三方库 toml
try:
    import tomllib
except ImportError:
    try:
        import toml as tomllib
    except ImportError:
        print("错误: 需要 'toml' 或 'tomllib' 包。")
        print("对于 Python < 3.11, 请运行: pip install toml")
        sys.exit(1)

# --- 路径配置 ---
# 获取此脚本的绝对路径
_SCRIPT_PATH = os.path.abspath(__file__)
# 获取项目根目录 (dev_src 的上级目录)
PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_PATH))
# 定义插件目录和模板配置文件的路径
PLUGINS_DIR = os.path.join(PROJECT_ROOT, "src", "plugins")
CONFIG_TEMPLATE_PATH = os.path.join(PROJECT_ROOT, "config-template.toml")


def get_plugin_names():
    """扫描插件目录并返回所有有效插件的名称列表。"""
    plugin_names = []
    if not os.path.isdir(PLUGINS_DIR):
        print(f"警告: 插件目录未找到: {PLUGINS_DIR}")
        return []

    for item in os.listdir(PLUGINS_DIR):
        item_path = os.path.join(PLUGINS_DIR, item)
        # 如果一个项目是目录并且包含 __init__.py，则我们认为它是一个插件
        if os.path.isdir(item_path) and os.path.exists(os.path.join(item_path, "__init__.py")):
            plugin_names.append(item)
    return sorted(plugin_names)  # 排序以保证输出一致性


def update_config_template():
    """
    同步 config-template.toml 和插件目录：
    1. 移除配置中存在但目录中不存在的插件条目。
    2. 为目录中存在但配置中缺失的插件添加条目。
    """
    print(f"正在同步配置文件: {CONFIG_TEMPLATE_PATH}")

    if not os.path.exists(CONFIG_TEMPLATE_PATH):
        print(f"错误: 配置文件模板未找到: {CONFIG_TEMPLATE_PATH}")
        return

    # 1. 从目录结构中获取所有插件的名称
    actual_plugin_names = get_plugin_names()
    print(f"发现 {len(actual_plugin_names)} 个实际插件: {', '.join(actual_plugin_names)}")

    # --- Pass 1: Cleanup (移除失效条目) ---
    with open(CONFIG_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    cleaned_lines = []
    plugins_section_started = False
    plugins_section_ended = False
    orphaned_plugins_found = False

    enable_plugin_pattern = re.compile(r"^\s*enable_([a-zA-Z0-9_]+)\s*=")

    for line in lines:
        # 定位 [plugins] 区域
        if not plugins_section_started and line.strip() == "[plugins]":
            plugins_section_started = True
        elif plugins_section_started and not plugins_section_ended and line.strip().startswith("["):
            plugins_section_ended = True

        # 如果在 [plugins] 区域内
        if plugins_section_started and not plugins_section_ended:
            match = enable_plugin_pattern.match(line)
            if match:
                plugin_name_in_config = match.group(1)
                # 如果配置中的插件在实际目录中不存在，则跳过此行（即删除）
                if plugin_name_in_config not in actual_plugin_names:
                    print(f"  - 正在移除失效条目: {line.strip()}")
                    orphaned_plugins_found = True
                    continue

        cleaned_lines.append(line)

    if not orphaned_plugins_found:
        print("未发现失效的插件条目。")

    # 更新行列表以进行下一步处理
    lines = cleaned_lines

    # --- Pass 2: Addition (添加新条目) ---
    try:
        # 从清理后的内容重新解析配置
        config_data = tomllib.loads("".join(lines))
    except tomllib.TOMLDecodeError as e:
        print(f"错误: 解析清理后的 TOML 内容失败: {e}")
        return

    plugins_config = config_data.get("plugins", {})
    missing_plugins = []
    for name in actual_plugin_names:
        enable_key = f"enable_{name}"
        if enable_key not in plugins_config:
            missing_plugins.append(name)

    if not missing_plugins:
        print("未发现缺失的插件条目。")
        # 如果两个阶段都没有变化，则文件已是最新
        if not orphaned_plugins_found:
            print("配置已是最新，无需更改。")
            return
    else:
        print(f"发现 {len(missing_plugins)} 个缺失的插件条目: {', '.join(missing_plugins)}")

        try:
            plugins_section_index = next(i for i, line in enumerate(lines) if line.strip() == "[plugins]")
        except StopIteration:
            print("错误: 在配置文件中未找到 '[plugins]' 区域。无法添加新条目。")
            return

        # --- 新的、更智能的插入逻辑 ---

        # 1. 在 [plugins] 区域内找到所有可用的空行"插槽"以及区域的结束位置
        available_slots = []
        section_end_index = plugins_section_index + 1
        while section_end_index < len(lines) and not lines[section_end_index].strip().startswith("["):
            if not lines[section_end_index].strip():
                available_slots.append(section_end_index)
            section_end_index += 1

        # 准备要插入的新行
        lines_to_add = [f"enable_{name} = true\n" for name in sorted(missing_plugins)]
        plugins_to_insert_in_bulk = []

        # 2. 首先使用找到的空行"插槽"来添加新条目
        for line_content in lines_to_add:
            if available_slots:
                slot_index = available_slots.pop(0)
                print(f"  -> 正在使用第 {slot_index + 1} 行的空行添加: {line_content.strip()}")
                lines[slot_index] = line_content
            else:
                # 如果没有可用的插槽，则将剩余的条目收集起来以便批量插入
                plugins_to_insert_in_bulk.append(line_content)

        # 3. 如果有需要批量插入的条目，则在区域末尾插入它们
        if plugins_to_insert_in_bulk:
            print(f"  -> 正在区域末尾批量添加 {len(plugins_to_insert_in_bulk)} 个剩余插件。")
            """
            # 优雅地处理空行，确保格式美观
            # 只有当 [plugins] 区域不为空，且其最后一行不是空行时，才在前面加一个空行
            if section_end_index > plugins_section_index + 1 and lines[section_end_index - 1].strip():
                plugins_to_insert_in_bulk.insert(0, '\n')
            
            # 如果插入点不是文件末尾，且其后不是空行，则在后面加一个空行
            if section_end_index < len(lines) and lines[section_end_index].strip():
                plugins_to_insert_in_bulk.append('\n')
            """
            # 在区域末尾（下一个section之前）插入剩余的插件条目
            lines[section_end_index:section_end_index] = plugins_to_insert_in_bulk

    # --- Final Step: Write back to file ---
    try:
        with open(CONFIG_TEMPLATE_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print("成功同步 config-template.toml 文件。")
    except IOError as e:
        print(f"错误: 写入文件失败: {e}")


if __name__ == "__main__":
    update_config_template()
