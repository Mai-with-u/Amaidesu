"""结构化人物设定档案解析与提示词渲染。

主播在杂谈或日常互动中需要稳定知道自己的来历、此刻站在镜头前的舞台外观、其他形态、
钦定喜好与直播日常，同时严禁编造无出处的现实生活经历。本模块将本地不入库的设定档案
（Markdown 或 TOML）解析为五个固定小节，并以不超过 900 字的静态文本拼入决策层
「她是谁」与表达层「你是谁」末尾，整场保持不变以命中模型前缀缓存。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict

from src.modules.logging import get_logger

logger = get_logger("PersonaProfile")

# 静态拼入系统提示词的总字数硬上限，避免挤占上下文预算并确保整场稳定命中前缀缓存
MAX_PROFILE_CHARS = 900

# 防背书守则：提示模型设定只是角色底子，只在自我介绍、相关话头展开或观众问起时自然聊及
_PLANNER_GUARDRAIL = (
    "以上设定是关于她自己的真实底子：自我介绍环节、相关话头展开或观众问起来历、外形与 AI 身份时"
    "自然拿出来聊；平时聊其他话题时不强行往设定词上扯，档案与记忆里都没写的现实生活经历一律不编。"
)
_REPLYER_GUARDRAIL = (
    "以上是你自己的真实底子：自我介绍、聊到相关话头或观众问起你的来历、身上的细节与 AI 身份时"
    "自然讲出来；平时聊别的事不用每句往设定上靠，档案和交接材料里没写的现实生活经历不编。"
)

# 不含任何真人信息的通用设定档案格式说明与样板，供测试与运营本地新建档案参考
PERSONA_PROFILE_TEMPLATE_MD = """\
<!--
人物设定档案格式说明（请保存至本地 config/persona_profile.md，该目录已被 .gitignore 忽略）：
1. 使用二级标题划分五个标准小节：身份与来历 / 舞台形象 / 其他形态 / 喜好与执念 / 身边的人与直播日常。
2. 「舞台形象」下可用三级标题 `### <形象键名>：<显示名称>` 登记多套外观，由配置项 active_outfit 切换当前上台的一套，未上台的形态会自动归入「其他形态」。
3. 只写长期成立的角色底子与公开设定，不写偶发口癖标签，不写未公开的个人隐私信息。
-->

## 身份与来历
- 角色公开身世、成为虚拟主播的来由与 AI 身份定位。

## 舞台形象
### default_2d：默认舞台形象
- 发色发型、瞳色、标志性配饰与服装细节。

### chibi_form：迷你形态
- 迷你或非人形形态的外观锚点。

## 喜好与执念
- 长期钦定的喜好与在意的梗。

