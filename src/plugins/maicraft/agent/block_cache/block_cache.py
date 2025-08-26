"""
方块缓存系统
用于缓存和管理所有获取过的方块信息
支持位置更新、查询和统计功能
"""
from typing import Dict, List, Optional, Tuple, Set, Any
from datetime import datetime
from collections import defaultdict
from src.utils.logger import get_logger
from ..basic_info import BlockPosition, Position

logger = get_logger("BlockCache")


class CachedBlock:
    """缓存的方块信息"""
    def __init__(self, block_type: int, position: BlockPosition, last_seen: datetime, first_seen: datetime, seen_count: int = 1) -> None:
        self.block_type = block_type
        self.position = position
        self.last_seen = last_seen
        self.first_seen = first_seen
        self.seen_count = seen_count
        
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "block_type": self.block_type,
            "position": self.position.to_dict(),
            "last_seen": self.last_seen.isoformat(),
            "first_seen": self.first_seen.isoformat(),
            "seen_count": self.seen_count
        }
    
    def __hash__(self):
        """使对象可哈希，用于集合操作"""
        return hash((self.position.x, self.position.y, self.position.z))
    
    def __eq__(self, other):
        """比较两个方块是否在同一位置"""
        if not isinstance(other, CachedBlock):
            return False
        return (self.position.x, self.position.y, self.position.z) == (other.position.x, other.position.y, other.position.z)


