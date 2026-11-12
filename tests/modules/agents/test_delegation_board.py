"""委派看板与文本协议单测：在做/待办读取、待办编号稳定、搁下重送达拼装、递话抬头判别。"""

from __future__ import annotations

from src.modules.agents.delegation_board import (
    BACKLOG_NO_KEY,
    BACKLOG_SEQ_KEY,
    DELIVERED_AT_KEY,
    DELIVERIES_KEY,
    HELD_KEY,
    SHELVED_AT_KEY,
    SHELVED_PROGRESS_KEY,
    find_item,
    read_board,
)
from src.modules.agents.delegation_text import (
    DELEGATE_DIALOGUE_HEAD,
    DELEGATE_HEAD,
    compose_redelivery,
    is_chat_prompt,
    mark_prompt,
    own_words_of,
)
from src.modules.tools.tasks import TaskLedger


def test_read_board_orders_backlog_and_resolves_short_labels() -> None:
    """在做至多一件；待办按 backlog_seq 升序排列，短编号按各自 backlog_no 保持稳定、不因顺序调整改号。"""
    ledger = TaskLedger()
    ledger.register(
        task_id="deleg_active",
        provider="framework",
        tool="framework_delegate",
        initiator="streamer",
        executor="minecraft",
        status="running",
        source="agent",
        snapshot={"own_words": "造刷铁机", DELIVERIES_KEY: 1, DELIVERED_AT_KEY: 2000},
    )
    ledger.register(
        task_id="deleg_fresh",
        provider="framework",
        tool="framework_delegate",
        initiator="streamer",
        executor="minecraft",
        status="queued",
        source="agent",
        snapshot={"own_words": "开菜地", BACKLOG_NO_KEY: 1, BACKLOG_SEQ_KEY: 10, DELIVERIES_KEY: 0},
    )
    # 编号 2 是后来搁下放回待办最前的（seq=-1 < 10）：排在队首，但短编号仍叫"待办2"
    ledger.register(
        task_id="deleg_shelved",
        provider="framework",
        tool="framework_delegate",
        initiator="streamer",
        executor="minecraft",
        status="queued",
        source="agent",
        snapshot={
            "own_words": "盖木屋",
            BACKLOG_NO_KEY: 2,
            BACKLOG_SEQ_KEY: -1,
            DELIVERIES_KEY: 1,
            SHELVED_AT_KEY: 3000,
            SHELVED_PROGRESS_KEY: "墙立了一半",
        },
    )

    board = read_board(ledger, "minecraft")
    assert board.in_hand is not None and board.in_hand.task_id == "deleg_active"
    assert board.in_hand.label == "在做"
    assert [item.task_id for item in board.backlog] == ["deleg_shelved", "deleg_fresh"]
    assert [item.label for item in board.backlog] == ["待办2", "待办1"]
    assert board.backlog[0].shelved is True and board.backlog[0].shelved_progress == "墙立了一半"
    assert board.backlog[1].shelved is False and board.backlog[1].deliveries == 0

    assert find_item(board, "在做") == board.in_hand
    assert find_item(board, "待办2") == board.backlog[0]
    assert find_item(board, "1") == board.backlog[1]
    assert find_item(board, "deleg_fresh") == board.backlog[1]
    assert find_item(board, "待办9") is None


def test_next_ready_skips_held_shelved_item() -> None:
    """单独搁下且标了 held 的待办件不会被立即自动送回，排在后面的就绪件先轮到。"""
    ledger = TaskLedger()
    ledger.register(
        task_id="deleg_held",
        provider="framework",
        tool="framework_delegate",
        initiator="streamer",
        executor="minecraft",
        status="queued",
        source="agent",
        snapshot={"own_words": "先停下的活", BACKLOG_NO_KEY: 1, BACKLOG_SEQ_KEY: -1, HELD_KEY: True},
    )
    ledger.register(
        task_id="deleg_ready",
        provider="framework",
        tool="framework_delegate",
        initiator="streamer",
        executor="minecraft",
        status="queued",
        source="agent",
        snapshot={"own_words": "后面排着的活", BACKLOG_NO_KEY: 2, BACKLOG_SEQ_KEY: 1, HELD_KEY: False},
    )
    board = read_board(ledger, "minecraft")
    ready = board.next_ready()
    assert ready is not None and ready.task_id == "deleg_ready"


def test_redelivery_preserves_own_words_and_prompt_kind_roundtrips() -> None:
    """搁下重送达拼上之前做到哪，但 own_words_of 仍只取发起方原话；递话抬头区分聊天回话与补充要求。"""
    full = f"{DELEGATE_HEAD}盖一座二层木屋{DELEGATE_DIALOGUE_HEAD}逐字引用]\n[]"
    redelivered = compose_redelivery(full, "一层墙体已完工")
    assert "一层墙体已完工" in redelivered and "先核对现场" in redelivered
    assert own_words_of(redelivered) == "盖一座二层木屋"

    chat_text = mark_prompt("回玩家甲：「马上来」", chat=True)
    req_text = mark_prompt("屋顶改成深色橡木", chat=False)
    assert is_chat_prompt(chat_text) is True
    assert is_chat_prompt(req_text) is False
    assert mark_prompt(chat_text, chat=False) == chat_text
