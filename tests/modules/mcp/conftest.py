"""
Pytest fixtures for MCP module tests.

Provides httpx mocking to simulate API responses without a live server.
"""

from unittest.mock import patch

import httpx
import pytest


class MockResponse:
    """Mock httpx.Response object."""

    def __init__(
        self,
        json_data=None,
        status_code: int = 200,
        is_success: bool = True,
    ):
        self._json_data = json_data or {}
        self._status_code = status_code
        self._is_success = is_success
        self.content = b'{"success": true}' if is_success else b'{"success": false, "error": "API error"}'

    def is_success(self) -> bool:
        return self._is_success

    def json(self):
        return self._json_data


class MockAsyncClient:
    """Mock httpx.AsyncClient for testing."""

    def __init__(self, mock_behavior: str = "success"):
        self.mock_behavior = mock_behavior
        self.timeout = 10.0

    async def post(self, url: str, json=None):
        if self.mock_behavior == "timeout":
            raise httpx.TimeoutException("Connection timeout")
        elif self.mock_behavior == "error":
            return MockResponse(
                json_data={"success": False, "error": "API error"},
                status_code=500,
                is_success=False,
            )
        else:
            return MockResponse(json_data={"success": True, "intent_id": "test-intent-id"})

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


# Global client instance that can be configured per test
_mock_behavior = "success"


def get_mock_client(**kwargs):
    return MockAsyncClient(_mock_behavior)


def set_mock_client_behavior(behavior: str):
    global _mock_behavior
    _mock_behavior = behavior


@pytest.fixture(autouse=True)
def mock_httpx_based_on_test(request):
    """Auto-use fixture that patches httpx based on test name."""
    # Reset to default (success) behavior
    set_mock_client_behavior("success")

    # Check test name and set appropriate mocking
    test_name = request.node.name

    if "timeout" in test_name:
        set_mock_client_behavior("timeout")
    elif "api_error" in test_name:
        set_mock_client_behavior("error")

    # Patch httpx.AsyncClient in the service module where it's used
    with patch("src.modules.mcp.service.httpx.AsyncClient", side_effect=get_mock_client):
        yield
