"""
Pytest configuration for InputCollectorManager tests

W5 迁移后仅保留 text_adv_game；其他旧 Collector 已迁移到 src/modules/collectors/。
"""

# 触发装饰器注册：未迁移组件 + 框架层 Collector（让 conftest 可独立导入）
import src.modules.collectors  # noqa: F401  - 框架层（BaseCollector + CollectorManager）
import src.modules.collectors.bilibili  # noqa: F401  - B 站官方版 + 旧版
import src.modules.collectors.console  # noqa: F401
import src.modules.collectors.mock  # noqa: F401
import src.modules.collectors.screen  # noqa: F401
import src.modules.collectors.stt  # noqa: F401
import src.modules.events.interceptors  # noqa: F401  - 拦截器（rate_limit + similar_filter）

# 未迁移组件（仍走旧路径，供 InputCollectorManager 兼容）
import src.stages.input.collectors  # noqa: F401
import src.stages.input.collectors.text_adv_game  # noqa: F401
