"""日志历史查询 Schema

日志查看器历史查询端点（``GET /api/v1/logs``）的请求/响应数据模型。
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class LogEntryItem(BaseModel):
    """单条落盘日志（对照 LogStreamer 落盘行的形状）。"""

    timestamp: str = Field(description="日志时刻（ISO 格式本地时间）")
    level: str = Field(description="日志级别名，如 INFO / WARNING / ERROR")
    module: str = Field(description="模块名（logger 参数名）")
    message: str = Field(description="日志正文")
    exception: Optional[str] = Field(default=None, description="异常堆栈（仅异常路径日志携带）")


class LogQueryResponse(BaseModel):
    """日志历史查询响应（时间升序窗口，双向游标）。"""

    entries: List[LogEntryItem] = Field(default_factory=list)
    total: int = Field(default=0, description="本次返回条数")
    has_more_older: bool = Field(default=False, description="窗口内是否还有更早的命中条目")
    has_more_newer: bool = Field(default=False, description="窗口内是否还有更晚的命中条目")
    persist_available: bool = Field(default=True, description="落盘是否可用（未启用/目录缺失时为 False，历史降级为空）")
    window_start_ms: int = Field(default=0, description="实际生效的窗口起点（Unix 毫秒；缺省=本次启动时刻）")
