# src/plugins/keyword_action/actions/dg_lab_shock.py

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.amaidesu_core import AmaidesuCore
    from maim_message import MessageBase

async def execute(core: "AmaidesuCore", message: "MessageBase"):
    """
    通过调用 dg_lab_control 服务来执行电击动作。
    """
    # 从核心获取服务实例
    dg_lab_service = core.get_service("dg_lab_control")

    if dg_lab_service:
        core.logger.info("动作脚本: 正在调用 dg_lab_control 服务...")
        # 调用服务提供的公共方法
        # 这里可以添加更复杂的逻辑，例如从 message 中解析参数
        await dg_lab_service.trigger_shock()
    else:
        core.logger.error("动作脚本: 未能找到 'dg_lab_control' 服务。请确保 dg_lab_service 插件已启用。") 