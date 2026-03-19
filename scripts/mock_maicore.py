"""
对maicore的mock

启用一个ws服务端和一个控制台输入任务，便于模拟麦麦的回应来测试插件功能

使用方法：

```bash
python mock_maicore.py
```

命令行参数:
--debug    启用DEBUG级别日志输出
--filter   仅显示指定模块的INFO/DEBUG级别日志
"""

import argparse  # 导入 argparse
import asyncio
import base64
import json
import os
import random
import sys
import time
import tomllib
import uuid
from typing import Any, Callable, Dict, List, Optional, Set

from aiohttp import WSMsgType, web
from maim_message import MessageBase
from maim_message.message_base import BaseMessageInfo, FormatInfo, Seg, UserInfo
from src.modules.logging import get_logger

logger = get_logger("mock_maicore")


# ANSI 颜色代码
COLOR_RESET = "\033[0m"
COLOR_RED = "\033[91m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_BLUE = "\033[94m"
COLOR_MAGENTA = "\033[95m"
COLOR_CYAN = "\033[96m"


CONFIG_FILE_PATH = "config.toml"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
EMOJI_PATH = "data/emoji"

# 存储所有连接的 WebSocket 客户端
clients: Set[web.WebSocketResponse] = set()

# 自定义命令系统
commands: Dict[str, Dict[str, Any]] = {}


def command(name: str, description: str, usage: str = None):
    """命令注册装饰器"""

    def decorator(func: Callable):
        commands[name] = {
            "callback": func,
            "description": description,
            "usage": usage or f"/{name}",
        }
        return func

    return decorator


async def handle_websocket(request: web.Request):
    """处理新的 WebSocket 连接。"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    logger.info(f"客户端已连接: {request.remote}")
    clients.add(ws)

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    logger.debug(json.dumps(data, indent=2, ensure_ascii=False))

                    message_base = MessageBase.from_dict(data)
                    timestamp = time.strftime("%H:%M:%S", time.localtime(message_base.message_info.time))
                    user_info = message_base.message_info.user_info
                    user_display = f"{user_info.user_nickname}"
                    if message_base.message_segment.type == "text":
                        print(
                            f"{COLOR_GREEN}{message_base.message_info.platform}{COLOR_RESET} [{timestamp}] {COLOR_YELLOW}{user_display}{COLOR_RESET} > {message_base.message_segment.data}"
                        )
                    else:
                        print(
                            f"{COLOR_GREEN}{message_base.message_info.platform}{COLOR_RESET} [{timestamp}] {COLOR_YELLOW}{user_display}{COLOR_RESET} > [{message_base.message_segment.type}类型的消息]"
                        )

                except Exception as e:
                    logger.error(f"处理接收到的消息时出错: {e}", exc_info=True)

            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WebSocket 连接错误: {ws.exception()}")

    except asyncio.CancelledError:
        logger.info(f"WebSocket 任务被取消 ({request.remote})")
    except Exception as e:
        logger.error(f"WebSocket 连接异常 ({request.remote}): {e}", exc_info=True)
    finally:
        logger.info(f"客户端已断开连接: {request.remote}")
        clients.discard(ws)

    return ws


async def broadcast_message(message: MessageBase):
    """向所有连接的客户端广播消息。"""
    if not clients:
        logger.warning("没有连接的客户端，无法广播消息。")
        return
    # 转换为json
    message_json = json.dumps(message.to_dict())
    logger.info(f"准备广播消息给 {len(clients)} 个客户端: {str(message_json)[:100]}...")
    # 创建发送任务列表
    send_tasks = [asyncio.create_task(ws.send_str(message_json)) for ws in clients]
    # 等待所有发送完成，并处理可能出现的错误
    results = await asyncio.gather(*send_tasks, return_exceptions=True)
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            # 从 tasks 获取对应的 ws (这种方式有点脆弱，但可行)
            ws_list = list(clients)
            failed_ws = ws_list[i] if i < len(ws_list) else "Unknown WS"
            logger.error(
                f"向客户端 {failed_ws.remote if hasattr(failed_ws, 'remote') else failed_ws} 发送消息失败: {result}"
            )


def get_random_emoji() -> str:
    """从表情包目录中随机选择一个表情包并转换为base64"""
    try:
        emoji_files = [f for f in os.listdir(EMOJI_PATH) if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))]
        if not emoji_files:
            logger.warning("表情包目录为空")
            return None

        random_emoji = random.choice(emoji_files)
        emoji_path = os.path.join(EMOJI_PATH, random_emoji)

        with open(emoji_path, "rb") as f:
            image_data = f.read()
            base64_data = base64.b64encode(image_data).decode("utf-8")
            return base64_data
    except Exception as e:
        logger.error(f"处理表情包时出错: {e}")
        return None


def build_message(content: str, message_type: str = "text") -> MessageBase:
    """构建MessageBase"""
    msg_id = str(uuid.uuid4())
    now = time.time()

    platform = "mock-maicore"

    user_info = UserInfo(
        platform=platform,
        user_id=123456,
        user_nickname="麦麦",
        user_cardname="麦麦",
    )

    group_info = None

    format_info = FormatInfo(
        content_format=["text", "emoji"],
        accept_format=["text", "emoji"],
    )

    message_info = BaseMessageInfo(
        platform=platform,
        message_id=msg_id,
        time=now,
        user_info=user_info,
        group_info=group_info,
        template_info=None,
        format_info=format_info,
        additional_config={},
    )

    if message_type == "emoji":
        message_segment = Seg(type="emoji", data=content)
    else:
        message_segment = Seg(type="text", data=content)

    return MessageBase(message_info=message_info, message_segment=message_segment, raw_message=content)


# 命令处理函数
@command("help", "显示所有可用命令", "/help")
async def cmd_help(args: List[str]) -> Optional[MessageBase]:
    """显示所有可用命令的帮助信息"""
    help_text = f"\n{COLOR_CYAN}===== 可用命令列表 ====={COLOR_RESET}\n"

    for _cmd_name, cmd_info in sorted(commands.items()):
        help_text += f"{COLOR_YELLOW}{cmd_info['usage']}{COLOR_RESET} - {cmd_info['description']}\n"

    print(help_text)
    return None  # 不发送任何消息到websocket


@command("gift", "发送虚假礼物消息", "/gift [用户名] [礼物名] [数量]")
async def cmd_gift(args: List[str]) -> Optional[MessageBase]:
    """发送虚假礼物消息"""
    # 默认参数
    username = args[0] if len(args) > 0 else "测试用户"
    gift_name = args[1] if len(args) > 1 else "辣条"
    gift_count = args[2] if len(args) > 2 else "1"

    try:
        count = int(gift_count)
    except ValueError:
        count = 1

    user_id = f"test_gift_{hash(username) % 10000}"
    message_id = f"test_gift_{int(time.time())}"

    message_base = MessageBase(
        message_info=BaseMessageInfo(
            platform="bilibili",
            message_id=message_id,
            time=int(time.time()),
            user_info=UserInfo(platform="bilibili", user_id=user_id, user_nickname=username, user_cardname=username),
            format_info=FormatInfo(content_format=["text"], accept_format=["text", "gift"]),
        ),
        message_segment=Seg(
            "seglist",
            [
                Seg(type="gift", data=f"{gift_name}:{count}"),
                Seg("priority_info", {"message_type": "vip", "priority": 1}),
            ],
        ),
        raw_message=f"{username} 送出了 {count} 个 {gift_name}",
    )

    print(f"{COLOR_GREEN}💝 发送礼物: {username} -> {count}个{gift_name}{COLOR_RESET}")
    return message_base


@command("sc", "发送虚假醒目留言", "/sc [用户名] [内容]")
async def cmd_sc(args: List[str]) -> Optional[MessageBase]:
    """发送虚假醒目留言（SuperChat）"""
    # 默认参数
    username = args[0] if len(args) > 0 else "SC大佬"
    content = " ".join(args[1:]) if len(args) > 1 else "这是一条测试醒目留言！"

    user_id = f"test_sc_{hash(username) % 10000}"
    message_id = f"test_sc_{int(time.time())}"

    message_base = MessageBase(
        message_info=BaseMessageInfo(
            platform="bilibili",
            message_id=message_id,
            time=int(time.time()),
            user_info=UserInfo(platform="bilibili", user_id=user_id, user_nickname=username, user_cardname=username),
            format_info=FormatInfo(content_format=["text"], accept_format=["text"]),
        ),
        message_segment=Seg(
            "seglist",
            [Seg(type="text", data=content), Seg("priority_info", {"message_type": "super_vip", "priority": 2})],
        ),
        raw_message=f"{username} 发送了醒目留言：{content}",
    )

    print(f"{COLOR_YELLOW}⭐ 发送醒目留言: {username} -> {content}{COLOR_RESET}")
    return message_base


@command("guard", "发送虚假大航海开通消息", "/guard [用户名] [等级]")
async def cmd_guard(args: List[str]) -> Optional[MessageBase]:
    """发送虚假大航海开通消息"""
    # 默认参数
    username = args[0] if len(args) > 0 else "大航海"
    guard_level = args[1] if len(args) > 1 else "舰长"

    # 验证大航海等级
    valid_levels = ["舰长", "提督", "总督"]
    if guard_level not in valid_levels:
        guard_level = "舰长"

    user_id = f"test_guard_{hash(username) % 10000}"
    message_id = f"test_guard_{int(time.time())}"

    message_base = MessageBase(
        message_info=BaseMessageInfo(
            platform="bilibili",
            message_id=message_id,
            time=int(time.time()),
            user_info=UserInfo(platform="bilibili", user_id=user_id, user_nickname=username, user_cardname=username),
            format_info=FormatInfo(content_format=["text"], accept_format=["text"]),
        ),
        message_segment=Seg(
            "seglist",
            [
                Seg(type="text", data=f"开通了{guard_level}"),
                Seg("priority_info", {"message_type": "super_vip", "priority": 3}),
            ],
        ),
        raw_message=f"{username} 开通了{guard_level}",
    )

    print(f"{COLOR_MAGENTA}⚓ 发送大航海: {username} -> {guard_level}{COLOR_RESET}")
    return message_base


async def handle_command(cmd_line: str):
    """处理命令行输入，解析命令和参数"""
    if not cmd_line.startswith("/"):
        return None

    # 去除前导斜杠并分割命令和参数
    parts = cmd_line[1:].strip().split()
    if not parts:
        return None

    cmd_name = parts[0].lower()
    args = parts[1:]

    if cmd_name in commands:
        cmd_func = commands[cmd_name]["callback"]
        try:
            return await cmd_func(args)
        except Exception as e:
            logger.error(f"执行命令 '{cmd_name}' 时出错: {e}", exc_info=True)
            print(f"{COLOR_RED}执行命令 '{cmd_name}' 时出错: {e}{COLOR_RESET}")
            return None
    else:
        print(f"{COLOR_RED}未知命令: '{cmd_name}'. 输入 /help 查看可用命令。{COLOR_RESET}")
        return None


async def console_input_loop():
    """异步监听控制台输入并广播消息。"""
    loop = asyncio.get_running_loop()
    logger.info("启动控制台输入监听。输入 '/help' 查看可用命令，输入 'quit' 或 'exit' 退出。")

    # 启动时显示帮助信息
    await cmd_help([])

    while True:
        try:
            # 使用 run_in_executor 在单独的线程中运行阻塞的 input()
            line = await loop.run_in_executor(None, lambda: input(f"{COLOR_BLUE}mock_maicore{COLOR_RESET} > "))
            line = line.strip()
            if not line:
                continue
            if line.lower() in ["quit", "exit"]:
                logger.info("收到退出指令，正在停止...")
                # 可以触发应用的关闭逻辑
                tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                [task.cancel() for task in tasks]
                break

            # 处理命令
            if line.startswith("/"):
                message_to_send = await handle_command(line)
                if message_to_send:
                    await broadcast_message(message_to_send)
                continue

            # 处理普通消息
            message_to_send = build_message(line)
            logger.debug(f"准备从控制台发送消息: {message_to_send}")
            await broadcast_message(message_to_send)

        except (EOFError, KeyboardInterrupt):
            logger.info("检测到 EOF 或中断信号，正在退出...")
            break
        except asyncio.CancelledError:
            logger.info("控制台输入任务被取消。")
            break
        except Exception as e:
            logger.error(f"控制台输入循环出错: {e}", exc_info=True)
            # 防止无限循环错误，稍微等待一下
            await asyncio.sleep(1)


def load_config() -> dict:
    """加载配置文件并返回配置。"""
    try:
        with open(CONFIG_FILE_PATH, "rb") as f:  # tomllib 需要二进制模式打开文件
            config = tomllib.load(f)  # 使用 tomllib.load
            return config
    except FileNotFoundError:
        logger.warning(f"配置文件 {CONFIG_FILE_PATH} 未找到，将使用默认配置: ws://{DEFAULT_HOST}:{DEFAULT_PORT}")
    except tomllib.TOMLDecodeError as e:  # 使用 tomllib 的特定异常
        logger.error(f"解析配置文件 {CONFIG_FILE_PATH} 时出错: {e}，将使用默认配置: ws://{DEFAULT_HOST}:{DEFAULT_PORT}")
    except Exception as e:
        logger.error(
            f"读取配置文件 {CONFIG_FILE_PATH} 时发生其他错误: {e}，将使用默认配置: ws://{DEFAULT_HOST}:{DEFAULT_PORT}"
        )
    return {}


async def main():
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description="Mock MaiCore 服务模拟器")
    # 添加 --debug 参数，用于控制日志级别
    parser.add_argument("--debug", action="store_true", help="启用 DEBUG 级别日志输出")
    # 解析命令行参数
    args = parser.parse_args()

    # --- 配置日志 ---
    base_level = "DEBUG" if args.debug else "INFO"
    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{line: <4}</cyan> | <cyan>{extra[module]}</cyan> - <level>{message}</level>"

    # 清除所有预设的 handler (包括 src/utils/logger.py 中添加的)
    logger.remove()

    # 添加最终的 handler，应用过滤器（如果定义了）
    logger.add(
        sys.stderr,
        level=base_level,
        colorize=True,
        format=log_format,
    )

    # 打印日志级别和过滤器状态相关的提示信息
    if args.debug:
        logger.info("已启用 DEBUG 日志级别。")
    else:
        logger.info("已启用 INFO 日志级别。")

    config = load_config()

    host = config.get("host", DEFAULT_HOST)
    port = config.get("port", DEFAULT_PORT)

    app = web.Application()
    app.router.add_get("/ws", handle_websocket)
    logger.info(f"模拟 MaiCore 启动，监听地址: ws://{host}:{port}/ws (从 {CONFIG_FILE_PATH} 读取)")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)

    try:
        await site.start()
        logger.info("WebSocket 服务器已启动。")
        # 启动控制台输入任务
        console_task = asyncio.create_task(console_input_loop())
        # 等待控制台任务结束（表示用户想退出）或服务器被外部停止
        await console_task  # 等待控制台输入循环结束

    except asyncio.CancelledError:
        logger.info("主任务被取消。")
    except Exception as e:
        logger.error(f"启动或运行服务器时发生错误: {e}", exc_info=True)
    finally:
        logger.info("开始关闭服务器...")
        await runner.cleanup()
        logger.info("服务器已关闭。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("通过 Ctrl+C 强制退出。")
    except Exception as e:
        logger.critical(f"程序意外终止: {e}", exc_info=True)
