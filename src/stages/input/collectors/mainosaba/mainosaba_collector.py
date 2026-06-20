"""
MainosabaCollector - 从游戏画面采集文本数据

该Collector从《魔法少女的魔女裁判》游戏画面截取屏幕，
使用VLM识别游戏文本，并产生NormalizedMessage。
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

import pyautogui
from PIL import ImageGrab
from pydantic import Field, field_validator

from src.stages.input.registry import collector
from src.modules.config.schemas.base import BaseConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.prompts.manager import PromptManager
from src.modules.types.base.normalized_message import NormalizedMessage


class ControlMethod(Enum):
    """游戏控制方式枚举"""

    MOUSE_CLICK = "mouse_click"
    ENTER_KEY = "enter_key"
    SPACE_KEY = "space_key"


@collector("mainosaba")
class MainosabaCollector:
    """
    MainosabaCollector - 从游戏画面采集文本数据

    职责：
    - 截取游戏画面
    - 使用VLM识别游戏文本
    - 产生NormalizedMessage
    """

    class ConfigSchema(BaseConfig):
        """Mainosaba游戏画面采集输入Collector配置"""

        type: Literal["mainosaba"] = "mainosaba"
        full_screen: bool = Field(default=True, description="全屏截图")
        game_region: Optional[List[int]] = Field(default=None, description="游戏区域 [x1, y1, x2, y2]")
        check_interval: int = Field(default=1, description="检查间隔（秒）", ge=1)
        screenshot_min_interval: float = Field(default=0.5, description="最小截图间隔（秒）", ge=0.1)
        response_timeout: int = Field(default=10, description="响应超时（秒）", ge=1)
        control_method: Literal["mouse_click", "enter_key", "space_key"] = Field(
            default="mouse_click", description="游戏控制方式"
        )
        click_position: List[int] = Field(default_factory=lambda: [1920 // 2, 1080 // 2], description="点击位置 [x, y]")

        @field_validator("game_region")
        @classmethod
        def validate_game_region(cls, v: Optional[List[int]]) -> Optional[List[int]]:
            if v is not None:
                if len(v) != 4:
                    raise ValueError("game_region必须包含4个值 [x1, y1, x2, y2]")
                if any(x < 0 for x in v):
                    raise ValueError("game_region坐标不能为负数")
            return v

        @field_validator("click_position")
        @classmethod
        def validate_click_position(cls, v: List[int]) -> List[int]:
            if len(v) != 2:
                raise ValueError("click_position必须包含2个值 [x, y]")
            if any(x < 0 for x in v):
                raise ValueError("click_position坐标不能为负数")
            return v

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        vlm_client: Any = None,
        prompt_manager: Optional[PromptManager] = None,
    ):
        """
        初始化MainosabaCollector

        Args:
            config: 配置字典
            event_bus: 事件总线实例
            vlm_client: VLM客户端（用于图像识别）
            prompt_manager: 提示词管理器
        """
        self.config = config
        self.event_bus = event_bus
        self.vlm_client = vlm_client
        self.prompt_manager = prompt_manager
        self.logger = get_logger(self.__class__.__name__)

        self.typed_config = self.ConfigSchema(**config)
        self.full_screen = self.typed_config.full_screen
        self.game_region = self.typed_config.game_region
        self.check_interval = self.typed_config.check_interval
        self.screenshot_min_interval = self.typed_config.screenshot_min_interval

        self.last_screenshot_time = 0
        self.last_game_text = ""
        self.waiting_for_response = False
        self.last_message_time = 0
        self.is_started = False

    def stream(self) -> AsyncIterator[NormalizedMessage]:
        if not self.is_started:
            raise RuntimeError("Collector 未启动，请先调用 start()")

        async def _generate():
            try:
                async for message in self.collect():
                    yield message
            finally:
                self.is_started = False

        return _generate()

    async def start(self) -> None:
        self.is_started = True

    async def stop(self) -> None:
        self.is_started = False

    async def cleanup(self) -> None:
        self.logger.info("MainosabaCollector 已清理")

    async def collect(self) -> AsyncIterator[NormalizedMessage]:
        """采集游戏文本数据"""
        self.is_started = True

        try:
            self.logger.info("开始采集游戏文本数据...")

            while self.is_started:
                try:
                    if self.waiting_for_response:
                        if time.time() - self.last_message_time > self.typed_config.response_timeout:
                            self.logger.debug("等待回应超时，继续游戏")
                            await self.advance_game()
                            self.waiting_for_response = False
                    else:
                        self.logger.debug("开始截屏识别...")
                        game_text = await self.capture_and_recognize()
                        self.logger.debug(f"识别完成，结果: {game_text[:50] if game_text else 'None'}...")

                        if game_text is None:
                            self.logger.debug("未识别到有效文本，继续监听")
                        elif game_text == self.last_game_text:
                            self.logger.debug("识别到的文本与上次相同，跳过")
                        else:
                            self.logger.debug(f"检测到新游戏文本: {game_text[:50]}...")
                            yield NormalizedMessage(
                                text=game_text,
                                source="mainosaba_game",
                                data_type="text",
                                importance=0.5,
                                user_id="mainosaba_game",
                                user_nickname="Mainosaba游戏",
                                platform="mainosaba",
                            )
                            self.last_game_text = game_text
                            self.last_message_time = time.time()
                            self.waiting_for_response = True

                    await asyncio.sleep(self.check_interval)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"游戏监听循环出错: {e}", exc_info=True)
                    await asyncio.sleep(5)

            self.logger.info("游戏文本采集结束")

        finally:
            self.is_started = False

    async def capture_and_recognize(self) -> Optional[str]:
        """截取屏幕并识别游戏文本"""
        try:
            current_time = time.time()
            if current_time - self.last_screenshot_time < self.screenshot_min_interval:
                return None

            if self.full_screen or self.game_region is None:
                screenshot = ImageGrab.grab()
            else:
                screenshot = ImageGrab.grab(bbox=tuple(self.game_region))

            self.last_screenshot_time = current_time

            img_buffer = io.BytesIO()
            screenshot.save(img_buffer, format="PNG")
            img_base64 = base64.b64encode(img_buffer.getvalue()).decode()

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
            if not self.prompt_manager:
                raise ValueError("prompt_service 未注入，请检查 Collector 初始化配置")
            prompt = self.prompt_manager.get_raw("input/mainosaba_ocr")

            image_data_url = f"data:image/png;base64,{image_base64}"

            result = await self.vlm_client.vision_completion(prompt=prompt, images=image_data_url, max_tokens=500)

            if not result["success"]:
                self.logger.error(f"VLM识别失败: {result.get('error')}")
                return None

            content = result["content"].strip()

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
            control_method_str = self.typed_config.control_method
            try:
                control_method = ControlMethod(control_method_str)
            except ValueError:
                self.logger.warning(f"未知的控制方式: {control_method_str}，使用默认值 mouse_click")
                control_method = ControlMethod.MOUSE_CLICK

            click_position = self.typed_config.click_position

            if control_method == ControlMethod.MOUSE_CLICK:
                x, y = click_position
                pyautogui.click(x, y)
                self.logger.debug(f"已点击位置 ({x}, {y}) 推进游戏")
            elif control_method == ControlMethod.ENTER_KEY:
                pyautogui.press("enter")
                self.logger.debug("已按Enter键推进游戏")
            elif control_method == ControlMethod.SPACE_KEY:
                pyautogui.press("space")
                self.logger.debug("已按空格键推进游戏")

            await asyncio.sleep(0.5)

        except Exception as e:
            self.logger.error(f"推进游戏出错: {e}", exc_info=True)
