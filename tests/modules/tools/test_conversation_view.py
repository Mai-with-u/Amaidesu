"""对话观察渲染：超过 1KB 且仅含 base64 字符集的无空白串判为二进制载荷并替换为占位符；其余长内容原样保留"""

from __future__ import annotations

import json

import pytest

from src.modules.tools.conversation import extract_image_data_urls, to_conversation_view
from src.modules.tools.models import ResultBlock, ToolExecutionResult

PLACEHOLDER = "[图像 1 KB 已省略]"


def test_base64_payload_replaced_without_mutating_original() -> None:
    image = "A" * 2048  # 纯 base64 字符集、超 1KB、无空白
    payload = {"text": "", "latency_ms": 36, "image": image, "nested": {"frame": [image]}, "b": 3, "c": None}

    view = to_conversation_view(payload)

    assert view["image"] == PLACEHOLDER and view["nested"]["frame"] == [PLACEHOLDER]
    assert (view["b"], view["c"]) == (3, None) and "AAA" not in json.dumps(view)  # 递归处理，标量原样
    assert payload["image"] == image and payload["nested"]["frame"] == [image]  # 返回新容器，UI 事件通道仍取完整载荷
    assert to_conversation_view("A" * 2048) == PLACEHOLDER  # 载荷本身是字符串同样处理；KB 数按解码后字节量换算


def test_one_kb_boundary_and_short_encodings_are_kept() -> None:
    assert to_conversation_view("A" * 1024) == "A" * 1024
    assert to_conversation_view("A" * 1025) == PLACEHOLDER
    short = "abc123+/=" * 100  # ID、校验值、短 token 不过阈值
    assert to_conversation_view({"token": short}) == {"token": short}


@pytest.mark.parametrize(
    "text",
    [
        "这是一段很长的中文正文，讨论红石比较器的次序语义。" * 200,
        json.dumps({"results": ["正文" * 500], "source": "web_search"}, ensure_ascii=False),
        "https://example.com/wiki/红石?lang=zh&q=" + "术语+".join(["redstone"] * 100),
        "x " * 2000,
        "word\n" * 500,
    ],
)
def test_legitimate_long_content_is_kept(text: str) -> None:
    assert to_conversation_view(text) == text


def test_extract_image_data_urls_from_blocks() -> None:
    image = "A" * 2048
    result = ToolExecutionResult(
        tool_name="vision_look_at_screen",
        success=True,
        blocks=[
            ResultBlock(kind="text", text="画面描述"),
            ResultBlock(kind="image", data=image, mime_type="image/png"),
            ResultBlock(kind="image", data="", mime_type="image/jpeg"),  # 缺数据的图像块跳过
        ],
    )
    assert extract_image_data_urls(result) == [f"data:image/png;base64,{image}"]
    assert extract_image_data_urls(ToolExecutionResult(tool_name="x", success=True)) == []
