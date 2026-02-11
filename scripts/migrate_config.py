#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置迁移脚本：从旧格式迁移到新格式

此脚本用于将旧配置键名迁移到新格式，使用 tomlkit 保留所有注释和格式。
- inputs -> enabled_inputs
- outputs -> enabled_outputs
"""

import io
import sys
from pathlib import Path

import tomlkit

# Windows 终端编码修复
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def migrate_main_config(config_path: Path) -> bool:
    """
    迁移主配置文件

    Args:
        config_path: 配置文件路径

    Returns:
        是否进行了迁移
    """
    if not config_path.exists():
        print(f"配置文件不存在: {config_path}")
        return False

    # 使用 tomlkit 加载配置（保留注释和格式）
    with open(config_path, "r", encoding="utf-8") as f:
        doc = tomlkit.load(f)

    migrated = False

    # 迁移 inputs -> enabled_inputs
    if "providers" in doc and "input" in doc["providers"]:
        input_section = doc["providers"]["input"]
        if "inputs" in input_section and "enabled_inputs" not in input_section:
            print("  迁移: providers.input.inputs -> providers.input.enabled_inputs")
            input_section.add(tomlkit.comment("已迁移: inputs -> enabled_inputs"))
            input_section["enabled_inputs"] = input_section["inputs"]
            del input_section["inputs"]
            migrated = True

    # 迁移 outputs -> enabled_outputs
    if "providers" in doc and "output" in doc["providers"]:
        output_section = doc["providers"]["output"]
        if "outputs" in output_section and "enabled_outputs" not in output_section:
            print("  迁移: providers.output.outputs -> providers.output.enabled_outputs")
            output_section.add(tomlkit.comment("已迁移: outputs -> enabled_outputs"))
            output_section["enabled_outputs"] = output_section["outputs"]
            del output_section["outputs"]
            migrated = True

    if migrated:
        # 写回文件，保留注释和格式
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))
        print(f"✓ 配置文件已迁移: {config_path}")
    else:
        print("ℹ 配置文件已是最新格式，无需迁移")

    return migrated


def main():
    """主函数"""
    import sys

    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.toml")

    print(f"正在迁移配置文件: {config_path}")
    migrate_main_config(config_path)


if __name__ == "__main__":
    main()
