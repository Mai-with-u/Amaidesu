"""上下文原语：分层布局下各 Agent 共用的预算、计量、检查点与缓存纪律工具。

一次请求按易变程度分四层，越稳定越靠前，服务端前缀缓存才能命中：

- L0 固定层：系统提示词 + 工具声明；
- L1 纪要层：检查点摘要（按章只追加）+ 不经摘要的固定事实；
- L2 工作层：上次检查点以来的原始记录，只追加；
- L3 尾部：本次请求现拼的情境、相对时间、易逝图，用完即弃。

本包只提供与具体 Agent 无关的机制；每层放什么、摘要怎么写，留在各 Agent 包内。
"""

from src.modules.context.budget import UNKNOWN_WINDOW_TOKENS, ContextBudgetConfig, Watermarks, resolve_watermarks
from src.modules.context.checkpoint import BackgroundCheckpoint, fold_count, group_messages
from src.modules.context.prefix import PrefixGuard
from src.modules.context.tokens import IMAGE_TOKEN_ESTIMATE, TokenCounter
from src.modules.context.transient_images import TransientImages, absorb_result_images, is_visual_caller

__all__ = [
    "BackgroundCheckpoint",
    "ContextBudgetConfig",
    "IMAGE_TOKEN_ESTIMATE",
    "PrefixGuard",
    "TokenCounter",
    "TransientImages",
    "UNKNOWN_WINDOW_TOKENS",
    "Watermarks",
    "absorb_result_images",
    "fold_count",
    "group_messages",
    "is_visual_caller",
    "resolve_watermarks",
]
