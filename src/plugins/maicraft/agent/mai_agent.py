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
from src.plugins.maicraft.agent.environment import global_environment
from src.plugins.maicraft.agent.prompt_manager.prompt_manager import prompt_manager
from src.plugins.maicraft.agent.prompt_manager.template import init_templates
from src.plugins.maicraft.agent.prompt_manager.template_place import init_templates_place
from .utils import parse_json, convert_mcp_tools_to_openai_format, parse_tool_result, filter_action_tools, format_executed_goals, extract_between
from .tool_handler import ToolHandlerRegistry, PlaceBlockHandler
from .to_do_list import ToDoList, ToDoItem
from .memory.position_memory import FurnaceMemory, CraftingTableMemory
from .action.recipe_action import recipe_finder
import traceback
from .nearby_block import NearbyBlockManager
from src.plugins.maicraft.agent.block_cache.block_cache import global_block_cache
from .block_cache.block_cache_viewer import BlockCacheViewer
from .place_action import PlaceAction
from .move_action import MoveAction

class ThinkingJsonResult:
    def __init__(self):
        self.result_str = ""
        self.done = False
        self.new_task = ""
        self.new_task_criteria = ""
        self.progress = ""


class MaiAgent:
    def __init__(self, config: MaicraftConfig, mcp_client):
        self.config = config
        self.mcp_client = mcp_client
        self.logger = get_logger("MaiAgent")

        # 初始化LLM客户端
        self.llm_client: Optional[LLMClient] = None
        self.llm_client_fast: Optional[LLMClient] = None

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
            self.logger.info("[MaiAgent] LLM客户端初始化成功")
            
            
            self.tools = await self.mcp_client.get_tools_metadata()
            self.action_tools = filter_action_tools(self.tools)
            self.logger.info(f"[MaiAgent] 获取到 {len(self.action_tools)} 个可用工具")
            self.openai_tools = convert_mcp_tools_to_openai_format(self.action_tools)

            # 创建并启动环境更新器
            self.environment_updater = EnvironmentUpdater(
                mcp_client = self.mcp_client,
                update_interval=0.2,  # 默认5秒更新间隔
            )
            
            self.place_action = PlaceAction(self.config)
            self.move_action = MoveAction(self.config)
            # 启动环境更新器
            if self.environment_updater.start():
                self.logger.info("[MaiAgent] 环境更新器启动成功")
            else:
                self.logger.error("[MaiAgent] 环境更新器启动失败")

            # 初始化NearbyBlockManager
            self.nearby_block_manager = NearbyBlockManager(self.config)
            self.logger.info("[MaiAgent] NearbyBlockManager初始化成功")

            self.initialized = True
            self.logger.info("[MaiAgent] 初始化完成")

            # 启动方块缓存预览窗口（后台，不阻塞事件循环）
            await self._start_block_cache_viewer()
            
            

        except Exception as e:
            self.logger.error(f"[MaiAgent] 初始化失败: {e}")
            raise
        
        
    async def run_plan_loop(self):
        """
        运行主循环
        """
        while True:            
            goal = "放置背包里的方块，建造一个仓库"
            
            await self.propose_all_task(goal=goal, to_do_list=self.to_do_list, environment_info=global_environment.get_summary())
            while True:
                if self.to_do_list.check_if_all_done():
                    self.logger.info(f"[MaiAgent] 所有任务已完成，目标{goal}完成")
                    self.to_do_list.clear()
                    self.goal_list.append((goal, "done", "所有任务已完成"))
                    break
                
                if self.to_do_list.need_edit:
                    self.logger.info(f"[MaiAgent] 任务列表需要修改: {self.to_do_list.need_edit}")
                    await self.rewrite_all_task(
                        goal=goal, 
                        to_do_list=self.to_do_list, 
                        environment_info=global_environment.get_summary(),
                        suggestion=self.to_do_list.need_edit
                    )
                    
                    
                await asyncio.sleep(1)
                
    async def run_execute_loop(self):
        """
        运行执行循环
        """
        while True:
            if len(self.to_do_list.items) == 0 or self.to_do_list.need_edit:
                await asyncio.sleep(1)
                continue
            
            
            choose_success, choose_result, details = await self.choose_next_task(to_do_list=self.to_do_list, environment_info=global_environment.get_summary())
            if not choose_success:            
                if choose_result == "edit":
                    self.logger.info(f"[MaiAgent] 任务列表需要修改: {details}")
                    self.to_do_list.need_edit = details
                else:
                    self.logger.error(f"[MaiAgent] 任务选择失败: {choose_result}")
                    await asyncio.sleep(5)
                    continue
            
            if choose_result == "done":
                task = self.to_do_list.get_task_by_id(details)
                if task is not None:
                    task.done = True
                    task.details = f"任务{details}已完成"
                    self.logger.info(f"[MaiAgent] 任务{details}已完成")
                    continue
                continue
            
            if choose_result == "do":
                
                success, result = await self.execute_next_task(details)
                if not success:
                    self.logger.error(f"[MaiAgent] 任务执行失败: {result}")
                    self.task_done_list.append((False,details,result))
                    continue
                self.logger.info(f"[MaiAgent] 任务执行成功: {result}")
                self.task_done_list.append((True,details,"任务完成"))
            
    async def rewrite_all_task(self, goal: str, to_do_list: ToDoList, environment_info: str, suggestion: str):
        self.logger.info("[MaiAgent] 开始修改任务")
        # 使用原有的提示词模板，但通过call_tool传入工具
        await self.environment_updater.perform_update()
        nearby_block_info = await self.nearby_block_manager.get_block_details_mix_str(global_environment.block_position, "")
        input_data = {
            "goal": goal,
            "environment": environment_info,
            "to_do_list": to_do_list.__str__(),
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
        

        self.to_do_list.need_edit = ""
        self.to_do_list.clear()

        for task in tasks_list:
            try:
                details = str(task.get("details", "")).strip()
                done_criteria = str(task.get("done_criteria", "")).strip()
                self.to_do_list.add_task(details=details, done_criteria=done_criteria)
            except Exception as e:
                self.logger.error(f"[MaiAgent] 处理任务时异常: {e}，任务内容: {task}")
                continue
            

        

    
    async def propose_all_task(self, goal: str, to_do_list: ToDoList, environment_info: str) -> bool:
        self.logger.info("[MaiAgent] 开始提议任务")
        # 使用原有的提示词模板，但通过call_tool传入工具
        await self.environment_updater.perform_update()
        nearby_block_info = await self.nearby_block_manager.get_block_details_mix_str(global_environment.block_position, "")
        input_data = {
            "goal": goal,
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
            self.block_cache_viewer = BlockCacheViewer(update_interval_seconds=0.5)
            # 使用 asyncio.to_thread 符合用户偏好，避免阻塞事件循环
            import asyncio
            asyncio.create_task(asyncio.to_thread(self.block_cache_viewer.run))
            self._viewer_started = True
            self.logger.info("[MaiAgent] 方块缓存预览窗口已启动（每3秒刷新）")
        except Exception as e:
            self.logger.error(f"[MaiAgent] 启动方块缓存预览窗口失败: {e}")

    async def choose_next_task(self, to_do_list: ToDoList, environment_info: str):
        """
        执行目标
        返回: (执行结果, 执行状态)
        """
        try:
            await self.environment_updater.perform_update()
            nearby_block_info = await self.nearby_block_manager.get_block_details_mix_str(global_environment.block_position, "")
            
            input_data = {
                "to_do_list": to_do_list.__str__(),
                "environment": environment_info,
                "position": global_environment.get_position_str(),
                "task_done_list": self._format_task_done_list(),
                "nearby_block_info": nearby_block_info,
            }
            prompt = prompt_manager.generate_prompt("minecraft_choose_task", **input_data)
            # self.logger.info(f"[MaiAgent] 任务选择提示词: {prompt}")
            
            response = await self.llm_client.simple_chat(prompt)
            self.logger.info(f"[MaiAgent] 任务选择响应: {response}")

            # 解析响应，支持形如：
            # <修改：修改的原因>
            # <执行：执行的任务id>
            text = str(response) if response is not None else ""

            # 优先判断修改
            modification_reason = extract_between(text, "<修改：", "<修改:")
            if modification_reason is None and "<修改>" in text:
                modification_reason = ""
            if modification_reason is not None or "<修改>" in text:
                reason = modification_reason if modification_reason is not None else ""
                return False, "edit", reason

            # 判断执行
            execute_task_id = extract_between(text, "<执行：", "<执行:")
            if execute_task_id is None and "<执行>" in text:
                execute_task_id = ""
            if execute_task_id is not None or "<执行>" in text:
                task_id = execute_task_id if execute_task_id is not None else ""
                return True, "do", task_id
                
            # 判断完成
            done_task_id = extract_between(text, "<完成：", "<完成:")
            if done_task_id is None and "<完成>" in text:
                done_task_id = ""
            if done_task_id is not None or "<完成>" in text:
                task_id = done_task_id if done_task_id is not None else ""
                return True, "done", task_id

            # 兜底：无法解析
            self.logger.warning(f"[MaiAgent] 无法从响应中解析任务选择结果: {text}")
            return False, "edit", "无法解析任务选择结果"
        except Exception as e:
            self.logger.error(f"[MaiAgent] 任务选择解析异常: {e}")
            return False, "edit", f"解析异常: {str(e)}"


    
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


    async def execute_next_task(self, id: str) -> tuple[bool, str]:
        """
        执行目标
        返回: (执行结果, 执行状态)
        """
        try:
            task = self.to_do_list.get_task_by_id(id)
            if task is None:
                # 尝试从id中提取数字，再次查询
                import re
                self.logger.error(f"[MaiAgent] 任务不存在: {id}，尝试提取数字后再次查询")
                match = re.search(r'\d+', str(id))
                if match:
                    new_id = match.group(0)
                    task = self.to_do_list.get_task_by_id(new_id)
                    if task is not None:
                        self.logger.info(f"[MaiAgent] 通过提取数字找到任务: {new_id}")
                    else:
                        self.logger.error(f"[MaiAgent] 通过提取数字后仍未找到任务: {new_id}")
                        return False, "任务不存在"
                else:
                    return False, "任务不存在"
            
            # 记录工具执行历史
            executed_tools_history = []
            done = False
            attempt = 0
            max_attempts = 20
            
            thinking_list: List[str] = []
            
            while not done and attempt < max_attempts:
                if len(thinking_list) > 10:
                    thinking_list = thinking_list[-10:]
                attempt += 1
                self.logger.info(f"[MaiAgent]\n执行任务:\n {task} \n尝试 {attempt}/{max_attempts}")
                
                # 获取当前环境信息
                await self.environment_updater.perform_update()
                environment_info = global_environment.get_summary()
                # 取最新两条思考记录
                nearby_block_info = await self.nearby_block_manager.get_block_details_mix_str(global_environment.block_position, thinking_list[-1:])
                
                # 只取最新加入的10条已执行工具历史
                executed_tools_str = self._format_executed_tools_history(executed_tools_history[-10:]) if executed_tools_history else ""
                
                # 使用原有的提示词模板，但通过call_tool传入工具
                input_data = {
                    "task": task.__str__(),
                    "environment": environment_info,
                    "executed_tools": executed_tools_str,
                    "thinking_list": "\n".join(thinking_list),
                    "nearby_block_info": nearby_block_info,
                    "position": global_environment.get_position_str(),
                }
                prompt = prompt_manager.generate_prompt("minecraft_excute_task_thinking", **input_data)
                self.logger.info(f"[MaiAgent] 执行任务提示词: {prompt}")
                
                
                thinking = await self.llm_client.simple_chat(prompt)
                # self.logger.info(f"[MaiAgent] 任务想法: {thinking}")
                
                json_obj, json_before = await self.parse_thinking(thinking, task)
                
                time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                
                if json_obj:
                    self.logger.info(f"[MaiAgent] 想法: {json_before}")
                    self.logger.info(f"[MaiAgent] 动作: {json_obj}")
                    
                    thinking_list.append(f"时间：{time_str} 思考结果：{json_before}")
                    
                    result = await self.excute_json(json_obj)
                    
                    thinking_list.append(f"时间：{time_str} 执行结果：{result.result_str}")
                    if result.done:
                        return True, f"任务执行成功: {task.__str__()}"
                    elif result.new_task:
                        return False, f"新任务: {result.new_task}"
                    elif result.progress:
                        task.progress = result.progress
                else:
                    self.logger.info(f"[MaiAgent] 想法:{json_before}")
                    thinking_list.append(f"时间：{time_str} 思考结果：{json_before}")


                if task.done:
                    done = True
                    return True, f"任务执行成功: {task.__str__()}"

            if not done:
                self.logger.warning(f"[MaiAgent] 任务执行超时: {task.__str__()}")
                return False, f"任务执行超时: {task.__str__()}"

        except Exception as e:
            self.logger.error(f"[MaiAgent] 任务执行异常: {traceback.format_exc()}")
            return False, f"任务执行异常: {str(e)}"

    async def excute_json(self, json_obj: dict) -> ThinkingJsonResult:
        """
        执行json
        返回: ThinkingJsonResult
        """
        result = ThinkingJsonResult()
        action_type = json_obj.get("action_type")
        if action_type == "move":
            x = json_obj.get("x")
            y = json_obj.get("y")
            z = json_obj.get("z")
            args = {"x": x, "y": y, "z": z, "type": "coordinate","useAbsoluteCoords": True}
            result.result_str = f"尝试移动到：{x},{y},{z}\n"
            
            self_position = global_environment.block_position
            if abs(self_position.x - x) < 0.7 and abs(self_position.y - y) < 0.7 and abs(self_position.z - z) < 0.7:
                result.result_str += f"已经在{x},{y},{z} 附近，不需要移动"
                return result
            
            standing_block = global_block_cache.get_block(x, y-1, z)
            if not standing_block or standing_block.block_type == "air":
                result.result_str += f"位置{x},{y},{z}，在空中，脚下(x={x},y={y-1},z={z})没有方块，无法移动"
                return result
            
            target_block = global_block_cache.get_block(x, y, z)
            target_block_up = global_block_cache.get_block(x, y+1, z)
            # if target_block and target_block.block_type != "air":
            #     result.result_str += f"位置{x},{y},{z}，脚下(x={x},y={y-1},z={z})有方块，无法移动"
            #     return result
            
            if target_block_up and target_block_up.block_type != "air":
                result.result_str += f"位置{x},{y},{z}，头部(x={x},y={y+1},z={z})有方块，无法移动"
                return result
            
            call_result = await self.mcp_client.call_tool_directly("move", args)
            is_success, result_content = await self.parse_action_result(action_type, args, call_result)
            if is_success:
                result.result_str += self._translate_move_tool_result(result_content, args)
            else:
                result.result_str += f"移动失败: {result_content}"
        elif action_type == "craft_item":
            item = json_obj.get("item")
            count = json_obj.get("count")
            args = {"item": item, "count": count}
            result.result_str = f"尝试合成：{item} 数量：{count}\n"
            call_result = await self.mcp_client.call_tool_directly("craft_item", args)
            is_success, result_content = await self.parse_action_result(action_type, args, call_result)
            if is_success:
                result.result_str += self._translate_craft_item_tool_result(result_content)
            else:
                result.result_str += f"合成失败: {result_content}"
        elif action_type == "mine_block":
            block = json_obj.get("block")
            count = json_obj.get("count")
            args = {"name": block, "count": count}
            result.result_str = f"尝试挖掘：{block} 数量：{count}\n"
            call_result = await self.mcp_client.call_tool_directly("mine_block", args)
            is_success, result_content = await self.parse_action_result(action_type, args, call_result)
            if is_success:
                result.result_str += self._translate_mine_block_tool_result(result_content)
            else:
                result.result_str += f"挖掘失败: {result_content}"
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
            if is_success:
                result.result_str += result_content
            else:
                result.result_str += f"放置失败: {result_content}"
        elif action_type == "chat":
            message = json_obj.get("message")
            args = {"message": message}
            call_result = await self.mcp_client.call_tool_directly("chat", args)
            is_success, result_content = await self.parse_action_result(action_type, args, call_result)
            if is_success:
                result.result_str = self._translate_chat_tool_result(result_content)
            else:
                result.result_str = f"聊天失败: {result_content}"
        elif action_type == "get_recipe":
            item = json_obj.get("item")
            result.result_str = f"尝试查询：{item} 的合成表：\n"
            recipe_str = await recipe_finder.find_recipe(item)
            result.result_str += recipe_str
        # 任务动作
        elif action_type == "update_task_progress":
            progress = json_obj.get("progress")
            done = json_obj.get("done")
            if done:
                result.done = done
                result.progress = progress
                result.result_str = "任务已完成"
                return result
            result.result_str = f"任务进度已更新: {progress}"
            result.progress = progress
        elif action_type == "create_new_task":
            new_task = json_obj.get("new_task")
            new_task_criteria = json_obj.get("new_task_criteria")
            result.new_task = new_task
            result.new_task_criteria = new_task_criteria
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
    
    
    def _format_executed_tools_history(self, executed_tools_history: List[Dict[str, Any]]) -> str:
        """格式化已执行工具的历史记录"""
        if not executed_tools_history:
            return "暂无已执行的工具"
        
        formatted_history = []
        for record in executed_tools_history:
            # status 变量不再使用，保留逻辑可读性但避免未使用告警
            _ = "成功" if record["success"] else "失败"
            timestamp = record["timestamp"]
            tool_name = record["tool_name"]
            arguments = record["arguments"]
            result = record["result"]
            thinking = record["thinking"]
            
            # 格式化参数
            if isinstance(arguments, dict):
                args_str = ", ".join([f"{k}={v}" for k, v in arguments.items()])
            else:
                args_str = str(arguments)
            
            # 格式化结果
            if hasattr(result, 'content') and result.content:
                result_text = ""
                for content in result.content:
                    if hasattr(content, 'text'):
                        result_text += content.text
                result_str = result_text
            else:
                # 根据工具类型使用专门的翻译函数
                if tool_name == "move":
                    result_str = self._translate_move_tool_result(result, arguments)
                elif tool_name == "craft_item":
                    result_str = self._translate_craft_item_tool_result(result)
                elif tool_name == "mine_block":
                    result_str = self._translate_mine_block_tool_result(result)
                elif tool_name == "chat":
                    result_str = self._translate_chat_tool_result(result)
                else:
                    result_str = str(result)
            
            # 将时间戳转换为可读格式（不是13位毫秒时间戳，直接按秒处理）
            readable_time = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
                
            formatted_record = f"{readable_time}，你的想法：{thinking}\n基于想法，你使用工具： {tool_name}({args_str})\n结果: {result_str}\n"
            formatted_history.append(formatted_record)
        
        return "\n".join(formatted_history)
            
    
    def _translate_move_tool_result(self, result: Any, arguments: Any = None) -> str:
        """
        翻译move工具的执行结果，使其更可读
        
        Args:
            result: move工具的执行结果
            arguments: 工具调用参数，用于提供更准确的错误信息
            
        Returns:
            翻译后的可读文本
        """
        try:
            # 如果结果是字符串，尝试解析JSON
            if isinstance(result, str):
                try:
                    result_data = json.loads(result)
                except json.JSONDecodeError:
                    return str(result)
            else:
                result_data = result
            
            # 检查是否是move工具的结果
            if not isinstance(result_data, dict):
                return str(result)
            
            # 提取关键信息
            ok = result_data.get("ok", False)
            data = result_data.get("data", {})
            
            if not ok:
                # 处理移动失败的情况
                error_msg = result_data.get("error", "")
                if "MOVE_FAILED" in error_msg:
                    if "Took to long to decide path to goal" in error_msg:
                        # 根据工具参数提供更准确的错误信息
                        if arguments and isinstance(arguments, dict):
                            if "block" in arguments:
                                block_name = arguments["block"]
                                return f"移动失败: 这附近没有{block_name}"
                            elif "type" in arguments and arguments["type"] == "coordinate":
                                return "移动失败: 无法到达指定坐标"
                        return "移动失败: 这附近没有目标方块"
                    else:
                        return f"移动失败: {error_msg}"
                else:
                    return f"移动失败: {error_msg}"
            
            # 提取移动信息
            target = data.get("target", "")
            distance = data.get("distance", 0)
            position = data.get("position", {})
            
            # 格式化位置信息
            x = position.get("x", 0)
            y = position.get("y", 0)
            z = position.get("z", 0)
            
            # 构建可读文本
            readable_text = f"成功移动到坐标 ({x}, {y}, {z})"
            
            if target:
                readable_text += f"，目标：{target}"
            
            if distance is not None:
                readable_text += f"，距离目标：{distance} 格"
            
            return readable_text
            
        except Exception:
            # 如果解析失败，返回原始结果
            return str(result)
    
    def _translate_craft_item_tool_result(self, result: Any) -> str:
        """
        翻译craft_item工具的执行结果，使其更可读
        
        Args:
            result: craft_item工具的执行结果
            
        Returns:
            翻译后的可读文本
        """
        try:
            # 如果结果是字符串，尝试解析JSON
            if isinstance(result, str):
                try:
                    result_data = json.loads(result)
                except json.JSONDecodeError:
                    return str(result)
            else:
                result_data = result
            
            # 检查是否是craft_item工具的结果
            if not isinstance(result_data, dict):
                return str(result)
            
            # 提取关键信息
            ok = result_data.get("ok", False)
            data = result_data.get("data", {})
            
            if not ok:
                return "合成物品失败，可能是物品不存在或缺少工作台"
            
            # 提取合成信息
            item_name = data.get("item", "未知物品")
            count = data.get("count", 1)
            
            # 构建可读文本
            if count == 1:
                readable_text = f"成功合成1个{item_name}"
            else:
                readable_text = f"成功合成{count}个{item_name}"
            
            return readable_text
            
        except Exception:
            # 如果解析失败，返回原始结果
            return str(result)
    
    def _translate_mine_block_tool_result(self, result: Any) -> str:
        """
        翻译mine_block工具的执行结果，使其更可读
        
        Args:
            result: mine_block工具的执行结果
            
        Returns:
            翻译后的可读文本
        """
        try:
            # 如果结果是字符串，尝试解析JSON
            if isinstance(result, str):
                try:
                    result_data = json.loads(result)
                except json.JSONDecodeError:
                    return str(result)
            else:
                result_data = result
            
            # 检查是否是mine_block工具的结果
            if not isinstance(result_data, dict):
                return str(result)
            
            # 提取关键信息
            ok = result_data.get("ok", False)
            data = result_data.get("data", {})
            
            if not ok:
                return "挖掘方块失败"
            
            # 检查是否有挖掘数据
            if "minedCount" in data:
                mined_count = data["minedCount"]
                block_name = data.get("blockName", "未知方块")

                
                # 构建可读文本
                if mined_count == 1:
                    readable_text = f"成功挖掘了1个{block_name}"
                else:
                    readable_text = f"成功挖掘了{mined_count}个{block_name}"
                
                return readable_text
            else:
                # 如果没有挖掘数据，返回原始结果
                return str(result)
            
        except Exception:
            # 如果解析失败，返回原始结果
            return str(result)
    
    def _translate_chat_tool_result(self, result: Any) -> str:
        """
        翻译chat工具的执行结果，使其更可读
        
        Args:
            result: chat工具的执行结果
            
        Returns:
            翻译后的可读文本
        """
        try:
            # 如果结果是字符串，尝试解析JSON
            if isinstance(result, str):
                try:
                    result_data = json.loads(result)
                except json.JSONDecodeError:
                    return str(result)
            else:
                result_data = result
            
            # 检查是否是chat工具的结果
            if not isinstance(result_data, dict):
                return str(result)
            
            # 提取关键信息
            ok = result_data.get("ok", False)
            data = result_data.get("data", {})
            
            if not ok:
                return "聊天失败"
            
            # 提取聊天信息
            message = data.get("message", "未知消息")
            
            # 构建可读文本
            readable_text = f"成功发送消息：{message}"
            
            return readable_text
            
        except Exception:
            # 如果解析失败，返回原始结果
            return str(result)
    