"""build_prompt_preview：取最后一条有正文的消息，兼容两种消息形态，截 50 字符"""

from __future__ import annotations

import pytest

from src.modules.llm.request_history_manager import build_prompt_preview, count_image_parts


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        (
            [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "这局怎么走"},
                {"role": "assistant", "content": ""},
            ],
            "这局怎么走",
        ),
        # OpenAI 多模态 content 列表：拼接 text 片段
        (
            [{"role": "user", "content": [{"type": "text", "text": "看看这张图"}, {"type": "image_url"}]}],
            "看看这张图",
        ),
        # 中立 payload：parts 拼接，图像片段以占位符呈现
        (
            [{"role": "user", "parts": [{"type": "image"}, {"type": "text", "text": "画面里发生了什么"}]}],
            "[图片内容已省略]\n画面里发生了什么",
        ),
        ([{"role": "user", "content": "啊" * 200}], "啊" * 50),
        ([], ""),
        ([{"role": "assistant", "content": ""}], ""),
    ],
)
def test_prompt_preview(messages: list, expected: str) -> None:
    assert build_prompt_preview({"messages": messages}) == expected
    assert build_prompt_preview(None) == build_prompt_preview({}) == ""


def test_count_image_parts_counts_neutral_image_parts() -> None:
    """注入截图计数：中立契约 messages 里 {type:"image"} part 逐条累加。"""
    messages = [
        {"role": "system", "parts": ["系统提示"]},
        {
            "role": "user",
            "parts": [{"type": "text", "text": "看到了什么"}, {"type": "image", "image": "data:image/png;base64,xxx"}],
        },
        {"role": "assistant", "parts": [{"type": "text", "text": "一只猫"}]},
        {"role": "user", "parts": [{"type": "image", "image": "data:image/jpeg;base64,yyy"}, {"type": "image"}]},
    ]
    assert count_image_parts({"messages": messages}) == 3
    assert count_image_parts({"messages": [{"role": "user", "content": "纯文本"}]}) == 0
    assert count_image_parts(None) == count_image_parts({}) == 0
