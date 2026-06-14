"""
Pytest configuration for InputCollectorManager tests

This ensures all input collectors are registered before tests run.
"""

import src.domains.input.providers.bili_danmaku
import src.domains.input.providers.bili_danmaku_official
import src.domains.input.providers.bili_danmaku_official_maicraft
import src.domains.input.providers.console_input
import src.domains.input.providers.mainosaba
import src.domains.input.providers.mock_danmaku
import src.domains.input.providers.read_pingmu
import src.domains.input.providers.stt
