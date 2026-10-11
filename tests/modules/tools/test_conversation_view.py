"""对话观察渲染契约单元测试：安全视图滤除 + 视觉带图消息组装。

判定契约：长度超过 1KB、仅含 base64 字符集（A-Za-z0-9+/=）且无空白
字符的字符串判为二进制载荷，替换为带真实 KB 数的占位符；中文长文、
URL、JSON 文本等合法长内容必须原样保留。视觉调用方的带图观察由
extract_image_data_urls + build_tool_message 组装（ADR-037）。
"""

from __future__ import annotations

import json

from src.modules.tools.conversation import (
    build_tool_message,
    extract_image_data_urls,
    is_tool_message,
    to_conversation_view,
)
from src.modules.tools.models import ResultBlock, ToolExecutionResult


def test_base64_image_replaced_with_placeholder() -> None:
    """形似截图载荷的 base64 串替换为占位符，二进制本体不进对话视图。"""
    image = "A" * 2048  # 纯 base64 字符集、超 1KB、无空白
    payload = {"text": "", "latency_ms": 36, "image": image}

    view = to_conversation_view(payload)

    assert view["image"] == "[图像 1 KB 已省略]"
    assert "AAA" not in json.dumps(view)


def test_original_payload_not_mutated() -> None:
    """返回新容器，原载荷不动——UI 事件通道仍要完整取用 structured_content。"""
    image = "A" * 2048
    payload = {"image": image, "nested": {"frame": [image]}}

    view = to_conversation_view(payload)

    assert view["image"] == "[图像 1 KB 已省略]"
    assert payload["image"] == image
    assert payload["nested"]["frame"] == [image]


def test_placeholder_reports_decoded_size() -> None:
    """KB 数按 base64 解码后的真实字节量换算：2048 字符 ≈ 1536 字节 → 1 KB。"""
    assert to_conversation_view("A" * 2048) == "[图像 1 KB 已省略]"


def test_short_base64_like_strings_are_kept() -> None:
    """1KB 以内的短编码（ID、校验值、短 token）原样保留。"""
    short = "abc123+/=" * 100  # 900 字符，未过长度阈值
    assert to_conversation_view({"token": short}) == {"token": short}


def test_length_boundary_at_one_kb() -> None:
    """阈值边界：恰好 1KB 保留，超出 1 字符即判为载荷。"""
    assert to_conversation_view("A" * 1024) == "A" * 1024
    assert to_conversation_view("A" * 1025) == "[图像 1 KB 已省略]"


def test_long_chinese_prose_and_json_text_are_kept() -> None:
    """合法长文本不被误伤：中文长文（远超 1KB）与 JSON 文本原样保留。"""
    article = "这是一段很长的中文正文，讨论红石比较器的次序语义。" * 200
    blob = json.dumps({"results": [article], "source": "web_search"}, ensure_ascii=False)
    payload = {"content": article, "raw": blob}

    assert to_conversation_view(payload) == payload


def test_urls_and_whitespace_bearing_text_are_kept() -> None:
    """含标点的 URL、含空白的长串不是 base64 形态，原样保留。"""
    url = "https://example.com/wiki/红石?lang=zh&q=" + "术语+".join(["redstone"] * 100)
    cases = [url, "x " * 2000, "word\n" * 500]
    for text in cases:
        assert to_conversation_view(text) == text


def test_nested_structures_and_scalars_pass_through() -> None:
    """递归处理 dict / list；标量与其他类型原样返回。"""
    image = "A" * 2048
    payload = {"a": [{"deep": image}], "b": 3, "c": None, "d": True, "e": 1.5}

    view = to_conversation_view(payload)

    assert view["a"][0]["deep"] == "[图像 1 KB 已省略]"
    assert view["b"] == 3
    assert view["c"] is None
    assert view["d"] is True
    assert view["e"] == 1.5


def test_plain_string_payload_is_sanitized_too() -> None:
    """载荷本身就是字符串（content 透传形态）同样按判定处理。"""
    assert to_conversation_view("正文" * 10) == "正文" * 10
    assert to_conversation_view("A" * 2048) == "[图像 1 KB 已省略]"


# =============================================================================
# 视觉带图观察组装（extract_image_data_urls / build_tool_message / is_tool_message）
# =============================================================================


def test_extract_image_data_urls_from_blocks() -> None:
    """图像块转 data URL；文本块与缺数据的块跳过；无图返回空列表。"""
    image = "A" * 2048
    result = ToolExecutionResult(
        tool_name="vision_look_at_screen",
        success=True,
        blocks=[
            ResultBlock(kind="text", text="画面描述"),
            ResultBlock(kind="image", data=image, mime_type="image/png"),
            ResultBlock(kind="image", data="", mime_type="image/jpeg"),
        ],
    )

    urls = extract_image_data_urls(result)

    assert urls == [f"data:image/png;base64,{image}"]
    assert extract_image_data_urls(ToolExecutionResult(tool_name="x", success=True)) == []


def test_build_tool_message_shapes_openai_dialect() -> None:
    """带图消息 = 观察文本 part + 图 image_url part，tool_call_id 配对当前调用。"""
    message = build_tool_message(
        rendered='{"ok": true, "image": "[图像 1 KB 已省略]"}',
        image_data_urls=["data:image/png;base64,AAAA"],
        tool_call_id="call_1",
    )

    assert message["role"] == "tool"
    assert message["tool_call_id"] == "call_1"
    assert message["content"][0] == {"type": "text", "text": '{"ok": true, "image": "[图像 1 KB 已省略]"}'}
    assert message["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


def test_is_tool_message_distinguishes_observation_shapes() -> None:
    """三种观察返回形态的判别：视觉消息 dict / 纯文本 / 载荷 dict。"""
    message = build_tool_message(rendered="{}", image_data_urls=["data:image/png;base64,A"], tool_call_id="c")
    assert is_tool_message(message) is True
    assert is_tool_message('{"ok": true}') is False
    assert is_tool_message({"ok": True}) is False
    assert is_tool_message(None) is False
