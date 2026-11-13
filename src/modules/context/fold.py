"""主动折叠：模型自己挑出已经用完的历史区间折成摘要，原文留在块库里随时可以取回。

宿主的集中整理只能按位置从最老处压，猜不到哪些记录还要用；刚读过这些记录的模型
最清楚哪些已经用完。本模块把"模型写摘要"之外的事都包下来：

- 编号：每条新追加的 user / tool 消息开头写上 ``[m0012]``，模型用编号指定区间。
  编号只写在还没发出过的新消息上，已发出的消息一个字节都不动，前缀缓存不受影响；
  编号在 Agent 的整个生命周期里只增不减，旧任务留下的折叠提议对不上新任务的消息；
- 折叠：区间按"工具调用 + 回执"整组扩展，不拆开配对；固定层（哪些算固定由 Agent
  判定，如系统说明、任务事实、原始指令）和最近几组不能折；摘要轻微超限时在缓冲内
  保留、超出缓冲则在句末或换行处安全截断并附注取回提示；区间太小、或折后替身未能
  比原区间净省足够 token 的不执行——省下的空间抵不过一次前缀缓存失效；
- 块库：被折叠的原文整段存进块库，按块号读回，或按关键词只取命中的片段。读回的
  原文只作为一次工具结果出现在尾部，不塞回历史中段；
- 收起：到了预算线又来不及等摘要时，把最老的大条记录换成一行占位、原文进块库。
  不调模型、瞬间完成，角色不必停下来等。

折叠和收起都会改写历史中段，Agent 要在下一次请求前向前缀自检预告。摘要怎么写、
什么时候请模型来折，由各 Agent 决定；本模块不调模型，也不出现任何游戏名。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from src.modules.context.checkpoint import group_spans
from src.modules.context.tokens import TokenCounter

__all__ = [
    "BLOCK_KEY",
    "REF_KEY",
    "ContextFolds",
    "FoldBlock",
    "FoldOutcome",
    "FoldPacer",
    "FoldRange",
    "FoldRequest",
]

# 消息内部标记（下划线开头，模型看不到）：消息编号；折叠块替身消息的块号
REF_KEY = "_context_ref"
BLOCK_KEY = "_context_block"

# 摘要允许超出字数上限的缓冲比例：模型多写几十到两三百字时保留全文，避免仅超几个字就整单报废白付输出
_SUMMARY_GRACE_SHARE = 0.1
# 超出缓冲后在后半段寻找自然断点的标点集合：优先换行或句末标点，退而求其次取分句逗号与顿号
_SENTENCE_ENDS = "。！？；.!?;"
_CLOSING_MARKS = "”’）)\"'"
_CLAUSE_ENDS = "，、,"
# 折后完整替身消息必须严格小于原区间，且至少省下原区间四成（并不少于最少净省下限），否则抵不过前缀缓存失效
_MIN_SAVED_SHARE = 0.4
_MIN_SAVED_TOKENS = 400

# 关键词查找最多返回的片段数，以及每个片段在命中处前后各带的字符数
_MAX_HITS = 8
_SNIPPET_RADIUS = 160


@dataclass(frozen=True)
class FoldRange:
    """一段可以折叠的连续区间（交给模型挑）：起止编号、约多少 token、期间调用过哪些工具。"""

    start: str
    end: str
    tokens: int
    tools: Tuple[str, ...]


@dataclass(frozen=True)
class FoldRequest:
    """模型要求的一次折叠：起止编号（也可以写已有的块号）与替代原文的摘要。"""

    start: str
    end: str
    summary: str


@dataclass
class FoldBlock:
    """块库里的一块：被折叠或收起的原文，以及它在历史里留下的替身占多少 token。

    ``kind`` 为 ``fold``（模型写了摘要）或 ``stow``（宿主收起，只留一行占位）。
    """

    block_id: str
    kind: str
    start: str
    end: str
    summary: str
    originals: List[Dict[str, Any]]
    tokens_before: int
    tokens_after: int


@dataclass
class FoldOutcome:
    """一批折叠的结果：做成的块与逐条拒绝原因（拒绝不影响同批其他区间）。"""

    blocks: List[FoldBlock] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def tokens_saved(self) -> int:
        """这批折叠一共省下的 token。"""
        return sum(block.tokens_before - block.tokens_after for block in self.blocks)


class FoldPacer:
    """决定什么时候请模型来折：越过折叠线、且距上次发起又涨了一截才再请。

    每请一次都要模型写摘要；历史刚折完、或模型认为没东西可折时立刻再请，只会反复
    白付输出费。基线在发起时和折叠落地后各记一次；历史被别的途径压小（集中整理、
    收起、对话重开）时基线跟着降到当前体积，"又涨了一截"从压小后的体积起算。
    """

    def __init__(self) -> None:
        self._baseline = 0

    def due(self, tokens: int, *, line: int, growth: int) -> bool:
        """历史到了折叠线、且比上次基线多出 ``growth`` 才该再请。

        历史比基线还小：说明上次记基线之后被别的途径压小过，先把基线降到当前体积。
        不降的话，基线停在压小前的高位，历史要涨回那个高位再多一截才会再请——
        集中整理的触发线往往比那更低，折叠就再也轮不上了。
        """
        if tokens < self._baseline:
            self._baseline = tokens
        return tokens >= line and tokens - self._baseline >= growth

    def settle(self, tokens: int) -> None:
        """记下这一刻的历史体积，作为下次判断涨了多少的基线。"""
        self._baseline = tokens

    def reset(self) -> None:
        """开新任务：基线归零。"""
        self._baseline = 0


class ContextFolds:
    """一个 Agent 的折叠账：消息编号、块库与就地改写（单个 Agent 持有，非线程安全）。

    ``is_pinned`` 判定固定层消息（不编号、不能折、不能收起）；``recall_hint`` 是替身
    消息里告诉模型怎么取回原文的一句话（含取回工具名，工具名属于 Agent）。
    """

    def __init__(
        self,
        counter: TokenCounter,
        *,
        is_pinned: Callable[[Mapping[str, Any]], bool],
        recall_hint: str,
    ) -> None:
        self._counter = counter
        self._is_pinned = is_pinned
        self._recall_hint = recall_hint
        self._next_ref = 1
        self._next_block = 1
        self._blocks: Dict[str, FoldBlock] = {}

    @property
    def blocks(self) -> List[FoldBlock]:
        """块库里现有的块（按创建先后）。"""
        return list(self._blocks.values())

    def clear(self) -> None:
        """开新任务：块库清空；编号与块号继续往后数，旧提议里的编号对不上新消息。"""
        self._blocks.clear()

    def number(self, messages: Sequence[MutableMapping[str, Any]]) -> int:
        """给还没编号的 user / tool 消息编号并把 ``[m0012]`` 写进正文开头，返回编了几条。

        必须在这些消息第一次发给模型之前调用：编号写进正文后不再改，前缀照样稳定。
        """
        count = 0
        for message in messages:
            if REF_KEY in message or BLOCK_KEY in message or message.get("role") not in ("user", "tool"):
                continue
            if self._is_pinned(message):
                continue
            ref = f"m{self._next_ref:04d}"
            self._next_ref += 1
            message[REF_KEY] = ref
            _prefix_content(message, f"[{ref}] ")
            count += 1
        return count

    def ranges(self, messages: Sequence[Mapping[str, Any]], *, keep_recent: int, min_tokens: int) -> List[FoldRange]:
        """列出可折叠的连续区间（旧→新），每段至少 ``min_tokens``，供请模型折叠时参考。"""
        spans = group_spans(messages)
        allowed = self._allowed_groups(messages, spans, keep_recent)
        runs: List[Tuple[int, int]] = []
        for index, (start, end) in enumerate(spans):
            if not allowed[index]:
                continue
            if runs and runs[-1][1] == start:
                runs[-1] = (runs[-1][0], end)
            else:
                runs.append((start, end))
        result: List[FoldRange] = []
        for start, end in runs:
            labels = [label for label in (_label(message) for message in messages[start:end]) if label]
            tokens = self._counter.messages(messages[start:end])
            if not labels or tokens < min_tokens:
                continue
            result.append(FoldRange(labels[0], labels[-1], tokens, _tool_counts(messages[start:end])))
        return result

    def apply(
        self,
        messages: List[Dict[str, Any]],
        requests: Sequence[FoldRequest],
        *,
        keep_recent: int,
        min_tokens: int,
        max_summary_chars: int,
    ) -> FoldOutcome:
        """把一批折叠就地套到当前历史上：每段换成一条替身消息，原文进块库。

        提议可以是基于稍早一份历史算出的：折叠只碰旧区间，而历史平时只在尾部追加，
        按编号在当前历史里重新定位即可；编号已不在（被集中整理掉、对话重开）的那段拒绝。
        """
        outcome = FoldOutcome()
        spans = group_spans(messages)
        allowed = self._allowed_groups(messages, spans, keep_recent)
        group_of = {index: number for number, (start, end) in enumerate(spans) for index in range(start, end)}
        positions = {label: index for index, message in enumerate(messages) if (label := _label(message))}
        accepted: List[Tuple[int, int, str]] = []
        for request in requests:
            start, end, summary = request.start.strip(), request.end.strip(), request.summary.strip()
            first, last = positions.get(start), positions.get(end)
            error = ""
            if first is None or last is None:
                error = "编号不在当前历史里（可能已被整理掉）"
            elif first > last:
                error = "起点在终点之后"
            elif not summary:
                error = "摘要为空"
            else:
                # 先扩展到完整工具调用配对组并核对区间边界，再收敛超长摘要，最后拿完整替身消息对比原区间算净省 token：
                # 轻微超限保留全文、超出缓冲在句号或换行处截断附注，但折后若不比原区间小或省不下足够空间则拒收，防止折出比原文更胖的摘要。
                head, tail = group_of[first], group_of[last]
                low, high = spans[head][0], spans[tail][1]
                originals = messages[low:high]
                before = self._counter.messages(originals)
                if not all(allowed[head : tail + 1]):
                    error = "区间含不能折的消息（固定层或最近几组记录）"
                elif any(low < taken_high and taken_low < high for taken_low, taken_high, _ in accepted):
                    error = "与同批另一段区间重叠"
                elif before < min_tokens:
                    error = f"区间只有约 {before} token，不值得折（至少 {min_tokens}）"
                else:
                    fitted = _fit_summary(summary, max_summary_chars, self._recall_hint)
                    labels = [label for label in (_label(message) for message in originals) if label]
                    preview = self._build_stand_in(
                        f"b{self._next_block + len(accepted)}",
                        labels[0],
                        labels[-1],
                        before,
                        fitted,
                    )
                    after = self._counter.message(preview)
                    floor_saved = min(_MIN_SAVED_TOKENS, max(1, min_tokens // 2))
                    required_saved = max(floor_saved, int(before * _MIN_SAVED_SHARE))
                    if after >= before or before - after < required_saved:
                        error = (
                            f"折后约 {after} token（原文约 {before} token），净省不足 {required_saved} token，不划算"
                        )
                    else:
                        accepted.append((low, high, fitted))
            if error:
                outcome.errors.append(f"{start}–{end}：{error}")
        # 从后往前替换：后面的区间先换，前面区间的下标不受影响；块号仍按历史先后分配
        blocks: List[Tuple[int, int, FoldBlock, Dict[str, Any]]] = []
        for low, high, summary in sorted(accepted):
            block, stand_in = self._fold_block(messages[low:high], summary)
            blocks.append((low, high, block, stand_in))
        for low, high, _, stand_in in reversed(blocks):
            messages[low:high] = [stand_in]
        outcome.blocks = [block for _, _, block, _ in blocks]
        return outcome

    def stow(
        self,
        messages: List[Dict[str, Any]],
        *,
        keep_recent: int,
        current_tokens: int,
        target_tokens: int,
        min_tokens: int,
    ) -> int:
        """从最老的可折记录起，把单条不少于 ``min_tokens`` 的消息换成一行占位，直到降到目标线。

        原文进块库，按块号可以完整取回；消息仍在原位、编号与工具调用配对都不变，
        只是正文变短。返回一共省下的 token（降不到目标线时尽力而为）。
        """
        spans = group_spans(messages)
        allowed = self._allowed_groups(messages, spans, keep_recent)
        saved = 0
        for number, (start, end) in enumerate(spans):
            if current_tokens - saved <= target_tokens:
                break
            if not allowed[number]:
                continue
            for index in range(start, end):
                message = messages[index]
                if message.get("role") not in ("user", "tool") or BLOCK_KEY in message:
                    continue
                before = self._counter.message(message)
                if before < min_tokens:
                    continue
                block_id = self._take_block_id()
                ref = str(message.get(REF_KEY) or "")
                note = f"[原文已收起 {block_id}：约 {before} token，{self._recall_hint}]"
                stand_in = {**message, "content": f"[{ref}] {note}" if ref else note}
                after = self._counter.message(stand_in)
                self._blocks[block_id] = FoldBlock(block_id, "stow", ref, ref, note, [deepcopy(message)], before, after)
                messages[index] = stand_in
                saved += before - after
                if current_tokens - saved <= target_tokens:
                    break
        return saved

    def recall(self, *, block: str = "", query: str = "", max_chars: int) -> Dict[str, Any]:
        """取回原文：给块号读整块（过长截断并说明）；给关键词在块库里找命中片段；两者都给只在该块里找。"""
        block, query = block.strip(), query.strip()
        if not block and not query:
            return {"ok": False, "error": "block 和 query 至少填一个"}
        if block:
            found = self._blocks.get(block)
            if found is None:
                return {"ok": False, "error": f"块库里没有 {block}", "blocks": list(self._blocks)}
            text = _render(found.originals)
            if query:
                hits = _snippets(text, query)
                return {"ok": True, "block": block, "hits": hits} if hits else _miss(query, block)
            result: Dict[str, Any] = {
                "ok": True,
                "block": block,
                "range": _span_label(found),
                "original": text[:max_chars],
            }
            if len(text) > max_chars:
                result["truncated"] = f"原文共 {len(text)} 字，这里只给前 {max_chars} 字；带上 query 只看命中的片段"
            return result
        found_hits: List[Dict[str, Any]] = []
        for candidate in self._blocks.values():
            for snippet in _snippets(_render(candidate.originals), query):
                found_hits.append({"block": candidate.block_id, "range": _span_label(candidate), "snippet": snippet})
        if not found_hits:
            return _miss(query, "")
        return {"ok": True, "hits": found_hits[:_MAX_HITS]}

    def _build_stand_in(self, block_id: str, start: str, end: str, before: int, summary: str) -> Dict[str, Any]:
        """拼装折叠替身消息：统一用于入账前的折后 token 预估与正式落块，保证两侧计量口径一致。"""
        span = start if start == end else f"{start}–{end}"
        return {
            "role": "user",
            "content": f"[已折叠 {block_id} · {span} · 原文约 {before} token，{self._recall_hint}]\n{summary}",
            BLOCK_KEY: block_id,
        }

    def _fold_block(self, originals: Sequence[Dict[str, Any]], summary: str) -> Tuple[FoldBlock, Dict[str, Any]]:
        """为一段原文建块并生成替身消息（user 角色，带块号标记，不再编号）。"""
        labels = [label for label in (_label(message) for message in originals) if label]
        start, end = labels[0], labels[-1]
        before = self._counter.messages(originals)
        block_id = self._take_block_id()
        stand_in = self._build_stand_in(block_id, start, end, before, summary)
        block = FoldBlock(
            block_id,
            "fold",
            start,
            end,
            summary,
            [deepcopy(message) for message in originals],
            before,
            self._counter.message(stand_in),
        )
        self._blocks[block_id] = block
        return block, stand_in

    def _take_block_id(self) -> str:
        block_id = f"b{self._next_block}"
        self._next_block += 1
        return block_id

    def _allowed_groups(
        self, messages: Sequence[Mapping[str, Any]], spans: Sequence[Tuple[int, int]], keep_recent: int
    ) -> List[bool]:
        """每组能不能折：组里没有固定层消息，且不在最近 ``keep_recent`` 组里。"""
        cutoff = max(0, len(spans) - keep_recent)
        return [
            number < cutoff and not any(self._is_pinned(message) for message in messages[start:end])
            for number, (start, end) in enumerate(spans)
        ]


def _find_safe_cut(window: str, floor: int) -> Optional[int]:
    """在截断窗口后半段从后往前找自然断点：优先换行或句末标点（保留紧跟的闭合引号/括号），其次取逗号与顿号。"""
    for index in range(len(window) - 1, floor - 1, -1):
        char = window[index]
        if char == "\n":
            return index
        if char in _SENTENCE_ENDS:
            end = index + 1
            while end < len(window) and window[end] in _CLOSING_MARKS:
                end += 1
            return end
    for index in range(len(window) - 1, floor - 1, -1):
        if window[index] in _CLAUSE_ENDS:
            return index
    return None


def _fit_summary(summary: str, max_chars: int, recall_hint: str) -> str:
    """收敛超长折叠摘要：轻微超限在缓冲内原样放行；超出缓冲则在句号或换行处截断并附注原文取回方式。"""
    grace_limit = max_chars + max(1, int(max_chars * _SUMMARY_GRACE_SHARE))
    if len(summary) <= grace_limit:
        return summary
    note = f"（超长部分已截断，{recall_hint}）"
    budget = max_chars - len(note) - 1 if max_chars > len(note) * 2 else max(1, max_chars)
    window = summary[:budget]
    cut = _find_safe_cut(window, budget // 2)
    head = (window[:cut] if cut is not None else window).rstrip()
    return f"{head}\n{note}" if head else note


def _label(message: Mapping[str, Any]) -> Optional[str]:
    """模型能用来指定区间的标签：消息编号或折叠块号。"""
    value = message.get(REF_KEY) or message.get(BLOCK_KEY)
    return str(value) if value else None


def _span_label(block: FoldBlock) -> str:
    return block.start if block.start == block.end else f"{block.start}–{block.end}"


def _prefix_content(message: MutableMapping[str, Any], prefix: str) -> None:
    """把编号写进正文开头：字符串直接加；分段正文加在第一个文本段前，没有文本段就插一段。"""
    content = message.get("content")
    if isinstance(content, list):
        parts = list(content)
        for index, part in enumerate(parts):
            if isinstance(part, Mapping) and part.get("type") == "text":
                parts[index] = {**part, "text": prefix + str(part.get("text", ""))}
                break
        else:
            parts.insert(0, {"type": "text", "text": prefix.rstrip()})
        message["content"] = parts
        return
    message["content"] = prefix + ("" if content is None else str(content))


def _tool_counts(messages: Sequence[Mapping[str, Any]]) -> Tuple[str, ...]:
    """区间里调用过的工具，按首次出现排序并带次数（如 ``observe×3``）。"""
    counts: Dict[str, int] = {}
    for message in messages:
        for call in message.get("tool_calls") or []:
            name = str((call.get("function") or {}).get("name") or call.get("name") or "")
            if name:
                counts[name] = counts.get(name, 0) + 1
    return tuple(name if count == 1 else f"{name}×{count}" for name, count in counts.items())


def _content_text(content: Any) -> str:
    """正文转纯文本：分段正文只取文本段，图像引用写一个占位。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "text":
                pieces.append(str(part.get("text", "")))
            elif isinstance(part, Mapping) and part.get("type") in ("image_ref", "image_url"):
                pieces.append("[图]")
            else:
                pieces.append(str(part))
        return "".join(pieces)
    return str(content)


