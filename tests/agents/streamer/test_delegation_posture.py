"""委派处境判定（delegation_posture）：任务账本状态与存储委派账记录的处境归类、待办事实判定。"""

from __future__ import annotations

import pytest

from src.agents.streamer.delegation_posture import (
    DelegationPosture,
    posture_of_delegation_record,
    posture_of_task_status,
    snapshot_awaits_decision,
    was_queued,
)
from src.modules.storage.models.events import DelegationRecord
from src.modules.tools.tasks import TERMINAL_TASK_STATES


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("accepted", DelegationPosture.IN_HAND),
        ("running", DelegationPosture.IN_HAND),
        ("waiting_for_decision", DelegationPosture.AWAITING_DECISION),
        ("queued", DelegationPosture.QUEUED),
        ("interrupted", DelegationPosture.INTERRUPTED),
        ("", DelegationPosture.UNRECOGNIZED),
        ("something_else", DelegationPosture.UNRECOGNIZED),
    ],
)
def test_posture_of_task_status(status: str, expected: DelegationPosture) -> None:
    assert posture_of_task_status(status) is expected


@pytest.mark.parametrize("status", sorted(TERMINAL_TASK_STATES))
def test_terminal_statuses_are_settled(status: str) -> None:
    assert posture_of_task_status(status) is DelegationPosture.SETTLED


def _record(status: str, prior_status: str = "", summary: str = "") -> DelegationRecord:
    return DelegationRecord(
        live_session_id=1,
        task_id="deleg_1",
        game="minecraft",
        status=status,
        prior_status=prior_status,
        summary=summary,
    )


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (_record("accepted"), DelegationPosture.IN_HAND),
        (_record("queued"), DelegationPosture.QUEUED),
        (_record("interrupted"), DelegationPosture.INTERRUPTED),
        (_record("cancelled"), DelegationPosture.SETTLED),
        (_record("succeeded"), DelegationPosture.SETTLED),
    ],
)
def test_posture_of_delegation_record(record: DelegationRecord, expected: DelegationPosture) -> None:
    assert posture_of_delegation_record(record) is expected


def test_snapshot_awaits_decision() -> None:
    assert snapshot_awaits_decision({"status": "waiting_for_decision"}) is True
    assert snapshot_awaits_decision({"status": "running"}) is False
    assert snapshot_awaits_decision(None) is False
    assert snapshot_awaits_decision({}) is False


def test_was_queued_by_prior_status() -> None:
    assert was_queued(_record("cancelled", prior_status="queued")) is True
    assert was_queued(_record("interrupted", prior_status="queued")) is True
    assert was_queued(_record("interrupted", prior_status="accepted")) is False


def test_was_queued_falls_back_to_summary_sniff() -> None:
    # 旧账面行没有 delegation_queued 受理行时，排队事实只写在终态摘要里
    assert was_queued(_record("cancelled", prior_status="accepted", summary="下播自动收尾（待办，还没开工）")) is True
    assert was_queued(_record("cancelled", prior_status="accepted", summary="下播自动收尾（当时在做）")) is False
