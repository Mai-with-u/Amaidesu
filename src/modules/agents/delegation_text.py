"""委派正文的格式：发起方自己的原话 + 可选的附带材料，框架据此把两段分开。

主播委派时，正文是"[本次目标]"一段她自己的原话，后面附上"[来源对话：……]"一段观众与玩家的逐字原话
（游戏侧只拿它核对物品名写法与禁用条件）。附带材料里有观众昵称、弹幕原话、玩家公屏原话，
又常有上千字：任务账本照存全文（游戏侧要用），但拒受理理由里回给发起方的、落库留给下播核对与
续播认领的，都只要发起方自己的原话。两段的抬头在这里定一次，拼的一方（主播侧）与分的一方
（受理核心）用同一份，改格式不会一边改了另一边对不上。没按这个格式拼的正文（运营直派）整段就是原话。
"""

from __future__ import annotations

__all__ = [
    "DELEGATE_DIALOGUE_HEAD",
    "DELEGATE_HEAD",
    "PROMPT_CHAT_HEAD",
    "PROMPT_REQUIREMENT_HEAD",
    "compose_redelivery",
    "is_chat_prompt",
    "mark_prompt",
    "own_words_of",
]

# 本次目标那一段的抬头（后面紧跟发起方原话）
DELEGATE_HEAD = "[本次目标]\n"
# 附带来源对话那一段的抬头（前面空一行；后面接说明与逐字原话）
DELEGATE_DIALOGUE_HEAD = "\n\n[来源对话："
# 搁下过的委派重新送达时，接在原指令后面的那一段的抬头
REDELIVERY_HEAD = "\n\n[这件事之前做过一段、后来搁下了]\n"

# 递话正文的两种抬头：给手上的事加要求，或只是让对方在聊天里回一句话。
# 发的一方按种类加抬头，收的一方凭抬头判断这句是不是在改手上那件事的要求
PROMPT_REQUIREMENT_HEAD = "[补充要求]\n"
PROMPT_CHAT_HEAD = "[聊天回话]\n"


def compose_redelivery(instruction: str, progress: str) -> str:
    """搁下过的委派重新送达时的正文：原指令全文 + 之前做到哪。

    执行方收到时是按一件新任务开的（上次的对话与临时记录都不在了），所以把当时做到哪写在后面，
    并交代先核对现场：搁下期间世界可能变了，照着旧进度接着做会做错。
    """
    done = progress.strip() or "没记下做到哪"
    return f"{instruction}{REDELIVERY_HEAD}当时做到：{done}\n现在接着做：先核对现场还是不是当时的样子，再从没做完的地方继续。"


def mark_prompt(content: str, *, chat: bool) -> str:
    """给递话正文加上种类抬头；已经带着任一种抬头的原样返回（发的一方先加过了）。"""
    if content.startswith((PROMPT_REQUIREMENT_HEAD, PROMPT_CHAT_HEAD)):
        return content
    return (PROMPT_CHAT_HEAD if chat else PROMPT_REQUIREMENT_HEAD) + content


def is_chat_prompt(content: str) -> bool:
    """这句递话是不是只让对方在聊天里回一句话（不改手上那件事的要求）。"""
    return content.startswith(PROMPT_CHAT_HEAD)


def own_words_of(instruction: str) -> str:
    """委派正文里发起方自己的原话：按上面的格式拼过的取出本次目标那一段，没拼过的原样返回。"""
    head, sep, rest = instruction.partition(DELEGATE_HEAD)
    if head or not sep:
        return instruction
    own, sep, _ = rest.partition(DELEGATE_DIALOGUE_HEAD)
    return own if sep else instruction
