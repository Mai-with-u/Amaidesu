"""
Pytest configuration for InputCollectorManager tests

This ensures all input collectors are registered before tests run.
"""

import src.stages.input.collectors.bili_danmaku
import src.stages.input.collectors.bili_danmaku_official
import src.stages.input.collectors.console_input
import src.stages.input.collectors.mainosaba
import src.stages.input.collectors.mock_danmaku
import src.stages.input.collectors.read_pingmu
import src.stages.input.collectors.stt
