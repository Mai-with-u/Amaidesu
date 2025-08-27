import asyncio
import json
import time
from datetime import datetime
from typing import Dict, List, Any, Optional
from langchain_core.tools import BaseTool
from src.utils.logger import get_logger
from ..config import MaicraftConfig
from ..openai_client.llm_request import LLMClient
from ..openai_client.modelconfig import ModelConfig
from .environment_updater import EnvironmentUpdater
from src.plugins.maicraft.agent.environment import global_environment, EnvironmentInfo
from src.plugins.maicraft.agent.prompt_manager.prompt_manager import prompt_manager
from src.plugins.maicraft.agent.prompt_manager.template import init_templates
from src.plugins.maicraft.agent.prompt_manager.template_place import init_templates_place
from .utils import parse_json, convert_mcp_tools_to_openai_format, parse_tool_result, filter_action_tools, extract_between
from .tool_handler import ToolHandlerRegistry, PlaceBlockHandler
from src.plugins.maicraft.agent.to_do_list import ToDoList, ToDoItem
from .memory.position_memory import FurnaceMemory, CraftingTableMemory
from .action.recipe_action import recipe_finder
import traceback
from .nearby_block import NearbyBlockManager
# global_block_cache 在当前文件未直接使用，避免未使用告警
from .block_cache.block_cache_viewer import BlockCacheViewer
from .place_action import PlaceAction
from .move_action import MoveAction
from .utils_tool_translation import (
    translate_move_tool_result, 
    translate_craft_item_tool_result, 
    translate_mine_nearby_tool_result, 
    translate_mine_block_tool_result, 
    translate_place_block_tool_result, 
    translate_chat_tool_result,
)

class ThinkingJsonResult:
    def __init__(self):
        self.result_str = ""
        self.done = False
        self.new_task = ""
        self.new_task_id = ""
        self.progress = ""
        self.rewrite = ""
        self.task_id = ""
        
        
class TaskResult:
    def __init__(self):
        self.status = "rechoice"
        self.message = ""
        self.result = ""
        self.action = ""
        
        self.new_task_id = ""
        self.rewrite = ""

