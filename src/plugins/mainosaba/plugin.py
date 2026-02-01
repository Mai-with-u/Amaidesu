# Mainosaba Plugin - 新Plugin架构实现

import asyncio
import time
import io
import base64
from typing import Dict, Any, List, Optional, AsyncIterator
from enum import Enum
from PIL import ImageGrab
import pyautogui

from src.core.base.input_provider import InputProvider
from src.data_types.raw_data import RawData
from src.core.event_bus import EventBus
from src.utils.logger import get_logger


class ControlMethod(Enum):
    """游戏控制方式枚举"""

    MOUSE_CLICK = "mouse_click"  # 鼠标单击
    ENTER_KEY = "enter_key"  # Enter键
    SPACE_KEY = "space_key"  # 空格键


class MainosabaInputProvider(InputProvider):
    """
    Mainosaba InputProvider - 从游戏画面采集文本数据

    职责：
    - 截取游戏画面
    - 使用VLM识别游戏文本
    - 产生RawData供后续处理
    """

    def __init__(self, config: Dict[str, Any], vlm_client=None, event_bus: Optional[EventBus] = None):
        super().__init__(config)
        self.logger = get_logger("MainosabaInputProvider")
        self.vlm_client = vlm_client
        self.event_bus = event_bus

        # 游戏配置
        self.full_screen = config.get("full_screen", True)
        self.game_region = config.get("game_region", None)
        self.check_interval = config.get("check_interval", 1)
        self.screenshot_min_interval = config.get("screenshot_min_interval", 0.5)

        # 状态
        self.last_screenshot_time = 0
        self.last_game_text = ""
        self.waiting_for_response = False
        self.last_message_time = 0

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集游戏文本数据"""
        self.logger.info("开始采集游戏文本数据...")

        while self.is_running:
            try:
                # 检查是否在等待回应
                if self.waiting_for_response:
                    # 检查是否超时
                    if time.time() - self.last_message_time > self.config.get("response_timeout", 10):
                        self.logger.info("等待回应超时，继续游戏")
                        await self.advance_game()
                        self.waiting_for_response = False
                else:
                    # 正常监听游戏文本
                    self.logger.debug("开始截屏识别...")
                    game_text = await self.capture_and_recognize()
                    self.logger.debug(f"识别完成，结果: {game_text[:50] if game_text else 'None'}...")

                    if game_text is None:
                        self.logger.debug("未识别到有效文本，继续监听")
                    elif game_text == self.last_game_text:
                        self.logger.debug("识别到的文本与上次相同，跳过")
                    else:
                        self.logger.info(f"检测到新游戏文本: {game_text[:50]}...")
                        # 产生RawData
                        yield RawData(
                            source="mainosaba_game",
                            content=game_text,
                            data_type="text",
                            timestamp=time.time(),
                            metadata={
                                "platform": "mainosaba",
                                "source": "manosaba_game",
                                "maimcore_reply_probability_gain": 1.5,
                            },
                        )
                        self.last_game_text = game_text
                        self.last_message_time = time.time()
                        self.waiting_for_response = True

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"游戏监听循环出错: {e}", exc_info=True)
                await asyncio.sleep(5)  # 出错时等待5秒再继续

        self.logger.info("游戏文本采集结束")

    async def capture_and_recognize(self) -> Optional[str]:
        """截取屏幕并识别游戏文本"""
        try:
            # 检查截屏频率限制
            current_time = time.time()
            if current_time - self.last_screenshot_time < self.screenshot_min_interval:
                return None

            # 截取全屏或指定区域
            if self.full_screen or self.game_region is None:
                screenshot = ImageGrab.grab()  # 全屏截图
            else:
                screenshot = ImageGrab.grab(bbox=tuple(self.game_region))  # 指定区域截图

            self.last_screenshot_time = current_time

            # 转换为base64用于API调用
            img_buffer = io.BytesIO()
            screenshot.save(img_buffer, format="PNG")
            img_base64 = base64.b64encode(img_buffer.getvalue()).decode()

            # 调用VLM API识别文本
            game_text = await self.recognize_game_text(img_base64)
            return game_text

        except Exception as e:
            self.logger.error(f"截屏识别出错: {e}", exc_info=True)
            return None

    async def recognize_game_text(self, image_base64: str) -> Optional[str]:
        """识别游戏截图中的文本（使用 VLM）"""
        if not self.vlm_client:
            self.logger.warning("VLM 客户端未初始化，无法识别文本")
            return None

        try:
            # 构建 prompt
            prompt = """请识别这张游戏截图中的对话文本。
这是《魔法少女的魔女裁判》游戏的界面。

