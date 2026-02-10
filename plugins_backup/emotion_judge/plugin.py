# src/plugins/vtube_studio/plugin.py
import time
from typing import Any, Dict, Optional

from maim_message.message_base import MessageBase
from openai import AsyncOpenAI
from src.core.amaidesu_core import AmaidesuCore

# 从 core 导入基类和核心类
from src.core.plugin_manager import BasePlugin

# logger = get_logger("EmotionJudgePlugin")


# --- Helper Function ---
# def load_plugin_config() -> Dict[str, Any]:
#     # (Config loading logic - keep for now, might be needed)
#     config_path = os.path.join(os.path.dirname(__file__), "config.toml")
#     try:
#         with open(config_path, "rb") as f:
#             if hasattr(tomllib, "load"):
#                 return tomllib.load(f)
#             else:
#                 try:
#                     import toml
#
#                     with open(config_path, "r", encoding="utf-8") as rf:
#                         return toml.load(rf)
#                 except ImportError:
#                     logger.error("toml package needed for Python < 3.11.")
#                     return {}
#                 except FileNotFoundError:
#                     logger.warning(f"Config file not found: {config_path}")
#                     return {}
#     except Exception as e:
#         logger.error(f"Error loading config: {config_path}: {e}", exc_info=True)
#         return {}


