"""
Minecraft环境信息存储类
用于存储和管理游戏环境数据
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
from src.utils.logger import get_logger
from .basic_info import Player, Position, Entity, Event, BlockPosition
from src.plugins.maicraft.agent.block_cache.block_cache import global_block_cache

logger = get_logger("EnvironmentInfo")

class EnvironmentInfo:
    """Minecraft环境信息存储类"""
    
    def __init__(self):
        # 玩家信息
        self.player_name: str = ""
        
        # 位置信息(脚)
        self.position: Optional[Position] = None
        self.block_position: Optional[BlockPosition] = None
        
        # 状态信息
        self.health: int = 0
        self.health_max: int = 20
        self.health_percentage: int = 0
        self.food: int = 0
        self.food_max: int = 20
        self.food_saturation: int = 0
        self.food_percentage: int = 0
        self.experience: int = 0
        self.level: int = 0
        self.oxygen: int = 0

        
        # 物品栏
        self.inventory: List[Any] = field(default_factory=list)
        
        self.occupied_slot_count: int = 0
        self.empty_slot_count: int = 0
        self.slot_count: int = 0
        
        
        # 环境信息
        self.weather: str = ""
        self.time_of_day: int = 0
        self.dimension: str = ""
        
        # 附近玩家
        self.nearby_players: List[Player] = field(default_factory=list)
        
        # 附近实体
        self.nearby_entities: List[Entity] = field(default_factory=list)
        
        
        # 最近事件
        self.recent_events: List[Event] = field(default_factory=list)
        
        # 系统状态
        self.status: str = ""
        self.request_id: str = ""
        self.elapsed_ms: int = 0
        
        # 时间戳
        self.last_update: Optional[datetime] = None

    def update_from_observation(self, observation_data: Dict[str, Any]) -> None:
        """从观察数据更新环境信息"""
        if not observation_data.get("ok"):
            return
        
        data = observation_data.get("data", {})

        
        # 更新游戏状态信息 (来自 query_game_state)
        self.weather = data.get("weather", "")
        self.time_of_day = data.get("timeOfDay", 0)
        self.dimension = data.get("dimension", "")
        
        self.player_name = data.get("username", "")
        
        # 更新在线玩家信息 (来自 query_game_state)
        online_players = data.get("onlinePlayers", [])
        self.nearby_players = []
        for player_name in online_players:
            # 在线玩家只提供名称，创建基本的Player对象
            player = Player(
                uuid="",  # 在线玩家列表中没有UUID
                username=player_name,
                display_name=player_name,
                ping=0,  # 在线玩家列表中没有ping信息
                gamemode=0  # 在线玩家列表中没有游戏模式信息
            )
            self.nearby_players.append(player)
    
        
        # 更新位置信息
        pos_data = data.get("position")
        if pos_data and isinstance(pos_data, dict):
            self.position = Position(
                x=pos_data.get("x", 0.0),
                y=pos_data.get("y", 0.0),
                z=pos_data.get("z", 0.0)
            )
        else:
            # 如果没有位置数据，设置为默认位置或保持为None
            self.position = None
            logger.warning("未找到有效的位置数据，位置信息未更新")
            
        self.block_position = BlockPosition(self.position)
        
        # 更新状态信息
        health_data = data.get("health", {})
        self.health = health_data.get("current", 0)
        self.health_max = health_data.get("max", 20)
        self.health_percentage = health_data.get("percentage", 0)
        
        food_data = data.get("food", {})
        self.food = food_data.get("current", 0)
        self.food_max = food_data.get("max", 20)
        self.food_saturation = food_data.get("saturation", 0)
        self.food_percentage = food_data.get("percentage", 0)

        experience_data = data.get("experience", {})
        self.experience = experience_data.get("points", 0)
        self.level = experience_data.get("level", 0)
        
        self.oxygen = data.get("oxygen", 0)
        
        
        # 更新物品栏
        inventory_data = data.get("inventory", {})
        self.inventory = [] 

        # 新格式：包含统计信息和槽位数据
        slots = inventory_data.get("slots", [])
        if isinstance(slots, list):
            for slot_data in slots:
                if isinstance(slot_data, dict):
                    # 构建标准化的物品信息
                    item_info = {
                        'slot': slot_data.get('slot', 0),
                        'count': slot_data.get('count', 0),
                        'name': slot_data.get('name', ''),
                        'displayName': slot_data.get('name', '')  # 使用name作为displayName
                    }
                    self.inventory.append(item_info)
        
        # 记录物品栏统计信息
        self.occupied_slot_count = inventory_data.get('fullSlotCount', 0)
        self.empty_slot_count = inventory_data.get('emptySlotCount', 0)
        self.slot_count = inventory_data.get('slotCount', 0)
        


        # 更新最近事件 (来自 query_recent_events)
        self.recent_events = []
        for event_data in data.get("recentEvents", []):
            try:
                event = Event(
                    type=event_data.get("type", ""),
                    timestamp=event_data.get("gameTick", 0),  # 使用gameTick作为时间戳
                    server_id="",  # 新格式中没有serverId
                    player_name=event_data.get("playerName", "")
                )
                
                # 根据事件类型设置特定属性
                if event_data.get("player"):
                    player_data = event_data["player"]
                    event.player = Player(
                        uuid=player_data.get("uuid", ""),
                        username=player_data.get("username", ""),
                        display_name=player_data.get("displayName", ""),
                        ping=player_data.get("ping", 0),
                        gamemode=player_data.get("gamemode", 0)
                    )
                    # 设置玩家名称
                    event.player_name = player_data.get("username", "")
                
                # 处理playerInfo字段 (playerJoin事件)
                if event_data.get("playerInfo"):
                    player_info = event_data["playerInfo"]
                    event.player = Player(
                        uuid=player_info.get("uuid", ""),
                        username=player_info.get("username", ""),
                        display_name=player_info.get("displayName", ""),
                        ping=player_info.get("ping", 0),
                        gamemode=player_info.get("gamemode", 0)
                    )
                    # 设置玩家名称
                    event.player_name = player_info.get("username", "")
                
                # 处理位置信息 (playerRespawn事件)
                if event_data.get("position"):
                    pos_data = event_data["position"]
                    if isinstance(pos_data, dict):
                        event.new_position = Position(
                            x=pos_data.get("x", 0.0),
                            y=pos_data.get("y", 0.0),
                            z=pos_data.get("z", 0.0)
                        )
                    elif isinstance(pos_data, list) and len(pos_data) >= 3:
                        # 如果位置是列表格式 [x, y, z]
                        event.new_position = Position(
                            x=float(pos_data[0]) if pos_data[0] is not None else 0.0,
                            y=float(pos_data[1]) if pos_data[1] is not None else 0.0,
                            z=float(pos_data[2]) if pos_data[2] is not None else 0.0
                        )
                
                # 处理健康更新事件
                if event.type == "healthUpdate":
                    # 处理新的health格式
                    health_data = event_data.get("health", {})
                    if isinstance(health_data, dict):
                        event.health = health_data.get("current", 0)
                    else:
                        event.health = event_data.get("health", 0)
                    
                    # 处理新的food格式
                    food_data = event_data.get("food", {})
                    if isinstance(food_data, dict):
                        event.food = food_data.get("current", 0)
                        event.saturation = food_data.get("saturation", 0)
                    else:
                        event.food = event_data.get("food", 0)
                        event.saturation = event_data.get("saturation", 0)
                
                self.recent_events.append(event)
            except Exception as e:
                # 记录事件处理错误，但继续处理其他事件
                import traceback
                print(f"处理事件数据时出错: {e}")
                print(f"事件数据: {event_data}")
                print(f"错误详情: {traceback.format_exc()}")
                continue
        
        # 更新周围环境 - 玩家 (来自 query_surroundings("players"))
        if "nearbyPlayers" in data:
            nearby_players_data = data["nearbyPlayers"]
            if isinstance(nearby_players_data, list):
                # 如果nearbyPlayers是列表，直接使用
                self.nearby_players = []
                for player_data in nearby_players_data:
                    try:
                        if isinstance(player_data, dict):
                            player = Player(
                                uuid=player_data.get("uuid", ""),
                                username=player_data.get("username", ""),
                                display_name=player_data.get("displayName", ""),
                                ping=player_data.get("ping", 0),
                                gamemode=player_data.get("gamemode", 0)
                            )
                            self.nearby_players.append(player)
                        else:
                            # 如果只是玩家名称字符串
                            player = Player(
                                uuid="",
                                username=str(player_data),
                                display_name=str(player_data),
                                ping=0,
                                gamemode=0
                            )
                            self.nearby_players.append(player)
                    except Exception as e:
                        # 记录玩家处理错误，但继续处理其他玩家
                        import traceback
                        print(f"处理玩家数据时出错: {e}")
                        print(f"玩家数据: {player_data}")
                        print(f"错误详情: {traceback.format_exc()}")
                        continue
        
        # 更新周围环境 - 实体 (来自 query_surroundings("entities"))
        if "nearbyEntities" in data:
            nearby_entities_data = data["nearbyEntities"]
            if isinstance(nearby_entities_data, list):
                self.nearby_entities = []
                for entity_data in nearby_entities_data:
                    try:
                        if isinstance(entity_data, dict):
                            # 安全地获取位置信息
                            position = Position(0.0, 0.0, 0.0)
                            if "position" in entity_data:
                                pos_data = entity_data["position"]
                                if isinstance(pos_data, dict):
                                    position = Position(
                                        x=pos_data.get("x", 0.0),
                                        y=pos_data.get("y", 0.0),
                                        z=pos_data.get("z", 0.0)
                                    )
                                elif isinstance(pos_data, list) and len(pos_data) >= 3:
                                    # 如果位置是列表格式 [x, y, z]
                                    position = Position(
                                        x=float(pos_data[0]) if pos_data[0] is not None else 0.0,
                                        y=float(pos_data[1]) if pos_data[1] is not None else 0.0,
                                        z=float(pos_data[2]) if pos_data[2] is not None else 0.0
                                    )
                            
                            entity = Entity(
                                id=entity_data.get("id", 0),
                                type=entity_data.get("type", ""),
                                name=entity_data.get("name", ""),
                                position=position
                            )
                            self.nearby_entities.append(entity)
                    except Exception as e:
                        # 记录实体处理错误，但继续处理其他实体
                        import traceback
                        print(f"处理实体数据时出错: {e}")
                        print(f"实体数据: {entity_data}")
                        print(f"错误详情: {traceback.format_exc()}")
                        continue
        
        # 更新请求信息
        self.request_id = observation_data.get("request_id", "")
        self.elapsed_ms = observation_data.get("elapsed_ms", 0)
        
        # 更新时间戳
        self.last_update = datetime.now()
        
    def get_position_str(self) -> str:
        """获取位置信息"""
        if self.block_position:
            block_on_feet = global_block_cache.get_block(self.block_position.x, self.block_position.y-1, self.block_position.z)
            if block_on_feet:
                block_on_feet_str = f"你正站在方块 {block_on_feet.block_type} 上"
            else:
                block_on_feet_str = "脚下没有方块"
        position_str = f"""
