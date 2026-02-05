"""
Pytest configuration for InputProviderManager tests

This ensures all input providers are registered before tests run.
"""

# Import all input providers to trigger registration
import src.layers.input.providers.bili_danmaku  # noqa: F401
import src.layers.input.providers.bili_danmaku_official  # noqa: F401
import src.layers.input.providers.console_input  # noqa: F401
import src.layers.input.providers.mock_danmaku  # noqa: F401
import src.layers.input.providers.remote_stream  # noqa: F401
