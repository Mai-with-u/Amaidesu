"""token 预算驱动的对话历史压缩。

## 定位

把"超长对话历史按条数硬切"升级为"按 profile 配置的 token 预算压缩"：
超出预算时，把最老的一段历史消息摘要成一条压缩消息（user 角色），
替代"从会话中段硬切导致首条消息角色不合法"的旧行为。

## 设计取舍

- **摘要走同步规则性抽取，不走 LLM**：压缩发生在每次决策请求的装配路径上，
  一次额外的摘要调用会把主调用链延迟翻倍（直播晚段本就有 28% 调用超 15 秒）。
  抽取式摘要零延迟、零费用、结果确定；保留关键事实靠规则（疑问 / 承诺 /
  决策 / 失败与成功结论）而非语义重写。LLM 摘要能力经构造器注入的
  ``summarizer`` 可后续替换，接口为同步 Callable（minecraft 侧的集中整理
  是另一条已有机制，不受本模块影响）。
- **回执优先**：工具回执（role=tool）是已消费过的过程数据，minecraft 的
  observation_context 支持分页检索历史回执，压掉它们信息损失最小。
  压缩顺序：先压最老的回执组，仍超预算再从最老的对话组开始压。
- **结构合法性**：assistant 工具调用与其回执捆绑成组、整组同进退，
  不会出现"回执被压、调用悬空"；压缩发生过就前置一条 user 摘要消息，
  保证首个对话消息角色是 user，不出现 assistant 开头的历史。
- **纯函数式替换**：``compact`` 返回新列表、不改入参；预算内原样返回
  同一对象，调用方以 ``is`` 判断是否发生压缩。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from src.modules.llm.context_meter import count_text_tokens
from src.modules.logging import get_logger

logger = get_logger("HistoryCompaction")

# 压缩消息的内容标记（调用方与测试据此识别压缩产物；也是 prompt 内的显著分隔）
SUMMARY_MARKER = "[历史摘要]"

# OpenAI chat 协议每条消息的固定包装开销（与 context_meter 同口径）
_TOKENS_PER_MESSAGE = 4

# 单条抽取摘要的最大字符数（超预算场景下摘要本身也是成本，取保守上限）
_SUMMARY_MAX_CHARS = 1200

# 摘要最小 token 预留位：尚未压出内容时预算判断按此估计摘要开销
_MIN_SUMMARY_TOKENS = 48

# 抽取关键词：命中即视为关键事实（疑问/承诺/决策/成败结论）进入摘要
_KEY_FACT_KEYWORDS = (
    "？",
    "?",
    "承诺",
    "答应",
    "保证",
    "我会",
    "决定",
    "目标",
    "待办",
    "未决",
    "问题",
    "失败",
    "成功",
    "卡住",
    "还差",
    "约定",
    "总结",
)


def estimate_history_tokens(messages: List[Dict[str, Any]]) -> int:
    """估算消息列表的 token 总量（文本走 tokenizer，附加协议包装开销）。"""
    total = 0
    for message in messages:
        content = message.get("content")
        total += count_text_tokens(content) if isinstance(content, str) else 0
        tool_calls = message.get("tool_calls")
        if tool_calls:
            total += count_text_tokens(json.dumps(tool_calls, ensure_ascii=False, default=str))
        total += _TOKENS_PER_MESSAGE
    return total


def _group_messages(messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """把消息序列切成不可拆的组：assistant 工具调用与其回执捆绑，其余单条成组。

    悬空的 role=tool 回执（无前置调用）独立成组——它们本身就可以整体压缩。
    """
    groups: List[List[Dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        role = message.get("role")
        if message.get("tool_calls"):
            end = index + 1
            while end < len(messages) and messages[end].get("role") == "tool":
                end += 1
            groups.append(messages[index:end])
            index = end
        elif role == "tool":
            end = index
            while end < len(messages) and messages[end].get("role") == "tool":
                end += 1
            groups.append(messages[index:end])
            index = end
        else:
            groups.append([message])
            index += 1
    return groups


def _is_receipt_group(group: List[Dict[str, Any]]) -> bool:
    """回执组 = 组内全是 role=tool 的消息（不含发起调用的 assistant）。"""
    return bool(group) and all(message.get("role") == "tool" for message in group)


def _is_key_line(line: str) -> bool:
    """判断一行对话是否承载关键事实（疑问 / 承诺 / 决策 / 成败结论）。"""
    stripped = line.strip()
    if not stripped:
        return False
    return any(keyword in stripped for keyword in _KEY_FACT_KEYWORDS)


def extractive_summary(messages: List[Dict[str, Any]]) -> str:
    """规则性抽取摘要：拣选关键事实行，回执只记条数与检索提示。

    输入是被压缩的旧消息（dict 形状）；输出一段可直接作为 user 消息正文的
    中文文本。确定性问题（如全部是寒暄）给兜底行，摘要不为空。
    """
    receipt_count = sum(1 for message in messages if message.get("role") == "tool")
    key_lines: List[str] = []
    plain_lines: List[str] = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        label = "主播" if role == "assistant" else "观众"
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            target = key_lines if _is_key_line(stripped) else plain_lines
            target.append(f"{label}: {stripped}")
    lines = key_lines if key_lines else plain_lines
    if key_lines and plain_lines:
        # 关键行为主，首尾各补一行原始语境，避免摘要失去时间锚点
        lines = [plain_lines[0], *key_lines, plain_lines[-1]]
    body = "\n".join(lines)[:_SUMMARY_MAX_CHARS]
    if not body:
        body = "（被压缩的旧消息没有可抽取的文本事实）"
    parts: List[str] = []
    if receipt_count:
        parts.append(f"已压缩 {receipt_count} 条工具回执：结果当时已处理；详情可经观察索引分页检索。")
    parts.append(body)
    return "\n".join(parts)


class HistoryCompactor:
    """按 token 预算压缩对话历史（回执优先、结构合法、纯函数式替换）。"""

    def __init__(
        self,
        summarizer: Optional[Callable[[List[Dict[str, Any]]], str]] = None,
    ) -> None:
        """初始化压缩器。

        Args:
            summarizer: 摘要函数（被压缩消息列表 → 摘要文本）；缺省用规则性
                抽取（同步零延迟）。注入点保留给未来的 LLM 摘要实现。
        """
        self._summarize = summarizer or extractive_summary

    def compact(
        self,
        messages: List[Dict[str, Any]],
        budget_tokens: int,
        *,
        profile_name: str = "",
    ) -> List[Dict[str, Any]]:
        """把历史压进预算：超预算时压缩最老回执组、再压最老对话组。

        Args:
            messages: 完整对话历史（可含前导 system 消息，原样保留）。
            budget_tokens: 历史 token 预算；<=0 视为不启用，原样返回。
            profile_name: 仅用于日志归属。

        Returns:
            压缩后的新消息列表。预算内返回入参同一对象（未发生压缩）；
            压缩发生过则首条非 system 消息必为 user 角色的摘要消息。
        """
        if budget_tokens <= 0 or not messages:
            return messages
        if estimate_history_tokens(messages) <= budget_tokens:
            return messages

        # 前导 system 消息不属于对话历史，原样保留在压缩结果之外
        lead = 0
        while lead < len(messages) and messages[lead].get("role") == "system":
            lead += 1
        system_part = list(messages[:lead])
        groups = _group_messages(list(messages[lead:]))

        # 最新一组承载当前对话走向，永远保留；压缩从最老开始
        protected = len(groups) - 1
        kept = set(range(len(groups)))
        compressed: List[Dict[str, Any]] = []

        def kept_tokens() -> int:
            return sum(estimate_history_tokens(groups[index]) for index in kept)

        def fits() -> bool:
            # 尚无压缩内容时按最小摘要预留位判断，避免"还没压就判为已装下"
            summary_tokens = (
                count_text_tokens(self._summarize(compressed)) + _TOKENS_PER_MESSAGE
                if compressed
                else _MIN_SUMMARY_TOKENS
            )
            return kept_tokens() + summary_tokens <= budget_tokens

        # 第一优先级：回执组（已消费的过程数据，可检索回来）按从老到新压缩
        for index, group in enumerate(groups):
            if index == protected or not _is_receipt_group(group):
                continue
            kept.discard(index)
            compressed.extend(group)
            if fits():
                break

        # 第二优先级：仍超预算则从最老的对话组开始压缩，直到装进预算
        if not fits():
            for index in range(len(groups)):
                if fits():
                    break
                if index == protected or index not in kept:
                    continue
                kept.discard(index)
                compressed.extend(groups[index])

        if not compressed:
            # 只剩最新一组还超预算：压无可压，原样返回并告警（调用链不受影响）
            logger.warning(
                f"历史压缩无空间（profile={profile_name}，"
                f"预算={budget_tokens}，实际={estimate_history_tokens(messages)}），本轮原样携带"
            )
            return messages

        receipt_count = sum(1 for message in compressed if message.get("role") == "tool")
        summary_text = self._summarize(compressed)
        summary_message: Dict[str, Any] = {
            "role": "user",
            "content": (
                f"{SUMMARY_MARKER}（由 {len(compressed)} 条旧消息压缩"
                f"{'，含 ' + str(receipt_count) + ' 条工具回执' if receipt_count else ''}）\n{summary_text}"
            ),
        }
        result = system_part + [summary_message] + [m for index in sorted(kept) for m in groups[index]]
        logger.info(
            f"历史已压缩（profile={profile_name}）："
            f"{estimate_history_tokens(messages)} -> {estimate_history_tokens(result)} token，"
            f"压缩 {len(compressed)} 条（含回执 {receipt_count} 条）"
        )
        return result
