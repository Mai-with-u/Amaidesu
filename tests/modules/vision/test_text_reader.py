"""LlmVisionTextReader：完整透传长问题与长答案、question/模板/默认提示三种 prompt 来源、失败降级为空串、主动取消不吞"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from src.modules.vision.look_at_screen import LlmVisionTextReader

DEFAULT_PROMPT = "描述屏幕上正在显示的内容。"


class StubPromptManager:
    """按模板名返回固定串，不读真实模板文件；只校验 reader 把渲染结果透传给 llm_manager。"""

    def __init__(self, mapping: Optional[dict[str, str]] = None) -> None:
        # 显式传 {} 表示"模板缺失"场景
        self._mapping = (
            {"screen_vlm_prompt": "stub-screen-prompt", "screen_vlm_system": "stub-screen-system"}
            if mapping is None
            else mapping
        )
        self.calls: List[str] = []

    def render(self, template_name: str, **kwargs: Any) -> str:
        self.calls.append(template_name)
        if template_name not in self._mapping:
            raise KeyError(f"未注册模板: {template_name}")
        return self._mapping[template_name]


class FakeLlmManager:
    """generate_vision 按调用顺序返回预设结果，或抛异常，或挂起。"""

    def __init__(
        self, *, responses: Optional[List[Any]] = None, hang: bool = False, raise_exc: Optional[BaseException] = None
    ) -> None:
        self._responses = list(responses or [])
        self._hang = hang
        self._raise = raise_exc
        self.calls: List[dict[str, Any]] = []

    async def generate_vision(self, prompt: str, images: List[Any], *, profile=None, system=None) -> Any:
        self.calls.append({"prompt": prompt, "images": list(images), "profile": profile, "system": system})
        if self._raise is not None:
            raise self._raise
        if self._hang:
            await asyncio.Event().wait()  # 模拟持续生成，由调用方主动取消结束
        if not self._responses:
            return SimpleNamespace(success=False, content=None, error="no_response_queued")
        return self._responses.pop(0)


def _ok(content: str) -> SimpleNamespace:
    return SimpleNamespace(success=True, content=content, error=None)


async def test_long_question_and_answer_survive_the_round_trip() -> None:
    text = "文字" * 8000 + "最后一行"
    question = "识别" * 800 + "列出按钮"
    llm = FakeLlmManager(responses=[_ok(text)])
    assert await LlmVisionTextReader(llm_manager=llm).read(b"image", question=question) == text
    assert llm.calls[0]["prompt"] == question


async def test_prompt_comes_from_question_then_template_then_builtin_default() -> None:
    llm = FakeLlmManager(responses=[_ok("  屏幕上有一个选项  \n")])
    manager = StubPromptManager()
    reader = LlmVisionTextReader(llm_manager=llm, prompt_manager=manager)
    assert await reader.read(b"\x89PNG", question="屏幕上显示什么？") == "屏幕上有一个选项"  # 内容 strip
    call = llm.calls[0]
    assert call["prompt"] == "屏幕上显示什么？" and call["system"] == "stub-screen-system"  # question 透传，不走模板
    assert call["profile"] == "vision" and call["images"] == [b"\x89PNG"]

    llm.calls.clear()
    llm._responses = [_ok("some text")]
    await reader.read(b"\x89PNG")  # 无 question：渲染 screen_vlm_prompt 模板
    assert llm.calls[0]["prompt"] == "stub-screen-prompt" and "screen_vlm_prompt" in manager.calls

    for prompt_manager in (None, StubPromptManager(mapping={})):  # 无 prompt_manager / 模板缺失：用内置默认提示，不抛
        bare = FakeLlmManager(responses=[_ok("ok")])
        assert await LlmVisionTextReader(llm_manager=bare, prompt_manager=prompt_manager).read(b"\x89PNG") == "ok"
        assert bare.calls[0]["prompt"] == DEFAULT_PROMPT
    assert bare.calls[0]["system"] is None


@pytest.mark.parametrize(
    "llm",
    [
        FakeLlmManager(responses=[SimpleNamespace(success=False, content=None, error="rate_limit")]),
        FakeLlmManager(raise_exc=RuntimeError("vlm transport broken")),
    ],
    ids=["success-false", "exception"],
)
async def test_failures_degrade_to_empty_string(llm: FakeLlmManager) -> None:
    reader = LlmVisionTextReader(llm_manager=llm, prompt_manager=StubPromptManager())
    assert await reader.read(b"\x89PNG", question="q") == "" and llm.calls  # 真调到 generate_vision，但不向外抛


async def test_caller_cancellation_is_not_disguised_as_a_blank_screen() -> None:
    llm = FakeLlmManager(hang=True)
    task = asyncio.create_task(LlmVisionTextReader(llm_manager=llm).read(b"image"))
    await asyncio.sleep(0)
    assert llm.calls and not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
