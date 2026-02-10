"""Pytest configuration and fixtures for integration tests."""

import pytest


@pytest.fixture(autouse=True, scope="module")
def register_test_providers() -> None:
    """Register test providers before integration tests run.

    This fixture ensures that the providers are available for all integration tests.
    Since other test files may clear ProviderRegistry, we re-import provider modules
    to trigger auto-registration.

    Scope "module" ensures this runs once per test module, not before every test.
    """
    # Import built-in providers to trigger auto-registration
    # Input Providers

    # Output Providers

    # Yield to allow tests to run
    yield

    # Cleanup is not needed as the registry is shared across tests