请仔细识别并提取游戏中的对话内容，只返回角色说的台词文本。
如果是系统提示或界面元素，请忽略。
如果没有对话文本，才返回对画面的描述。

请以纯文本形式返回识别到的对话内容，如果没有角色只有旁白，那么就以"旁白"作为角色名。格式为：

游戏角色名:发言内容
或者
游戏角色名:(角色心理描写)
"""

            # 构建图像 URL（LLMClient 支持 base64 格式）
            image_data_url = f"data:image/png;base64,{image_base64}"

            # 调用 VLM（自动处理 HTTP 请求、错误、Token 统计）
            result = await self.vlm_client.vision_completion(prompt=prompt, images=image_data_url, max_tokens=500)

            if not result["success"]:
                self.logger.error(f"VLM识别失败: {result.get('error')}")
                return None

            content = result["content"].strip()

            # 过滤无效响应
            if content in ["无对话文本", "没有对话文本", "No dialogue text", ""]:
                return None

            self.logger.debug(f"VLM识别结果: {content[:100]}...")
            return content

        except Exception as e:
            self.logger.error(f"识别游戏文本出错: {e}", exc_info=True)
            return None

    async def advance_game(self) -> None:
        """推进游戏到下一句对话"""
        try:
            control_method_str = self.config.get("control_method", "mouse_click")
            try:
                control_method = ControlMethod(control_method_str)
            except ValueError:
                self.logger.warning(f"未知的控制方式: {control_method_str}，使用默认值 mouse_click")
                control_method = ControlMethod.MOUSE_CLICK

            click_position = self.config.get("click_position", [1920 // 2, 1080 // 2])

            if control_method == ControlMethod.MOUSE_CLICK:
                # 鼠标点击指定位置
                x, y = click_position
                pyautogui.click(x, y)
                self.logger.info(f"已点击位置 ({x}, {y}) 推进游戏")
            elif control_method == ControlMethod.ENTER_KEY:
                # 按Enter键
                pyautogui.press("enter")
                self.logger.info("已按Enter键推进游戏")
            elif control_method == ControlMethod.SPACE_KEY:
                # 按空格键
                pyautogui.press("space")
                self.logger.info("已按空格键推进游戏")

            # 等待一下让游戏界面更新
            await asyncio.sleep(0.5)

        except Exception as e:
            self.logger.error(f"推进游戏出错: {e}", exc_info=True)


class MainosabaPlugin:
    """
    Mainosaba插件 - 《魔法少女的魔女裁判》游戏助手

    功能：
    - 截取游戏画面并识别台词
    - 将识别的文本发送给系统
    - 检测回应后自动推进游戏

    迁移到新的Plugin架构，包装MainosabaInputProvider。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus = None
        self._providers: List[MainosabaInputProvider] = []

        # 配置检查
        self.enabled = self.config.get("enabled", True)
        if not self.enabled:
            self.logger.warning("MainosabaPlugin 在配置中已禁用。")
            return

        # VLM 客户端（将在 setup 中初始化）
        self.vlm_client = None

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表（包含MainosabaInputProvider）
        """
        self.event_bus = event_bus

        if not self.enabled:
            return []

        # 获取 VLM 客户端（需要从全局或插件配置）
        # 注意：这里需要集成到新的LLM客户端系统
        # 暂时保持为None，让InputProvider自己处理
        try:
            # 检查插件级VLM配置
            llm_config = self.config.get("llm_config", {})
            if llm_config:
                self.logger.info("检测到插件级VLM配置")
                # 这里应该创建LLMClient，但由于依赖关系，暂时跳过
                pass
        except Exception as e:
            self.logger.warning(f"VLM配置检查失败: {e}")

        # 创建Provider
        try:
            provider = MainosabaInputProvider(self.config, self.vlm_client, event_bus)
            self._providers.append(provider)
            self.logger.info("MainosabaInputProvider 已创建")
        except Exception as e:
            self.logger.error(f"创建Provider失败: {e}", exc_info=True)
            return []

        return self._providers

    async def cleanup(self):
        """清理资源"""
        self.logger.info("开始清理 MainosabaPlugin...")

        for provider in self._providers:
            try:
                await provider.stop()
            except Exception as e:
                self.logger.error(f"清理Provider时出错: {e}", exc_info=True)

        self._providers.clear()
        self.logger.info("MainosabaPlugin 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "Mainosaba",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "Mainosaba插件，《魔法少女的魔女裁判》游戏助手",
            "category": "input",
            "api_version": "1.0",
        }


# 插件入口点
plugin_entrypoint = MainosabaPlugin
