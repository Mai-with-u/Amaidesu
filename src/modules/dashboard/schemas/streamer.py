"""Streamer 测试台 API Schema（主播发言调试）

定义主播发言测试面（DevTools「主播发言测试」）的请求/响应数据模型。

设计要点
--------
- ``test-decision`` 是**同步长请求**（两段 LLM 调用，典型 10~30s），
  前端需单独放宽 HTTP 超时；响应携带完整中间产物（DecisionPlan + 发言），
  让测试方能观测"为什么没回"而不只是最终台词。
- plan 内部结构保持 ``dict`` 透传（DecisionPlan 五字段），外层强类型外壳。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StreamerTestDanmakuItem(BaseModel):
    """模拟弹幕（单条）。"""

    nickname: str = Field(default="测试观众", description="观众昵称（空则用默认值）")
    text: str = Field(..., min_length=1, description="弹幕文本")


class StreamerTestDecisionRequest(BaseModel):
    """手动触发两阶段决策请求。

    ``batch`` 与 ``proactive`` 互斥：主动发言由房间状态驱动，不接受弹幕批次。
    """

    batch: List[StreamerTestDanmakuItem] = Field(default_factory=list)
    forced: bool = Field(default=False, description="强制响应（豁免 Planner 低置信度降级）")
    proactive: bool = Field(default=False, description="主动发言模式（空批次，绕过限流）")


class StreamerTestDecisionResponse(BaseModel):
    """两阶段决策结果视图（含中间产物）。"""

    success: bool
    message: Optional[str] = None
    error: Optional[str] = None
    trigger_reason: Optional[str] = None
    proactive: bool = False
    forced: bool = False
    elapsed_ms: Optional[int] = None
    plan: Optional[Dict[str, Any]] = None
    speech: Optional[str] = None
    emotion: Optional[str] = None
    utterance_id: Optional[str] = None


class StreamerStatusResponse(BaseModel):
    """主播 Agent 状态 + 运行统计（降级安全：agent 未注册时 available=false）。"""

    available: bool
    message: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    statistics: Dict[str, Any] = Field(default_factory=dict)


class TriggerProactiveRequest(BaseModel):
    """真实限流链路主动发言触发请求。"""

    topic_hint: Optional[str] = Field(default=None, description="话题提示（仅日志记录）")


class TriggerProactiveResponse(BaseModel):
    """触发置位响应（置位 ≠ 触发：下个 flush tick 经限流判定后才可能开口）。"""

    success: bool
    message: str
