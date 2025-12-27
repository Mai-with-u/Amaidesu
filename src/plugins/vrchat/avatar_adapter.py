"""
VRChat OSC 适配器

将通用虚拟形象控制接口转换为 VRChat OSC 特定的 API 调用。
"""

from typing import Dict, Optional
from src.core.avatar.adapter_base import AvatarAdapter, ParameterMetadata, ActionMetadata
from src.utils.logger import get_logger


class VRCAdapter(AvatarAdapter):
    """VRChat OSC 适配器

    通过 VRChat OSC 协议控制虚拟形象。

    VRChat OSC 参数地址规范：
    - 表情参数: /avatar/parameters/{参数名}
    - 输入参数: /input/{类型}/{名称}
    """

    # VRChat 常用 OSC 参数定义
    VRC_PARAMETERS = [
        # 眼睛参数
        ("EyeOpen", "眼睛开合度", "face", ["eye"], "控制眼睛的开合程度，0为闭眼，1为睁眼"),
        ("EyeX", "眼球水平位置", "face", ["eye"], "眼球水平移动，-1左，1右"),
        ("EyeY", "眼球垂直位置", "face", ["eye"], "眼球垂直移动，-1上，1下"),
        # 嘴巴参数
        ("MouthSmile", "微笑程度", "face", ["mouth", "emotion"], "控制微笑程度，负值为皱眉，正值为微笑"),
        ("MouthOpen", "嘴巴张开度", "face", ["mouth"], "控制嘴巴张开程度"),
        # 眉毛参数
        ("EyebrowLeft", "左眉位置", "face", ["eyebrow"], "左眉上下移动"),
        ("EyebrowRight", "右眉位置", "face", ["eyebrow"], "右眉上下移动"),
        # 头部参数
        ("HeadRotX", "头部俯仰", "body", ["head"], "头部上下旋转（俯仰）"),
        ("HeadRotY", "头部偏航", "body", ["head"], "头部左右旋转（偏航）"),
        ("HeadRotZ", "头部翻滚", "body", ["head"], "头部倾斜旋转（翻滚）"),
    ]

    # VRChat 输入参数（使用 /input/ 前缀）
    VRC_INPUT_PARAMETERS = [
        ("/input/face/eyes", "眼睛开合", "face", ["eye"], "VRChat 标准眼睛开合输入"),
        ("/input/face/mouth_smile", "微笑", "face", ["mouth", "emotion"], "VRChat 标准微笑输入"),
        ("/input/face/eyebrow", "眉毛", "face", ["eyebrow"], "VRChat 标准眉毛输入"),
    ]

    def __init__(self, vrc_plugin):
        """
        初始化 VRC 适配器

        Args:
            vrc_plugin: VRChatPlugin 实例
        """
        super().__init__("vrc", vrc_plugin.config)
        self.vrc_plugin = vrc_plugin
        self.logger = get_logger("VRCAdapter")

        # 注册 VRC 参数元数据
        self._setup_parameters()

    def _setup_parameters(self):
        """注册 VRC 参数元数据"""
        # 注册标准 avatar 参数
        for param_info in self.VRC_PARAMETERS:
            name, display_name, category, tags, description = param_info
            self.register_parameter(
                ParameterMetadata(
                    name=name,
                    display_name=display_name,
                    param_type="float",
                    min_value=-1.0 if "Rot" in name or name in ["EyeX", "EyeY", "MouthSmile"] else 0.0,
                    max_value=1.0,
                    default_value=1.0 if "Eye" in name and "Open" in name else 0.0,
                    description=description,
                    category=category,
                    tags=tags,
                )
            )

        # 注册 /input/ 参数
        for param_info in self.VRC_INPUT_PARAMETERS:
            name, display_name, category, tags, description = param_info
            self.register_parameter(
                ParameterMetadata(
                    name=name,
                    display_name=display_name,
                    param_type="float",
                    min_value=0.0,
                    max_value=1.0,
                    default_value=0.0,
                    description=description,
                    category=category,
                    tags=tags,
                )
            )

    def register_gesture_as_action(self, gesture_name: str, display_name: str = None, description: str = ""):
        """注册 VRChat 手势作为动作

        Args:
            gesture_name: 手势名称（如 "Wave", "Peace" 等）
            display_name: 显示名称
            description: 描述
        """
        if display_name is None:
            display_name = gesture_name

        self.register_action(
            ActionMetadata(
                name=gesture_name,
                display_name=display_name,
                description=description or f"触发手势: {gesture_name}",
                category="gesture",
                parameters={"gesture_type": "string"},
            )
        )

    async def connect(self) -> bool:
        """连接到 VRChat OSC（实际连接由插件管理）

        Returns:
            是否已连接
        """
        # VRC 插件管理自己的连接
        return self.vrc_plugin.is_connected()

    async def disconnect(self) -> bool:
        """断开连接（由插件管理）

        Returns:
            True（实际断开由插件处理）
        """
        # VRC 插件管理自己的断开
        return True

    async def set_parameter(self, param_name: str, value: float) -> bool:
        """设置单个参数值

        Args:
            param_name: 参数名称（可以是 "EyeOpen" 或 "/input/face/eyes"）
            value: 参数值

        Returns:
            是否设置成功
        """
        # 构建完整的 OSC 地址
        if param_name.startswith("/"):
            osc_address = param_name
        else:
            osc_address = f"/avatar/parameters/{param_name}"

        # 通过插件发送 OSC 消息
        return await self.vrc_plugin.send_osc(osc_address, value)

    async def set_parameters(self, parameters: Dict[str, float]) -> bool:
        """批量设置参数值

        Args:
            parameters: 参数名到值的映射

        Returns:
            是否全部设置成功
        """
        all_success = True
        for param_name, value in parameters.items():
            success = await self.set_parameter(param_name, value)
            if not success:
                self.logger.warning(f"设置参数 '{param_name}' 失败")
                all_success = False
        return all_success

    async def get_parameter(self, param_name: str) -> Optional[float]:
        """获取参数当前值

        注意：VRChat OSC 不支持读取参数值，此方法总是返回 None

        Args:
            param_name: 参数名称

        Returns:
            None（OSC 是单向的）
        """
        # VRChat OSC 不支持读取
        self.logger.debug(f"VRChat OSC 不支持读取参数 '{param_name}'")
        return None

    async def trigger_action(self, action_name: str, **kwargs) -> bool:
        """触发预设动作（手势）

        VRChat 支持的手势：
        - Wave, Peace, ThumbsUp, RocknRoll, ...
        - 通过 /avatar/parameters/VRCEmote OSC 地址触发

        Args:
            action_name: 手势名称
            **kwargs: 额外参数

        Returns:
            是否触发成功
        """
        # 将手势名称转换为 VRChat GestureSend 值
        gesture_map = {
            "Neutral": 0,
            "Wave": 1,
            "Peace": 2,
            "ThumbsUp": 3,
            "RocknRoll": 4,
            "HandGun": 5,
            "Point": 6,
            "Victory": 7,
            "Cross": 8,
        }

        gesture_value = gesture_map.get(action_name)
        if gesture_value is None:
            self.logger.warning(f"未知的手势: {action_name}")
            return False

        # 发送 OSC 消息
        return await self.vrc_plugin.send_osc("/avatar/parameters/VRCEmote", gesture_value)
