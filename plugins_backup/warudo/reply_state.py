import asyncio
import logging

from .action_sender import action_sender
from .mai_state import WarudoStateManager
from .small_actions.talking_head import talking_head
from .small_actions.throw_fish import throw_fish
from .small_actions.typing_action import typing_action


class ReplyState:
    def __init__(self, state_manager: WarudoStateManager, logger: logging.Logger):
        self.state_manager = state_manager
        self.logger = logger

        self.is_thinking = False
        self.is_internal_thinking = False
        self.is_replying = False
        self.is_viewing = False
        self.is_talking = False

        self.is_typing = False

    async def deal_state(self, state: str):
        if state == "start_thinking":
            self.is_thinking = True
        if state == "finish_thinking":
            self.is_thinking = False
        if state == "start_replying":
            self.is_replying = True

        if state == "start_viewing":
            if not self.is_replying and not self.is_talking:
                await self.send_loading()
                self.state_manager.sight_state.set_state("danmu", 1.0)

            asyncio.create_task(throw_fish.throw_fish())

        if state == "start_internal_thinking":
            self.is_thinking = True
            self.is_internal_thinking = True
            await self.send_loading()
            self.state_manager.sight_state.set_state("phone", 1.0)

        if state == "typing":
            self.is_typing = True
            typing_action.is_typing = True
            asyncio.create_task(typing_action.send_typing_action())

        if state == "stop_typing":
            self.is_typing = False
            typing_action.is_typing = False

    async def start_talking(self):
        self.is_talking = True
        await self.send_unloading()

        self.state_manager.sight_state.set_state("camera", 1.0)

        talking_head.is_talking = True
        asyncio.create_task(talking_head.send_random_head_action())

    async def stop_talking(self):
        self.is_talking = False
        talking_head.is_talking = False

    async def send_loading(self):
        await action_sender.send_action("loading", "......")

    async def send_unloading(self):
        await action_sender.send_action("loading", "")
