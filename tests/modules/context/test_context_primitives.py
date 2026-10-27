"""上下文原语单测：预算换算、计量缓存、按组切分、后台准备、前缀自检、易逝图。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest
from pydantic import ValidationError

from src.modules.context import (
    IMAGE_TOKEN_ESTIMATE,
    BackgroundCheckpoint,
    ContextBudgetConfig,
    PrefixGuard,
    TokenCounter,
    TransientImages,
    absorb_result_images,
    fold_count,
    group_messages,
    resolve_watermarks,
)
from src.modules.tools.models import ResultBlock, ToolExecutionResult


def _user(text: str) -> Dict[str, Any]:
    return {"role": "user", "content": text}


def _call(call_id: str) -> Dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "t", "arguments": "{}"}}],
    }


def _receipt(call_id: str, text: str = "ok") -> Dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": text}


# 预算换算


def test_watermarks_follow_window_ratio_and_cap() -> None:
    """窗口 × 比例与上限取小；窗口未配置时按保守的 128k 窗口换算，不按上限盲发。"""
    config = ContextBudgetConfig(window_ratio=0.25, max_tokens=128_000, prepare_ratio=0.7, target_ratio=0.4)
    big = resolve_watermarks(config, 1_000_000)
    assert (big.budget, big.prepare, big.target) == (128_000, 89_600, 51_200)
    small = resolve_watermarks(config, 200_000)
    assert small.budget == 50_000
    assert resolve_watermarks(config, 0).budget == 32_000


def test_target_ratio_must_stay_below_prepare_ratio() -> None:
    """回落线不低于提前整理线时，检查点刚做完就会再次触发，配置期拦下。"""
    with pytest.raises(ValidationError):
        ContextBudgetConfig(prepare_ratio=0.5, target_ratio=0.5)


# 计量


def test_token_counter_caches_text_and_counts_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一文本只分词一次；工具调用、分段文本、图像引用都计入，内部标记不计。"""
    calls: List[str] = []

    def fake_count(text: str) -> int:
        calls.append(text)
        return len(text)

    monkeypatch.setattr("src.modules.context.tokens.count_text_tokens", fake_count)
    counter = TokenCounter()
    message = {"role": "user", "content": "abcd", "_marker": "x" * 100}
    assert counter.message(message) == 4 + 4
    assert counter.message(message) == 8
    assert calls.count("abcd") == 1

    parts = {"role": "user", "content": [{"type": "text", "text": "ab"}, {"type": "image_ref", "ref": "img_1"}]}
    assert counter.message(parts) == 4 + 2 + IMAGE_TOKEN_ESTIMATE
    assert counter.message(_call("c1")) > 4


