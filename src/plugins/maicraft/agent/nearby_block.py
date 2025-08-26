from src.plugins.maicraft.agent.block_cache.block_cache import global_block_cache
from src.plugins.maicraft.agent.environment import global_environment
from .basic_info import Position, BlockPosition
from ..openai_client.llm_request import LLMClient
from ..openai_client.modelconfig import ModelConfig
from ..config import MaicraftConfig
from src.utils.logger import get_logger

logger = get_logger("NearbyBlockManager")


class NearbyBlockManager:
    def __init__(self,config: MaicraftConfig):
        model_config = ModelConfig(
            model_name=config.llm_fast.model,
            api_key=config.llm_fast.api_key,
            base_url=config.llm_fast.base_url,
            max_tokens=config.llm_fast.max_tokens,
            temperature=config.llm_fast.temperature
        )
        
        self.llm_client_fast = LLMClient(model_config)
        
        self.block_cache = global_block_cache

    def get_block_details_raw_str(self, position: BlockPosition):
        around_blocks = self.block_cache.get_blocks_in_range(position.x, position.y, position.z, 3)
        around_blocks_str = ""
        for block in around_blocks:
            if block.block_type == "air":
                around_blocks_str += f"无方块: x={block.position.x}, y={block.position.y}, z={block.position.z}\n"
            else:
                around_blocks_str += f"{block.block_type} : x={block.position.x}, y={block.position.y}, z={block.position.z}\n"
        return around_blocks_str
    
    async def get_block_details_mix_str(self, position: BlockPosition, require: str, distance: int = 5):
        around_blocks = self.block_cache.get_blocks_in_range(position.x, position.y, position.z, distance)
        around_blocks_str = ""
        block_num = 0
        
        for block in around_blocks:
            # if block.block_type != "air":
            block_num += 1
            if block.position == position:
                around_blocks_str += f"玩家所在位置: x={block.position.x}, y={block.position.y}, z={block.position.z}\n"
                continue
            if block.position.x == position.x and block.position.y == position.y+1 and block.position.z == position.z:
                around_blocks_str += f"玩家头部位置: x={block.position.x}, y={block.position.y}, z={block.position.z}\n"
                continue
            
            if block.block_type == "air":
                around_blocks_str += f"无方块: x={block.position.x}, y={block.position.y}, z={block.position.z}\n"
            else:
                around_blocks_str += f"{block.block_type} : x={block.position.x}, y={block.position.y}, z={block.position.z}\n"
                
        if block_num >64:
            around_blocks_str = await self.get_block_details_str(position, require,distance)
        
        return around_blocks_str
    
    async def get_block_details_str(self, position: BlockPosition, require: str = "",distance: int = 5):
        """仔细观察周围方块信息"""
        around_under_feet_blocks_str = ""
        around_under_feet_blocks = self.block_cache.get_blocks_in_range(position.x, position.y, position.z, distance)

            
        if around_under_feet_blocks:
            for block in around_under_feet_blocks:
                if block.block_type == "air":
                    around_under_feet_blocks_str += f"无方块: x={block.position.x}, y={block.position.y}, z={block.position.z}\n"
                else:
                    around_under_feet_blocks_str += f"{block.block_type} : x={block.position.x}, y={block.position.y}, z={block.position.z}\n"
        
        # 使用LLM分析方块信息
        prompt = f"""
        {global_environment.get_position_str()}
        
        请你分析周围10格以内方块的信息，并给出分析结果。
        周围方块信息: {around_under_feet_blocks_str}
        
        请根据这些信息，用一段简短的平文本概括你脚下方块和周围方块的信息。
        分析和观察时需要考虑的点：{require}
        1. 注意当前空间大小和方块的类型，没有方块的地方是空气
        2. 考虑当前所属环境，可能是矿洞，矿道，开阔区域，森林，建筑物，草原等等.....
        
        请你根据Minecraft游戏相关知识和分析和观察时需要考虑的点给出结果
        
        简短的分析结果:
        """
        
        logger.info(prompt)
        
        response = await self.llm_client_fast.simple_chat(prompt)
        
        
            
            
        return response
        
        
        
        
        
        
        