def _render(messages: Sequence[Mapping[str, Any]]) -> str:
    """把一段原文渲染成可读文本：每条一行起头写角色，助手的工具调用写出名字和参数。"""
    lines = []
    for message in messages:
        role = str(message.get("role") or "")
        text = _content_text(message.get("content"))
        calls = [
            f"{(call.get('function') or {}).get('name', '')} {(call.get('function') or {}).get('arguments', '')}"
            for call in message.get("tool_calls") or []
        ]
        if calls:
            text = (text + "\n" if text else "") + "调用 " + "；".join(calls)
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _snippets(text: str, query: str) -> List[str]:
    """在文本里找关键词，返回命中处前后各一段的片段；整句找不到时按空格拆开逐词找。"""
    folded = text.casefold()
    terms = [query] + (query.split() if len(query.split()) > 1 else [])
    for term in terms:
        needle = term.casefold()
        positions: List[int] = []
        start = folded.find(needle)
        while start >= 0 and len(positions) < _MAX_HITS:
            if not positions or start - positions[-1] > _SNIPPET_RADIUS * 2:
                positions.append(start)
            start = folded.find(needle, start + len(needle))
        if positions:
            return [text[max(0, at - _SNIPPET_RADIUS) : at + len(needle) + _SNIPPET_RADIUS] for at in positions]
    return []


def _miss(query: str, block: str) -> Dict[str, Any]:
    where = f"块 {block}" if block else "块库"
    return {"ok": True, "hits": [], "note": f"{where}里没找到「{query}」"}
