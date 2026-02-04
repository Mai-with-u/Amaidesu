"""
VTube Studio 适配器

将通用虚拟形象控制接口转换为 VTS 特定的 API 调用。
"""

from typing import Dict, Optional
from src.core.avatar.adapter_base import AvatarAdapter, ParameterMetadata, ActionMetadata
from src.utils.logger import get_logger


class VTSAdapter(AvatarAdapter):
    """VTube Studio 适配器

    通过 VTS API 控制虚拟形象。
    """

    # VTS 常用参数定义
    VTS_PARAMETERS = [
        # 眼睛参数
        ("EyeOpenLeft", "左眼开合度", "face", ["eye"], "控制左眼的开合程度，0为闭眼，1为睁眼"),
        ("EyeOpenRight", "右眼开合度", "face", ["eye"], "控制右眼的开合程度，0为闭眼，1为睁眼"),
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

    def __init__(self, vts_plugin):
        """
        初始化 VTS 适配器

        Args:
            vts_plugin: VTubeStudioPlugin 实例
        """
        super().__init__("vts", vts_plugin.config)
        self.vts_plugin = vts_plugin
        self.logger = get_logger("VTSAdapter")

        # 注册 VTS 参数元数据
        self._setup_parameters()

    def _setup_parameters(self):
        """注册 VTS 参数元数据"""
        for param_info in self.VTS_PARAMETERS:
            name, display_name, category, tags, description = param_info
            self.register_parameter(ParameterMetadata(
                name=name,
                display_name=display_name,
                param_type="float",
                min_value=-1.0 if "Rot" in name or name in ["EyeX", "EyeY", "MouthSmile"] else 0.0,
                max_value=1.0,
                default_value=1.0 if "Eye" in name and "Open" in name else 0.0,
                description=description,
                category=category,
                tags=tags
            ))

        # 注册口型同步参数
        lip_sync_params = [
            ("VoiceVolume", "音量", "mouth", ["lip_sync", "voice"], "音频音量，用于驱动口型"),
            ("VoiceA", "A音口型", "mouth", ["lip_sync", "vowel"], "A元音的口型"),
            ("VoiceI", "I音口型", "mouth", ["lip_sync", "vowel"], "I元音的口型"),
            ("VoiceU", "U音口型", "mouth", ["lip_sync", "vowel"], "U元音的口型"),
            ("VoiceE", "E音口型", "mouth", ["lip_sync", "vowel"], "E元音的口型"),
            ("VoiceO", "O音口型", "mouth", ["lip_sync", "vowel"], "O元音的口型"),
        ]

        for name, display_name, category, tags, description in lip_sync_params:
            self.register_parameter(ParameterMetadata(
                name=name,
                display_name=display_name,
                param_type="float",
                min_value=0.0,
                max_value=1.0,
                default_value=0.0,
                description=description,
                category=category,
                tags=tags
            ))

    def register_hotkey_as_action(
        self,
        hotkey_name: str,
        display_name: str = None,
        description: str = ""
    ):
        """注册 VTS 热键作为动作

        Args:
            hotkey_name: 热键名称/ID
            display_name: 显示名称
            description: 描述
        """
        if display_name is None:
            display_name = hotkey_name

        self.register_action(ActionMetadata(
            name=hotkey_name,
            display_name=display_name,
            description=description or f"触发热键: {hotkey_name}",
            category="hotkey"
        ))

    async def connect(self) -> bool:
        """连接到 VTS（实际连接由插件管理）

        Returns:
            是否已连接
        """
        # VTS 插件管理自己的连接
        return self.vts_plugin._is_connected_and_authenticated

    async def disconnect(self) -> bool:
        """断开连接（由插件管理）

        Returns:
            True（实际断开由插件处理）
        """
        # VTS 插件管理自己的断开
        return True

    async def set_parameter(self, param_name: str, value: float) -> bool:
        """设置单个参数值

        Args:
            param_name: 参数名称
            value: 参数值

        Returns:
            是否设置成功
        """
        return await self.vts_plugin.set_parameter_value(param_name, value)

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

        Args:
            param_name: 参数名称

        Returns:
            参数值，如果获取失败返回 None
        """
        result = await self.vts_plugin.get_parameter_value(param_name)
        if result is False:
            return None
        return float(result) if result is not None else None

    async def trigger_action(self, action_name: str, **kwargs) -> bool:
        """触发预设动作（热键）

        Args:
            action_name: 热键名称
            **kwargs: 额外参数（VTS 热键不需要）

        Returns:
            是否触发成功
        """
        return await self.vts_plugin.trigger_hotkey(action_name)
