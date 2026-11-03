"""人设边界栏的统一写法：三条说话通路（Planner、Replyer、嘴）共用一份渲染。

边界栏是运营者在人设里写的"她不答应的事"（``[agents.streamer.persona].boundaries``）。
三条通路各自把它放进规矩，但措辞与折行规则只在这里定义一次：运营者在 WebUI 里写成
多行时，折成一行用"；"连接，免得第二行起在规矩列表里变成没有列表记号的裸文本；
没写（空串或只有空白）时返回空串，调用方据此整行省略、不留空行。
"""

from __future__ import annotations

# 边界栏在规矩里的行首标签：默认文本是并列短语（"线下见面、钱和红包……"），
# 标签带出"不答应"，两者拼起来读作一句完整的规矩
BOUNDARIES_LABEL = "不答应的事："


def boundaries_text(boundaries: str) -> str:
    """把边界栏折成一行：去掉每行首尾空白与空行，多行用"；"连接；没写时返回空串。"""
    lines = [line.strip() for line in (boundaries or "").splitlines()]
    return "；".join(line for line in lines if line)


def boundaries_line(boundaries: str) -> str:
    """规矩里的那一行"不答应的事：…"（不带列表记号与换行）；没写边界时返回空串。"""
    text = boundaries_text(boundaries)
    return f"{BOUNDARIES_LABEL}{text}" if text else ""


__all__ = ["BOUNDARIES_LABEL", "boundaries_line", "boundaries_text"]
