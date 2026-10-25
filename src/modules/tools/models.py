"""
工具契约数据类

- 全部 ``slots=True`` 减少内存占用
- 时刻/时长字段约定：``*_ms``（毫秒 int）；不在本模块涉及时间字段
- 多模态结果：``ResultBlock(kind)`` 支持 "text" / "image"
- ``kind``：sync / async（唯一判别维度）
- ``provider``：提供者标识（vts / warudo / obs / vision / memory / maicraft ...）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# 工具判别维度：sync（gather 等齐）/ async（fire-and-forget + 事件回传）
Kind = Literal["sync", "async"]

# 多模态结果块种类
BlockKind = Literal["text", "image"]

# 默认异步结果事件名（前缀）；可定制（如 some_tool → tool.result.some_tool_feedback）
DEFAULT_RESULT_EVENT_PREFIX = "tool.result."


@dataclass(slots=True)
class ToolSpec:
    """工具规格（注册时声明；Planner 看到的就是这个）

    Attributes:
        name: 工具声明名（provider 内唯一，**不带前缀**）；对外全名 =
              ``full_name``（``<provider>_<name>`` 动态派生，不存下来）
        description: LLM 看的工具描述
        parameters_schema: JSON Schema 形态的参数描述（主流共识）；可为 None
        kind: "sync"（gather 等齐结果）/ "async"（fire-and-forget）
        result_event: 异步工具结果事件名，默认 ``tool.result.<全名>``；
                      可定制（如 some_tool → ``tool.result.some_tool_feedback``）
        provider: 提供者标识（如 vts / warudo / obs / vision / memory /
                  maicraft；溯源、全名前缀与过滤用，非白名单枚举）
        output_schema: 可选的 JSON Schema 形态的输出描述
        result_section: 调用结果在上下文计量中另记的分段（如技能读取记入
                        ``skills``）；空串 = 计入对话消息
    """

    name: str
    description: str
    parameters_schema: Optional[Dict[str, Any]] = None
    kind: Kind = "sync"
    result_event: str = ""
    provider: str = ""
    output_schema: Optional[Dict[str, Any]] = None
    result_section: str = ""

    @property
    def full_name(self) -> str:
        """派生唯一全名：``<provider>_<工具名>``。

        需要字符串的边界（注册表索引键、LLM 工具列表、调用路由、停用
        列表匹配、事件名）一律现算本值；全仓禁止手拼前缀或反向剥离，
        也不把派生值写回本对象。provider 为空（匿名工具）时全名即工具名。
        """
        if not self.provider:
            return self.name
        return f"{self.provider}_{self.name}"

    def resolve_result_event(self) -> str:
        """返回实际的异步结果事件名（默认 ``tool.result.<全名>``）。"""
        if self.result_event:
            return self.result_event
        return f"{DEFAULT_RESULT_EVENT_PREFIX}{self.full_name}"


@dataclass(slots=True)
class ResultBlock:
    """多模态结果块（当前轮给 LLM；历史中摘要化文本防膨胀）

    Attributes:
        kind: "text" / "image"
        text: 文本内容（``kind="text"`` 时使用）
        data: base64 编码数据（``kind="image"`` 时使用）
        mime_type: MIME 类型（``kind="image"`` 时使用，如 "image/png"）
    """

    kind: BlockKind = "text"
    text: str = ""
    data: str = ""
    mime_type: str = ""


@dataclass(slots=True)
class ToolExecutionResult:
    """工具执行结果（同步工具返回；异步工具仅作回传失败/异常）

    Attributes:
        tool_name: 工具名
        success: 调用是否成功；业务拒绝也返回 False，供调用者修正请求
        content: 文本内容/历史摘要
        blocks: 多模态块列表（同步结果给 LLM；历史中已摘要化文本）
        error_message: 错误信息（success=False 时非空）
        structured_content: 结构化结果（如 dict，保留以备校验/未来使用）
        duration_ms: 执行耗时（毫秒）
        timestamp_ms: 完成时刻（毫秒）
        failure_kind: 业务拒绝或执行故障；未分类的失败沿用执行故障的健康计数
    """

    tool_name: str
    success: bool
    content: str = ""
    blocks: List[ResultBlock] = field(default_factory=list)
    error_message: str = ""
    structured_content: Any = None
    duration_ms: int = 0
    timestamp_ms: int = 0
    # 已收到业务拒绝说明服务可以应答，不能因调用者传错参数而摘除工具。
    failure_kind: Optional[Literal["business", "execution"]] = None


def tool_result_ok(tool_name: str, success: bool, structured: Any = None) -> ToolExecutionResult:
    """成功/业务结果工厂：``structured`` 非空时同步文本化进 ``content``。

    各平台 Provider 的工具实现共用同一种结果形状（原来在 vts / warudo 等
    provider 内各有一份同构私有工厂，收敛到结果类型旁统一定义）。
    """
    return ToolExecutionResult(
        tool_name=tool_name,
        success=bool(success),
        structured_content=structured,
        content="" if structured is None else str(structured),
    )


def tool_result_fail(tool_name: str, error_message: str) -> ToolExecutionResult:
    """失败结果工厂（``success=False``，仅携带错误信息）。"""
    return ToolExecutionResult(
        tool_name=tool_name,
        success=False,
        error_message=error_message,
    )


@dataclass(slots=True)
class ToolCallAttachment:
    """随调用附带的交接材料，registry 不感知，由具体工具的调用方与 Provider 约定形状。

    标记基类：框架层只提供类型位置，不定义任何字段——具体形状由各 Agent
    域内的子类承载（如 streamer 的 ``ReplyRoundAttachment``），保证依赖方向
    不反向（tools/ 不 import agents/）。与 ``arguments`` 的分工：arguments
    由 LLM 生成、模型可伪造；attachment 由宿主代码构造、模型不可见。
    """


@dataclass(slots=True)
class ToolInvocation:
    """工具调用请求（Planner → Registry → 执行函数）

    Attributes:
        tool_name: 工具名（对应 ToolSpec.name）
        arguments: 参数（dict 形态；执行函数自行解析具体 schema）
        call_id: 调用 ID（用于一次性回合内配对；可空）
        invoked_at_ms: 调用时刻（毫秒）
        source: 调用方标识（如 "planner-react" / "minecraft-react"）；
            经 registry invoke 后透传到 tool.result 事件的 caller_source
        round_id: 关联决策轮次 ID（调用发生在主播决策轮上下文内时填写；
            经 registry invoke 后透传到 tool.result 事件的 round_id）
        caller_profile: 调用方 LLM 用途档名（如 "planner"），供工具按调用方
            视觉能力分叉（ADR-037）。与 source 的分工：source 记"调用出于
            什么场景"（观测维度），本字段记"结果由谁消费"（能力维度）。
            管线式调用（结果不进任何 LLM 对话，如 text_adv 读屏）留空串
            ——空串按非视觉处理（保守降级）
        attachment: 随调用附带的交接材料（``ToolCallAttachment`` 子类，由调用方
            与具体工具的 Provider 约定形状）；None = 本窗无宿主材料。与
            arguments 的分工：arguments 由 LLM 生成、模型可伪造，attachment
            由宿主代码构造、不经 LLM
    """

    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    invoked_at_ms: int = 0
    source: str = ""
    round_id: str = ""
    caller_profile: str = ""
    attachment: Optional[ToolCallAttachment] = None


__all__ = [
    "Kind",
    "BlockKind",
    "DEFAULT_RESULT_EVENT_PREFIX",
    "ToolSpec",
    "ResultBlock",
    "ToolExecutionResult",
    "tool_result_ok",
    "tool_result_fail",
    "ToolCallAttachment",
    "ToolInvocation",
]
