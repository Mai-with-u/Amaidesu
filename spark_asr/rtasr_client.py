# -*- encoding:utf-8 -*-
import hashlib
import hmac
import base64
import json
import time
import threading
import urllib.parse
import logging
import uuid
import asyncio
from websocket import create_connection, WebSocketException
import websocket
import datetime
import queue
import sounddevice as sd
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from spark_asr.microphone_recorder import MicrophoneRecorder

# 全局配置：与服务端确认的固定参数
FIXED_PARAMS = {
    "audio_encode": "pcm_s16le",
    "lang": "autodialect",
    "samplerate": "16000"  # 固定16k采样率，对应每40ms发送1280字节
}
AUDIO_FRAME_SIZE = 1280  # 每帧音频字节数（16k采样率、16bit位深、40ms）
FRAME_INTERVAL_MS = 40    # 每帧发送间隔（毫秒）


class RTASRClient():
    def __init__(self, app_id, access_key_id, access_key_secret, on_result=None):
        self.app_id = app_id
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.base_ws_url = "wss://office-api-ast-dx.iflyaisol.com/ast/communicate/v1"
        self.ws = None
        self.is_connected = False
        self.recv_thread = None
        self.session_id = None
        self.is_sending_audio = False  # 防止并发发送
        # 录音相关
        self.audio_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=50)
        self._mic: MicrophoneRecorder | None = None
        self.stop_event = threading.Event()
        # 延迟统计
        self.first_audio_sent_ms: float | None = None
        self.end_sent_ms: float | None = None
        self.first_result_reported: bool = False
        # 增量输出缓存
        self._prev_hypothesis: str = ""
        # 用户可注入的结果回调：签名 on_result(text: str, is_final: bool, raw: dict)
        self.on_result = on_result
        # 回调投递目标事件循环（可选）：若设置，则以该 loop 执行 on_result
        self.result_loop: asyncio.AbstractEventLoop | None = None

    def set_result_loop(self, loop: asyncio.AbstractEventLoop | None = None):
        """设置回调投递的事件循环；不设置则默认使用当前接收线程的 loop。"""
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
        self.result_loop = loop

    # =============== 录音 ===============
    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"【麦克风状态】{status}")
        # indata: numpy int16 [frames, channels]
        try:
            # 保证单声道，dtype=int16
            chunk_bytes = indata.copy().tobytes()
            # 丢弃策略：队列满则丢弃最旧的一帧，避免阻塞
            if self.audio_queue.full():
                try:
                    _ = self.audio_queue.get_nowait()
                except queue.Empty:
                    pass
            self.audio_queue.put_nowait(chunk_bytes)
        except Exception as e:
            # 避免在回调中抛异常中断流
            print(f"【回调异常】{e}")

    def start_microphone(self, device: int | str | None = None):
        """启动麦克风输入，16k/16bit/mono，块大小对应40ms=640采样"""
        if self._mic is not None:
            print("【录音提示】麦克风已启动")
            return True
        try:
            samplerate = int(FIXED_PARAMS["samplerate"])  # 16000
            channels = 1
            blocksize = 640  # 40ms * 16kHz = 640 samples
            dtype = "int16"
            self.stop_event.clear()

            self._mic = MicrophoneRecorder(
                samplerate=samplerate,
                channels=channels,
                dtype=dtype,
                blocksize=blocksize,
                device=device,
                callback=self._audio_callback,
            )
            ok = self._mic.start()
            if not ok:
                print("【录音失败】无法打开麦克风：sounddevice 初始化失败")
                return False

            print(f"【录音开始】设备={device if device is not None else '默认'} | {samplerate}Hz/{dtype}/mono | 块={blocksize}样本≈{FRAME_INTERVAL_MS}ms")
            return True
        except Exception as e:
            print(f"【录音失败】无法打开麦克风：{e}")
            return False

    def stop_microphone(self):
        if self._mic is not None:
            try:
                self._mic.stop()
            except Exception as e:
                print(f"【录音关闭异常】{e}")
            finally:
                self._mic = None
                print("【录音结束】麦克风已关闭")

    def _generate_auth_params(self):
        """生成鉴权参数（严格按字典序排序，匹配Java TreeMap）"""
        auth_params = {
            "accessKeyId": self.access_key_id,
            "appId": self.app_id,
            "uuid": uuid.uuid4().hex,
            "utc": self._get_utc_time(),
            **FIXED_PARAMS
        }

        # 计算签名：过滤空值 → 字典序排序 → URL编码 → 拼接基础字符串
        sorted_params = dict(sorted([
            (k, v) for k, v in auth_params.items()
            if v is not None and str(v).strip() != ""
        ]))
        base_str = "&".join([
            f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
            for k, v in sorted_params.items()
        ])

        # HMAC-SHA1 加密 + Base64编码
        signature = hmac.new(
            self.access_key_secret.encode("utf-8"),
            base_str.encode("utf-8"),
            hashlib.sha1
        ).digest()
        auth_params["signature"] = base64.b64encode(signature).decode("utf-8")
        return auth_params

    def _get_utc_time(self):
        """生成服务端要求的UTC时间格式：yyyy-MM-dd'T'HH:mm:ss+0800"""
        beijing_tz = datetime.timezone(datetime.timedelta(hours=8))
        now = datetime.datetime.now(beijing_tz)
        return now.strftime("%Y-%m-%dT%H:%M:%S%z")

    def connect(self):
        """建立WebSocket连接（增加稳定性配置）"""
        try:
            auth_params = self._generate_auth_params()
            params_str = urllib.parse.urlencode(auth_params)
            full_ws_url = f"{self.base_ws_url}?{params_str}"

            # 初始化WebSocket连接（禁用自动文本解析，延长超时）
            self.ws = create_connection(
                full_ws_url,
                timeout=30,
                enable_multithread=True  # 支持多线程并发
            )
            self.is_connected = True
            print("【连接成功】WebSocket握手完成，等待服务端就绪...")
            time.sleep(1.5)  # 确保服务端完全初始化

            # 启动接收线程（单独处理服务端消息）
            self.recv_thread = threading.Thread(target=asyncio.run, args=(self._recv_msg(),), daemon=True)
            self.recv_thread.start()
            return True
        except WebSocketException as e:
            print(f"【连接失败】WebSocket错误：{str(e)}")
            if hasattr(e, 'status_code'):
                print(f"【服务端状态码】{e.status_code}")
            return False
        except Exception as e:
            print(f"【连接异常】其他错误：{str(e)}")
            return False

    async def _recv_msg(self):
        """接收服务端消息：仅输出识别文本和必要调试信息，并统计延迟"""
        while True:
            # 先判断连接状态，避免操作已关闭的连接
            if not self.is_connected or not self.ws:
                # 连接关闭时退出
                break

            try:
                msg = self.ws.recv()
                if not msg:
                    print("【提示】服务端关闭连接")
                    self.close()
                    break

                # 仅处理文本消息（服务端返回的JSON均为文本）
                if isinstance(msg, str):
                    try:
                        # print(f"【收到消息】{msg}")
                        msg_json = json.loads(msg)
                        # 更新会话ID（用于结束标记关联）
                        if (msg_json.get('msg_type') == 'action' 
                            and 'sessionId' in msg_json.get('data', {})):
                            self.session_id = msg_json['data']['sessionId']
                            continue

                        # 仅处理识别结果
                        if msg_json.get('msg_type') == 'result' and msg_json.get('res_type') == 'asr':
                            now_ms = time.time() * 1000
                            text = self._extract_text(msg_json)
                            if text:
                                # 首包延迟
                                if not self.first_result_reported and self.first_audio_sent_ms is not None:
                                    first_latency = now_ms - self.first_audio_sent_ms
                                    print(f"【首包延迟】{first_latency:.0f}ms")
                                    self.first_result_reported = True
                                # 增量后缀输出
                                # suffix = self._diff_suffix(self._prev_hypothesis, text)
                                # if suffix:
                                    # print(f"识别: {suffix}")

                            # 最终结果判定：仅依据 data.cn.st.type（0=最终，1=中间）；缺失/异常按中间处理
                            is_final = False
                            try:
                                st = (msg_json.get('data') or {}).get('cn') or {}
                                st = (st.get('st') or {})
                                st_type = st.get('type')
                                if isinstance(st_type, (int, float)):
                                    is_final = int(st_type) == 0
                                elif isinstance(st_type, str) and st_type.strip().isdigit():
                                    is_final = int(st_type.strip()) == 0
                                else:
                                    is_final = False
                            except Exception:
                                is_final = False

                            if is_final:
                                if self.end_sent_ms is not None:
                                    tail_latency = now_ms - self.end_sent_ms
                                    print(f"【尾包延迟】{tail_latency:.0f}ms")
                                # 句子结束，重置缓存
                                self._prev_hypothesis = ""
                                # 回调：最终结果（非阻塞投递到事件循环）
                                if self.on_result is not None:
                                    try:
                                        coro = self.on_result(text or "", True, msg_json)
                                        if self.result_loop is not None:
                                            asyncio.run_coroutine_threadsafe(coro, self.result_loop)
                                        else:
                                            asyncio.create_task(coro)
                                    except Exception:
                                        pass
                            else:
                                # 未结束时更新累计假设
                                self._prev_hypothesis = text
                                # 回调：增量结果（非阻塞投递到事件循环）
                                if self.on_result is not None and text:
                                    try:
                                        coro = self.on_result(text, False, msg_json)
                                        if self.result_loop is not None:
                                            asyncio.run_coroutine_threadsafe(coro, self.result_loop)
                                        else:
                                            asyncio.create_task(coro)
                                    except Exception:
                                        pass
                            continue
                    except json.JSONDecodeError:
                        # 忽略非JSON文本
                        pass
                else:
                    # 忽略二进制消息
                    pass

            except WebSocketException as e:
                print(f"【异常】连接中断：{str(e)}")
                self.close()
                break
            except OSError as e:
                # 捕获Windows套接字错误（如非套接字操作）
                print(f"【异常】系统套接字错误：{str(e)}")
                self.close()
                break
            except Exception as e:
                print(f"【异常】未知错误：{str(e)}")
                self.close()
                break

    def _extract_text(self, msg_json: dict) -> str:
        """从 asr 结果中抽取当前片段文本（按 ws->cw 拼接）"""
        try:
            data = msg_json.get('data') or {}
            cn = data.get('cn') or {}
            st = cn.get('st') or {}
            rt_list = st.get('rt') or []
            pieces: list[str] = []
            for rt in rt_list:
                for ws in rt.get('ws', []):
                    cws = ws.get('cw', [])
                    if not cws:
                        continue
                    w = cws[0].get('w', '')
                    if w:
                        pieces.append(w)
            return ''.join(pieces)
        except Exception:
            return ''

    def _diff_suffix(self, old: str, new: str) -> str:
        """返回 new 相对于 old 的最长公共前缀之后的后缀；若出现回退改写，仍按公共前缀截断。"""
        try:
            max_len = min(len(old), len(new))
            i = 0
            while i < max_len and old[i] == new[i]:
                i += 1
            return new[i:]
        except Exception:
            return new

    def send_audio(self):
        """
        精确控制音频发送节奏（实时录音）：
        1. 16k采样率每40ms发送1280字节
        2. 基于起始时间计算理论发送时间，抵消累计误差
        3. 从麦克风回调队列取帧，按节奏发送
        """
        if not self.is_connected or not self.ws:
            print("【发送失败】WebSocket未连接")
            return False
        if self.is_sending_audio:
            print("【发送失败】已有发送任务在执行")
            return False

        self.is_sending_audio = True
        frame_index = 0  # 帧索引（从0开始）
        start_time = None  # 第一次发送的时间戳（毫秒）
        
        try:
            print(f"【发送】每{FRAME_INTERVAL_MS}ms从队列取一帧并发送（1280字节）")
            # 循环发送音频帧（实时）
            while not self.stop_event.is_set():
                try:
                    # 最长等待一帧时间，避免空转
                    chunk = self.audio_queue.get(timeout=1.0)
                except queue.Empty:
                    # 若长时间无数据，继续等待/检查停止状态
                    continue

                if not chunk:
                    continue

                # 1. 记录第一次发送的起始时间（毫秒级）
                if start_time is None:
                    start_time = time.time() * 1000  # 转换为毫秒
                    self.first_audio_sent_ms = start_time
                    print(f"【开始】开始发送音频")

                # 2. 计算当前帧的理论发送时间（基于起始时间和帧索引）
                expected_send_time = start_time + (frame_index * FRAME_INTERVAL_MS)

                # 3. 计算当前实际时间与理论时间的差值，动态调整休眠
                current_time = time.time() * 1000  # 当前时间（毫秒）
                time_diff = expected_send_time - current_time  # 差值（ms）

                # 4. 休眠（仅当差值为正时休眠，避免负向等待）
                if time_diff > 0.1:
                    time.sleep(time_diff / 1000)

                # 5. 发送当前帧（明确为二进制消息）
                # 仅发送前1280字节（若驱动返回块大小变化，进行截断/填充）
                if len(chunk) != AUDIO_FRAME_SIZE:
                    if len(chunk) > AUDIO_FRAME_SIZE:
                        chunk = chunk[:AUDIO_FRAME_SIZE]
                    else:
                        # 填充静音
                        chunk = chunk + b"\x00" * (AUDIO_FRAME_SIZE - len(chunk))
                self.ws.send_binary(chunk)
                frame_index += 1

            # 循环结束：主动发送结束标记
            end_msg = {"end": True}
            if self.session_id:
                end_msg["sessionId"] = self.session_id
            end_msg_str = json.dumps(end_msg, ensure_ascii=False)
            self.ws.send(end_msg_str)
            self.end_sent_ms = time.time() * 1000
            print("【结束】已发送结束标记")
            return True
        except WebSocketException as e:
            print(f"【异常】发送时连接中断：{str(e)}")
            self.close()
            return False
        except Exception as e:
            print(f"【异常】发送未知错误：{str(e)}")
            self.close()
            return False
        finally:
            self.is_sending_audio = False

    def close(self):
        """安全关闭WebSocket连接（增加状态保护）"""
        # 停止录音
        self.stop_event.set()
        self.stop_microphone()
        if self.is_connected and self.ws:
            self.is_connected = False
            try:
                # 先判断连接是否仍处于打开状态
                if self.ws.connected:
                    self.ws.close(status=1000, reason="客户端正常关闭")
                print("【连接关闭】WebSocket已安全关闭")
            except Exception as e:
                print(f"【关闭异常】关闭时出错：{str(e)}")
        else:
            print("【连接关闭】WebSocket已断开或未初始化")

    # ================= 异步包装：适配 asyncio =================
    async def connect_async(self) -> bool:
        """在后台线程执行阻塞式 connect。"""
        return await asyncio.to_thread(self.connect)

    async def start_microphone_async(self, device: int | str | None = None) -> bool:
        """在后台线程执行阻塞式 start_microphone。"""
        return await asyncio.to_thread(self.start_microphone, device)

    async def send_audio_async(self) -> bool:
        """在后台线程执行阻塞式 send_audio（内部循环）。"""
        return await asyncio.to_thread(self.send_audio)

if __name__ == "__main__":
    # 配置日志（过滤冗余信息）
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("websocket").setLevel(logging.WARNING)

    # 1. 从控制台获取密钥信息：https://console.xfyun.cn/services/rta_new
    APP_ID = "12341ff9"
    ACCESS_KEY_ID = "8efdbbb4a5cc840d6b6388e7183774be"
    ACCESS_KEY_SECRET = "N2VkNTJhMzJiODg2YTU1ODcwODMxNTll"
    # 2. 执行核心流程
    client = RTASRClient(APP_ID, ACCESS_KEY_ID, ACCESS_KEY_SECRET)
    try:
        # 建立连接
        if not client.connect():
            print("【程序退出】连接失败")
            exit(1)

        # 启动麦克风
        if not client.start_microphone():
            print("【程序退出】麦克风启动失败")
            exit(1)

        # 阻塞式发送（直到 Ctrl+C 或 stop_event 被设置）
        if not client.send_audio():
            print("【程序退出】音频发送失败")
            exit(1)

        print("【程序结束】实时识别流程完成")
    except KeyboardInterrupt:
        print("\n【程序退出】用户手动中断，正在发送结束标记并关闭...")
        client.stop_event.set()
    finally:
        client.close()
