"""
总体设计思路说明

考虑到mc的所有合成（合成，熔炼等）均使用了recipe系统，因此这里提供统一的查询接口

首先检查所需要的数据文件夹是否存在，不存在则Raise

对于每一个物品合成的查询：
1. 首先查询合成所需要的材料种类并返回给调用方
2. 调用方将给出的所有的材料的数量返回给检查接口
3. 检查接口返回是否可合成以及合成时间（如果存在）

现在的某些合成查询比较依赖于文件名，因此需要保证查询json符合mc本体的命名规范
因此最好使用来自mc本身的json。
之后可能会改一下查询方式，以便更好地适应不同的情况，或者有mod的情况。

Special Thanks: Minecraft ZH Wiki https://zh.minecraft.wiki

使用说明
"""

import json
from pathlib import Path
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field


@dataclass
class RecipeReturn:
    can_craft_with_existing_items: bool  # 标记现有材料是否可以合成
    craft_count: int  # 可合成的数量
    craft_time_in_ticks: int = field(default=0)  # 制作所需要的时间，单位tick（游戏刻，1 tick = 1/20秒，正常情况下）
    craft_time_in_readable: float = field(default=0)  # 制作所需要的时间，单位秒
    need_crafting_table: bool = field(default=False)  # 需要工作台
    need_furnace: bool = field(default=False)  # 需要熔炉
    need_blast_furnace: bool = field(default=False)  # 需要高炉
    need_smoker: bool = field(default=False)  # 需要烟熏炉
    need_campfire: bool = field(default=False)  # 需要营火（篝火）
    need_soul_campfire: bool = field(default=False)  # 需要灵魂营火


@dataclass
class RecipeQuery:
    item_name: str
    """所请求的物品的名称"""
    required_items: List[str] = field(default_factory=list)
    """物品名称列表，其中物品名称为标准minecraft命名空间ID，即minecraft:XXX格式"""


class RecipeQuerySystem:
    def __init__(self):
        # 获取当前父文件夹的绝对路径
        self.current_parent_path = Path(__file__).parent.resolve()
        self.data_file_path = self.current_parent_path / "minecraft_json_files"

        self.items_dir = self.data_file_path / "items"
        self.recipes_dir = self.data_file_path / "recipes"

        if not self.items_dir.is_dir():
            raise FileNotFoundError("items文件夹不存在")
        if not self.recipes_dir.is_dir():
            raise FileNotFoundError("recipes文件夹不存在")

        self.special_namespace_mapping: Dict[str, List[str]] = {}
        self._load_special_namespace_mapping()

    def _load_special_namespace_mapping(self):
        for item_file in self.items_dir.glob("*.json"):
            with open(item_file, "r", encoding="utf-8") as f:
                special_namespace_name = item_file.stem  # 文件名就是特殊的命名空间ID
                item_json: Dict = json.load(f)
                self.special_namespace_mapping[f"minecraft:{special_namespace_name}"] = item_json.get("values", [])

    def _recursive_parse_special_items(self, item_name: str) -> List[str]:
        unpacked_item_name_list = self.special_namespace_mapping.get(item_name, [])
        item_name_list: List[str] = []
        for unpacked_item in unpacked_item_name_list:
            if unpacked_item.startswith("#"):
                parsed_item_name_list = self._recursive_parse_special_items(unpacked_item[1:])  # 去掉#号
                item_name_list.extend(parsed_item_name_list)
            else:
                item_name_list.append(unpacked_item)
        return list(set(item_name_list))  # 去重返回

    def query_recipe_data(self, item_name: str) -> dict:
        """
        查询合成表data

        Args:
            item_name: 物品名称
        Returns:
            dict: 物品的合成配方json，未解析的代码
        Raise:
            FileNotFoundError: 如果未找到物品
        """
        processed_item_name = item_name[10:]  # 去掉前面的minecraft:
        file_list = list(self.recipes_dir.glob(f"{processed_item_name}*.json"))
        if len(file_list) > 1:
            raise RuntimeError(f"找到多个物品文件: {file_list}，暂(我)不（不）支（会）持（写）")
        item_file = file_list[0] if file_list else self.recipes_dir / f"{processed_item_name}.json"
        if not item_file.is_file():
            raise FileNotFoundError(f"未找到物品文件: {item_file}")

        with open(item_file, "r", encoding="utf-8") as f:
            item_data = json.load(f)
        return item_data

    def _process_crafting_shaped_recipe(self, raw_data: Dict[str, Any]) -> RecipeQuery:
        """处理有序合成情况"""
        raw_key_mapping: Dict[str, Dict[str, str]] = raw_data["key"]
        key_mapping: Dict[str, str] = {key: key_info["item"] for key, key_info in raw_key_mapping.items()}
        pattern: List[str] = raw_data["pattern"]
        if not pattern:
            raise ValueError("pattern不能为空")
        recipe_query = RecipeQuery()
        for target_item in key_mapping.values():
            if target_item.startswith("#"):
                parsed_item_name_list = self._recursive_parse_special_items(target_item[1:])
                recipe_query.required_items.extend(parsed_item_name_list)
            else:
                recipe_query.required_items.append(target_item)
        recipe_query.required_items = list(set(recipe_query.required_items))  # 去重
        return recipe_query

    def _check_crafting_shaped_recipe(self, raw_data: Dict[str, Any], provided_items: Dict[str, int]) -> RecipeReturn:
        pass

    def query_recipe_internal(self, item: str) -> RecipeQuery:
        """
        查询物品的合成配方

        Args:
            item: 物品名称
        Returns:
            RecipeQuery: 物品的合成配方
        Raise:
            FileNotFoundError: 如果未找到物品
        """
        item_raw_recipe_data: Dict[str, Any] = {}
        try:
            if item.startswith("minecraft:"):
                # 对应传进来的就是命名空间ID情况
                item_raw_recipe_data = self.query_recipe_data(item)
            else:
                item_raw_recipe_data = self.query_recipe_data(f"minecraft:{item}")
        except FileNotFoundError as e:
            raise ValueError(f"未找到物品: {item}，请检查物品名称是否正确") from e
        except Exception as other_e:
            raise other_e
        crafting_type: str = item_raw_recipe_data["type"]
        match crafting_type:
            case "minecraft:crafting_shaped":
                # 处理有序合成
                return self._process_crafting_shaped_recipe(item_raw_recipe_data)
            case "minecraft:crafting_shapeless":
                # 处理无序合成
                pass
            case _:
                # 处理未知合成类型
                raise ValueError(f"未知合成类型: {crafting_type}")


recipe_query_system = RecipeQuerySystem()

if __name__ == "__main__":
    special_name = "minecraft:trimmable_armor"
    print(recipe_query_system._recursive_parse_special_items(special_name))
