"""视觉识别完整返回长文本，失败降级与主动取消各自保持语义。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from src.modules.vision.look_at_screen import (
    LlmVisionTextReader,
)


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------


class StubPromptManager:
    """PromptManager stub：按 template_name 返回固定串；不读真实模板文件。

    任务说明明确"不要依赖真实模板文件"——screen_vlm_prompt 模板正被 Task 3 迁移，
    测试里只校验 reader 把渲染结果透传给 llm_manager，不依赖模板正文。
    """

    def __init__(self, mapping: Optional[dict[str, str]] = None) -> None:
        if mapping is None:
            mapping = {
                "screen_vlm_prompt": "stub-screen-prompt",
                "screen_vlm_system": "stub-screen-system",
            }
        # 即使传入 {} 也视为显式空模板集合（用于"模板缺失"测试场景）
        self._mapping = mapping
        self.calls: List[tuple[str, dict[str, Any]]] = []

    def render(self, template_name: str, **kwargs: Any) -> str:
        self.calls.append((template_name, kwargs))
        if template_name not in self._mapping:
            raise KeyError(f"未注册模板: {template_name}")
        return self._mapping[template_name]


class FakeLlmManager:
    """LLMManager stub：generate_vision 可按调用顺序返回预设结果，或抛异常，或挂起。"""

    def __init__(
        self,
        *,
        responses: Optional[List[Any]] = None,
        hang: bool = False,
        raise_exc: Optional[BaseException] = None,
    ) -> None:
        self._responses = list(responses or [])
        self._hang = hang
        self._raise = raise_exc
        self.calls: List[dict[str, Any]] = []

    async def generate_vision(
        self,
        prompt: str,
        images: List[Any],
        *,
        profile: Optional[str] = None,
        system: Optional[str] = None,
    ) -> Any:
        self.calls.append(
            {
                "prompt": prompt,
                "images": list(images),
                "profile": profile,
                "system": system,
            }
        )
        if self._raise is not None:
            raise self._raise
        if self._hang:
            # 模拟持续生成，由调用方主动取消测试结束。
            await asyncio.Event().wait()
        if not self._responses:
            return SimpleNamespace(success=False, content=None, error="no_response_queued")
        return self._responses.pop(0)


# ---------------------------------------------------------------------------
# 默认超时常量
# ---------------------------------------------------------------------------


async def test_long_question_and_answer_are_preserved() -> None:
    """长识别要求与完整画面文字经过 reader 后仍保留尾部。"""
    text = "文字" * 8000 + "最后一行"
    question = "识别" * 800 + "列出按钮"
    llm = FakeLlmManager(responses=[SimpleNamespace(success=True, content=text)])
    reader = LlmVisionTextReader(llm_manager=llm)
    assert await reader.read(b"image", question=question) == text
    assert llm.calls[0]["prompt"] == question


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------


async def test_read_returns_trimmed_content_on_success() -> None:
    """VLM 成功 → 返回 content.strip()，question 透传为 prompt。"""
    llm = FakeLlmManager(responses=[SimpleNamespace(success=True, content="  屏幕上有一个选项  \n", error=None)])
    prompt_mgr = StubPromptManager()
    reader = LlmVisionTextReader(llm_manager=llm, prompt_manager=prompt_mgr)

    out = await reader.read(b"\x89PNG", question="屏幕上显示什么？")

    assert out == "屏幕上有一个选项"
    # question 透传为 user prompt，没有走模板
    assert llm.calls[0]["prompt"] == "屏幕上显示什么？"
    # system 透传 prompt_manager.render 的结果
    assert llm.calls[0]["system"] == "stub-screen-system"
    # profile 固定为 vision
    assert llm.calls[0]["profile"] == "vision"
    # images 只含传入的 bytes
    assert llm.calls[0]["images"] == [b"\x89PNG"]


async def test_read_uses_default_template_when_no_question() -> None:
    """question 为 None → 渲染 screen_vlm_prompt 模板作 prompt。"""
    llm = FakeLlmManager(responses=[SimpleNamespace(success=True, content="some text", error=None)])
    prompt_mgr = StubPromptManager()
    reader = LlmVisionTextReader(llm_manager=llm, prompt_manager=prompt_mgr)

    out = await reader.read(b"\x89PNG")

    assert out == "some text"
    assert llm.calls[0]["prompt"] == "stub-screen-prompt"
    # 模板被实际渲染过
    assert any(name == "screen_vlm_prompt" for name, _ in prompt_mgr.calls)


async def test_read_works_without_prompt_manager() -> None:
    """未注入 prompt_manager → 用内置默认 user prompt，system=None。"""
    llm = FakeLlmManager(responses=[SimpleNamespace(success=True, content="ok", error=None)])
    reader = LlmVisionTextReader(llm_manager=llm, prompt_manager=None)

    out = await reader.read(b"\x89PNG", question="自定义问题")

    assert out == "ok"
    assert llm.calls[0]["prompt"] == "自定义问题"
    assert llm.calls[0]["system"] is None


# ---------------------------------------------------------------------------
# 降级路径：超时
# ---------------------------------------------------------------------------


async def test_active_read_propagates_parent_cancellation() -> None:
    """调用方停止读屏时取消识别，不把主动取消伪装成空白画面。"""
    llm = FakeLlmManager(hang=True)
    task = asyncio.create_task(LlmVisionTextReader(llm_manager=llm).read(b"image"))
    await asyncio.sleep(0)
    assert llm.calls and not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# 降级路径：success=False
# ---------------------------------------------------------------------------


async def test_read_degrades_to_empty_on_success_false() -> None:
    """VLM 返回 success=False → 返回空串（不抛）。"""
    llm = FakeLlmManager(responses=[SimpleNamespace(success=False, content=None, error="rate_limit")])
    reader = LlmVisionTextReader(llm_manager=llm, prompt_manager=StubPromptManager())

    out = await reader.read(b"\x89PNG", question="q")

    assert out == ""
    assert llm.calls  # 真调到 generate_vision


# ---------------------------------------------------------------------------
# 降级路径：异常
# ---------------------------------------------------------------------------


async def test_read_degrades_to_empty_on_exception() -> None:
    """VLM 调用抛异常 → 返回空串（不抛）。"""
    llm = FakeLlmManager(raise_exc=RuntimeError("vlm transport broken"))
    reader = LlmVisionTextReader(llm_manager=llm, prompt_manager=StubPromptManager())

    out = await reader.read(b"\x89PNG", question="q")

    assert out == ""


async def test_read_degrades_when_prompt_template_missing() -> None:
    """question=None 且 prompt_manager 缺模板 → 用内置默认 user prompt，不抛。"""
    llm = FakeLlmManager(responses=[SimpleNamespace(success=True, content="ok", error=None)])
    prompt_mgr = StubPromptManager(mapping={})  # 无任何模板
    reader = LlmVisionTextReader(llm_manager=llm, prompt_manager=prompt_mgr)

    out = await reader.read(b"\x89PNG")

    assert out == "ok"
    # 内置默认 user prompt 被透传给 VLM
    assert llm.calls[0]["prompt"] == "描述屏幕上正在显示的内容。"
