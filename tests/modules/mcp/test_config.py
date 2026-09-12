"""McpExternalConfig / McpServerConfig 配置校验单测

覆盖：
- McpServerConfig：http url 校验、stdio 必填、extra=forbid
- McpExternalConfig：servers 解析、enabled_servers、parse_extra 容错
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.modules.mcp.config import McpExternalConfig, McpServerConfig


class TestServerConfig:
    def test_http_url_valid(self) -> None:
        cfg = McpServerConfig(transport="http", url="http://127.0.0.1:8766/mcp")
        assert cfg.url == "http://127.0.0.1:8766/mcp"
        assert cfg.enabled is True

    def test_http_bad_url_rejected(self) -> None:
        with pytest.raises(ValidationError):
            McpServerConfig(transport="http", url="127.0.0.1:8766")

    def test_stdio_command(self) -> None:
        cfg = McpServerConfig(transport="stdio", command="npx", args=["-y", "server"])
        assert cfg.transport == "stdio"
        assert cfg.command == "npx"

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            McpServerConfig(transport="http", url="http://x/mcp", unknown_key=1)

    def test_timeout_default(self) -> None:
        cfg = McpServerConfig(transport="http", url="http://x/mcp")
        assert cfg.timeout_seconds >= 1.0


class TestExternalConfig:
    def test_parse_servers(self) -> None:
        raw = {
            "servers": {
                "serverA": {"transport": "http", "url": "http://127.0.0.1:8766/mcp"},
                "bad_server": {"transport": "stdio", "command": "npx", "args": ["-y", "mcp"]},
            }
        }
        cfg = McpExternalConfig.parse_extra(raw)
        assert len(cfg.servers) == 2
        assert cfg.enabled_servers() == ["serverA", "bad_server"]

    def test_enabled_filters_out_disabled(self) -> None:
        raw = {
            "servers": {
                "on": {"enabled": True, "url": "http://a/mcp"},
                "off": {"enabled": False, "url": "http://b/mcp"},
            }
        }
        cfg = McpExternalConfig.parse_extra(raw)
        assert cfg.enabled_servers() == ["on"]

    def test_parse_extra_none_and_garbage(self) -> None:
        assert McpExternalConfig.parse_extra(None).servers == {}
        assert McpExternalConfig.parse_extra("garbage").servers == {}

    def test_empty_servers(self) -> None:
        cfg = McpExternalConfig.parse_extra({})
        assert cfg.enabled_servers() == []

    def test_extra_keys_tolerated(self) -> None:
        # McpExternalConfig extra="allow"（未来扩展容错），未知键不抛
        cfg = McpExternalConfig.parse_extra({"servers": {}, "future_key": 1})
        assert cfg.servers == {}
