"""Pytest configuration and fixtures for integration tests."""

import pytest
from typing import Dict, Any
from src.core.provider_registry import ProviderRegistry
from src.domains.input.providers.mock_danmaku.mock_danmaku_provider import MockDanmakuInputProvider


@pytest.fixture(autouse=True, scope="module")
def register_test_providers() -> None:
    """Register test providers before integration tests run.

    This fixture ensures that the mock_danmaku provider is available
    for all integration tests that require it.
    """
    # Check if mock_danmaku provider is already registered
    if "mock_danmaku" not in ProviderRegistry._input_providers:
        # Register the mock_danmaku provider
        ProviderRegistry.register_input(
            "mock_danmaku",
            MockDanmakuInputProvider,
            source="test:mock_danmaku"
        )

    # Yield to allow tests to run
    yield

    # Cleanup is not needed as the registry is shared across tests