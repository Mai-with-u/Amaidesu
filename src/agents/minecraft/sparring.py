"""切磋打完：``fight(spar=true)`` 的结果读成给主播的一句话。

别的玩家蹲起再拿剑对着她挥，多半是约切磋；游戏 Agent 接了就下 ``fight(target=他, spar=true)``，打到停手线或对方停手为止。
切磋中他打中她的不进挨打的 ``hurt``，所以主播那边只看得到"约切磋"那一下，看不到打得怎样。这一场收手时，
结果细节 ``spar`` 写着怎么收的手（``ended_by``）、他打中她几下（``hits_taken``），``hits`` 是她打中他几下；
据此给主播一句话：怎么收的、谁赢了、各打中几下，后面附口径——切磋是玩，输赢都俏皮带过，不提血量。

没开打（她的血已经在停手线下）、找不到人、被取消的，``ended_by`` 都是 ``cancelled``，没打起来，不说。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .maicraft import GoalRun

__all__ = ["spar_outcome_line"]

#: 怎么收的手 → 主播嘴里的说法（含输赢）。``cancelled`` 不在表里：没打起来或中途取消，不说
_ENDINGS: Dict[str, str] = {
    "they_stopped": "他先停手了",
    "they_at_line": "他先到了停手线，算我赢了",
    "finishing_blow": "再砍一刀他就倒了，我收住了手，算我赢了",
    "me_at_line": "我先到了停手线，算我输了",
    "they_left": "他走开了，没分出胜负",
    "no_hits": "半天谁都没打中谁",
}

#: 给主播的口径：切磋是玩，开心优先，不念血量
_HOW_TO_TELL_IT = "切磋是玩，输赢都俏皮带过，开心优先，不提血量"


def spar_outcome_line(run: GoalRun) -> Optional[str]:
    """一场切磋收手后给主播的一句话；不是切磋、没打起来或被取消时返回 None。"""
    details = (run.result or {}).get("details")
    spar = details.get("spar") if isinstance(details, dict) else None
    if not isinstance(spar, dict):
        return None
    ending = _ENDINGS.get(str(spar.get("ended_by") or ""))
    if ending is None:
        return None
    what = f"切磋了一场（{run.purpose}）" if run.purpose else "切磋了一场"
    hits = _count(details.get("hits"))
    taken = _count(spar.get("hits_taken"))
    score = f"；我打中他 {hits} 下，他打中我 {taken} 下" if hits is not None and taken is not None else ""
    return f"{what}：{ending}{score}。{_HOW_TO_TELL_IT}"


def _count(value: Any) -> Optional[int]:
    """打中几下：只认整数（布尔值不算）；缺了或写错为 None，那句比分就不说。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
