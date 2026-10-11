"""build_prompt_preview 单元测试：两种消息形态的预览摘要提取。"""

from __future__ import annotations

from src.modules.llm.request_history_manager import build_prompt_preview


def test_preview_takes_last_message_with_body() -> None:
    """取最后一条有正文的消息（跳过空正文）。"""
    params = {
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "这局怎么走"},
        ]
    }
    assert build_prompt_preview(params) == "这局怎么走"


def test_preview_openai_multimodal_content_list() -> None:
    """OpenAI 多模态 content 列表：拼接 text 片段。"""
    params = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看看这张图"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                ],
            }
        ]
    }
    assert build_prompt_preview(params) == "看看这张图"


def test_preview_neutral_parts_payload() -> None:
    """中立 payload 契约：parts 片段拼接，图像片段以占位符呈现。"""
    params = {
        "messages": [
            {
                "role": "user",
                "parts": [
                    {"type": "image"},
                    {"type": "text", "text": "画面里发生了什么"},
                ],
            }
        ]
    }
    assert build_prompt_preview(params) == "[图片内容已省略]\n画面里发生了什么"


def test_preview_truncated_to_50_chars() -> None:
    """超长正文截断到 50 字符（摘要列不成为新的膨胀源）。"""
    params = {"messages": [{"role": "user", "content": "啊" * 200}]}
    assert build_prompt_preview(params) == "啊" * 50


def test_preview_empty_inputs() -> None:
    """无 messages / 空 messages / 全空正文 → 空串。"""
    assert build_prompt_preview(None) == ""
    assert build_prompt_preview({}) == ""
    assert build_prompt_preview({"messages": []}) == ""
    assert build_prompt_preview({"messages": [{"role": "assistant", "content": ""}]}) == ""