class MaiAgent:
    def __init__(self, config: MaicraftConfig, mcp_client):
        self.config = config
        self.mcp_client = mcp_client
        self.logger = get_logger("MaiAgent")

        # 初始化LLM客户端
        self.llm_client: Optional[LLMClient] = None
        self.llm_client_fast: Optional[LLMClient] = None
        
        
        self.vlm: Optional[LLMClient] = None
        


        # 初始化LLM和工具适配器
        # 延迟初始化
        self.tools: Optional[List[BaseTool]] = None

        # 环境更新器
        self.environment_updater: Optional[EnvironmentUpdater] = None
        
        # 延迟初始化NearbyBlockManager，确保在EnvironmentUpdater启动后再创建
        self.nearby_block_manager: Optional[NearbyBlockManager] = None

        self.place_action: Optional[PlaceAction] = None
        self.move_action: Optional[MoveAction] = None

        # 初始化状态
        self.initialized = False
        
        
        self.goal_list: list[tuple[str, str, str]] = []  # (goal, status, details)

        self.goal = "制作合适的工具，挖到半组铁矿并制作一把铁镐"

        self.memo_list: list[str] = []
        
        self.on_going_task_id = ""
        
        
        self.to_do_list: ToDoList = ToDoList()
        self.task_done_list: list[tuple[bool, str, str]] = []
        
        # 工具处理器注册表
        self.tool_handler_registry: ToolHandlerRegistry = ToolHandlerRegistry()
        self.tool_handler_registry.register(PlaceBlockHandler())
        
        self.furnace_memory: FurnaceMemory = FurnaceMemory()
        self.crafting_table_memory: CraftingTableMemory = CraftingTableMemory()
        
        # 方块缓存预览窗口
        self.block_cache_viewer: Optional[BlockCacheViewer] = None
        self._viewer_started: bool = False
        
        
    async def initialize(self):
        """异步初始化"""
        try:
            self.logger.info("[MaiAgent] 开始初始化")
            
            init_templates()
            init_templates_place()
            # 初始化LLM客户端
            model_config = ModelConfig(
                model_name=self.config.llm.model,
                api_key=self.config.llm.api_key,
                base_url=self.config.llm.base_url,
                max_tokens=self.config.llm.max_tokens,
                temperature=self.config.llm.temperature
            )
            
            
            self.llm_client = LLMClient(model_config)
            
            model_config = ModelConfig(
                model_name=self.config.vlm.model,
                api_key=self.config.vlm.api_key,
                base_url=self.config.vlm.base_url,
                max_tokens=self.config.vlm.max_tokens,
                temperature=self.config.vlm.temperature
            )
            
            self.vlm = LLMClient(model_config)
            global_environment.set_vlm(self.vlm)
            
            
            self.logger.info("[MaiAgent] LLM客户端初始化成功")
            
            
            self.tools = await self.mcp_client.get_tools_metadata()
            self.action_tools = filter_action_tools(self.tools)
            self.logger.info(f"[MaiAgent] 获取到 {len(self.action_tools)} 个可用工具")
            self.openai_tools = convert_mcp_tools_to_openai_format(self.action_tools)

            await self._start_block_cache_viewer()
            
            # 创建并启动环境更新器
            self.environment_updater = EnvironmentUpdater(
                mcp_client = self.mcp_client,
                block_cache_viewer = self.block_cache_viewer,
                update_interval=0.1,  # 默认5秒更新间隔
            )
            
            self.place_action = PlaceAction(self.config)
            self.move_action = MoveAction(self.config)
            # 启动环境更新器
            if self.environment_updater.start():
                self.logger.info("[MaiAgent] 环境更新器启动成功")
            else:
                self.logger.error("[MaiAgent] 环境更新器启动失败")

            
            self.mode = "main_action"
            
            # 初始化NearbyBlockManager
            self.nearby_block_manager = NearbyBlockManager(self.config)
            self.logger.info("[MaiAgent] NearbyBlockManager初始化成功")

            self.initialized = True
            self.logger.info("[MaiAgent] 初始化完成")

            # 启动方块缓存预览窗口（后台，不阻塞事件循环）
            
            
            
            self.inventory_old:List[Any] = []
            
            

        except Exception as e:
            self.logger.error(f"[MaiAgent] 初始化失败: {e}")
            raise
        
        
    async def run_plan_loop(self):
        """
        运行主循环
        """
        while True:            
            await self.propose_all_task(to_do_list=self.to_do_list, environment_info=global_environment.get_summary())
            while True:
                if self.to_do_list.check_if_all_done():
                    self.logger.info(f"[MaiAgent] 所有任务已完成，目标{self.goal}完成")
                    self.to_do_list.clear()
                    self.goal_list.append((self.goal, "done", "所有任务已完成"))
                    break
                await asyncio.sleep(1)
                
    async def run_execute_loop(self):
        """
        运行执行循环
        """
        self.on_going_task_id = ""
        while True:
            if len(self.to_do_list.items) == 0:
                self.logger.info("[MaiAgent] 任务列表为空，等待任务列表更新")
                await asyncio.sleep(1)
                continue

            result = await self.execute_next_task()
            
            if result.status == "done":
                self.logger.info(f"[MaiAgent] 任务执行成功: {result.message}")
                self.task_done_list.append((True,self.on_going_task_id,"任务完成"))
                self.on_going_task_id = ""
            elif result.status == "new_task":
                self.logger.info(f"[MaiAgent] 创建新任务: {result.message}")
                self.task_done_list.append((True,self.on_going_task_id,"任务未完成，需要完成前置任务"))
            elif result.status == "change":
                self.logger.info(f"[MaiAgent] 更换任务: {result.message}")
                self.task_done_list.append((True,self.on_going_task_id,f"任务未完成，更换到任务{result.new_task_id}"))
            elif result.status == "rewrite":
                self.logger.info(f"[MaiAgent] 任务列表修改: {result.message}")
                self.task_done_list.append((True,self.on_going_task_id,"修改了任务列表"))
                await self.rewrite_all_task(
                    suggestion=result.rewrite
                )
            
                
            
    async def rewrite_all_task(self, suggestion: str):
        self.logger.info("[MaiAgent] 开始修改任务")
        # 使用原有的提示词模板，但通过call_tool传入工具
        await self.environment_updater.perform_update()
        nearby_block_info = await self.nearby_block_manager.get_block_details_mix_str(global_environment.block_position)
        input_data = {
            "goal": self.goal,
            "environment": global_environment.get_summary(),
            "to_do_list": self.to_do_list.__str__(),
            "suggestion": suggestion,
            "position": global_environment.get_position_str(),
            "nearby_block_info": nearby_block_info,
        }
        prompt = prompt_manager.generate_prompt("minecraft_rewrite_task", **input_data)
        self.logger.info(f"[MaiAgent] 任务修改提示词: {prompt}")
        
        response = await self.llm_client.simple_chat(prompt)
        self.logger.info(f"[MaiAgent] 任务修改响应: {response}")
        
        tasks_list = parse_json(response)
        if tasks_list.get("tasks",""):
            tasks_list = tasks_list.get("tasks",[])
        
        self.to_do_list.clear()

        for task in tasks_list:
            try:
                details = str(task.get("details", "")).strip()
                done_criteria = str(task.get("done_criteria", "")).strip()
                self.to_do_list.add_task(details=details, done_criteria=done_criteria)
            except Exception as e:
                self.logger.error(f"[MaiAgent] 处理任务时异常: {e}，任务内容: {task}")
                continue
            

        

    
    async def propose_all_task(self, to_do_list: ToDoList, environment_info: str) -> bool:
        self.logger.info("[MaiAgent] 开始提议任务")
        # 使用原有的提示词模板，但通过call_tool传入工具
        await self.environment_updater.perform_update()
        nearby_block_info = await self.nearby_block_manager.get_block_details_mix_str(global_environment.block_position)
        input_data = {
            "goal": self.goal,
            "environment": environment_info,
            "to_do_list": to_do_list.__str__(),
            "nearby_block_info": nearby_block_info,
            "position": global_environment.get_position_str(),
        }
        prompt = prompt_manager.generate_prompt("minecraft_to_do", **input_data)
        self.logger.info(f"[MaiAgent] 任务提议提示词: {prompt}")
        
        response = await self.llm_client.simple_chat(prompt)
        self.logger.info(f"[MaiAgent] 任务提议响应: {response}")
        
        tasks_list = parse_json(response)
        if tasks_list.get("tasks",""):
            tasks_list = tasks_list.get("tasks",[])
        
        for task in tasks_list:
            try:
                details = str(task.get("details", "")).strip()
                done_criteria = str(task.get("done_criteria", "")).strip()
                self.to_do_list.add_task(details=details, done_criteria=done_criteria)
            except Exception as e:
                self.logger.error(f"[MaiAgent] 处理任务时异常: {e}，任务内容: {task}")
                continue
        
        return True

    async def _start_block_cache_viewer(self) -> None:
        """以后台线程启动方块缓存预览窗口。"""
        if self._viewer_started:
            return
        try:
            self.block_cache_viewer = BlockCacheViewer(update_interval_seconds=0.6)
            # 使用 asyncio.to_thread 符合用户偏好，避免阻塞事件循环
            import asyncio
            asyncio.create_task(self.block_cache_viewer.run_loop())
            asyncio.create_task(asyncio.to_thread(self.block_cache_viewer.run))
            self._viewer_started = True
            self.logger.info("[MaiAgent] 方块缓存预览窗口已启动（每3秒刷新）")
        except Exception as e:
            self.logger.error(f"[MaiAgent] 启动方块缓存预览窗口失败: {e}")

    
    def _format_task_done_list(self) -> str:
        """将任务执行记录翻译成可读文本，只取最近10条。

        任务记录为 (success: bool, task_id: str, message: str)
        """
        if not self.task_done_list:
            return "暂无任务执行记录"

        lines: list[str] = []
        # 仅取最近10条
        for success, task_id, message in self.task_done_list[-10:]:
            status_text = "成功" if success else "失败"
            # 规避 None/空值
            safe_task_id = str(task_id) if task_id is not None else ""
            safe_message = str(message) if message is not None else ""
            lines.append(f"任务ID {safe_task_id}：{status_text}（{safe_message}）")

        return "\n".join(lines)


    async def execute_next_task(self) -> TaskResult:
        """
        执行目标
        返回: (执行结果, 执行状态)
        """
        try:
            task_result = TaskResult()
            
            
            if not self.on_going_task_id:
                self.mode = "task_action"
                task = None
            else:
                task = self.to_do_list.get_task_by_id(self.on_going_task_id)
                if task is None:
                    task_result.status = "rechoice"
                    task_result.message = f"任务不存在: {self.on_going_task_id}"
                    return task_result
                else:
                    self.mode = "main_action"
            
            thinking_list: List[str] = []
            
            # 限制思考上下文
            while True:
                if len(thinking_list) > 20:
                    thinking_list = thinking_list[-20:]
                    
                if task:
                    self.logger.info(f"[MaiAgent]\n执行任务:\n {task} \n尝试")
                else:
                    self.logger.info(f"[MaiAgent] 没有任务，开始选择任务")
                
                # 获取当前环境信息
                await self.environment_updater.perform_update()
                environment_info = global_environment.get_summary()
                nearby_block_info = await self.nearby_block_manager.get_block_details_mix_str(global_environment.block_position)
                # executed_tools_str = self._format_executed_tools_history(executed_tools_history[-10:]) if executed_tools_history else ""
                
                # 使用原有的提示词模板，但通过call_tool传入工具
                input_data = {
                    "task": task.__str__(),
                    "environment": environment_info,
                    # "executed_tools": executed_tools_str,
                    "thinking_list": "\n".join(thinking_list),
                    "nearby_block_info": nearby_block_info,
                    "position": global_environment.get_position_str(),
                    "memo_list": "\n".join(self.memo_list),
                }
                
                
                if self.mode == "main_action":
                    prompt = prompt_manager.generate_prompt("minecraft_excute_task_thinking", **input_data)
                    self.logger.info(f"[MaiAgent] 执行任务提示词: {prompt}")
                elif self.mode == "task_action":
                    input_data["to_do_list"] = self.to_do_list.__str__()
                    input_data["task_done_list"] = self._format_task_done_list()
                    input_data["goal"] = self.goal
                    prompt = prompt_manager.generate_prompt("minecraft_excute_task_action", **input_data)
                    self.logger.info(f"\033[38;5;153m[MaiAgent] 执行任务提示词: {prompt}\033[0m")
                elif self.mode == "move_action":
                    prompt = prompt_manager.generate_prompt("minecraft_excute_move_action", **input_data)
                    self.logger.info(f"\033[38;5;208m[MaiAgent] 执行任务提示词: {prompt}\033[0m")
                elif self.mode == "craft_action":
                    prompt = prompt_manager.generate_prompt("minecraft_excute_craft_action", **input_data)
                    
                    
                
                
                
                thinking = await self.llm_client.simple_chat(prompt)
                # self.logger.info(f"[MaiAgent] 任务想法: {thinking}")
                
                json_obj, json_before = await self.parse_thinking(thinking, task)
                
                time_str = time.strftime("%H:%M:%S", time.localtime())
                
                if json_obj:
                    self.logger.info(f"\033[32m[MaiAgent] 想法: {json_before}\033[0m")
                    self.logger.info(f"\033[32m[MaiAgent] 动作: {json_obj}\033[0m")
                    
                    if json_before:
                        thinking_list.append(f"时间：{time_str} 思考结果：{json_before}")
                    
                    result = await self.excute_json(json_obj)
                    
                    thinking_list.append(f"时间：{time_str} 执行结果：{result.result_str}")
                    if result.done:
                        update_task = self.to_do_list.get_task_by_id(result.task_id)
                        if update_task:
                            update_task.done = result.done
                            update_task.progress = result.progress
                            task_result.status = "done"
                            task_result.message = f"任务执行成功: {update_task.__str__()}"
                            return task_result
                    elif result.new_task:
                        task_result.status = "new_task"
                        task_result.message = f"新任务: {result.new_task}"
                        return task_result
                    elif result.progress:
                        task.progress = result.progress
                    elif result.new_task_id:
                        task_result.status = "change"
                        task_result.message = f"当前任务未完成，切换到任务: {result.new_task_id}"
                        task_result.new_task_id = result.new_task_id
                        return task_result
                    elif result.rewrite:
                        task_result.status = "rewrite"
                        task_result.message = f"修改任务列表: {result.rewrite}"
                        task_result.rewrite = result.rewrite
                        return task_result
                        
                        
                else:
                    self.logger.info(f"[MaiAgent] 想法:{json_before}")
                    if json_before:
                        thinking_list.append(f"时间：{time_str} 思考结果：{json_before}")
                    

        except Exception as e:
            self.logger.error(f"[MaiAgent] 任务执行异常: {traceback.format_exc()}")
            task_result.status = "rechoice"
            task_result.message = f"任务执行异常: {str(e)}"
            return task_result

    async def excute_json(self, json_obj: dict) -> ThinkingJsonResult:
        """
        执行json
        返回: ThinkingJsonResult
        """
        result = ThinkingJsonResult()
        
        if self.mode == "move_action":
            x=json_obj.get("x")
            y=json_obj.get("y")
            z=json_obj.get("z")
            args = {"x": x, "y": y, "z": z, "type": "coordinate"}
            result.result_str = f"尝试移动到：{x},{y},{z}\n"
            
            call_result = await self.mcp_client.call_tool_directly("move", args)
            is_success, result_content = await self.parse_action_result("move", args, call_result)
            result.result_str += translate_move_tool_result(result_content, args)

            self.mode = "main_action"
            return result
        
        
        action_type = json_obj.get("action_type")
        if action_type == "move":
            self.mode = "move_action"
            reason = json_obj.get("reason")
            result.result_str = f"选择进行移动动作: \n原因: {reason}\n"            
            return result
        
        elif action_type == "abandon_craft":
            reason = json_obj.get("reason")
            result.result_str = f"放弃合成: \n原因: {reason}\n"
            self.mode = "main_action"
            return result
        
        elif action_type == "craft":
            reason = json_obj.get("reason")
            item = json_obj.get("item")
            count = json_obj.get("count")
            self.inventory_old = global_environment.inventory
            
            ok, summary = await recipe_finder.craft_item_smart(item, count, global_environment.inventory, global_environment.block_position)
            if ok:
                result.result_str = f"合成成功：{item} x{count}\n{summary}\n"
            else:
                result.result_str = f"合成未完成：{item} x{count}\n{summary}\n"
            return result
        
            

        elif action_type == "mine_block":
            x = json_obj.get("x")
            y = json_obj.get("y")
            z = json_obj.get("z")
            args = {"x": x, "y": y, "z": z}
            result.result_str = f"尝试挖掘位置：{x},{y},{z}\n"
            call_result = await self.mcp_client.call_tool_directly("mine_block", args)
            is_success, result_content = await self.parse_action_result(action_type, args, call_result)
            if is_success:
                result.result_str += translate_mine_block_tool_result(result_content)
            else:
                result.result_str += f"挖掘失败: {result_content}"
        elif action_type == "mine_nearby":
            name = json_obj.get("name")
            count = json_obj.get("count")
            args = {"name": name, "count": count}
            result.result_str = f"尝试采集：{name} 数量：{count}\n"
            call_result = await self.mcp_client.call_tool_directly("mine_block", args)
            is_success, result_content = await self.parse_action_result(action_type, args, call_result)
            if is_success:
                result.result_str += translate_mine_nearby_tool_result(result_content)
            else:
                result.result_str += f"采集失败: {result_content}"
        elif action_type == "place_block":
            block = json_obj.get("block")
            x = json_obj.get("x")
            y = json_obj.get("y")
            z = json_obj.get("z")
            
            result_str, args = await self.place_action.place_action(block, x, y, z)            
            result.result_str = result_str

            if not args:
                return result
            
            call_result = await self.mcp_client.call_tool_directly("place_block", args)
            is_success, result_content = await self.parse_action_result(action_type, args, call_result)
            result.result_str += translate_place_block_tool_result(result_content,args)

        elif action_type == "chat":
            message = json_obj.get("message")
            args = {"message": message}
            call_result = await self.mcp_client.call_tool_directly("chat", args)
            is_success, result_content = await self.parse_action_result(action_type, args, call_result)
            if is_success:
                result.result_str = translate_chat_tool_result(result_content)
            else:
                result.result_str = f"聊天失败: {result_content}"
        elif action_type == "get_recipe":
            item = json_obj.get("item")
            result.result_str = f"尝试查询：{item} 的合成表：\n"
            recipe_str = await recipe_finder.find_recipe(item)
            result.result_str += recipe_str
        elif action_type == "add_memo":
            memo = json_obj.get("memo")
            result.result_str = f"添加备忘录: {memo}\n"
            self.memo_list.append(memo)
        # 任务动作
        elif action_type == "update_task_list":
            self.mode = "task_action"
            reason = json_obj.get("reason")
            result.result_str = f"选择进行修改任务列表: \n原因: {reason}\n"
            return result
        elif action_type == "change_task":
            new_task_id = json_obj.get("new_task_id")
            reason  = json_obj.get("reason")
            result.new_task_id = new_task_id
            result.result_str = f"选择更换任务: {new_task_id},原因是: {reason}\n"
            self.on_going_task_id = new_task_id
            return result
        elif action_type == "rewrite_task_list":
            reason = json_obj.get("reason")
            result.rewrite = reason
            result.result_str = f"选择修改任务列表: \n原因: {reason}\n"
            self.mode = "task_action"
            self.on_going_task_id = ""
            return result
        elif action_type == "update_task_progress":
            progress = json_obj.get("progress")
            done = json_obj.get("done")
            task_id = json_obj.get("task_id")
            
            result.task_id = task_id
            
            if done:
                result.done = done
                result.progress = progress
                result.result_str = "任务已完成"
                self.mode = "main_action"
                return result
            result.result_str = f"任务进度已更新: {progress}"
            result.progress = progress
            self.mode = "main_action"
        elif action_type == "create_new_task":
            new_task = json_obj.get("new_task")
            new_task_criteria = json_obj.get("new_task_criteria")
            
            # 创建并执行新任务
            new_task_item = self.to_do_list.add_task(new_task, new_task_criteria)
            self.on_going_task_id = new_task_item.id
            self.mode = "main_action"
        else:
            self.logger.warning(f"[MaiAgent] 不支持的action_type: {action_type}")
        
        return result
    
    async def parse_action_result(self, action_type: str, arguments: dict, result: str) -> tuple[bool, str]:
        """
        解析动作执行结果
        返回: 动作执行结果
        """
        handler = self.tool_handler_registry.find(action_type)
        if handler is not None:
            result = await handler.post_process(
                tool_name=action_type,
                arguments=arguments,
                result=result,
                environment=global_environment,
                logger=self.logger,
            )
        
        # 解析工具执行结果，判断是否真的成功
        is_success, result_content = parse_tool_result(result)
        return is_success, result_content
    
    async def parse_thinking(self, thinking: str, task: 'ToDoItem') -> tuple[str, str, dict, str]:
        """
        解析思考结果
        1. 先解析thinking中有没有json，如果有，就获取第一个json的完整内容
        2. 拆分出第一个json前所有内容
        返回: (是否成功, 思考结果, 第一个json对象, json前内容)
        """
        # 匹配第一个json对象（支持嵌套大括号）
        def find_first_json(text):
            stack = []
            start = None
            for i, c in enumerate(text):
                if c == '{':
                    if not stack:
                        start = i
                    stack.append('{')
                elif c == '}':
                    if stack:
                        stack.pop()
                        if not stack and start is not None:
                            return text[start:i+1], start, i+1
            return None, None, None

        json_obj = None
        json_str, json_start, json_end = find_first_json(thinking)
        json_before = ""
        if json_str:
            json_before = thinking[:json_start].strip()
            try:
                json_obj = parse_json(json_str)
            except Exception:
                self.logger.error(f"[MaiAgent] 解析思考结果时异常: {json_str}")
        else:
            json_before = thinking.strip()

        # 移除json_before中的 ```json 和 ```
        if "```json" in json_before:
            json_before = json_before.replace("```json", "")
        if "```" in json_before:
            json_before = json_before.replace("```", "")
        
        
        return json_obj, json_before