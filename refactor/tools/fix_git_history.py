"""
Git历史修复脚本
使用git mv将原始插件移动到plugins目录，保留Git历史
同时修复 Extension → Plugin 的命名不一致问题

问题1：在Phase 5插件迁移过程中，使用了git add而不是git mv，
        导致Git历史丢失。
问题2：Phase 5使用了Extension命名，与项目原有的Plugin术语不一致。
解决方案：使用git mv重新移动原始插件，保留完整的历史记录，
      同时将Extension重命名为Plugin，统一术语。
"""

import os
import subprocess
import sys
import re
from typing import List, Tuple

# 需要迁移的插件列表（21个已迁移到Extension系统的插件）
PLUGINS_TO_MIGRATE = [
    # B站弹幕系列
    "bili_danmaku",
    "bili_danmaku_official",
    "bili_danmaku_official_maicraft",
    # 优先级1插件（简单输入/输出）
    "console_input",
    "dg_lab_service",
    "emotion_judge",
    "gptsovits_tts",
    "keyword_action",
    "mock_danmaku",
    "remote_stream",
    "sticker",
    "stt",
    "subtitle",
    "tts",
    "vtube_studio",
    # 优先级2,3插件（复杂游戏交互）
    "maicraft",
    "mainosaba",
    "obs_control",
    "omni_tts",
    "read_pingmu",
    "screen_monitor",
    "vrchat",
    "warudo",
]


def run_command(cmd: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """运行shell命令"""
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"❌ Error: {result.stderr}")
        sys.exit(1)
    return result


def check_git_repo() -> bool:
    """检查是否在Git仓库中"""
    try:
        result = run_command("git rev-parse --is-inside-work-tree")
        return result.stdout.strip() == "true"
    except Exception:
        return False


def get_current_branch() -> str:
    """获取当前Git分支"""
    result = run_command("git branch --show-current")
    return result.stdout.strip()


def create_backup_branch(branch_name: str) -> bool:
    """创建备份分支"""
    # 检查分支是否已存在
    result = run_command("git branch --list", check=False)
    if branch_name in result.stdout:
        print(f"⚠️  备份分支 '{branch_name}' 已存在，跳过")
        return False

    run_command(f"git branch {branch_name}")
    print(f"✅ 创建备份分支: {branch_name}")
    return True


def migrate_plugin(plugin_name: str) -> Tuple[bool, str]:
    """
    迁移单个插件

    Args:
        plugin_name: 插件名称

    Returns:
        (是否成功, 消息)
    """
    src_path = f"src/plugins/{plugin_name}"
    temp_dst_path = f"src/plugins_new/{plugin_name}"

    # 检查源路径是否存在
    if not os.path.exists(src_path):
        return False, f"❌ 源路径不存在: {src_path}"

    # 使用git mv移动目录到临时位置
    try:
        # 确保目标目录存在
        os.makedirs("src/plugins_new", exist_ok=True)
        run_command(f"git mv {src_path} {temp_dst_path}")
        return True, f"✅ 迁移成功: {src_path} -> {temp_dst_path}"
    except Exception as e:
        return False, f"❌ 迁移失败: {e}"


def rename_extension_to_plugin(plugin_name: str) -> Tuple[bool, str]:
    """
    将Extension重命名为Plugin

    Args:
        plugin_name: 插件名称

    Returns:
        (是否成功, 消息)
    """
    plugin_dir = f"src/plugins_new/{plugin_name}"
    extension_file = f"{plugin_dir}/extension.py"

    if not os.path.exists(extension_file):
        return True, f"⏭️  跳过: {extension_file} 不存在"

    try:
        # 重命名文件
        new_file = f"{plugin_dir}/plugin.py"
        run_command(f"git mv {extension_file} {new_file}")

        # 读取并修改文件内容
        with open(new_file, "r", encoding="utf-8") as f:
            content = f.read()

        # 替换类名和引用
        replacements = {
            r"Extension": "Plugin",
            r"BaseExtension": "BasePlugin",
            r"ExtensionManager": "PluginManager",
            r"ExtensionInfo": "PluginInfo",
        }

        for pattern, replacement in replacements.items():
            content = re.sub(pattern, replacement, content)

        # 写回文件
        with open(new_file, "w", encoding="utf-8") as f:
            f.write(content)

        run_command(f"git add {new_file}")
        return True, f"✅ 重命名成功: {extension_file} -> {new_file}"
    except Exception as e:
        return False, f"❌ 重命名失败: {e}"


