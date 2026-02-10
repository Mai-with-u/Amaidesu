"""
MainosabaPlugin - 《魔法少女的魔女裁判》游戏助手插件

该插件通过截取屏幕、调用VLM模型识别游戏台词，
发送给MaiBot后等待任意回应（或超时），然后自动继续游戏。
"""

import asyncio
import base64
import io
import time
from enum import Enum
from typing import Any, Dict, Optional

import pyautogui
from maim_message import BaseMessageInfo, FormatInfo, MessageBase, Seg, UserInfo
from PIL import ImageGrab
from src.core.plugin_manager import BasePlugin


class ControlMethod(Enum):
    """游戏控制方式枚举"""

    MOUSE_CLICK = "mouse_click"  # 鼠标单击
    ENTER_KEY = "enter_key"  # Enter键
    SPACE_KEY = "space_key"  # 空格键


class MainosabaPlugin(BasePlugin):
    """《魔法少女的魔女裁判》游戏助手插件"""

    def __init__(self, core, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.plugin_name = "MainosabaPlugin"

        # 游戏配置
        self.full_screen = plugin_config.get("full_screen", True)  # 是否全屏截图
        self.game_region = plugin_config.get("game_region", None)  # [x1, y1, x2, y2] 如果None则全屏
        self.response_timeout = plugin_config.get("response_timeout", 10)  # 秒
        self.check_interval = plugin_config.get("check_interval", 1)  # 秒

        # 控制配置
        control_method_str = plugin_config.get("control_method", "mouse_click")
        try:
            self.control_method = ControlMethod(control_method_str)
        except ValueError:
            self.logger.warning(f"未知的控制方式: {control_method_str}，使用默认值 mouse_click")
            self.control_method = ControlMethod.MOUSE_CLICK
        self.click_position = plugin_config.get("click_position", [1920 // 2, 1080 // 2])  # 默认屏幕中心

        # 运行状态
        self.is_running = False
        self.last_screenshot_time = 0
        self.last_game_text = ""
        self.waiting_for_response = False
        self.last_message_time = 0
        self.monitor_task: Optional[asyncio.Task] = None

        # VLM 客户端（将在 setup 中初始化）
        self.vlm_client = None

        self.logger.info(f"{self.plugin_name} 插件初始化完成")

    async def setup(self) -> None:
        """插件启动时的设置"""
        # 获取 VLM 客户端
        try:
            self.vlm_client = self.get_vlm_client()
            self.logger.info("VLM 客户端已初始化")
        except Exception as e:
            self.logger.error(f"VLM 客户端初始化失败: {e}")
            self.logger.warning("插件将在无 VLM 支持的情况下运行")

        # 注册消息处理器来检测MaiBot回应
        self.core.register_websocket_handler("*", self.handle_maicore_message)

        # 启动游戏监听任务
        self.is_running = True
        self.monitor_task = asyncio.create_task(self.game_monitor_loop())

        self.logger.info(f"{self.plugin_name} 插件启动完成")

    async def cleanup(self) -> None:
        """插件清理资源"""
        self.is_running = False
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass

        self.logger.info(f"{self.plugin_name} 插件清理完成")

    async def handle_maicore_message(self, message: MessageBase):
        """处理来自MaiCore的消息，检测MaiBot回应"""
        try:
            # 如果正在等待MaiBot回应，检测到任何消息就继续游戏
            if self.waiting_for_response:
                # 忽略自己发送的游戏消息，避免循环
                if (
                    message.message_info
                    and message.message_info.additional_config
                    and message.message_info.additional_config.get("source") == "manosaba_game"
                ):
                    return

                self.logger.info("检测到MaiBot回应，继续游戏")
                self.waiting_for_response = False
                await self.advance_game()

        except Exception as e:
            self.logger.error(f"处理消息时出错: {e}")

    async def game_monitor_loop(self) -> None:
        """游戏监听主循环"""
        self.logger.info("开始游戏监听循环")

        while self.is_running:
            try:
                # 检查是否在等待回应
                if self.waiting_for_response:
                    # 检查是否超时
                    if time.time() - self.last_message_time > self.response_timeout:
                        self.logger.info("等待回应超时，继续游戏")
                        await self.advance_game()
                        self.waiting_for_response = False
                else:
                    # 正常监听游戏文本
                    self.logger.info("开始截屏识别...")
                    game_text = await self.capture_and_recognize()
                    self.logger.info(f"识别完成，结果: {game_text[:50] if game_text else 'None'}...")

                    if game_text is None:
                        self.logger.info("未识别到有效文本，继续监听")
                    elif game_text == self.last_game_text:
                        self.logger.info("识别到的文本与上次相同，跳过")
                    else:
                        self.logger.info(f"检测到新游戏文本: {game_text[:50]}...")
                        await self.send_game_text_to_maibot(game_text)
                        self.last_game_text = game_text
                        self.last_message_time = time.time()
                        self.waiting_for_response = True

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"游戏监听循环出错: {e}")
                await asyncio.sleep(5)  # 出错时等待5秒再继续

        self.logger.info("游戏监听循环结束")

    async def capture_and_recognize(self) -> Optional[str]:
        """截取屏幕并识别游戏文本"""
        try:
            # 检查截屏频率限制
            current_time = time.time()
            if current_time - self.last_screenshot_time < 0.5:  # 最小0.5秒间隔
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
            self.logger.error(f"截屏识别出错: {e}")
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

            self.logger.info(f"VLM识别结果: {content[:100]}...")
            return content

        except Exception as e:
            self.logger.error(f"识别游戏文本出错: {e}", exc_info=True)
            return None

    async def send_game_text_to_maibot(self, game_text: str) -> None:
        """将游戏文本发送给MaiBot"""
        try:
            # 直接发送游戏原文，不做任何加工
            content = game_text

            timestamp = time.time()
            message_id = f"manosaba_game_{int(timestamp * 1000)}"

            # 创建用户信息（游戏角色）
            game_user_info = UserInfo(
                platform=self.core.platform,
                user_id="game_character",
                user_nickname="《魔法少女的魔女审判》游戏内容同步助手",
                user_cardname="Mainosaba",
            )

            # 创建格式信息
            format_info = FormatInfo(content_format=["text"], accept_format=["text"])

            # 附加配置，用于标识这是游戏消息
            additional_config = {
                "source": "manosaba_game",
                "maimcore_reply_probability_gain": 1.5,  # 提高回复概率
            }

            # 创建消息信息
            message_info = BaseMessageInfo(
                platform=self.core.platform,
                message_id=message_id,
                time=timestamp,
                user_info=game_user_info,
                group_info=None,
                template_info=None,
                format_info=format_info,
                additional_config=additional_config,
            )

            # 创建消息段
            message_segment = Seg(type="text", data=content)

            # 创建最终消息
            message = MessageBase(message_info=message_info, message_segment=message_segment, raw_message=content)

            # 发送消息到MaiCore
            await self.core.send_to_maicore(message)
            self.logger.info(f"已发送游戏文本给MaiBot: {game_text[:50]}...")

        except Exception as e:
            self.logger.error(f"发送游戏文本到MaiBot出错: {e}")

    async def advance_game(self) -> None:
        """推进游戏到下一句对话"""
        try:
            if self.control_method == ControlMethod.MOUSE_CLICK:
                # 鼠标点击指定位置
                x, y = self.click_position
                pyautogui.click(x, y)
                self.logger.info(f"已点击位置 ({x}, {y}) 推进游戏")
            elif self.control_method == ControlMethod.ENTER_KEY:
                # 按Enter键
                pyautogui.press("enter")
                self.logger.info("已按Enter键推进游戏")
            elif self.control_method == ControlMethod.SPACE_KEY:
                # 按空格键
                pyautogui.press("space")
                self.logger.info("已按空格键推进游戏")

            # 等待一下让游戏界面更新
            await asyncio.sleep(0.5)

        except Exception as e:
            self.logger.error(f"推进游戏出错: {e}")


# 插件入口点
plugin_entrypoint = MainosabaPlugin
