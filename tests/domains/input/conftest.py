"""
Pytest configuration for InputProviderManager tests

This ensures all input providers are registered before tests run.

Note: remote_stream has been moved to output domain as RemoteStreamOutputProvider.
"""

# Import all input providers to trigger registration
import src.domains.input.providers.bili_danmaku  # noqa: F401
import src.domains.input.providers.bili_danmaku_official  # noqa: F401
import src.domains.input.providers.console_input  # noqa: F401
import src.domains.input.providers.mock_danmaku  # noqa: F401
