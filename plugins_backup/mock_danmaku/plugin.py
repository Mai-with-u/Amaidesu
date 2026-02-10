# src/plugins/mock_danmaku/plugin.py

import asyncio
import json
import os
import traceback  # 导入 traceback 用于详细错误日志记录
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- 依赖检查 & TOML ---
# (保留 TOML 加载部分，它很有用)
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import toml as tomllib
    except ImportError:
        tomllib = None

# --- Amaidesu 核心导入 ---
# 直接导入 MessageBase 及其组件以供 from_dict 使用
from maim_message import MessageBase
from src.core.amaidesu_core import AmaidesuCore
from src.core.plugin_manager import BasePlugin

# 已移除导入: from src.utils.message_utils import deserialize_messagebase
# 移除多余的 get_logger 和 logger 初始化
# from src.utils.logger import get_logger

# --- 此插件的特定 Logger ---
# logger = get_logger("MockDanmakuPlugin") # 已由基类初始化


# --- 用于加载配置的辅助函数 (可复用) ---
# 移除旧的配置加载函数
# def load_plugin_config(plugin_name: str = "mock_danmaku") -> Dict[str, Any]:
#     # 构建相对于此文件目录的路径
#     config_path = os.path.join(os.path.dirname(__file__), "config.toml")
#     default_config = {}  # 文件未找到或出错时的默认配置
#
#     if not os.path.exists(config_path):
#         logger.warning(f"配置文件未找到: {config_path}, 使用默认配置。")
#         return default_config
#
#     if tomllib is None:
#         logger.error("未找到 toml/tomllib 库。请安装 (`pip install toml` 适用于 Python < 3.11)。无法加载配置。")
#         return default_config
#
#     try:
#         with open(config_path, "rb") as f:
#             loaded_config = tomllib.load(f)
#             return loaded_config.get(plugin_name, default_config)  # 返回特定部分
#     except Exception as e:
#         logger.error(f"加载或解析配置时出错: {config_path}: {e}", exc_info=True)
#         return default_config


