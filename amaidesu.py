from src.spark_asr.rtasr_client import RTASRClient
from src.openai_client.modelconfig import ModelConfig
from src.config.config import global_config
from src.utils.logger import get_logger
from src.openai_client.llm_request import LLMClient
from src.prompt_manager.prompt_manager import prompt_manager
from typing import List,Tuple
import asyncio
import traceback
from src.prompt_manager.template import init_templates
import time
logger = get_logger("LiveMai")

init_templates()

class LiveMai():
    def __init__(self):
        self.rtasr_client = RTASRClient(
            global_config.spark_rtasr.app_id,
            global_config.spark_rtasr.access_key_id,
            global_config.spark_rtasr.access_key_secret,
        )

        self.model_config = ModelConfig(
            global_config.llm.model,
            global_config.llm.api_key,
            global_config.llm.base_url,
            global_config.llm.max_tokens,
            global_config.llm.temperature,
        )
        print(global_config.llm.model)

        self.llm_client = LLMClient(
            self.model_config
        )

        self.chat_histroy:List[Tuple[str, str,str]] = []

        self.thinking = False
        self.talking = False

        # 流式回复控制：保存当前流任务与打断事件
        self.current_response_task = None
        self.current_stop_event = None
        self.response_lock = asyncio.Lock()

    async def on_result(self, text, is_final, msg_json):
        # 规则：
        # - 若当前未在生成 (self.thinking=False)：仅当 is_final=True 时触发生成
        # - 若当前正在生成 (self.thinking=True)：收到任何文本先打断；仅当 is_final=True 时才重启生成
        try:
            if not self.thinking:
                if is_final:
                    asyncio.create_task(self.response(text))
            else:
                # 正在生成中：先打断当前流
                if self.current_stop_event is not None and not self.current_stop_event.is_set():
                    self.current_stop_event.set()
                    self.thinking = False
                # 仅在最终分段时重启生成
                if is_final:
                    asyncio.create_task(self.response(text))
        except Exception as e:
            logger.error(f"触发回复失败: {e}")
            
    
    async def response(self, text):
        # 串行化响应启动与打断，避免竞态
        logger.info(f"开始回复: {text}")
        async with self.response_lock:
            # 打断正在进行的流
            if self.current_stop_event is not None:
                self.current_stop_event.set()

            # 新的 stop 事件
            stop_event = asyncio.Event()
            self.current_stop_event = stop_event

            # 记录用户输入
            self.add_chat_histroy(text, "用户", time.time())

            # 构建提示词
            dialogue_prompt = self.build_chat_string()
            reply_target_block = f"你想要回复用户说的: {text}"
            time_block = f"当前时间：{time.strftime('%H:%M:%S', time.localtime(time.time()))}"
            reply_template = prompt_manager.generate_prompt(
                "reply_template",
                time_block=time_block,
                dialogue_prompt=dialogue_prompt,
                reply_target_block=reply_target_block,
            )

            # 开启新的流式生成任务
            self.current_response_task = asyncio.create_task(
                self._run_stream_reply(reply_template, text, stop_event)
            )

        return ""  # 实际完整回复在 _run_stream_reply 内聚合与记录

    async def _run_stream_reply(self, prompt, source_text, stop_event: asyncio.Event):
        response_acc = []
        saved_full_response = False
        try:
            self.thinking = True
            async for piece in self.llm_client.simple_stream(prompt, stop_event=stop_event):
                # 打印或逐步处理增量
                response_acc.append(piece)
                # 可根据需要在此处做实时输出/播报
            full_response = "".join(response_acc)
            logger.info(f"回复用户的:{source_text}|回复内容:{full_response}")
            # 将最终回复写入历史
            self.add_chat_histroy(full_response, "麦麦", time.time())
            saved_full_response = True
            return full_response
        except Exception as e:
            if stop_event.is_set():
                # 被正常打断，无需报错
                return ""
            logger.error(f"流式回复失败: {e}")
            traceback.print_exc()
            return ""
        finally:
            self.thinking = False
            # 若未正常完成但已有增量，保存被打断/异常的片段
            if not saved_full_response and response_acc:
                partial_response = "".join(response_acc)
                logger.info(f"回复被打断，已保存片段:{partial_response}")
                self.add_chat_histroy(f"想法：{partial_response}（思考被对方发言中断）", "麦麦", time.time())
            
    
    def add_chat_histroy(self, text, role, time):
        self.chat_histroy.append((text, role, time))

    def build_chat_string(self):
        dialogue_prompt = ""
        for msg in self.chat_histroy:
            time_str = time.strftime("%H:%M:%S", time.localtime(msg[2]))
            dialogue_prompt += f"{time_str}: {msg[1]}\n{msg[0]}\n"
        return dialogue_prompt

    async def start_async(self):
        try:
            # 结果回调与回调所在事件循环
            self.rtasr_client.on_result = self.on_result
            self.rtasr_client.set_result_loop(asyncio.get_running_loop())

            ok = await self.rtasr_client.connect_async()
            if not ok:
                raise RuntimeError("ASR 连接失败")

            ok = await self.rtasr_client.start_microphone_async()
            if not ok:
                raise RuntimeError("麦克风启动失败")

            # 发音频循环放后台任务，避免阻塞
            asyncio.create_task(self.rtasr_client.send_audio_async())
        except Exception as e:
            logger.error(f"启动失败: {e}")
            raise e

    async def run(self):
        await self.start_async()
        # 主任务保持运行，直到外部打断；这里简单等待 stop_event
        try:
            while not self.rtasr_client.stop_event.is_set():
                await asyncio.sleep(0.1)
        finally:
            self.stop()

    def stop(self):
        self.rtasr_client.stop_microphone()
        self.rtasr_client.close()


async def main():
    live_mai = LiveMai()
    await live_mai.run()

if __name__ == "__main__":
    asyncio.run(main())