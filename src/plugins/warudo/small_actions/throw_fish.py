from ..action_sender import action_sender
import asyncio
import time
import random


class ThrowFish:
    def __init__(self):

        self.last_throw_time = 0
        
    async def throw_fish(self):
        
        if time.time() - self.last_throw_time > 5:
            random_num = random.randint(1, 5)
            if random_num == 5:
                await action_sender.send_action("throw_fish", random_num)
            else:
                await action_sender.send_action("throw_fish", 1)
            self.last_throw_time = time.time()

throw_fish = ThrowFish()