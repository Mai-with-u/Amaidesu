# src/plugins/keyword_action/actions/dg_lab_shock.py

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.amaidesu_core import AmaidesuCore
    from maim_message import MessageBase


async def execute(core: "AmaidesuCore", message: "MessageBase"):
    """
    通过事件系统触发电击动作。
    """
    # 使用事件系统触发电击动作
    if core.event_bus:
        core.logger.info("动作脚本: 正在发布 dg_lab_shock 事件...")
        await core.event_bus.emit(
            "dg_lab.shock",
            {"message": message},
            source="keyword_action"
        )
    else:
        core.logger.error("动作脚本: EventBus 不可用，无法触发电击动作。")