def test_token_counter_evicts_least_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存满了逐出最久未用的文本，再次查询时重新分词。"""
    calls: List[str] = []
    monkeypatch.setattr("src.modules.context.tokens.count_text_tokens", lambda text: calls.append(text) or 1)
    counter = TokenCounter(cache_size=2)
    for text in ("a", "b", "a", "c", "b"):
        counter.text(text)
    assert calls == ["a", "b", "c", "b"]


# 按组切分


def test_group_messages_binds_calls_with_receipts() -> None:
    """工具调用与紧随的回执是一组；没有前置调用的回执自成一组。"""
    messages = [_receipt("old"), _user("u1"), _call("c1"), _receipt("c1"), _receipt("c1b"), _user("u2")]
    groups = group_messages(messages)
    assert [len(group) for group in groups] == [1, 1, 3, 1]


def test_fold_count_keeps_protected_tail() -> None:
    """从最老开始压，压到剩余不超过目标；最后一组永远保留。"""
    assert fold_count([10, 10, 10, 10], keep_tokens=20) == 2
    assert fold_count([10, 10, 10, 10], keep_tokens=0) == 3
    assert fold_count([10, 10, 10, 10], keep_tokens=50) == 0
    assert fold_count([10, 10, 10], keep_tokens=0, protect_last=2) == 1


# 后台准备


async def test_background_checkpoint_single_flight_and_take() -> None:
    """同一时刻只有一个准备任务；算完取走即清，取走后可以再发起。"""
    gate = asyncio.Event()

    async def work(value: str) -> str:
        await gate.wait()
        return value

    runner: BackgroundCheckpoint[str] = BackgroundCheckpoint("测试")
    assert runner.start(work("第一章"), covered=3)
    assert not runner.start(work("重复"))
    assert runner.take() is None
    gate.set()
    assert await runner.wait() == "第一章"
    assert not runner.busy
    assert runner.start(work("第二章"))
    await asyncio.sleep(0)
    assert await runner.wait() == "第二章"


async def test_background_checkpoint_failure_clears_for_retry() -> None:
    """准备失败只记日志、返回 None，并允许下次重新发起。"""

    async def broken() -> str:
        raise RuntimeError("摘要模型超时")

    runner: BackgroundCheckpoint[str] = BackgroundCheckpoint("测试")
    runner.start(broken())
    assert await runner.wait() is None
    assert not runner.busy


async def test_background_checkpoint_cancel() -> None:
    """工作层被整体重置时放弃在跑的准备任务。"""
    runner: BackgroundCheckpoint[str] = BackgroundCheckpoint("测试")
    runner.start(asyncio.sleep(10, result="不会用到"), covered=1)
    await runner.cancel()
    assert not runner.busy and runner.meta == {}


# 前缀自检


def test_prefix_guard_accepts_append_and_flags_rewrite() -> None:
    """只追加不告警；中段被改写返回位置说明；预告过的检查点不告警。"""
    guard = PrefixGuard("测试")
    base = [_user("a"), _user("b")]
    assert guard.observe(base) is None
    assert guard.observe(base + [_user("c")]) is None
    detail = guard.observe([_user("a"), _user("改"), _user("c")])
    assert detail is not None and "第 2 条" in detail
    guard.expect_rewrite("检查点")
    assert guard.observe([_user("摘要"), _user("c")]) is None


def test_prefix_guard_ignores_internal_markers() -> None:
    """下划线开头的内部标记模型看不到，不影响前缀判断。"""
    guard = PrefixGuard("测试")
    guard.observe([{"role": "user", "content": "a", "_facts": True}])
    assert guard.observe([{"role": "user", "content": "a"}]) is None


# 易逝图


def test_transient_images_drain_once() -> None:
    """同一步多个带图回执合并成一条尾部 user 消息，取出即清空。"""
    tail = TransientImages()
    assert tail.drain() is None
    tail.add(["img_1"], source="vision_look_at_screen")
    tail.add(["img_2"], source="vision_look_at_screen")
    message = tail.drain()
    assert message is not None and message["role"] == "user"
    refs = [part["ref"] for part in message["content"] if part["type"] == "image_ref"]
    assert refs == ["img_1", "img_2"]
    assert "vision_look_at_screen" in message["content"][0]["text"]
    assert not tail and tail.drain() is None


def test_structured_parts_serialized_once_per_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一个工具调用对象只序列化一次；换成新对象（哪怕内容相同）才重算。"""
    dumps: List[Any] = []
    real_dumps = json.dumps

    def counting_dumps(value: Any, **kwargs: Any) -> str:
        dumps.append(value)
        return real_dumps(value, **kwargs)

    monkeypatch.setattr("src.modules.context.tokens.json.dumps", counting_dumps)
    counter = TokenCounter()
    call = _call("c1")
    first = counter.message(call)
    assert counter.message(call) == first and len(dumps) == 1
    counter.message(_call("c1"))
    assert len(dumps) == 2


