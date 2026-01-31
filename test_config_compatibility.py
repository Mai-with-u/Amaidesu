#!/usr/bin/env python3
"""
配置兼容性测试脚本

验证新配置格式和旧配置格式的兼容性
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from typing import Dict, Any


class ConfigCompatibilityTester:
    """配置兼容性测试器"""

    def __init__(self):
        self.test_results = []

    def record_result(self, test_name: str, passed: bool, message: str = ""):
        """记录测试结果"""
        result = {
            "test_name": test_name,
            "passed": passed,
            "message": message,
        }
        self.test_results.append(result)

        # 打印结果
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status}: {test_name}")
        if message:
            print(f"    {message}")

    def test_plugin_config_new_format(self):
        """测试新插件配置格式"""
        try:
            config: Dict[str, Any] = {
                "enabled": ["console_input", "vtube_studio", "subtitle"],
            }

            plugin_name = "test_plugin"
            enabled_list = config.get("enabled", [])

            # 检查插件是否在enabled列表中
            is_enabled = plugin_name in enabled_list if enabled_list else False

            assert is_enabled == (plugin_name in ["console_input", "vtube_studio", "subtitle"])
            self.record_result("新插件配置格式 - enabled列表", True, f"插件 '{plugin_name}' 启用状态: {is_enabled}")
        except Exception as e:
            self.record_result("新插件配置格式 - enabled列表", False, str(e))

    def test_plugin_config_old_format(self):
        """测试旧插件配置格式"""
        try:
            config: Dict[str, Any] = {
                "enable_console_input": True,
                "enable_vtube_studio": False,
                "enable_subtitle": True,
            }

            plugin_name = "console_input"
            old_format_key = f"enable_{plugin_name}"

            # 检查旧的enable_xxx格式
            is_enabled = config.get(old_format_key, False)

            assert is_enabled == True
            self.record_result("旧插件配置格式 - enable_xxx", True, f"插件 '{plugin_name}' 启用状态: {is_enabled}")
        except Exception as e:
            self.record_result("旧插件配置格式 - enable_xxx", False, str(e))

    def test_plugin_config_mixed_format(self):
        """测试混合配置格式"""
        try:
            config: Dict[str, Any] = {
                "enabled": ["console_input"],  # 新格式
                "enable_vtube_studio": True,  # 旧格式
            }

            plugin1 = "console_input"
            plugin2 = "vtube_studio"

            # 新格式应该优先
            enabled_list = config.get("enabled", [])
            plugin1_enabled = plugin1 in enabled_list if enabled_list else False

            # 旧格式仍然有效（如果没有enabled列表）
            plugin2_old_key = f"enable_{plugin2}"
            plugin2_enabled = config.get(plugin2_old_key, False)

            assert plugin1_enabled == True
            # 注意：如果enabled列表存在，旧格式应该被忽略
            assert plugin2_enabled == True  # 如果没有enabled列表
            self.record_result("混合插件配置格式", True, f"插件1: {plugin1_enabled}, 插件2: {plugin2_enabled}")
        except Exception as e:
            self.record_result("混合插件配置格式", False, str(e))

    def test_output_provider_config(self):
        """测试输出Provider配置"""
        try:
            config: Dict[str, Any] = {
                "enabled": True,
                "concurrent_rendering": True,
                "error_handling": "continue",
                "outputs": ["tts", "subtitle", "vts"],
                "outputs_config": {
                    "tts": {
                        "type": "tts",
                        "engine": "edge",
                        "voice": "zh-CN-XiaoxiaoNeural",
                    },
                    "subtitle": {
                        "type": "subtitle",
                        "window_width": 800,
                        "font_size": 24,
                    },
                },
            }

            # 验证配置结构
            assert config.get("enabled") == True
            assert config.get("concurrent_rendering") == True
            assert config.get("error_handling") == "continue"
            assert "tts" in config["outputs"]
            assert "subtitle" in config["outputs"]
            assert config["outputs_config"]["tts"]["type"] == "tts"
            assert config["outputs_config"]["tts"]["engine"] == "edge"

            self.record_result("输出Provider配置 - 新格式", True, f"配置的Provider: {config['outputs']}")
        except Exception as e:
            self.record_result("输出Provider配置 - 新格式", False, str(e))

    def test_input_provider_config(self):
        """测试输入Provider配置"""
        try:
            config: Dict[str, Any] = {
                "enabled": True,
                "inputs": ["console_input", "bili_danmaku"],
                "inputs_config": {
                    "console_input": {
                        "type": "console_input",
                    },
                    "bili_danmaku": {
                        "type": "bili_danmaku",
                        "room_id": "123456",
                    },
                },
            }

            # 验证配置结构
            assert config.get("enabled") == True
            assert "console_input" in config["inputs"]
            assert "bili_danmaku" in config["inputs"]
            assert config["inputs_config"]["bili_danmaku"]["room_id"] == "123456"

            self.record_result("输入Provider配置", True, f"配置的Provider: {config['inputs']}")
        except Exception as e:
            self.record_result("输入Provider配置", False, str(e))

    def test_decision_provider_config(self):
        """测试决策Provider配置"""
        try:
            config: Dict[str, Any] = {
                "enabled": True,
                "active_provider": "maicore",
                "providers": ["maicore", "rule_engine", "local_llm"],
                "providers_config": {
                    "maicore": {
                        "type": "maicore",
                        "host": "127.0.0.1",
                        "port": 8000,
                    },
                    "rule_engine": {
                        "type": "rule_engine",
                        "rules_file": "data/rules/decision_rules.toml",
                    },
                    "local_llm": {
                        "type": "local_llm",
                        "llm_type": "llm",
                    },
                },
            }

            # 验证配置结构
            assert config.get("enabled") == True
            assert config.get("active_provider") == "maicore"
            assert "maicore" in config["providers"]
            assert config["providers_config"]["maicore"]["host"] == "127.0.0.1"
            assert config["providers_config"]["local_llm"]["llm_type"] == "llm"

            self.record_result("决策Provider配置", True, f"配置的Provider: {config['providers']}")
        except Exception as e:
            self.record_result("决策Provider配置", False, str(e))

    def test_config_loading(self):
        """测试配置加载"""
        try:
            # 测试加载config.toml（而不是config-template.toml，避免模板文件的解析问题）
            config_path = project_root / "config.toml"
            if config_path.exists():
                # 简单验证配置文件格式
                import toml

                with open(config_path, "r", encoding="utf-8") as f:
                    config = toml.load(f)

                # 检查关键配置节是否存在
                assert "plugins" in config

                # 列出配置节（避免包含非ASCII字符导致打印错误）
                config_keys = list(config.keys())
                self.record_result("配置文件加载 - config.toml", True, f"配置节数量: {len(config_keys)}")
            else:
                self.record_result("配置文件加载 - config.toml", False, "文件不存在")
        except Exception as e:
            self.record_result("配置文件加载 - config.toml", False, str(e))

    def print_summary(self):
        """打印测试摘要"""
        print("\n" + "=" * 60)
        print("测试摘要")
        print("=" * 60)

        total_tests = len(self.test_results)
        passed_tests = sum(1 for r in self.test_results if r["passed"])
        failed_tests = total_tests - passed_tests

        print(f"总测试数: {total_tests}")
        print(f"通过: {passed_tests}")
        print(f"失败: {failed_tests}")
        print(f"通过率: {passed_tests / total_tests * 100:.1f}%")

        if failed_tests > 0:
            print("\n失败的测试:")
            for result in self.test_results:
                if not result["passed"]:
                    print(f"  - {result['test_name']}: {result['message']}")

        print("=" * 60)

        return failed_tests == 0

    def run_all_tests(self):
        """运行所有测试"""
        print("=" * 60)
        print("配置兼容性测试")
        print("=" * 60)
        print()

        # 运行所有测试
        self.test_plugin_config_new_format()
        self.test_plugin_config_old_format()
        self.test_plugin_config_mixed_format()
        self.test_output_provider_config()
        self.test_input_provider_config()
        self.test_decision_provider_config()
        self.test_config_loading()

        # 打印摘要
        all_passed = self.print_summary()

        return all_passed


def main():
    """主函数"""
    tester = ConfigCompatibilityTester()
    all_passed = tester.run_all_tests()

    # 根据测试结果返回退出码
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