def delete_old_plugins_dir():
    """删除旧的plugins目录"""
    if os.path.exists("src/plugins"):
        # 删除旧的plugins目录中的已迁移插件
        for plugin_name in PLUGINS_TO_MIGRATE:
            plugin_path = f"src/plugins/{plugin_name}"
            if os.path.exists(plugin_path):
                run_command(f"git rm -rf {plugin_path}")
                print(f"✅ 删除旧插件: {plugin_path}")
        return True
    return False


def rename_extensions_to_plugins():
    """重命名extensions目录为plugins_new"""
    if os.path.exists("src/extensions"):
        run_command("git mv src/extensions src/extensions_old")
        print("✅ 重命名: src/extensions -> src/extensions_old")
        return True
    return False


def rename_plugins_new_to_plugins():
    """重命名plugins_new为plugins"""
    if os.path.exists("src/plugins_new"):
        run_command("git mv src/plugins_new src/plugins")
        print("✅ 重命名: src/plugins_new -> src/plugins")
        return True
    return False


def rename_core_files():
    """重命名核心文件"""
    renames = [
        ("src/core/extension.py", "src/core/plugin.py"),
        ("src/core/extension_manager.py", "src/core/plugin_manager.py"),
        ("src/core/extensions/", "src/core/plugins/"),
    ]

    for src, dst in renames:
        if os.path.exists(src):
            run_command(f"git mv {src} {dst}")
            print(f"✅ 重命名核心文件: {src} -> {dst}")
            return True
    return False


def update_imports():
    """更新所有导入语句"""
    try:
        # 查找所有Python文件
        result = run_command("find src -name '*.py' -type f", check=False)
        py_files = result.stdout.strip().split("\n")

        updated_count = 0
        for py_file in py_files:
            if not py_file:
                continue

            try:
                with open(py_file, "r", encoding="utf-8") as f:
                    content = f.read()

                # 替换导入
                old_content = content
                content = re.sub(r"from.*\.extension import", "from .plugin import", content)
                content = re.sub(r"from src\.core\.extension import", "from src.core.plugin import", content)
                content = re.sub(
                    r"from src\.core\.extension_manager import", "from src.core.plugin_manager import", content
                )
                content = re.sub(r"from src\.core\.extensions\.", "from src.core.plugins.", content)

                if content != old_content:
                    with open(py_file, "w", encoding="utf-8") as f:
                        f.write(content)
                    run_command(f"git add {py_file}")
                    updated_count += 1
            except Exception as e:
                print(f"⚠️  警告: 无法处理 {py_file}: {e}")

        print(f"✅ 更新了 {updated_count} 个文件的导入语句")
        return updated_count > 0
    except Exception as e:
        print(f"❌ 更新导入失败: {e}")
        return False


