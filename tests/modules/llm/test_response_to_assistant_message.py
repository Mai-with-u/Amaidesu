"""response_to_assistant_message 的转换契约：响应转回工具循环可继续喂养的 assistant 消息。"""

from __future__ import annotations

import json

from src.modules.llm.payload import Reasoning, Response, ToolCall, response_to_assistant_message


def test_plain_text_response() -> None:
    """纯文本响应只产出 role 与 content 两个键，不引入空 tool_calls / reasoning 键。"""
    response = Response(success=True, content="你好，观众们")

    message = response_to_assistant_message(response)

    assert message == {"role": "assistant", "content": "你好，观众们"}


def test_tool_calls_become_protocol_shape() -> None:
    """中立 ToolCall 转成 OpenAI 协议的嵌套形状，参数回传为 JSON 字符串。"""
    response = Response(
        success=True,
        tool_calls=[
            ToolCall(id="call-1", name="reply", arguments={"text": "开饭了", "count": 2}),
            ToolCall(id="call-2", name="switch_scene", arguments={}),
        ],
    )

    message = response_to_assistant_message(response)

    assert message["role"] == "assistant"
    assert message["tool_calls"] == [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "reply", "arguments": json.dumps({"text": "开饭了", "count": 2}, ensure_ascii=False)},
        },
        {
            "id": "call-2",
            "type": "function",
            "function": {"name": "switch_scene", "arguments": "{}"},
        },
    ]


def test_structured_content_is_serialized() -> None:
    """content 为结构化非 str（部分 client 返回分段列表）时序列化为 JSON 字符串。"""
    structured = [{"type": "text", "text": '含"引号"与换行'}]
    response = Response(success=True, content=structured)

    message = response_to_assistant_message(response)

    assert message["content"] == json.dumps(structured, ensure_ascii=False, default=str)
    # 序列化结果必须是合法 JSON 字符串（协议要求 content 为 string 或 null）
    assert isinstance(message["content"], str)
    assert json.loads(message["content"]) == structured


def test_null_content_stays_none() -> None:
    """content 为 None 保持 None（协议允许 null），不序列化成字符串 "null"。"""
    response = Response(success=True, content=None)

    message = response_to_assistant_message(response)

    assert message["content"] is None


def test_reasoning_echo_is_carried() -> None:
    """模型开着思考回传开关时，思考原样写进 reasoning 键供下一轮带回同一个模型。"""
    echo = Reasoning(text="先看一下弹幕再决定", format="field", model="m")
    response = Response(success=True, content="来了", reasoning_echo=echo)

    message = response_to_assistant_message(response)

    assert message["reasoning"] == echo.model_dump()


def test_unserializable_argument_falls_back_to_str() -> None:
    """参数含不可 JSON 序列化的值时以 str 兜底，转换不抛异常导致整轮请求崩。"""

    class Opaque:
        def __str__(self) -> str:
            return "opaque"

    response = Response(success=True, tool_calls=[ToolCall(id="call-1", name="do", arguments={"obj": Opaque()})])

    message = response_to_assistant_message(response)

    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"obj": "opaque"}
