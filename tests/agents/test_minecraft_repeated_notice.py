"""身体通知的重复判断：是否还留在历史里、指回前面那条时怎么说。"""

from __future__ import annotations

from src.agents.minecraft.repeated_notice import notice_present, repeat_reference


def test_present_in_history_or_queue_only_when_the_exact_text_is_there() -> None:
    content = "[身体] 饿了但没吃上：箱子没有"
    history = [{"role": "system", "content": "说明"}, {"role": "user", "content": content}]

    assert notice_present(content, history, [])
    assert notice_present(content, [], [("", content)])
    assert not notice_present(content, [{"role": "user", "content": content + "。"}], [("", "别的")])
    # 助手自己说的话里带着同样的字也不算：只认交给模型的通知
    assert not notice_present(content, [{"role": "assistant", "content": content}], [])


def test_reference_names_the_notice_by_its_label() -> None:
    text = repeat_reference("饿了但没吃上：还要 8 个能吃的，问过各来源都拿不到；箱子：没有")

    assert "「饿了但没吃上」" in text and "没有新变化" in text
    assert "箱子" not in text, "不再抄正文"


def test_reference_falls_back_to_a_generic_label_when_the_head_is_not_a_label() -> None:
    long_head = "这是一整句没有冒号分隔的很长很长的说明文字用来模拟没有标签的情况" + "：后面还有"
    assert "「身体处理不了的事」" in repeat_reference(long_head)
    assert "「身体处理不了的事」" in repeat_reference("没有冒号的一句话")
