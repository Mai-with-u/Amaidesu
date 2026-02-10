import asyncio
import math
import random

from ..action_sender import action_sender


class TypingAction:
    def __init__(self):
        self.is_typing = False

    async def send_typing(self):
        await action_sender.send_action("typing", "1")

    async def send_finish_typing(self):
        await action_sender.send_action("typing", "0")

    async def send_typing_action(self):
        if random.random() < 0.5:
            t = 0

            while self.is_typing:
                # 每次循环都为周期和幅度增加轻微抖动
                amplitude = 0.005 + random.uniform(-0.01, 0.01)
                period = 1.2 + random.uniform(-0.6, 0.3)
                base = amplitude * math.sin(t)
                jitter = random.uniform(-0.005, 0.005)
                y = base + jitter
                head_dict = {"x": 0, "y": y, "z": 0}
                await action_sender.send_action("phone_position", head_dict)
                t += period
                await asyncio.sleep(0.1)

            await asyncio.sleep(1)
            await action_sender.send_action("phone_position", {"x": 0, "y": 0, "z": 0})


typing_action = TypingAction()