def main():
    """主函数"""
    print("=" * 70)
    print("Git历史修复 + 命名统一脚本")
    print("=" * 70)
    print("\n目的:")
    print("1. 使用git mv保留插件Git历史")
    print("2. 将Extension重命名为Plugin，统一术语")
    print("修复:")
    print("- Phase 5插件迁移过程中丢失的Git历史")
    print("- Extension与Plugin命名不一致的问题\n")

    # 检查是否在Git仓库中
    if not check_git_repo():
        print("❌ 错误: 不在Git仓库中")
        sys.exit(1)

    # 获取当前分支
    current_branch = get_current_branch()
    print(f"当前分支: {current_branch}\n")

    # 创建备份分支
    backup_branch = "backup/before-git-history-fix"
    if create_backup_branch(backup_branch):
        print(f"\n💾 备份分支已创建: {backup_branch}")
        print("   如需回滚，运行: git checkout {backup_branch}\n")

    # 确认操作
    print(f"将执行以下操作:")
    print(f"1. 迁移 {len(PLUGINS_TO_MIGRATE)} 个插件到 src/plugins_new/")
    print(f"2. 重命名 extension.py → plugin.py")
    print(f"3. 重命名核心文件: extension → plugin")
    print(f"4. 删除旧的 src/plugins/ 中已迁移的插件")
    print(f"5. 重命名 src/extensions/ → src/extensions_old/")
    print(f"6. 重命名 src/plugins_new/ → src/plugins/")
    print(f"7. 更新所有导入语句\n")

    response = input("是否继续？(y/n): ")
    if response.lower() != "y":
        print("❌ 操作已取消")
        sys.exit(0)

    print("\n开始修复...")
    print("-" * 70)

    # 统计
    success_count = 0
    skip_count = 0
    error_count = 0

    # 步骤1: 迁移插件到临时位置
    print("\n【步骤1/7】迁移插件到 src/plugins_new/")
    print("-" * 70)
    for plugin_name in PLUGINS_TO_MIGRATE:
        success, message = migrate_plugin(plugin_name)
        print(message)
        if success:
            success_count += 1
        else:
            if "源路径不存在" in message:
                skip_count += 1
            else:
                error_count += 1

    # 步骤2: 重命名extension.py为plugin.py
    print("\n【步骤2/7】重命名 extension.py → plugin.py")
    print("-" * 70)
    for plugin_name in PLUGINS_TO_MIGRATE:
        success, message = rename_extension_to_plugin(plugin_name)
        if success:
            print(message)

    # 步骤3: 重命名核心文件
    print("\n【步骤3/7】重命名核心文件")
    print("-" * 70)
    rename_core_files()

    # 步骤4: 删除旧插件
    print("\n【步骤4/7】删除旧的 src/plugins/ 中已迁移的插件")
    print("-" * 70)
    delete_old_plugins_dir()

    # 步骤5: 重命名extensions为extensions_old
    print("\n【步骤5/7】重命名 src/extensions/ → src/extensions_old/")
    print("-" * 70)
    rename_extensions_to_plugins()

    # 步骤6: 重命名plugins_new为plugins
    print("\n【步骤6/7】重命名 src/plugins_new/ → src/plugins/")
    print("-" * 70)
    rename_plugins_new_to_plugins()

    # 步骤7: 更新导入语句
    print("\n【步骤7/7】更新所有导入语句")
    print("-" * 70)
    update_imports()

    # 显示结果
    print("\n" + "=" * 70)
    print("修复完成！")
    print("=" * 70)
    print(f"\n统计:")
    print(f"  ✅ 成功: {success_count}")
    print(f"  ⚠️  跳过: {skip_count}")
    print(f"  ❌ 失败: {error_count}")

    if skip_count > 0 or error_count > 0:
        print("\n⚠️  注意: 有一些插件需要手动处理")
        print("   1. 查看上面的跳过/失败信息")
        print("   2. 手动处理冲突或合并")

    print("\n📝 重要变更:")
    print("  - src/plugins_new/ → src/plugins/")
    print("  - src/extensions/ → src/extensions_old/")
    print("  - extension.py → plugin.py (所有插件)")
    print("  - Extension → Plugin (所有类名)")
    print("  - src/core/extension.py → src/core/plugin.py")

    print("\n下一步操作:")
    print("1. 检查修复结果:")
    print("   git status")
    print("\n2. 查看迁移的文件:")
    print("   git diff --cached --name-only")
    print("\n3. 提交修复:")
    print("   git add -A")
    print("   git commit -m 'fix: preserve git history and unify Plugin terminology")

    print("\n4. 验证Git历史:")
    print("   git log --follow src/plugins/maicraft/")
    print("   应该能看到完整的插件历史")

    print("\n5. 推送到远程:")
    print("   git push origin <branch-name>")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ 操作被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        sys.exit(1)
