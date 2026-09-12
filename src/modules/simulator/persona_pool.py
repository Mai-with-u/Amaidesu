"""模拟直播间观众人设池。"""

import random
import uuid
from typing import Dict, List, Optional

from src.modules.simulator.config_schema import (
    SimulatorConfigSchema,
)
from src.modules.simulator.types import Persona, PersonaRole
from src.modules.storage.repos import SimRepo


class PersonaPool:
    """人设池管理：常驻 + 临时路人

    常驻人设持久化在 SQLite ``sim_personas`` 表（运行时数据，WebUI 管理），
    本池持有内存缓存，增删改写穿 DB；临时路人是瞬时对象，仅存内存。
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

    def __init__(self, sim_repo: SimRepo, rng: Optional[random.Random] = None):
        self._rng = rng or random.Random()
        self._residents: List[Persona] = []
        self._passersby: List[Persona] = []  # temporary
        self._config: Optional[SimulatorConfigSchema] = None
        self._messages_by_role: Dict[str, int] = {}
        self._all_residents: List[Persona] = []
        self._sim = sim_repo

    async def load(self, config: SimulatorConfigSchema) -> None:
        """从 DB 加载常驻人设并应用运行时筛选配置。"""
        self._config = config
        rows = await self._sim.list_sim_personas(include_inactive=True)
        self._all_residents = [
            Persona(
                user_id=row["user_id"],
                user_nickname=row["user_nickname"],
                role=PersonaRole(row["role"]),
                personality=row["personality"],
                speaking_style=row["speaking_style"],
                fans_medal_level=row["fans_medal_level"],
                guard_level=row["guard_level"],
                context_window_size=row["context_window_size"],
                is_active=bool(row["is_active"]),
                messages_generated=row["messages_generated"],
            )
            for row in rows
        ]
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

    async def add_personas(self, personas: List[Persona]) -> int:
        """批量新增常驻人设并持久化；昵称与已有常驻人设（含本批次内）重复的项会被跳过。

        Returns:
            实际新增的人设数量。
        """
        if not personas:
            return 0
        seen = {p.user_nickname for p in self._all_residents}
        fresh: List[Persona] = []
        for persona in personas:
            if persona.user_nickname in seen:
                continue
            seen.add(persona.user_nickname)
            fresh.append(persona)
        if not fresh:
            return 0
        for persona in fresh:
            await self._sim.insert_sim_persona(
                user_id=persona.user_id,
                user_nickname=persona.user_nickname,
                role=persona.role.value,
                personality=persona.personality,
                speaking_style=persona.speaking_style,
                fans_medal_level=persona.fans_medal_level,
                guard_level=persona.guard_level,
                context_window_size=persona.context_window_size,
            )
        self._all_residents.extend(fresh)
        self._apply_resident_filter()
        return len(fresh)

    async def update_persona(self, user_id: str, fields: Dict[str, object]) -> bool:
        """按字段更新常驻人设（role 接受枚举值字符串）并持久化。

        Returns:
            True 更新成功；False 人设不存在
        """
        target = next((p for p in self._all_residents if p.user_id == user_id), None)
        if target is None:
            return False
        db_fields: Dict[str, object] = {}
        for key, value in fields.items():
            if key == "role":
                target.role = PersonaRole(str(value))
                db_fields["role"] = target.role.value
            elif hasattr(target, key):
                setattr(target, key, value)
                db_fields[key] = value
        await self._sim.update_sim_persona(user_id=user_id, fields=db_fields)
        self._apply_resident_filter()
        return True

    async def delete_persona(self, user_id: str) -> bool:
        """删除常驻人设并持久化。

        Returns:
            True 删除成功；False 人设不存在
        """
        before = len(self._all_residents)
        self._all_residents = [p for p in self._all_residents if p.user_id != user_id]
        if len(self._all_residents) == before:
            return False
        await self._sim.delete_sim_persona(user_id=user_id)
        self._apply_resident_filter()
        return True

    def get_stats(self) -> Dict[str, int]:
        """返回按角色分组的消息生成计数。"""
        return dict(self._messages_by_role)

    def record_message(self, persona: Persona) -> None:
        """记录指定人设生成了一条消息（仅运行时计数，不回写 DB）。"""
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
