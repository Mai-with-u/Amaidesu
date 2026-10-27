"""build_prompt_preview：取最后一条有正文的消息，截 50 字符；快照归一化形状契约"""

from __future__ import annotations

import pytest

from src.modules.llm.call_observer import CallObserver
from src.modules.llm.observation import build_prompt_preview, count_image_parts
from src.modules.llm.payload import GenerateRequest, ImagePart, Message, TextPart
from src.modules.llm.request_history_manager import build_prompt_preview as reexported_preview
from src.modules.llm.request_history_manager import count_image_parts as reexported_count


@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        # 中立 payload dump 形状：parts 拼接，图像片段以占位符呈现
        (
            [
                {"role": "user", "parts": ["这局怎么走"]},
                {"role": "assistant", "parts": []},
            ],
            "这局怎么走",
        ),
        (
            [{"role": "user", "parts": [{"type": "image"}, {"type": "text", "text": "画面里发生了什么"}]}],
            "[图片内容已省略]\n画面里发生了什么",
        ),
        # 裸字符串片段是文本简写
        ([{"role": "user", "parts": ["啊" * 200]}], "啊" * 50),
        ([], ""),
        ([{"role": "assistant", "parts": []}], ""),
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
    assert count_image_parts({"messages": [{"role": "user", "parts": ["纯文本"]}]}) == 0
    assert count_image_parts(None) == count_image_parts({}) == 0


def test_build_request_params_produces_single_normalized_shape() -> None:
    """引擎请求参数快照是观测层唯一输入：messages 恒为中立 dump（parts），无 content 键。"""
    request = GenerateRequest(
        messages=[
            Message(role="user", parts=[TextPart(text="画面里有什么"), ImagePart(image="data:image/png;base64,zzz")]),
            Message(role="assistant", parts=["一只猫"]),
        ],
        system="你是直播助手",
    )
    request.temperature = 0.3
    params = CallObserver._build_request_params({"request": request})
    assert params["system"] == "你是直播助手"
    assert params["temperature"] == 0.3
    assert len(params["messages"]) == 2
    for message in params["messages"]:
        assert "content" not in message
        assert "parts" in message
    # 观测产物从同一快照单形状解析：预览取最后一条正文，图像计数命中全部图像 part
    assert build_prompt_preview(params) == "一只猫"
    assert count_image_parts(params) == 1


def test_build_request_params_without_request_is_empty_snapshot() -> None:
    """request 缺失（非 generate 入口的直连调用）返回空快照，观测层按无消息处理。"""
    assert CallObserver._build_request_params({"messages": [{"role": "user", "content": "hi"}]}) == {}


def test_request_history_manager_reexports_observation_helpers() -> None:
    """读面模块再导出的预览 / 计数助手与 observation 本体同源。"""
    assert reexported_preview is build_prompt_preview
    assert reexported_count is count_image_parts