# --- 插件类 ---
class MockDanmakuPlugin(BasePlugin):
    """模拟弹幕插件，从 JSONL 文件读取消息并按设定速率发送。"""

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        # self.logger = logger # 已由基类初始化

        # --- 加载自身配置 ---
        # self.config = load_plugin_config("mock_danmaku")  # 加载 [mock_danmaku] 部分
        self.config = self.plugin_config  # 直接使用注入的 plugin_config

        # --- 配置值 ---
        # 日志文件名，从配置中读取，默认为 msg_default.jsonl
        # 我们只关心文件名，路径固定在插件目录下的 data/ 子目录中
        log_filename_config = self.config.get("log_file_path", "msg_default.jsonl")
        log_filename = os.path.basename(log_filename_config)  # 提取文件名

        # 获取插件目录
        plugin_dir = Path(__file__).resolve().parent
        # 定义 data 目录路径
        data_dir = plugin_dir / "data"

        # 确保 data 目录存在
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            self.logger.debug(f"确保数据目录存在: {data_dir}")  # 此处 logger 已是 self.logger
        except OSError as e:
            self.logger.error(f"创建数据目录失败: {data_dir}: {e}", exc_info=True)  # 此处 logger 已是 self.logger
            # 根据需要决定是否禁用插件
            # self.enabled = False
            # return

        # 最终的日志文件路径
        self.log_file_path = data_dir / log_filename
        self.logger.info(f"将在插件数据目录中查找日志文件: {self.log_file_path}")  # 此处 logger 已是 self.logger

        # 检查最终路径是否存在
        if not self.log_file_path.exists():
            self.logger.warning(  # 此处 logger 已是 self.logger
                f"日志文件不存在: {self.log_file_path}。插件在setup时会尝试加载，如果文件仍不存在则无法发送消息。"
            )
            # 注意：这里不再因为文件不存在而立即禁用插件，允许后续创建文件

        self.send_interval = max(0.1, self.config.get("send_interval", 1.0))  # 确保最小间隔
        self.loop_playback = self.config.get("loop_playback", True)
        self.start_immediately = self.config.get("start_immediately", True)

        # --- 状态变量 ---
        self._message_lines: List[str] = []  # 存储原始行以允许循环播放而无需重新读取
        self._current_line_index: int = 0
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

        # self.logger.info( # 此日志可移除或保留，基类有通用初始化日志
        #     f"模拟弹幕插件初始化完成。源: '{self.log_file_path}', "
        #     f"间隔: {self.send_interval}s, 循环: {self.loop_playback}, 立即启动: {self.start_immediately}"
        # )

    async def setup(self):
        await super().setup()

        # --- 加载消息 ---
        await self._load_message_lines()

        if not self._message_lines:
            self.logger.warning(f"未从 '{self.log_file_path}' 加载任何消息。插件将不会发送任何内容。")
            return

        # --- 启动发送任务 (如果已配置) ---
        if self.start_immediately:
            self._start_sending_task()
        else:
            self.logger.info("配置 `start_immediately` 为 false。发送任务未自动启动。")

    def _start_sending_task(self):
        """创建并启动消息发送任务。"""
        if self._task and not self._task.done():
            self.logger.warning("发送任务已在运行。")
            return
        if not self._message_lines:
            self.logger.warning("未加载消息，无法启动发送任务。")
            return

        self._stop_event.clear()  # 启动前确保事件已清除
        self._task = asyncio.create_task(self._run_sending_loop(), name="MockDanmakuSender")
        self.logger.info(f"启动模拟弹幕发送任务 (源: {self.log_file_path.name})...")

    async def cleanup(self):
        self.logger.info(f"开始清理模拟弹幕插件 (源: {self.log_file_path.name})...")
        self._stop_event.set()
        if self._task and not self._task.done():
            self.logger.debug("正在取消模拟弹幕发送任务...")
            self._task.cancel()
            try:
                # 等待时间略长于间隔，以允许当前 sleep 完成
                await asyncio.wait_for(self._task, timeout=self.send_interval + 1)
            except asyncio.TimeoutError:
                self.logger.warning("模拟弹幕发送任务在超时后未结束。")
            except asyncio.CancelledError:
                self.logger.info("模拟弹幕发送任务已被取消。")  # 预期的行为

        # 重置状态
        self._message_lines = []
        self._current_line_index = 0
        self._task = None

        await super().cleanup()
        self.logger.info(f"模拟弹幕插件清理完成 (源: {self.log_file_path.name})。")

    async def _load_message_lines(self):
        """从 JSONL 文件加载消息行。"""
        self._message_lines = []
        self._current_line_index = 0
        if not self.log_file_path.exists() or not self.log_file_path.is_file():
            self.logger.error(f"日志文件未找到或不是文件: {self.log_file_path}")
            return

        try:
            with open(self.log_file_path, "r", encoding="utf-8") as f:
                self._message_lines = [line.strip() for line in f if line.strip()]
            self.logger.info(f"成功从 '{self.log_file_path.name}' 加载 {len(self._message_lines)} 行消息。")
        except Exception as e:
            self.logger.error(f"读取日志文件时出错: {self.log_file_path}: {e}", exc_info=True)
            self._message_lines = []  # 出错时清空

    async def _run_sending_loop(self):
        """后台循环，按间隔发送消息。"""
        self.logger.info("模拟弹幕发送循环开始。")
        while not self._stop_event.is_set():
            if not self._message_lines:
                self.logger.warning("消息列表为空，停止发送循环。")
                break

            if self._current_line_index >= len(self._message_lines):
                if self.loop_playback:
                    self.logger.info("到达文件末尾，循环播放已启用，重置索引。")
                    self._current_line_index = 0
                else:
                    self.logger.info("到达文件末尾，循环播放已禁用，停止发送。")
                    break  # 如果不循环则退出循环

            # 重置后再次检查索引
            if self._current_line_index >= len(self._message_lines):
                self.logger.warning("索引仍然超出范围，停止循环。")  # 正常情况下不应发生
                break

            line = self._message_lines[self._current_line_index]
            self._current_line_index += 1

            try:
                # --- 解析并发送 ---
                message = self._parse_line_to_message(line)
                if message:
                    self.logger.debug(
                        f"发送模拟消息 (行 {self._current_line_index}): {message.raw_message[:50] if message.raw_message else message.message_segment}"
                    )
                    await self.core.send_to_maicore(message)
                else:
                    self.logger.warning(
                        f"解析消息失败 (行 {self._current_line_index}): {line[:100]}..."
                    )  # 记录失败的行

                # --- 等待 ---
                await asyncio.sleep(self.send_interval)  # 发送下一条前等待

            except asyncio.CancelledError:
                self.logger.info("模拟弹幕发送循环被取消。")
                break  # 取消时干净地退出循环
            except Exception as e:
                self.logger.error(f"发送模拟消息时发生意外错误 (行 {self._current_line_index}): {e}", exc_info=True)
                # 可选地，在出错后继续之前添加短暂延迟
                await asyncio.sleep(1)

        self.logger.info("模拟弹幕发送循环已结束。")
        # 确保任务完成后清除任务引用
        self._task = None

    def _parse_line_to_message(self, line: str) -> Optional[MessageBase]:
        """解析单行 JSON 字符串为 MessageBase 对象。"""
        try:
            data: Dict[str, Any] = json.loads(line)
            # 直接使用 MessageBase 类方法
            message = MessageBase.from_dict(data)
            if message is None:
                # MessageBase.from_dict 可能返回 None 或根据实现抛出错误
                # 以防 from_dict 静默返回 None，在此处添加日志
                self.logger.warning(f"MessageBase.from_dict 为行返回 None: {line[:100]}...")
            return message
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON 解析错误: {e}. 行内容: {line[:100]}...")
            return None
        except KeyError as e:
            # 捕获 from_dict 调用期间缺少的键
            self.logger.error(f"字典转换为 MessageBase 时缺少键: {e}. 行内容: {line[:100]}...", exc_info=True)
            self.logger.error(traceback.format_exc())
            return None
        except Exception:
            # 捕获任何其他反序列化错误
            self.logger.error(f"从字典创建 MessageBase 时出错: {line[:100]}...", exc_info=True)
            return None

    # --- 公共控制方法 ---
    async def start_mocking(self):
        """公共方法，用于在未运行时启动消息发送。"""
        if self._task and not self._task.done():
            self.logger.info("模拟已在运行。")
            return
        if not self._message_lines:
            await self._load_message_lines()  # 尝试再次加载
            if not self._message_lines:
                self.logger.error("未加载消息，无法启动模拟。")
                return
        self._start_sending_task()

    async def stop_mocking(self):
        """公共方法，用于停止正在运行的消息发送任务。"""
        if not self._task or self._task.done():
            self.logger.info("模拟当前未运行，无需停止。")
            return
        self.logger.info("正在停止模拟弹幕发送...")
        self._stop_event.set()
        # The task will see the event and stop. Cleanup happens in the loop.

    async def reload_messages(self):
        """公共方法，用于重新加载消息文件。"""
        self.logger.info(f"正在从 {self.log_file_path.name} 重新加载消息...")
        was_running = self._task and not self._task.done()
        if was_running:
            await self.stop_mocking()  # 重新加载前停止
            # 等待片刻以使任务可能完成停止
            await asyncio.sleep(0.1)

        await self._load_message_lines()

        if was_running and self._message_lines:
            self.logger.info("重新加载后自动重启发送任务...")
            await self.start_mocking()  # 如果之前正在运行则重启


# --- 插件入口点 ---
plugin_entrypoint = MockDanmakuPlugin