# --- Plugin Class ---
class EmotionJudgePlugin(BasePlugin):
    """
    根据麦麦的回复，判断麦麦的情感状态，并触发对应的Live2D热键。
    """

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        # self.logger = logger # 已由基类初始化
        # 使用插件自己的配置 section 名称，例如 "emotion_judge"
        # loaded_config = load_plugin_config()
        # self.config = loaded_config.get("emotion_judge", {})
        self.config = self.plugin_config  # 直接使用注入的 plugin_config
        self.model = self.config.get("model", {})
        self.base_url = self.config.get("base_url", "https://api.siliconflow.cn/v1/")
        self.api_key = self.config.get("api_key", "")
        # 添加冷却时间配置和上次触发时间记录
        self.cool_down_seconds = self.config.get("cool_down_seconds", 5)  # 从配置读取冷却时间，默认为 5 秒
        self.last_trigger_time: float = 0.0  # 初始化上次触发时间

        # 初始化 OpenAI 客户端
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

        # self.logger.info("EmotionJudgePlugin initialized.") # 基类已有通用初始化日志

    async def setup(self):
        await super().setup()

        self.core.register_websocket_handler("*", self.handle_maicore_message)

        self.logger.info("EmotionJudgePlugin setup complete.")

    async def cleanup(self):
        self.logger.info("Cleaning up EmotionJudgePlugin...")
        # --- 新插件的清理逻辑 ---
        # 例如: 取消注册、关闭连接等
        # self.core.unregister_command(...)

        await super().cleanup()
        self.logger.info("EmotionJudgePlugin cleanup complete.")

    # --- 新插件的方法 ---
    # 例如: 处理消息、执行分析等
    # async def analyze_emotion(self, text: str): ...

    async def handle_maicore_message(self, message: MessageBase):
        """处理从 MaiCore 收到的消息，如果是文本类型，则进行处理，触发热键。"""
        # --- 将冷却时间检查移到此处 ---
        current_time = time.monotonic()
        if current_time - self.last_trigger_time < self.cool_down_seconds:
            remaining_cooldown = self.cool_down_seconds - (current_time - self.last_trigger_time)
            self.logger.debug(f"情感判断冷却中，跳过消息处理。剩余 {remaining_cooldown:.1f} 秒")
            return
        # --- 冷却时间检查结束 ---

        if message.message_segment and message.message_segment.type == "text":
            original_text = message.message_segment.data
            if not isinstance(original_text, str) or not original_text.strip():
                self.logger.debug("收到非字符串或空文本消息段，跳过")
                return

            self.logger.info(f"收到文本消息: '{original_text[:50]}...'")

            await self._judge_and_trigger(original_text)
        elif message.message_segment and message.message_segment.type == "seglist":
            # 递归处理 seglist
            await self._handle_seglist(message.message_segment.data)

    async def _handle_seglist(self, seg_list: list):
        """递归处理 seglist 数据"""
        for seg in seg_list:
            if seg.type == "text":
                original_text = seg.data
                if isinstance(original_text, str) and original_text.strip():
                    self.logger.info(f"从 seglist 中提取文本: '{original_text[:50]}...'")
                    await self._judge_and_trigger(original_text)
                else:
                    self.logger.debug("从 seglist 中收到非字符串或空文本消息段，跳过")
            elif seg.type == "seglist":
                self.logger.debug("在 seglist 中发现嵌套 seglist，递归处理...")
                await self._handle_seglist(seg.data)  # 递归调用
            else:
                self.logger.warning(f"在 seglist 中遇到不支持的段类型 '{seg.type}'，跳过")

    async def _judge_and_trigger(self, text: str) -> Optional[str]:
        """使用 LLM 判断文本的情感。"""
        self.logger.info(f"开始情感判断: '{text[:50]}...'")
        if not self.api_key:
            self.logger.warning("EmotionJudgePlugin 缺少 API Key，跳过情感判断。")
            return None

        hotkey_list = await self._get_hotkey_list()
        if not hotkey_list:
            self.logger.warning("无法获取热键列表，跳过情感判断。")
            return None

        hotkey_name_list = []
        for hotkey in hotkey_list:
            hotkey_name_list.append(hotkey["name"])
        self.logger.debug(f"获取到的热键列表: {hotkey_name_list}")

        # 将热键列表转换为字符串，以便拼接到 prompt 中
        hotkey_list_str = "\\n".join(hotkey_name_list)  # 使用换行符分隔

        try:
            response = await self.client.chat.completions.create(
                model=self.model.get("name", "Qwen/Qwen2.5-7B-Instruct"),
                messages=[
                    {
                        "role": "system",
                        "content": self.model.get(
                            "system_prompt",
                            "你是一个主播的助手，根据主播的文本内容，判断主播的情感状态，确定触发哪一个Live2D热键以帮助主播更好地表达情感。只输出热键名称，不要包含其他任何文字或解释。以下为热键列表：\\n",
                        )
                        + hotkey_list_str,
                    },
                    {"role": "user", "content": text},
                ],
                max_tokens=self.model.get("max_tokens", 10),
                temperature=self.model.get("temperature", 0.3),
            )

            if response.choices and response.choices[0].message:
                # 提取 LLM 返回的情感标签
                emotion = response.choices[0].message.content.strip()
                # 简单的后处理，去除可能的引号
                emotion = emotion.strip("'\\\"")
                self.logger.info(f"文本 '{text[:30]}...' 的情感判断结果: {emotion}")

                # 根据情感结果触发热键
                if emotion:  # 确保 emotion 非空
                    await self._trigger_hotkey(emotion)
                    # --- 更新上次触发时间 ---
                    self.last_trigger_time = time.monotonic()
                    self.logger.info(f"热键 '{emotion}' 已触发，冷却时间开始。")
                    # --- 更新结束 ---

                return emotion
            else:
                self.logger.warning("OpenAI API 返回了无效的响应结构")
                return None

        except Exception as e:
            self.logger.error(f"调用 OpenAI API 时发生错误: {e}", exc_info=True)
            return None

    async def _get_hotkey_list(self) -> Optional[str]:
        """获取 VTS 的热键列表。"""
        # 这里可以根据需要实现更复杂的映射逻辑
        vts_control_service = self.core.get_service("vts_control")
        if not vts_control_service:
            self.logger.warning("未找到 VTS 控制服务。无法触发热键。")
            return None

        return await vts_control_service.get_hotkey_list()

    async def _trigger_hotkey(self, hotkey_id: str):
        """触发情感表达。"""

        vts_control_service = self.core.get_service("vts_control")
        if not vts_control_service:
            self.logger.warning("未找到 VTS 控制服务。无法触发热键。")
            return

        await vts_control_service.trigger_hotkey(hotkey_id)


# --- Plugin Entry Point ---
plugin_entrypoint = EmotionJudgePlugin