class BlockCache:
    """方块缓存管理器"""
    def __init__(self):
        """
        初始化方块缓存
        
        Args:
            max_cache_size: 最大缓存方块数量
            cleanup_interval: 清理间隔（秒）
        """
        # 主缓存：位置 -> 方块信息
        self._position_cache: Dict[BlockPosition, CachedBlock] = dict()
        
        # 类型索引：方块类型 -> 位置集合
        self._type_index: Dict[int, Set[Tuple[float, float, float]]] = defaultdict(set)
        
        # 名称索引：方块名称 -> 位置集合
        self._name_index: Dict[str, Set[Tuple[float, float, float]]] = defaultdict(set)
        
        # 统计信息
        self._stats = {
            "total_blocks_cached": 0,
            "total_updates": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "last_cleanup": datetime.now()
        }
        
        logger.info("方块缓存系统初始化完成")
    
    def update_from_blocks(self, blocks_data: Dict[str, Any]) -> int:
        """
        从query_surroundings函数的结果更新方块缓存
        
        Args:
            query_result: query_surroundings函数返回的结果字典
            
        Returns:
            更新的方块数量
        """
        if not blocks_data:
            logger.debug("query_surroundings返回的方块数据为空")
            return 0
        
        
        updated_count = 0
        try:
            observed_positions: Set[Tuple[int, int, int]] = set()
            min_x = min_y = min_z = None
            max_x = max_y = max_z = None
            for block_type, block_info in blocks_data["blockMap"].items():
                positions = block_info.get("positions", [])
                for pos in positions:
                    x, y, z = int(pos[0]), int(pos[1]), int(pos[2])
                    pos = {"x": x, "y": y, "z": z}
                    self.add_block(block_type, BlockPosition(pos))
                    observed_positions.add((x, y, z))
                    # 更新边界
                    min_x = x if min_x is None else min(min_x, x)
                    max_x = x if max_x is None else max(max_x, x)
                    min_y = y if min_y is None else min(min_y, y)
                    max_y = y if max_y is None else max(max_y, y)
                    min_z = z if min_z is None else min(min_z, z)
                    max_z = z if max_z is None else max(max_z, z)
                    updated_count += 1

            # 以观测到的最小/最大 xyz 创建包围盒，填充缺失为 air
            if observed_positions and None not in (min_x, max_x, min_y, max_y, min_z, max_z):
                for ix in range(min_x, max_x + 1):
                    for iy in range(min_y, max_y + 1):
                        for iz in range(min_z, max_z + 1):
                            if (ix, iy, iz) not in observed_positions:
                                pos = {"x": ix, "y": iy, "z": iz}
                                self.add_block("air", BlockPosition(pos))
                                updated_count += 1

            # logger.info(f"从query_surroundings更新了 {updated_count} 个方块到缓存")
            return updated_count
        except Exception as e:
            logger.error(f"处理query_surroundings数据时出错: {e}")
            return 0
        
    
    def add_block(self, block_type: str, position: BlockPosition) -> CachedBlock:
        """
        添加或更新方块信息
        
        Args:
            block_type: 方块类型
            position: 方块位置
            
        Returns:
            缓存的方块对象
        """
        now = datetime.now()
        # logger.info(f"添加方块缓存: {block_type} at ({position.x}, {position.y}, {position.z})")
        # logger.info(f"方块缓存: {self._position_cache}")
        
        if position in self._position_cache:
            # 更新现有方块
            existing_block = self._position_cache[position]
            existing_block.block_type = block_type
            existing_block.last_seen = now
            existing_block.seen_count += 1
            
            # 更新索引
            self._update_indices(existing_block, block_type)
            
            self._stats["total_updates"] += 1
            
            return existing_block
        else:
            # 添加新方块
            new_block = CachedBlock(
                block_type=block_type,
                position=position,
                last_seen=now,
                first_seen=now
            )
            
            self._position_cache[position] = new_block
            self._type_index[block_type].add(position)
            
            self._stats["total_blocks_cached"] += 1
            self._stats["total_updates"] += 1
            
            return new_block
    
    def get_block(self, x: int, y: int, z: int) -> Optional[CachedBlock]:
        """
        获取指定位置的方块信息
        
        Args:
            x, y, z: 坐标位置
            
        Returns:
            方块信息，如果不存在则返回None
        """
        position = BlockPosition({"x": x, "y": y, "z": z})
        
        if position in self._position_cache:
            self._stats["cache_hits"] += 1
            return self._position_cache[position]
        else:
            self._stats["cache_misses"] += 1
            return None
    
    def get_blocks_by_type(self, block_type: int) -> List[CachedBlock]:
        """
        获取指定类型的所有方块
        
        Args:
            block_type: 方块类型ID
            
        Returns:
            方块列表
        """
        positions = self._type_index.get(block_type, set())
        return [self._position_cache[pos] for pos in positions if pos in self._position_cache]
    
    def get_blocks_in_range(self, center_x: float, center_y: float, center_z: float, 
                           radius: float) -> List[CachedBlock]:
        """
        获取指定范围内的所有方块
        
        Args:
            center_x, center_y, center_z: 中心点坐标
            radius: 搜索半径
            
        Returns:
            范围内的方块列表
        """
        radius_squared = radius * radius
        blocks_in_range = []
        
        for block in self._position_cache.values():
            dx = block.position.x - center_x
            dy = block.position.y - center_y
            dz = block.position.z - center_z
            distance_squared = dx*dx + dy*dy + dz*dz
            
            if distance_squared <= radius_squared:
                blocks_in_range.append(block)
        
        return blocks_in_range
    
    def remove_block(self, x: float, y: float, z: float) -> bool:
        """
        移除指定位置的方块缓存
        
        Args:
            x, y, z: 坐标位置
            
        Returns:
            是否成功移除
        """
        position = BlockPosition({"x": x, "y": y, "z": z})
        
        if position not in self._position_cache:
            return False
        
        block = self._position_cache[position]
        
        # 从主缓存移除
        del self._position_cache[position]
        
        # 从索引中移除
        self._type_index[block.block_type].discard(position)
        
        # 清理空的索引项
        if not self._type_index[block.block_type]:
            del self._type_index[block.block_type]
        
        logger.debug(f"移除方块缓存: {block.block_name} at ({x}, {y}, {z})")
        return True
    
    
    def get_cache_stats(self) -> dict:
        """获取缓存统计信息"""
        return {
            **self._stats,
            "current_cache_size": len(self._position_cache),
            "type_count": len(self._type_index),
            "cache_hit_rate": (self._stats["cache_hits"] / 
                              max(1, self._stats["cache_hits"] + self._stats["cache_misses"]))
        }
    
    def _update_indices(self, block: CachedBlock, new_type: int):
        """更新索引信息"""
        old_type = block.block_type
        
        # 如果类型或名称发生变化，需要更新索引
        if old_type != new_type:
            self._type_index[old_type].discard(block.position)
            if not self._type_index[old_type]:
                del self._type_index[old_type]
            self._type_index[new_type].add(block.position)
    
    def __len__(self) -> int:
        """返回缓存的方块数量"""
        return len(self._position_cache)
    
    def __contains__(self, position: BlockPosition) -> bool:
        """检查指定位置是否有缓存的方块"""
        return position in self._position_cache
    
# 全局单例实例
global_block_cache = BlockCache()