def test_prefix_guard_flags_replaced_tool_calls() -> None:
    """工具调用被换成内容不同的新对象时照样识别为改写。"""
    guard = PrefixGuard("测试")
    guard.observe([_user("a"), _call("c1")])
    assert guard.observe([_user("a"), _call("c1"), _user("b")]) is None
    assert guard.observe([_user("a"), _call("c2")]) is not None


# absorb_result_images：从工具结果收图进易逝图


class _RecordingStore:
    """记录 put 调用的假图库，返回可预测的句柄。"""

    def __init__(self) -> None:
        self.put_calls: List[str] = []

    def put(self, data_url: str) -> str:
        self.put_calls.append(data_url)
        return f"img_{len(self.put_calls)}"


def _visual_llm(visual: bool = True) -> Any:
    """带 is_profile_visual 的假引擎对象。"""

    class _FakeLLM:
        def is_profile_visual(self, profile: str) -> bool:
            return visual

    return _FakeLLM()


def _image_result(*, success: bool = True, image_count: int = 1) -> ToolExecutionResult:
    blocks: List[ResultBlock] = [ResultBlock(kind="text", text="截图如下")]
    blocks.extend(ResultBlock(kind="image", data="B" * 64, mime_type="image/png") for _ in range(image_count))
    return ToolExecutionResult(tool_name="vision_look_at_screen", success=success, content="ok", blocks=blocks)


def test_absorb_result_images_success_visual_with_images() -> None:
    """成功回执 + 视觉模型 + 带图：图入库换句柄，drain 产出含工具名的说明与 image_ref。"""
    images = TransientImages()
    store = _RecordingStore()
    absorb_result_images(images, _visual_llm(), "planner", "vision_look_at_screen", _image_result(), store=store)
    assert len(store.put_calls) == 1
    message = images.drain()
    assert message is not None and message["role"] == "user"
    assert "vision_look_at_screen" in message["content"][0]["text"]
    refs = [part["ref"] for part in message["content"] if part["type"] == "image_ref"]
    assert refs == ["img_1"]


def test_absorb_result_images_skips_failed_result() -> None:
    """失败回执不触发看图路径：即便 blocks 带图也不吸收。"""
    images = TransientImages()
    store = _RecordingStore()
    absorb_result_images(images, _visual_llm(), "planner", "shot", _image_result(success=False), store=store)
    assert store.put_calls == []
    assert images.drain() is None


def test_absorb_result_images_skips_non_visual() -> None:
    """模型不声明 visual：不吸收。"""
    images = TransientImages()
    store = _RecordingStore()
    absorb_result_images(images, _visual_llm(visual=False), "planner", "shot", _image_result(), store=store)
    assert store.put_calls == []
    assert images.drain() is None


@pytest.mark.parametrize("llm", [None, object(), "no-method"], ids=["none", "no-attr", "not-callable"])
def test_absorb_result_images_unqueryable_llm_treated_non_visual(llm: Any) -> None:
    """引擎不可查（None / 无该方法 / 不可调用）一律按非视觉。"""
    images = TransientImages()
    store = _RecordingStore()
    absorb_result_images(images, llm, "planner", "shot", _image_result(), store=store)
    assert images.drain() is None


def test_absorb_result_images_query_exception_treated_non_visual() -> None:
    """能力查询抛异常按非视觉处理，不外溢。"""

    class _ExplodingLLM:
        def is_profile_visual(self, profile: str) -> bool:
            raise RuntimeError("查询失败")

    images = TransientImages()
    store = _RecordingStore()
    absorb_result_images(images, _ExplodingLLM(), "planner", "shot", _image_result(), store=store)
    assert images.drain() is None


def test_absorb_result_images_success_without_image_blocks_is_noop() -> None:
    """成功 + 视觉但结果无图块：空操作。"""
    images = TransientImages()
    store = _RecordingStore()
    result = _image_result(image_count=0)
    absorb_result_images(images, _visual_llm(), "planner", "shot", result, store=store)
    assert store.put_calls == []
    assert images.drain() is None
