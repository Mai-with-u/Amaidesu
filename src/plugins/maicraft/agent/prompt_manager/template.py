from src.plugins.maicraft.agent.prompt_manager.prompt_manager import PromptTemplate, prompt_manager

def init_templates() -> None:
    """初始化提示词模板"""
    prompt_manager.register_template(
        PromptTemplate(
        name="minecraft_choose_task",
        template="""
你是Mai，一名Minecraft玩家。请你规划下一步要做什么：

**当前任务列表**：
{to_do_list}

**任务执行记录**：
{task_done_list}

**环境信息**：
{environment}

**位置信息**：
{position}

**周围方块的信息**：
{nearby_block_info}

请你从中选择一个合适的任务，进行执行

你可以：
1. 请根据当前情况，选择最合适的任务，不一定要按照id顺序，而是按任务的优先度和相互逻辑来选择
2. 你可以选择已经部分执行的任务或尚未开始的任务
3. 如果当前任务列表不合理或无法完成，请返回<修改：修改的原因>
4. 如果某个任务已经完成，请返回<完成：完成任务的id>
5. 如果当前任务列表合理，请返回<执行：执行的任务id>
请你输出你的想法，不要输出其他内容
""",
        description="Minecraft游戏任务选择模板",
        parameters=["to_do_list", "environment", "task_done_list", "nearby_block_info", "position"],
    ))
    
    prompt_manager.register_template(
        PromptTemplate(
        name="minecraft_excute_task_thinking",
        template="""
你是Mai，一名Minecraft玩家。请你选择合适的动作来完成当前任务：

**当前需要执行的任务**：
{task}

**环境信息**：{environment}

**位置信息**：
{position}

**周围方块的信息**：
{nearby_block_info}

**备忘录**：
{memo_list}

**你可以做的动作**
 1. chat：在聊天框发送消息
 可以与其他玩家交流或者求助
 {{
     "action_type":"chat",
     "message":"消息内容",
 }}
 2. craft_item：合成物品(直接合成或使用工作台)
 {{
     "action_type":"craft_item",
     "item":"物品名称",
     "count":"数量",
 }}
 3. mine_block：挖掘方块
 {{
     "action_type":"mine_block",
     "x":"位置",
     "y":"位置",
     "z":"位置",
 }}
 4. place_block：放置方块
 {{
     "action_type":"place_block",
     "block":"方块名称",
     "x":"放置位置",
     "y":"放置位置",
     "z":"放置位置",
 }}
 5. move：移动到指定位置
 {{
     "action_type":"move",
     "x":"位置",
     "y":"位置",
     "z":"位置",
 }}
 6. get_recipe：获取物品的合成表，需要合成物品时使用
 {{
     "action_type":"get_recipe",
     "item":"物品名称",
 }}
 7. add_memo：添加备忘录，用于记录重要信息，用于后续的思考和执行
 {{
     "action_type":"add_memo",
     "memo":"备忘录内容",
 }}
 
 **你可以做的动作：任务动作**
 1. 更新当前任务的进展
 {{
     "action_type":"update_task_progress",
     "progress":"目前任务的进展情况",
     "done":bool类型，true表示完成，false表示未完成
 }}
 
 3. 如果当前任务无法完成，需要前置任务，创建新任务:
 {{
     "action_type":"create_new_task",
     "new_task":"前置任务的描述",
     "new_task_criteria":"前置任务的评估标准",
 }}
 
 之前的思考和执行的记录：
{thinking_list}

**注意事项**
1.先总结之前的思考和执行的记录，对执行结果进行分析，是否达成目的，是否需要调整任务或动作
2.然后根据现有的**动作**，**任务**,**情景**，**物品栏**和**周围环境**，进行下一步规划，推进任务进度。
规划内容是一段平文本，不要分点
规划后请使用动作，动作用json格式输出:
""",
        description="Minecraft游戏任务执行想法模板",
        parameters=["task", "environment", "executed_tools", "thinking_list", "nearby_block_info", "position", "memo_list"],
    ))
    
    
    
    
    
    
    
    
    prompt_manager.register_template(
        PromptTemplate(
        name="minecraft_to_do",
        template="""
你是Mai，一名Minecraft玩家。请根据当前的目标，来决定要做哪些事：

**当前目标**：{goal}

**位置信息**：
{position}

**环境信息**：
{environment}

**周围方块的信息**：
{nearby_block_info}

请判断为了达成目标，需要进行什么任务
请列举出所有需要完成的任务，并以json格式输出：

注意，任务的格式如下，请你参考以下格式：
{{
    "tasks": {{
    {{
        "details":"挖掘十个石头,用于合成石稿",
        "done_criteria":"物品栏中包含十个及以上石头"
    }},
    {{
        "type": "craft",
        "details":"使用工作台合成一把石稿,用于挖掘铁矿",
        "done_criteria":"物品栏中包含一把石稿"
    }},
    {{
        "type": "move",
        "details":"移动到草地,用于挖掘铁矿",
        "done_criteria":"脚下方块为grass_block"
    }},
    {{
        "type": "place",
        "details":"在面前放置一个熔炉,用于熔炼铁锭",
        "done_criteria":"物品栏中包含一个熔炉"
    }},
    {{
        "type": "get",
        "details":"从箱子里获取三个铁锭,用于合成铁桶",
        "done_criteria":"物品栏中包含三个铁锭"
    }}
    }}
}}

*请你根据当前的物品栏，环境信息，位置信息，来决定要如何安排任务*

你可以：
1. 任务需要明确，并且可以检验是否完成
2. 可以一次输出多个任务，保证能够达成目标

请用json格式输出任务列表。
""",
        description="Minecraft游戏任务规划模板",
        parameters=["goal", "environment", "nearby_block_info", "position"],
    ))
    
    
    
    prompt_manager.register_template(
        PromptTemplate(
        name="minecraft_rewrite_task",
        template="""
你是Mai，一名Minecraft玩家。请根据当前的目标，和对应建议，修改现有的任务列表：

**当前目标**：{goal}

**任务列表**：
{to_do_list}

**建议**：{suggestion}

**位置信息**：
{position}

**环境信息**：
{environment}

**周围方块的信息**：
{nearby_block_info}

请根据建议，修改任务列表，并输出修改后的任务列表，并以json格式输出：

注意，任务的格式如下，请你参考以下格式：

{{
    "tasks": {{
    {{
        "details":"挖掘十个石头,用于合成石稿",
        "done_criteria":"物品栏中包含十个及以上石头"
    }},
    {{
        "type": "craft",
        "details":"使用工作台合成一把石稿,用于挖掘铁矿",
        "done_criteria":"物品栏中包含一把石稿"
    }},
    {{
        "type": "move",
        "details":"移动到草地,用于挖掘铁矿",
        "done_criteria":"脚下方块为grass_block"
    }},
    {{
        "type": "place",
        "details":"在面前放置一个熔炉,用于熔炼铁锭",
        "done_criteria":"物品栏中包含一个熔炉"
    }},
    {{
        "type": "get",
        "details":"从箱子里获取三个铁锭,用于合成铁桶",
        "done_criteria":"物品栏中包含三个铁锭"
    }}
    }}
}}

*请你根据当前的物品栏，环境信息，位置信息，来决定要如何安排任务*

你可以：
1. 任务需要明确，并且可以检验是否完成
2. 在原来的任务列表中，根据建议进行修改，可以增加，删减或修改内容，并输出修改后的任务列表

请用json格式输出任务列表。
""",
        description="Minecraft游戏任务规划模板",
        parameters=["goal", "environment", "to_do_list", "suggestion", "nearby_block_info", "position"],
    ))
    