## 身边的人与直播日常
- 直播主要内容方向与联机伙伴的公开泛称。
"""

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_OUTFIT_HEADER_RE = re.compile(
    r"^(?:\[(?P<bracket_key>[A-Za-z0-9_.-]+)\]\s*(?P<bracket_label>.*)|(?P<colon_key>[A-Za-z0-9_.-]+)\s*[:：]\s*(?P<colon_label>.*))$"
)


class OutfitEntry(BaseModel):
    """单套形象定义：键名用于匹配 active_outfit，label 为人类可读名称，body 为外观描述。"""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    body: str


class PersonaProfile(BaseModel):
    """解析后的结构化人物设定档案。"""

    model_config = ConfigDict(frozen=True)

    identity: str = ""
    active_outfit_name: str = ""
    active_outfit: str = ""
    other_forms: str = ""
    likes_and_quirks: str = ""
    companions_and_routine: str = ""

    def is_empty(self) -> bool:
        """五个结构化小节均为空时返回 True。"""
        return not any(
            (
                self.identity,
                self.active_outfit,
                self.other_forms,
                self.likes_and_quirks,
                self.companions_and_routine,
            )
        )

    def render_for_planner(self, *, max_chars: int = MAX_PROFILE_CHARS) -> str:
        """渲染拼入决策层系统提示词「她是谁」末尾的结构化设定文本。"""
        outfit_heading = (
            f"当前舞台形象（观众此刻看到的她：{self.active_outfit_name}）"
            if self.active_outfit_name
            else "当前舞台形象（观众此刻看到的她）"
        )
        sections = [
            ("身份与来历", self.identity),
            (outfit_heading, self.active_outfit),
            ("其他形态", self.other_forms),
            ("喜好与执念", self.likes_and_quirks),
            ("身边的人与直播日常", self.companions_and_routine),
        ]
        return _render_bounded(sections, guardrail=_PLANNER_GUARDRAIL, heading_prefix="##", max_chars=max_chars)

    def render_for_replyer(self, *, max_chars: int = MAX_PROFILE_CHARS) -> str:
        """渲染拼入表达层系统提示词「你是谁」末尾的结构化设定文本。"""
        outfit_heading = (
            f"当前舞台形象（观众眼里的你：{self.active_outfit_name}）"
            if self.active_outfit_name
            else "当前舞台形象（观众眼里的你）"
        )
        sections = [
            ("身份与来历", self.identity),
            (outfit_heading, self.active_outfit),
            ("其他形态", self.other_forms),
            ("喜好与执念", self.likes_and_quirks),
            ("身边的人与直播日常", self.companions_and_routine),
        ]
        return _render_bounded(sections, guardrail=_REPLYER_GUARDRAIL, heading_prefix="##", max_chars=max_chars)


def _normalize_block(text: str) -> str:
    """清理多余空行与行尾空白，保持紧凑列表文本。"""
    cleaned_lines: list[str] = []
    blank_pending = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if cleaned_lines:
                blank_pending = True
            continue
        if blank_pending:
            cleaned_lines.append("")
            blank_pending = False
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _classify_section(title: str) -> str | None:
    """将二级标题归类到五个标准槽位之一。"""
    normalized = title.strip()
    if any(k in normalized for k in ("其他形态", "本体形态", "其余形态")):
        return "other_forms"
    if any(k in normalized for k in ("当前舞台形象", "舞台形象", "当前形象", "形象", "外观", "穿搭")):
        return "outfits"
    if any(k in normalized for k in ("身份", "来历", "身世", "背景")):
        return "identity"
    if any(k in normalized for k in ("喜好", "执念", "偏好")):
        return "likes_and_quirks"
    if any(k in normalized for k in ("身边的人", "直播日常", "日常", "伙伴")):
        return "companions_and_routine"
    return None


def _parse_outfit_header(header: str) -> tuple[str, str]:
    """从三级标题提取 (形象键名, 显示名)；未带显式键名时以标题本身为键名与显示名。"""
    match = _OUTFIT_HEADER_RE.match(header.strip())
    if not match:
        clean = header.strip()
        return clean, clean
    if match.group("bracket_key"):
        key = match.group("bracket_key").strip()
        label = (match.group("bracket_label") or "").strip(" ：:-") or key
        return key, label
    key = match.group("colon_key").strip()
    label = (match.group("colon_label") or "").strip() or key
    return key, label


def _select_outfits(
    direct_text: str,
    entries: Sequence[OutfitEntry],
    explicit_other: str,
    active_outfit: str,
) -> tuple[str, str, str]:
    """根据 active_outfit 选出当前舞台形象，并将未上台的其余形象归入其他形态。"""
    target = active_outfit.strip()
    selected_label = ""
    selected_body = _normalize_block(direct_text)
    leftover_parts: list[str] = []

    if entries:
        chosen: OutfitEntry | None = None
        if target:
            for entry in entries:
                if entry.key == target or entry.label == target:
                    chosen = entry
                    break
            if chosen is None:
                logger.warning(
                    f"设定档案未找到 active_outfit='{target}' 对应的形象小节，回退使用首套形象 '{entries[0].key}'"
                )
        if chosen is None:
            chosen = entries[0]

        selected_label = chosen.label if chosen.label != chosen.key else ""
        chosen_body = _normalize_block(chosen.body)
        selected_body = f"{selected_body}\n{chosen_body}".strip() if selected_body else chosen_body

        for entry in entries:
            if entry is chosen:
                continue
            body_one_line = "；".join(
                ln.lstrip("-*• ").strip() for ln in _normalize_block(entry.body).splitlines() if ln.strip()
            )
            if body_one_line:
                title_part = entry.label or entry.key
                leftover_parts.append(f"- {title_part}：{body_one_line}")

    other_clean = _normalize_block(explicit_other)
    if other_clean:
        leftover_parts.append(other_clean)

    return selected_label, selected_body, "\n".join(leftover_parts)


def parse_persona_profile_markdown(content: str, *, active_outfit: str = "") -> PersonaProfile:
    """解析 Markdown 格式的人物设定档案。"""
    stripped = _HTML_COMMENT_RE.sub("", content or "")
    buckets: dict[str, list[str]] = {
        "identity": [],
        "outfits": [],
        "other_forms": [],
        "likes_and_quirks": [],
        "companions_and_routine": [],
    }
    outfit_entries: list[OutfitEntry] = []

    current_slot: str | None = None
    current_outfit_key: str | None = None
    current_outfit_label: str = ""
    current_outfit_lines: list[str] = []

    def _flush_outfit() -> None:
        nonlocal current_outfit_key, current_outfit_label, current_outfit_lines
        if current_outfit_key is not None:
            body = _normalize_block("\n".join(current_outfit_lines))
            if body:
                outfit_entries.append(OutfitEntry(key=current_outfit_key, label=current_outfit_label, body=body))
        current_outfit_key = None
        current_outfit_label = ""
        current_outfit_lines = []

    for raw_line in stripped.splitlines():
        h2_match = _H2_RE.match(raw_line)
        if h2_match:
            _flush_outfit()
            current_slot = _classify_section(h2_match.group(1))
            continue

        if current_slot == "outfits":
            h3_match = _H3_RE.match(raw_line)
            if h3_match:
                _flush_outfit()
                current_outfit_key, current_outfit_label = _parse_outfit_header(h3_match.group(1))
                continue
            if current_outfit_key is not None:
                current_outfit_lines.append(raw_line)
                continue

        if current_slot is not None:
            buckets[current_slot].append(raw_line)

    _flush_outfit()

    outfit_name, active_body, other_body = _select_outfits(
        direct_text="\n".join(buckets["outfits"]),
        entries=outfit_entries,
        explicit_other="\n".join(buckets["other_forms"]),
        active_outfit=active_outfit,
    )
    return PersonaProfile(
        identity=_normalize_block("\n".join(buckets["identity"])),
        active_outfit_name=outfit_name,
        active_outfit=active_body,
        other_forms=other_body,
        likes_and_quirks=_normalize_block("\n".join(buckets["likes_and_quirks"])),
        companions_and_routine=_normalize_block("\n".join(buckets["companions_and_routine"])),
    )


def parse_persona_profile_toml(content: str, *, active_outfit: str = "") -> PersonaProfile:
    """解析 TOML 格式的人物设定档案。"""
    data = tomllib.loads(content or "")

    def _read_text(value: object) -> str:
        if isinstance(value, str):
            return _normalize_block(value)
        if isinstance(value, list):
            return _normalize_block("\n".join(f"- {item}" for item in value if str(item).strip()))
        if isinstance(value, dict):
            desc = value.get("description") or value.get("body") or ""
            return _read_text(desc)
        return ""

    entries: list[OutfitEntry] = []
    raw_outfits = data.get("outfits")
    direct_outfit = _read_text(data.get("active_outfit"))
    if isinstance(raw_outfits, dict):
        for key, item in raw_outfits.items():
            if isinstance(item, dict):
                label = str(item.get("label") or item.get("title") or key).strip()
                body = _read_text(item.get("body") or item.get("description") or item.get("items") or "")
            else:
                label = str(key).strip()
                body = _read_text(item)
            if body:
                entries.append(OutfitEntry(key=str(key).strip(), label=label, body=body))

    outfit_name, active_body, other_body = _select_outfits(
        direct_text=direct_outfit,
        entries=entries,
        explicit_other=_read_text(data.get("other_forms")),
        active_outfit=active_outfit,
    )
    return PersonaProfile(
        identity=_read_text(data.get("identity")),
        active_outfit_name=outfit_name,
        active_outfit=active_body,
        other_forms=other_body,
        likes_and_quirks=_read_text(data.get("likes_and_quirks")),
        companions_and_routine=_read_text(data.get("companions_and_routine")),
    )


def resolve_profile_path(profile_file: str, *, base_dirs: Sequence[Path] | None = None) -> Path | None:
    """按候选根目录解析设定档案路径；相对路径优先查 config/ 再查仓库根。"""
    raw = (profile_file or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None

    roots = list(base_dirs) if base_dirs is not None else [Path("config"), Path(".")]
    seen: set[Path] = set()
    for root in roots:
        resolved = (root / candidate).resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def load_persona_profile(
    profile_file: str,
    *,
    active_outfit: str = "",
    base_dirs: Sequence[Path] | None = None,
) -> PersonaProfile | None:
    """加载并解析本地人物设定档案；未配置、文件缺失或格式损坏时安全返回 None。"""
    raw = (profile_file or "").strip()
    if not raw:
        return None

    path = resolve_profile_path(raw, base_dirs=base_dirs)
    if path is None:
        logger.warning(f"人物设定档案不存在，跳过加载: profile_file='{raw}'")
        return None

    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".toml":
            profile = parse_persona_profile_toml(text, active_outfit=active_outfit)
        else:
            profile = parse_persona_profile_markdown(text, active_outfit=active_outfit)
    except Exception as exc:
        logger.warning(f"人物设定档案解析失败，跳过加载: path='{path}'", exc=exc)
        return None

    if profile.is_empty():
        logger.warning(f"人物设定档案解析后无有效内容，跳过加载: path='{path}'")
        return None
    return profile


def _trim_to_sentence(text: str, budget: int) -> str:
    """在字数预算内裁剪文本，优先停在完整句号、分号或换行边界。"""
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    window = text[:budget]
    for sep in ("\n", "。", "！", "？", "；"):
        idx = window.rfind(sep)
        if idx >= budget // 2:
            return window[: idx + (0 if sep == "\n" else 1)].rstrip()
    if budget <= 1:
        return window
    return window[: budget - 1].rstrip() + "…"


def _render_bounded(
    sections: Sequence[tuple[str, str]],
    *,
    guardrail: str,
    heading_prefix: str,
    max_chars: int,
) -> str:
    """将非空设定小节与防背书守则拼装在总字数上限以内。"""
    non_empty = [(title, body) for title, body in sections if body.strip()]
    if not non_empty or max_chars <= 0:
        return ""

    def _assemble(items: Sequence[tuple[str, str]]) -> str:
        blocks = [f"{heading_prefix} {title}\n{body}" for title, body in items if body.strip()]
        if not blocks:
            return ""
        return "\n\n".join(blocks) + f"\n\n{guardrail}"

    full = _assemble(non_empty)
    if len(full) <= max_chars:
        return full

    logger.warning(f"人物设定档案渲染长度 {len(full)} 超出上限 {max_chars}，按小节配额裁剪")
    # 预留守则、标题与段落分隔开销，剩余字数按权重分配给各非空小节（身份与当前形象优先）
    fixed_overhead = len(guardrail) + 4 + sum(len(heading_prefix) + len(title) + 4 for title, _ in non_empty)
    available = max(0, max_chars - fixed_overhead)
    if available == 0:
        return _trim_to_sentence(guardrail, max_chars)

    weights = [3 if idx < 2 else 2 for idx in range(len(non_empty))]
    total_weight = sum(weights)
    trimmed_items: list[tuple[str, str]] = []
    for (title, body), weight in zip(non_empty, weights, strict=True):
        quota = max(24, available * weight // total_weight)
        trimmed_items.append((title, _trim_to_sentence(body, quota)))

    assembled = _assemble(trimmed_items)
    while len(assembled) > max_chars and trimmed_items:
        # 若仍因短小节保底略超上限，从排在后面的次要小节逐步缩减
        last_idx = len(trimmed_items) - 1
        overflow = len(assembled) - max_chars
        title, body = trimmed_items[last_idx]
        if len(body) > overflow + 12:
            trimmed_items[last_idx] = (title, _trim_to_sentence(body, len(body) - overflow - 2))
        else:
            trimmed_items.pop()
        assembled = _assemble(trimmed_items)

    return assembled[:max_chars] if len(assembled) > max_chars else assembled


__all__ = [
    "MAX_PROFILE_CHARS",
    "OutfitEntry",
    "PERSONA_PROFILE_TEMPLATE_MD",
    "PersonaProfile",
    "load_persona_profile",
    "parse_persona_profile_markdown",
    "parse_persona_profile_toml",
    "resolve_profile_path",
]
