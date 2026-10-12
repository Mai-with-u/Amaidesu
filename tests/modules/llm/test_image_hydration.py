"""图像引用的装配契约：窗口注水、逐出降级、入参不可变、守卫与第二检查点。

根治方案（.omo/drafts/vision-image-history-root-cure.md）：历史中图只以
image_ref 引用存在，引擎装配期注水为内联 ImagePart；任何一环失守都必须
近端硬错，而不是把 base64 或句柄字符串发给服务端。
"""

from __future__ import annotations

import pytest

from src.modules.llm.clients.openai.client import OpenAIClient
from src.modules.llm.engine import _guard_inline_payload, _hydrate_image_refs, _normalize_generate_input
from src.modules.llm.payload import GenerateRequest, ImagePart, ImageRefPart, TextPart
from src.modules.vision.image_store import VisionImageStore, set_image_store

_DATA_URL = "data:image/png;base64," + "A" * 128


def _request(messages: list) -> GenerateRequest:
    return _normalize_generate_input(messages, system=None, tools=None, temperature=None)


def _tool_message_with_ref(ref: str, call_id: str = "c1") -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": [
            {"type": "text", "text": '{"ok":true}'},
            {"type": "image_ref", "ref": ref},
        ],
    }


def test_fresh_ref_hydrates_to_inline_image() -> None:
    """窗口内且 store 命中：引用注水为 ImagePart，模型在紧接着的请求里能看到图。"""
    store = VisionImageStore()
    set_image_store(store)
    try:
        ref = store.put(_DATA_URL)
        request = _request([{"role": "user", "content": "看看"}, _tool_message_with_ref(ref)])
        _hydrate_image_refs(request)
        tool_msg = request.messages[-1]
        assert isinstance(tool_msg.parts[1], ImagePart)
        assert tool_msg.parts[1].image == _DATA_URL
    finally:
        set_image_store(VisionImageStore())


def test_ref_beyond_window_degrades_to_note() -> None:
    """越窗（引用消息之后 ≥ visible_turns 条 assistant）→ 提示文本，不再重发旧图。"""
    store = VisionImageStore()
    set_image_store(store)
    try:
        ref = store.put(_DATA_URL)
        messages: list = [{"role": "user", "content": "看看"}, _tool_message_with_ref(ref)]
        messages += [{"role": "assistant", "content": f"第 {i} 轮"} for i in range(store.visible_turns)]
        request = _request(messages)
        _hydrate_image_refs(request)
        tool_parts = request.messages[1].parts
        assert isinstance(tool_parts[1], TextPart)
        assert "截图不可用" in tool_parts[1].text
    finally:
        set_image_store(VisionImageStore())


def test_evicted_ref_within_window_degrades_to_note() -> None:
    """窗口内但已被逐出：同样降级为提示文本，模型被明确告知图没了。"""
    store = VisionImageStore()
    set_image_store(store)
    ref = store.put(_DATA_URL)
    request = _request([{"role": "user", "content": "看看"}, _tool_message_with_ref(ref)])
    store.clear()
    _hydrate_image_refs(request)
    tool_parts = request.messages[-1].parts
    assert isinstance(tool_parts[1], TextPart)
    assert "已被逐出" in tool_parts[1].text
    set_image_store(VisionImageStore())


def test_hydration_does_not_mutate_caller_messages() -> None:
    """归一化会原样引用调用方手中的 Message 对象——注水必须重建，不得改写入参。"""
    store = VisionImageStore()
    set_image_store(store)
    try:
        ref = store.put(_DATA_URL)
        from src.modules.llm.payload import Message

        original = Message(role="tool", tool_call_id="c1", parts=[ImageRefPart(ref=ref)])
        request = _normalize_generate_input([original], system=None, tools=None, temperature=None)
        _hydrate_image_refs(request)
        assert isinstance(request.messages[0].parts[0], ImagePart)
        assert isinstance(original.parts[0], ImageRefPart)
    finally:
        set_image_store(VisionImageStore())


def test_guard_rejects_oversized_text_payload() -> None:
    """装配守卫：文本字段超长（内联大载荷泄漏的形状）硬错，宁拒发不静默烧。"""
    request = _request([{"role": "user", "content": "B" * 200_001}])
    with pytest.raises(ValueError, match="装配守卫"):
        _guard_inline_payload(request)


def test_guard_allows_hydrated_image_part() -> None:
    """注水后的 data URL 图像是 base64 唯一合法形态——守卫不得误伤 ImagePart。"""
    request = _request([{"role": "user", "content": "看看"}])
    request.messages[0].parts.append(ImagePart(image="data:image/png;base64," + "A" * 300_000))
    _guard_inline_payload(request)


def test_client_checkpoint_rejects_unhydrated_ref() -> None:
    """第二道结构校验：引用到达翻译层 = 注水未执行，硬错而非把句柄发服务端。"""
    with pytest.raises(TypeError, match="注水"):
        OpenAIClient._part_to_openai(ImageRefPart(ref="img_ab12"))


def test_client_translates_hydrated_image_part() -> None:
    """注水后的正常翻译路径不受影响：ImagePart → image_url content。"""
    content = OpenAIClient._part_to_openai(ImagePart(image=_DATA_URL))
    assert content == {"type": "image_url", "image_url": {"url": _DATA_URL}}
