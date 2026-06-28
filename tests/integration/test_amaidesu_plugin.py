"""
Amaidesu 插件集成测试

测试 amaidesu_plugin 的功能:
1. Manifest 验证
"""

import pytest


def _get_amaidesu_plugin():
    """尝试导入 AmaidesuPlugin,如 maibot_sdk 不可用则返回 None"""
    try:
        from integrations.amaidesu_plugin.plugin import AmaidesuPlugin

        return AmaidesuPlugin
    except ModuleNotFoundError:
        return None


class TestAmaidesuPluginManifest:
    """测试插件清单"""

    def test_manifest_version(self):
        """验证 manifest 版本正确"""
        import json
        from pathlib import Path

        manifest_path = (
            Path(__file__).parent.parent.parent / "integrations" / "amaidesu_plugin" / "_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert manifest["manifest_version"] == 2
        assert manifest["id"] == "amaidesu.amaidesu-plugin"
        assert "sdk" in manifest
        assert manifest["sdk"]["min_version"] == "2.0.0"
