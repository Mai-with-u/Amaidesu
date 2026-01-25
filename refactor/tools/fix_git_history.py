"""
Git历史修复脚本
使用git mv将原始插件移动到extensions目录，保留Git历史

问题：在Phase 5插件迁移过程中，使用了git add而不是git mv，
导致Git历史丢失。

解决方案：使用git mv重新移动原始插件，保留完整的历史记录。
"""

import os
import subprocess
import sys
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


def run_command(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    """
    运行shell命令

    Args:
        cmd: 要执行的命令
        check: 如果为True，非零退出码会抛出异常

    Returns:
        subprocess.CompletedProcess对象
    """
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
    """
    创建备份分支

    Args:
        branch_name: 备份分支名称

    Returns:
        是否成功创建
    """
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
    dst_path = f"src/extensions/{plugin_name}"

    # 检查源路径是否存在
    if not os.path.exists(src_path):
        return False, f"❌ 源路径不存在: {src_path}"

    # 检查目标路径是否存在
    if os.path.exists(dst_path):
        # 目标已存在，需要检查是否需要合并
        return False, f"⚠️  目标路径已存在: {dst_path}（需要手动合并）"

    # 使用git mv移动目录
    try:
        run_command(f"git mv {src_path} {dst_path}")
        return True, f"✅ 迁移成功: {src_path} -> {dst_path}"
    except Exception as e:
        return False, f"❌ 迁移失败: {e}"


def main():
    """主函数"""
    print("=" * 70)
    print("Git历史修复脚本")
    print("=" * 70)
    print("\n目的: 使用git mv将原始插件移动到extensions目录，保留Git历史")
    print("修复: Phase 5插件迁移过程中丢失的Git历史\n")

    # 检查是否在Git仓库中
    if not check_git_repo():
        print("❌ 错误: 不在Git仓库中")
        sys.exit(1)

    # 获取当前分支
    current_branch = get_current_branch()
    print(f"当前分支: {current_branch}\n")

    # 创建备份分支
    backup_branch = "backup/extensions-before-git-fix"
    if create_backup_branch(backup_branch):
        print(f"\n💾 备份分支已创建: {backup_branch}")
        print("   如需回滚，运行: git checkout {backup_branch}\n")

    # 确认操作
    print(f"将迁移 {len(PLUGINS_TO_MIGRATE)} 个插件:")
    for plugin in PLUGINS_TO_MIGRATE:
        print(f"  - {plugin}")

    response = input("\n是否继续？(y/n): ")
    if response.lower() != "y":
        print("❌ 操作已取消")
        sys.exit(0)

    print("\n开始迁移插件...")
    print("-" * 70)

    # 统计
    success_count = 0
    skip_count = 0
    error_count = 0

    # 迁移每个插件
    for plugin_name in PLUGINS_TO_MIGRATE:
        success, message = migrate_plugin(plugin_name)
        print(message)

        if success:
            success_count += 1
        else:
            if "⚠️" in message:
                skip_count += 1
            else:
                error_count += 1

    # 显示结果
    print("\n" + "=" * 70)
    print("迁移完成！")
    print("=" * 70)
    print(f"\n统计:")
    print(f"  ✅ 成功: {success_count}")
    print(f"  ⚠️  跳过: {skip_count}")
    print(f"  ❌ 失败: {error_count}")

    if skip_count > 0 or error_count > 0:
        print("\n⚠️  注意: 有一些插件需要手动处理")
        print("   1. 查看上面的跳过/失败信息")
        print("   2. 手动处理冲突或合并extension.py")
        print("   3. 运行测试确保功能正常")

    print("\n下一步操作:")
    print("1. 检查迁移结果:")
    print("   git status")
    print("\n2. 查看迁移的文件:")
    print("   git diff --cached --name-only")
    print("\n3. 提交修复:")
    print("   git add -A")
    print("   git commit -m 'fix: preserve git history for plugin migration using git mv'")
    print("\n4. 验证Git历史:")
    print("   git log --follow src/extensions/maicraft/")
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
