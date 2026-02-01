"""测试配置迁移后的功能"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

# 设置 UTF-8 编码输出
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_config_utils_import():
    """测试 utils/config 导入"""
    print("测试 1: 导入新迁移的函数...")
    try:
        from src.utils.config import (
            check_and_update_config_with_version,
            _get_version_from_toml,
            _compare_versions,
            _update_config_from_template,
        )
        print("[PASS] 所有函数导入成功")
        return True
    except ImportError as e:
        print(f"[FAIL] 导入失败: {e}")
        return False


def test_version_parsing():
    """测试版本解析功能"""
    print("\n测试 2: 版本解析功能...")
    from src.utils.config import _get_version_from_toml, _compare_versions

    # 创建临时测试文件
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试 TOML 文件
        test_file = os.path.join(tmpdir, "test.toml")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write('[inner]\nversion = "1.0.0"\n')

        # 测试版本解析
        version = _get_version_from_toml(test_file)
        if version == "1.0.0":
            print(f"[PASS] 版本解析成功: {version}")
            success = True
        else:
            print(f"[FAIL] 版本解析失败: 期望 '1.0.0', 得到 {version}")
            success = False

        # 测试版本比较
        if _compare_versions("1.1.0", "1.0.0"):
            print("[PASS] 版本比较正确: 1.1.0 > 1.0.0")
        else:
            print("[FAIL] 版本比较错误")
            success = False

        return success


def test_src_config_removed():
    """测试 src/config 目录已删除"""
    print("\n测试 3: 验证 src/config 目录已删除...")
    config_dir = os.path.join(os.path.dirname(__file__), "..", "src", "config")
    if not os.path.exists(config_dir):
        print("[PASS] src/config 目录已成功删除")
        return True
    else:
        print("[FAIL] src/config 目录仍然存在")
        return False


def test_no_references_to_old_config():
    """测试没有代码引用旧的 src.config"""
    print("\n测试 4: 检查是否有遗留引用...")
    src_dir = os.path.join(os.path.dirname(__file__), "..", "src")

    found_references = []
    for root, dirs, files in os.walk(src_dir):
        # 跳过 __pycache__ 和 .git
        dirs[:] = [d for d in dirs if d not in ["__pycache__", ".git", "venv"]]

        for file in files:
            if file.endswith(".py"):
                filepath = os.path.join(root, file)
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                    if "from src.config" in content or "import src.config" in content:
                        found_references.append(filepath)

    if found_references:
        print(f"[FAIL] 发现 {len(found_references)} 个文件仍然引用 src.config:")
        for ref in found_references:
            print(f"  - {ref}")
        return False
    else:
        print("[PASS] 没有发现对 src.config 的引用")
        return True


def main():
    """运行所有测试"""
    print("=" * 60)
    print("配置迁移验证测试")
    print("=" * 60)

    results = []
    results.append(test_config_utils_import())
    results.append(test_version_parsing())
    results.append(test_src_config_removed())
    results.append(test_no_references_to_old_config())

    print("\n" + "=" * 60)
    print(f"测试结果: {sum(results)}/{len(results)} 通过")
    print("=" * 60)

    if all(results):
        print("\n[SUCCESS] 所有测试通过！配置迁移成功！")
        return 0
    else:
        print("\n[WARNING] 部分测试失败，请检查")
        return 1


if __name__ == "__main__":
    exit(main())