你现在的坐标(脚所在的坐标)是：x={self.block_position.x}, y={self.block_position.y}, z={self.block_position.z}，你有两格高，y代表高度
{block_on_feet_str}
        """
        return position_str

    def get_summary(self) -> str:
        """以可读文本形式返回所有环境信息"""
        lines = []
        
        # 玩家信息
        if self.player_name:
            lines.append("【自身信息】")
            lines.append(f"  用户名: {self.player_name}")
            # lines.append(f"  显示名: {self.player.display_name}")
            # lines.append(f"  游戏模式: {self._get_gamemode_name(self.player.gamemode)}")
            lines.append("")
        
        # 状态信息
        lines.append("【状态信息】")
        lines.append(f"  生命值: {self.health}/{self.health_max}")
        lines.append(f"  饥饿值: {self.food}/{self.food_max}")
        if self.food_saturation > 0:
            lines.append(f"  饥饿饱和度: {self.food_saturation}")
        # lines.append(f"  经验值: {self.experience}")
        lines.append(f"  等级: {self.level}")
        lines.append("")
        
        # 物品栏
        lines.append("【物品栏】")
        if self.inventory:
            if self.empty_slot_count == 0:
                lines.append(f"物品栏已满！无法装入新物品！")
            else:
                lines.append(f"物品栏有{self.empty_slot_count}个空槽位")
            # 按槽位排序显示物品
            sorted_inventory = sorted(self.inventory, key=lambda x: x.get('slot', 0) if isinstance(x, dict) else 0)
            
            for item in sorted_inventory:
                # 构建更可读的物品信息
                item_info = []
                
                # 添加类型检查，确保item是字典类型
                if isinstance(item, dict):
                    if 'name' in item and item['name']:
                        item_info.append(item['name'])
                    if 'count' in item and item['count'] > 0:
                        item_info.append(f"x{item['count']} ")
                elif isinstance(item, str):
                    # 如果item是字符串，直接显示
                    item_info.append(item)
                else:
                    # 其他类型，转换为字符串显示
                    item_info.append(str(item))
                
                # 组合物品信息
                item_str = " ".join(item_info)
                lines.append(f"  {item_str}")
        else:
            lines.append("  物品栏为空")
        lines.append("")
        
        # 附近玩家
        lines.append("【附近玩家】")
        if self.nearby_players:
            lines.append(f"  附近玩家数量: {len(self.nearby_players)}")
            for i, player in enumerate(self.nearby_players, 1):
                lines.append(f"  {i}. {player.display_name} ({player.username})")
                lines.append(f"     延迟: {player.ping}ms, 游戏模式: {player.gamemode}")
        else:
            lines.append("  附近没有其他玩家")
        lines.append("")
        
        # 附近实体
        lines.append("【附近实体】")
        if self.nearby_entities:
            lines.append(f"  附近实体数量: {len(self.nearby_entities)}")
            for i, entity in enumerate(self.nearby_entities, 1):
                pos = entity.position
                lines.append(f"  {i}. {entity.name} (ID: {entity.id}, 类型: {entity.type})")
                lines.append(f"     位置: X={pos.x:.2f}, Y={pos.y:.2f}, Z={pos.z:.2f}")
        lines.append("")
        
        lines.append("=" * 10)
        
        return "\n".join(lines)
    
    
    def _get_event_description(self, event: Event) -> str:
        """获取事件描述"""
        base_desc = f"{event.type} - {event.player_name}"
        
        if event.type == "playerMove" and event.old_position and event.new_position:
            old_pos = event.old_position
            new_pos = event.new_position
            return f"{base_desc} 从 ({old_pos.x:.1f}, {old_pos.y:.1f}, {old_pos.z:.1f}) 移动到 ({new_pos.x:.1f}, {new_pos.y:.1f}, {new_pos.z:.1f})"
        
        elif event.type == "blockBreak" and event.block:
            block = event.block
            pos = block.position
            return f"{base_desc} 破坏了 {block.name} 在 ({pos.x:.1f}, {pos.y:.1f}, {pos.z:.1f})"
        
        elif event.type == "experienceUpdate":
            return f"{base_desc} 经验值更新: {event.experience}, 等级: {event.level}"
        
        elif event.type == "healthUpdate":
            return f"{base_desc} 生命值更新: {event.health}, 饥饿值: {event.food}"
        
        else:
            return base_desc


# 全局环境信息实例
global_environment = EnvironmentInfo()
