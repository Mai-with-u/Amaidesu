"""
配置转换工具：将旧格式转换为新格式

旧格式 -> 新格式:
- [plugins] → [perception] + [rendering]
- 插件配置 → Provider配置
- MaiCore配置 → [decision.providers.maicore]
"""

import sys
from typing import Dict, Any

# 修复Windows编码问题
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# 尝试导入tomllib (Python 3.11+)
try:
    import tomllib

    _HAS_TOMLLIB = True
except ModuleNotFoundError:
    _HAS_TOMLLIB = False

# 导入toml用于写入
try:
    import toml as toml_writer
except ImportError:
    toml_writer = None
    print("警告: 'toml' 库未安装，无法保存新配置。请运行: pip install toml", file=sys.stderr)


def load_old_config(config_path: str) -> Dict[str, Any]:
    """
    加载旧配置文件

    Args:
        config_path: 配置文件路径

    Returns:
        配置字典
    """
    print(f"📖 读取旧配置: {config_path}")
    try:
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        print(f"❌ 错误: 配置文件未找到: {config_path}")
        sys.exit(1)
    except tomllib.TOMLDecodeError as e:
        print(f"❌ 错误: 配置文件格式无效: {e}")
        sys.exit(1)
    except NameError:
        # 如果tomllib不可用，使用toml库
        try:
            return toml_writer.load(config_path)
        except Exception as e:
            print(f"❌ 错误: 加载配置文件时发生错误: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"❌ 错误: 加载配置文件时发生错误: {e}")
        sys.exit(1)


def convert_to_new_format(old_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    将旧配置转换为新格式

    旧格式:
    - 插件配置分散在 [plugins] 和各个插件配置文件中
    - 无perception/rendering/decision分组

    新格式:
    - [perception] - 输入源配置
    - [rendering] - 输出目标配置
    - [decision] - 决策Provider配置
    - [data_cache] - 缓存配置

    Args:
        old_config: 旧配置字典

    Returns:
        新配置字典
    """
    new_config = {}

    # 1. 保留全局配置
    for key in ["general", "llm", "llm_fast", "vlm", "context_manager", "avatar", "pipelines"]:
        if key in old_config:
            new_config[key] = old_config[key]

    # 2. 转换感知层配置(从插件推断)
    print("🔄 转换感知层配置...")
    new_config["perception"] = {"inputs": {}}
    plugins = old_config.get("plugins", {})
    enabled_plugins = plugins.get("enabled", [])

    # 输入类插件
    input_plugins = {
        "bili_danmaku": ("danmaku", "bilibili_danmaku"),
        "bili_danmaku_official": ("danmaku", "bilibili_danmaku_official"),
        "bili_danmaku_selenium": ("danmaku", "bilibili_danmaku_selenium"),
        "mock_danmaku": ("danmaku", "mock_danmaku"),
        "stt": ("audio", "stt"),
        "funasr_stt": ("audio", "funasr_stt"),
        "console_input": ("console", "console_input"),
        "read_pingmu": ("screen", "read_pingmu"),
    }

    perception_inputs = {}
    for plugin_name, (input_type, provider_type) in input_plugins.items():
        if plugin_name in enabled_plugins:
            perception_inputs[plugin_name] = {"type": provider_type, "enabled": True}
            print(f"  ✅ 输入插件: {plugin_name} -> {input_type}/{provider_type}")

    new_config["perception"]["inputs"] = perception_inputs

    # 3. 转换渲染层配置(从插件推断)
    print("🔄 转换渲染层配置...")
    new_config["rendering"] = {"outputs": {}}
    output_plugins = {
        "tts": ("audio", "tts", "edge"),
        "gptsovits_tts": ("audio", "tts", "gptsovits"),
        "subtitle": ("visual", "subtitle"),
        "sticker": ("visual", "sticker"),
        "emotion_judge": ("visual", "emotion_judge"),
        "vtube_studio": ("virtual", "vts"),
        "warudo": ("virtual", "warudo"),
    }

    rendering_outputs = {}
    for plugin_name, (output_type, provider_type, *args) in output_plugins.items():
        if plugin_name in enabled_plugins:
            rendering_outputs[plugin_name] = {"type": provider_type, "enabled": True}
            if args:
                rendering_outputs[plugin_name]["provider"] = args[0]
            print(f"  ✅ 输出插件: {plugin_name} -> {output_type}/{provider_type}")

    new_config["rendering"]["outputs"] = rendering_outputs

    # 4. 配置决策层
    print("🔄 配置决策层...")
    new_config["decision"] = {
        "default_provider": "maicore",
        "providers": {
            "maicore": old_config.get("maicore", {"host": "127.0.0.1", "port": 8000}),
            "local_llm": {
                "model": old_config.get("llm", {}).get("model", "gpt-4"),
                "api_key": old_config.get("llm", {}).get("api_key", ""),
                "enabled": False,
            },
        },
    }
    print("  ✅ 决策Provider: maicore (默认)")

    # 5. 添加缓存配置(使用默认值)
    print("🔄 添加缓存配置...")
    new_config["data_cache"] = {
        "ttl_seconds": 300,
        "max_size_mb": 100,
        "max_entries": 1000,
        "eviction_policy": "ttl_or_lru",
    }
    print("  ✅ 缓存配置: TTL=300s, 最大=100MB")

    return new_config


def save_new_config(new_config: Dict[str, Any], output_path: str):
    """
    保存新配置文件

    Args:
        new_config: 新配置字典
        output_path: 输出文件路径
    """
    if toml_writer is None:
        print("❌ 错误: 'toml' 库未安装，无法保存新配置。请运行: pip install toml")
        sys.exit(1)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            toml_writer.dump(new_config, f)
        print(f"✅ 新配置已保存到: {output_path}")
    except Exception as e:
        print(f"❌ 错误: 保存新配置失败: {e}")
        sys.exit(1)


def main():
    """
    主函数
    """
    if len(sys.argv) < 2:
        print("用法: python config_converter.py <旧配置文件路径> [新配置文件路径]")
        print("")
        print("示例:")
        print("  python config_converter.py config.toml config-new.toml")
        print("  python config_converter.py config.toml  (自动保存为config-new.toml)")
        sys.exit(1)

    old_config_path = sys.argv[1]
    new_config_path = sys.argv[2] if len(sys.argv) > 2 else "config-new.toml"

    print("\n" + "=" * 60)
    print("Amaidesu 配置转换工具")
    print("=" * 60 + "\n")

    # 1. 加载旧配置
    old_config = load_old_config(old_config_path)

    # 2. 转换为新格式
    print("\n🔄 转换为新格式...")
    new_config = convert_to_new_format(old_config)

    # 3. 保存新配置
    print(f"\n💾 保存新配置: {new_config_path}")
    save_new_config(new_config, new_config_path)

    print("\n" + "=" * 60)
    print("✅ 转换完成！")
    print("=" * 60)
    print("\n⚠️  请检查新生成的配置文件，必要时调整配置项。")
    print("\n📝 接下来的步骤:")
    print("  1. 检查 config-new.toml 中的配置")
    print("  2. 填入必要的配置项 (如 API密钥、房间号等)")
    print("  3. 重命名为 config.toml 或在 main.py 中指定配置文件")
    print("  4. 运行程序测试: python main.py")


if __name__ == "__main__":
    main()
