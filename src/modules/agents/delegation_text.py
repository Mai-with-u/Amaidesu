"""委派正文的格式：发起方自己的原话 + 可选的附带材料，框架据此把两段分开。

主播委派时，正文是"[本次目标]"一段她自己的原话，后面附上"[来源对话：……]"一段观众与玩家的逐字原话
（游戏侧只拿它核对物品名写法与禁用条件）。附带材料里有观众昵称、弹幕原话、玩家公屏原话，
又常有上千字：任务账本照存全文（游戏侧要用），但拒受理理由里回给发起方的、落库留给下播核对与
续播认领的，都只要发起方自己的原话。两段的抬头在这里定一次，拼的一方（主播侧）与分的一方
（受理核心）用同一份，改格式不会一边改了另一边对不上。没按这个格式拼的正文（运营直派）整段就是原话。
"""

from __future__ import annotations

__all__ = ["DELEGATE_DIALOGUE_HEAD", "DELEGATE_HEAD", "own_words_of"]

# 本次目标那一段的抬头（后面紧跟发起方原话）
DELEGATE_HEAD = "[本次目标]\n"
# 附带来源对话那一段的抬头（前面空一行；后面接说明与逐字原话）
DELEGATE_DIALOGUE_HEAD = "\n\n[来源对话："


def own_words_of(instruction: str) -> str:
    """委派正文里发起方自己的原话：按上面的格式拼过的取出本次目标那一段，没拼过的原样返回。"""
    head, sep, rest = instruction.partition(DELEGATE_HEAD)
    if head or not sep:
        return instruction
    own, sep, _ = rest.partition(DELEGATE_DIALOGUE_HEAD)
    return own if sep else instruction
