"""模拟直播间观众人设池。"""

import random
import tomllib
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from src.modules.simulator.config_schema import (
    SimulatorConfigSchema,
)
from src.modules.simulator.types import Persona, PersonaRole


class PersonaPool:
    """人设池管理：常驻 + 临时路人

    常驻人设来自 ``data/simulator/residents.toml``（运行时数据，WebUI 生成/编辑）；
    文件不存在时空池，仅生成临时路人。
    """

    _PASSERBY_NAMES = [
        "路过的甲",
        "吃瓜群众",
        "潜水员",
        "刚好路过",
        "围观一下",
        "隔壁直播间来的",
    ]
    _PASSERBY_POOL_CAP = 50

    def __init__(self, rng: Optional[random.Random] = None, data_dir: Optional[Path] = None):
        self._rng = rng or random.Random()
        self._residents: List[Persona] = []
        self._passersby: List[Persona] = []  # temporary
        self._config: Optional[SimulatorConfigSchema] = None
        self._messages_by_role: Dict[str, int] = {}
        self._all_residents: List[Persona] = []
        self._data_dir = data_dir or Path(__file__).resolve().parents[3] / "data" / "simulator"

    async def load(self, config: SimulatorConfigSchema) -> None:
        """加载常驻人设并应用运行时筛选配置；数据文件不存在时为空池。"""
        self._config = config
        residents_path = self._data_dir / "residents.toml"
        self._all_residents = []
        if residents_path.exists():
            with residents_path.open("rb") as residents_file:
                data = tomllib.load(residents_file)
            items = data.get("residents", {}).get("items", [])
            self._all_residents = [Persona.model_validate(item) for item in items]
        self._apply_resident_filter()

    def pick_one(self) -> Persona:
        """按配置概率和角色权重随机选择一个人设。

        路人池采用懒加载：命中路人概率时若池空则按需生成。
        无常驻人设时降级为临时路人（不抛错）。
        """
        if self._config is None:
            raise RuntimeError("PersonaPool 尚未加载配置")

        choose_passerby = self._rng.random() < self._config.temp_passerby_ratio
        if not self._residents:
            choose_passerby = True
        if choose_passerby:
            if not self._passersby:
                self.generate_temporary_passerby()
            return self._rng.choices(
                self._passersby,
                weights=[0.5] * len(self._passersby),
                k=1,
            )[0]

        if not self._residents:
            if self._passersby:
                return self._rng.choices(
                    self._passersby,
                    weights=[0.5] * len(self._passersby),
                    k=1,
                )[0]
            raise RuntimeError("PersonaPool 中没有可选择的人设")

        weights = [1.5 if persona.role == PersonaRole.VETERAN else 1.0 for persona in self._residents]
        return self._rng.choices(self._residents, weights=weights, k=1)[0]

    def generate_temporary_passerby(self) -> Persona:
        """生成一个不持久化的临时路人人设（同步，内部无 I/O）。"""
        persona = Persona(
            user_id=f"passerby_{uuid.uuid4().hex[:8]}",
            user_nickname=self._rng.choice(self._PASSERBY_NAMES),
            role=PersonaRole.PASSERBY,
            personality="普通路人，没有特别立场",
            speaking_style="简短、口语化",
            fans_medal_level=0,
            guard_level=0,
            is_temporary=True,
        )
        self._passersby.append(persona)
        if len(self._passersby) > self._PASSERBY_POOL_CAP:
            self._passersby.pop(0)
        return persona

    def list_residents(self) -> List[Persona]:
        """返回当前可用常驻人设的列表副本。"""
        return list(self._residents)

    def get_stats(self) -> Dict[str, int]:
        """返回按角色分组的消息生成计数。"""
        return dict(self._messages_by_role)

    def record_message(self, persona: Persona) -> None:
        """记录指定人设生成了一条消息。"""
        persona.messages_generated += 1
        role = persona.role.value
        self._messages_by_role[role] = self._messages_by_role.get(role, 0) + 1

    def update_config(self, config: SimulatorConfigSchema) -> None:
        """更新运行时配置并重新应用居民筛选。"""
        self._config = config
        self._apply_resident_filter()

    def _apply_resident_filter(self) -> None:
        """根据当前配置从原始居民列表生成可用居民列表。"""
        enable_hater = self._config is not None and self._config.enable_hater
        self._residents = [
            persona for persona in self._all_residents if enable_hater or persona.role != PersonaRole.HATER
        ]
