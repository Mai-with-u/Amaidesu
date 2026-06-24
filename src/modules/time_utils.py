"""时间工具模块

项目内所有时刻字段统一使用 **int 毫秒**（Unix epoch milliseconds）作为标准时间表示，
时间相关的工具函数都集中在该模块中，避免在多个文件中重复实现 `time.time() * 1000`。

设计原则：
- 时刻字段（如事件时间戳）使用 `int` 毫秒，避免浮点精度问题。
- 时长字段（如超时、节流间隔）同样使用 `int` 毫秒，与时刻字段保持单位一致。
- 该模块不提供秒 ↔ 毫秒的单位转换函数，因为整个项目都统一为毫秒。
"""

import time
from datetime import datetime, timezone


def now_ms() -> int:
    """获取当前 Unix 毫秒时间戳

    Returns:
        int: 当前时间的 Unix epoch 毫秒数（13 位数量级，例如 1729612345678）
    """
    return int(time.time() * 1000)


def elapsed_ms(start_ms: int) -> int:
    """计算从过去的某个毫秒时刻到现在的经过时长

    Args:
        start_ms: 过去的起始时刻（Unix 毫秒时间戳）

    Returns:
        int: 经过的毫秒数。如果 start_ms 大于当前时间，返回 0。
    """
    diff = now_ms() - start_ms
    return diff if diff > 0 else 0


def format_duration_ms(ms: int) -> str:
    """将毫秒时长格式化为人类可读字符串

    输出格式（按范围自适应）：
    - 小于 1 秒：`"500ms"`
    - 小于 1 分钟：`"12.3s"`
    - 小于 1 小时：`"5m 23s"`
    - 大于等于 1 小时：`"1h 23m 45s"`

    Args:
        ms: 时长（毫秒，非负整数）

    Returns:
        str: 人类可读的时长字符串
    """
    if ms < 0:
        return "0ms"

    if ms < 1000:
        return f"{ms}ms"

    total_seconds = ms / 1000

    if total_seconds < 60:
        # 小于 1 分钟：显示带 1 位小数的秒
        return f"{total_seconds:.1f}s"

    total_minutes = int(total_seconds // 60)
    remaining_seconds = int(total_seconds - total_minutes * 60)

    if total_minutes < 60:
        # 小于 1 小时：显示 "Xm Ys"
        return f"{total_minutes}m {remaining_seconds}s"

    hours = total_minutes // 60
    minutes = total_minutes % 60
    seconds = remaining_seconds

    return f"{hours}h {minutes}m {seconds}s"


def ms_to_datetime(ms: int) -> datetime:
    """将毫秒时间戳转换为 datetime 对象（仅用于显示）

    该函数返回带 UTC 时区的 datetime 对象，便于日志显示与人类阅读，
    不应用于序列化或作为数据载体。

    Args:
        ms: Unix 毫秒时间戳

    Returns:
        datetime: 对应的 datetime 对象（UTC 时区）
    """
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
