"""上下文原语单测：预算换算、计量缓存、按组切分、后台准备、前缀自检、尾部图片。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest
from pydantic import ValidationError

from src.modules.context import (
    IMAGE_TOKEN_ESTIMATE,
    BackgroundCheckpoint,
    ContextBudgetConfig,
    PrefixGuard,
    TailImages,
    TokenCounter,
    fold_count,
    group_messages,
    resolve_watermarks,
)


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


# ---- 预算换算 ----


def test_watermarks_follow_window_ratio_and_cap() -> None:
    """窗口 × 比例与上限取小；窗口未配置时直接用上限。"""
    config = ContextBudgetConfig(window_ratio=0.25, max_tokens=128_000, prepare_ratio=0.7, target_ratio=0.4)
    big = resolve_watermarks(config, 1_000_000)
    assert (big.budget, big.prepare, big.target) == (128_000, 89_600, 51_200)
    small = resolve_watermarks(config, 200_000)
    assert small.budget == 50_000
    assert resolve_watermarks(config, 0).budget == 128_000


def test_target_ratio_must_stay_below_prepare_ratio() -> None:
    """回落线不低于提前整理线时，检查点刚做完就会再次触发，配置期拦下。"""
    with pytest.raises(ValidationError):
        ContextBudgetConfig(prepare_ratio=0.5, target_ratio=0.5)


# ---- 计量 ----


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


# ---- 按组切分 ----


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


# ---- 后台准备 ----


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


# ---- 前缀自检 ----


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


# ---- 尾部图片 ----


def test_tail_images_drain_once() -> None:
    """同一步多个带图回执合并成一条尾部 user 消息，取出即清空。"""
    tail = TailImages()
    assert tail.drain() is None
    tail.add(["img_1"], source="vision_look_at_screen")
    tail.add(["img_2"], source="vision_look_at_screen")
    message = tail.drain()
    assert message is not None and message["role"] == "user"
    refs = [part["ref"] for part in message["content"] if part["type"] == "image_ref"]
    assert refs == ["img_1", "img_2"]
    assert "vision_look_at_screen" in message["content"][0]["text"]
    assert not tail and tail.drain() is None
