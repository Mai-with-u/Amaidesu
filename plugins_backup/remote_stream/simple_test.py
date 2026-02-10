#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
简化的RemoteStream插件测试脚本
专门为Windows环境优化，避免multiprocessing问题
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

# 添加项目根目录到PATH
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

try:
    import websockets
except ImportError:
    print("依赖缺失: 请运行 'pip install websockets'", file=sys.stderr)
    sys.exit(1)

# 设置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("SimpleTest")


# 简单的WebSocket测试客户端
async def simple_client_test(host="127.0.0.1", port=8765):
    """简单的客户端测试，连接到RemoteStreamPlugin服务器"""
    uri = f"ws://{host}:{port}"
    logger.info(f"尝试连接到: {uri}")

    try:
        async with websockets.connect(uri) as websocket:
            logger.info("连接成功！")

            # 发送Hello消息
            hello_msg = {
                "type": "hello",
                "data": {"client_info": {"name": "SimpleTestClient", "type": "test_client"}},
                "timestamp": time.time(),
                "sequence": 1,
            }

            await websocket.send(json.dumps(hello_msg))
            logger.info("已发送Hello消息")

            # 等待并接收配置消息
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                logger.info(f"收到响应: {response[:100]}...")

                # 发送一个简单的状态消息
                status_msg = {
                    "type": "status",
                    "data": {"status": "connected", "message": "测试连接成功"},
                    "timestamp": time.time(),
                    "sequence": 2,
                }

                await websocket.send(json.dumps(status_msg))
                logger.info("已发送状态消息")

                # 等待几秒钟，然后断开
                await asyncio.sleep(3)
                logger.info("测试完成，断开连接")

            except asyncio.TimeoutError:
                logger.warning("等待响应超时")

    except ConnectionRefusedError:
        logger.error(f"无法连接到 {uri}，请确保服务器正在运行")
        return False
    except Exception as e:
        logger.error(f"连接过程中发生错误: {e}")
        return False

    return True


# 简单的WebSocket测试服务器
async def simple_server_test(host="127.0.0.1", port=8765):
    """简单的服务器测试，等待客户端连接"""
    logger.info(f"启动简单测试服务器: {host}:{port}")

    async def handle_client(websocket, path):
        logger.info(f"客户端连接: {websocket.remote_address}")

        try:
            # 发送配置消息
            config_msg = {
                "type": "config",
                "data": {"audio": {"sample_rate": 16000, "channels": 1}, "image": {"width": 640, "height": 480}},
                "timestamp": time.time(),
                "sequence": 0,
            }

            await websocket.send(json.dumps(config_msg))
            logger.info("已发送配置消息")

            # 处理客户端消息
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "unknown")
                    logger.info(f"收到消息类型: {msg_type}")

                    if msg_type == "hello":
                        client_info = data.get("data", {}).get("client_info", {})
                        logger.info(f"客户端信息: {client_info}")
                    elif msg_type == "status":
                        status = data.get("data", {})
                        logger.info(f"客户端状态: {status}")

                except json.JSONDecodeError:
                    logger.warning(f"收到无效JSON: {message[:50]}...")

        except websockets.ConnectionClosed:
            logger.info("客户端断开连接")
        except Exception as e:
            logger.error(f"处理客户端时发生错误: {e}")

    try:
        async with websockets.serve(handle_client, host, port):
            logger.info("服务器已启动，等待连接...")

            # 服务器运行10秒后自动停止（用于测试）
            await asyncio.sleep(10)
            logger.info("测试完成，停止服务器")

    except Exception as e:
        logger.error(f"服务器启动失败: {e}")


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="简化的RemoteStream测试")
    parser.add_argument(
        "--mode", choices=["server", "client"], default="client", help="测试模式: server=服务器, client=客户端"
    )
    parser.add_argument("--host", default="127.0.0.1", help="主机地址")
    parser.add_argument("--port", type=int, default=8765, help="端口")
    args = parser.parse_args()

    if args.mode == "server":
        await simple_server_test(args.host, args.port)
    else:
        success = await simple_client_test(args.host, args.port)
        if success:
            logger.info("客户端测试成功!")
        else:
            logger.error("客户端测试失败!")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
