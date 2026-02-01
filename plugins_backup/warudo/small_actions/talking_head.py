from ..action_sender import action_sender
import random
import asyncio
import math


class TalkingHead:
    def __init__(self):
        self.is_talking = False

    async def send_random_head_action(self):
        t = 0
        while self.is_talking:
            # 每次循环都为周期和幅度增加轻微抖动
            amplitude = 0.8 + random.uniform(-0.02, 0.02)
            period = 0.2 + random.uniform(-0.06, 0.1)
            base = amplitude * math.sin(t)
            jitter = random.uniform(-0.03, 0.08)
            y = base + jitter
            head_dict = {"x": 0, "y": y, "z": 0}
            await action_sender.send_action("head_action", head_dict)
            t += period
            await asyncio.sleep(0.1)

        await asyncio.sleep(1)
        await action_sender.send_action("head_action", {"x": 0, "y": 0, "z": 0})


talking_head = TalkingHead()
