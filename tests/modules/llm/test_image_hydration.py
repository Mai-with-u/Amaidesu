"""图像引用装配契约：历史中图只以 image_ref 存在，引擎装配期注水为内联 ImagePart。

任何一环失守都在近端硬错，而不是把 base64 或句柄字符串发给服务端。
"""

from __future__ import annotations

import pytest

from src.modules.llm.clients.openai.client import OpenAIClient
from src.modules.llm.engine import _guard_inline_payload, _hydrate_image_refs, _normalize_generate_input
from src.modules.llm.payload import GenerateRequest, ImagePart, ImageRefPart, Message, TextPart
from src.modules.vision.image_store import VisionImageStore, set_image_store

_DATA_URL = "data:image/png;base64," + "A" * 128


@pytest.fixture
def image_store():
    store = VisionImageStore()
    set_image_store(store)
    yield store
    set_image_store(VisionImageStore())


def _request(messages: list) -> GenerateRequest:
    return _normalize_generate_input(messages, system=None, tools=None, temperature=None)


def _with_ref(ref: str, assistant_turns_after: int = 0) -> GenerateRequest:
    content = [{"type": "text", "text": "{}"}, {"type": "image_ref", "ref": ref}]
    later = [{"role": "assistant", "content": f"第 {i} 轮"} for i in range(assistant_turns_after)]
    return _request(
        [{"role": "user", "content": "看看"}, {"role": "tool", "tool_call_id": "c1", "content": content}, *later]
    )


def test_fresh_ref_hydrates_without_mutating_caller_messages(image_store) -> None:
    request = _with_ref(image_store.put(_DATA_URL))
    _hydrate_image_refs(request)
    assert request.messages[1].parts[1] == ImagePart(image=_DATA_URL)

    # 归一化原样引用调用方的 Message 对象：注水必须重建，不得改写入参
    original = Message(role="tool", tool_call_id="c1", parts=[ImageRefPart(ref=image_store.put(_DATA_URL))])
    request = _request([original])
    _hydrate_image_refs(request)
    assert isinstance(request.messages[0].parts[0], ImagePart)
    assert isinstance(original.parts[0], ImageRefPart)


def test_ref_beyond_window_or_evicted_degrades_to_note(image_store) -> None:
    # 引用消息之后已有 visible_turns 条 assistant：不再重发旧图
    request = _with_ref(image_store.put(_DATA_URL), assistant_turns_after=image_store.visible_turns)
    _hydrate_image_refs(request)
    note = request.messages[1].parts[1]
    assert isinstance(note, TextPart) and "截图不可用" in note.text

    request = _with_ref(image_store.put(_DATA_URL))
    image_store.clear()
    _hydrate_image_refs(request)
    assert "已被逐出" in request.messages[1].parts[1].text


def test_guard_rejects_oversized_text_but_allows_hydrated_image() -> None:
    with pytest.raises(ValueError, match="装配守卫"):
        _guard_inline_payload(_request([{"role": "user", "content": "B" * 200_001}]))

    request = _request([{"role": "user", "content": "看看"}])
    request.messages[0].parts.append(ImagePart(image="data:image/png;base64," + "A" * 300_000))
    _guard_inline_payload(request)


def test_client_translation_requires_hydrated_parts() -> None:
    """第二道检查：引用到达翻译层 = 注水未执行，硬错。"""
    with pytest.raises(TypeError, match="注水"):
        OpenAIClient._part_to_openai(ImageRefPart(ref="img_ab12"))
    expected = {"type": "image_url", "image_url": {"url": _DATA_URL}}
    assert OpenAIClient._part_to_openai(ImagePart(image=_DATA_URL)) == expected
