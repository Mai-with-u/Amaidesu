"""build_prompt_preview：取最后一条有正文的消息，兼容两种消息形态，截 50 字符"""

from __future__ import annotations

import pytest

from src.modules.llm.request_history_manager import build_prompt_preview


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
