"""进程内后台长任务的登记处：让"正在后台跑的事"在 WebUI 看得见。

接入名单是任务型的长任务（有开始、有结束、可报进度）：下播整理、记忆索引补算。
常驻循环型（重连循环、摘要 worker）没有终态，不进面板。设计定案见
``.omo/drafts/settle-visibility-design.md`` §1。
"""

from .registry import (
    BackgroundTaskInfo,
    BackgroundTaskRegistry,
    get_registry,
    setup_registry,
)

__all__ = [
    "BackgroundTaskInfo",
    "BackgroundTaskRegistry",
    "get_registry",
    "setup_registry",
]
