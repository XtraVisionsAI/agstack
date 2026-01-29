#  Copyright (c) 2020-2025 XtraVisions, All rights reserved.

import uuid
from datetime import datetime

from casbin import persist
from casbin.persist.adapters.asyncio import AsyncAdapter, AsyncFilteredAdapter
from sqlobjects import ObjectModel
from sqlobjects.database import get_database
from sqlobjects.fields import Column, column


class PolicyFilter:
    """策略过滤条件"""

    def __init__(
        self,
        ptype: list[str] | None = None,
        v0: list[str] | None = None,
        v1: list[str] | None = None,
        v2: list[str] | None = None,
    ):
        self.ptype = ptype
        self.v0 = v0
        self.v1 = v1
        self.v2 = v2


class CasbinRules(ObjectModel):
    """Casbin 策略规则模型"""

    id: Column[uuid.UUID] = column(type="uuid", primary_key=True, default_factory=uuid.uuid4)
    ptype: Column[str] = column(type="string", length=100, nullable=False)
    v0: Column[str | None] = column(type="string", length=100)
    v1: Column[str | None] = column(type="string", length=100)
    v2: Column[str | None] = column(type="string", length=100)
    v3: Column[str | None] = column(type="string", length=100)
    v4: Column[str | None] = column(type="string", length=100)
    v5: Column[str | None] = column(type="string", length=100)
    created_at: Column[datetime] = column(type="datetime", default_factory=datetime.now)

    class Config:
        table_name = "system_casbin_rule"


class SqlObjectsAdapter(AsyncAdapter, AsyncFilteredAdapter):
    """基于 sqlobjects 的 Casbin Adapter（支持过滤加载）"""

    _inited: bool = False

    @classmethod
    async def create(cls, db_name: str | None = None):
        if not cls._inited:
            db = get_database(db_name)
            await db.create_tables(CasbinRules)

        return cls()

    def __init__(self):
        self._filtered = False

    async def load_policy(self, model):
        rules = await CasbinRules.objects.all()
        for rule in rules:
            line = self._rule_to_line(rule)
            persist.load_policy_line(line, model)
        self._filtered = False

    async def load_filtered_policy(self, model, filter: PolicyFilter):
        query = CasbinRules.objects

        if filter.ptype:
            query = query.filter(ptype__in=filter.ptype)
        if filter.v0:
            query = query.filter(v0__in=filter.v0)
        if filter.v1:
            query = query.filter(v1__in=filter.v1)
        if filter.v2:
            query = query.filter(v2__in=filter.v2)

        rules = await query.all()
        for rule in rules:
            line = self._rule_to_line(rule)
            persist.load_policy_line(line, model)
        self._filtered = True

    async def is_filtered(self) -> bool:  # type: ignore
        return self._filtered

    async def save_policy(self, model):
        await CasbinRules.objects.delete_all()
        for sec in ["p", "g"]:
            if model.model.get(sec) is None:
                continue
            for ptype, ast in model.model[sec].items():
                for rule in ast.policy:
                    await self._save_policy_line(ptype, rule)

    async def add_policy(self, sec: str, ptype: str, rule: list[str]):
        await self._save_policy_line(ptype, rule)

    async def remove_policy(self, sec: str, ptype: str, rule: list[str]):
        query = CasbinRules.objects.filter(CasbinRules.ptype == ptype)
        for i, value in enumerate(rule):
            if value:
                field = getattr(CasbinRules, f"v{i}")
                query = query.filter(field == value)
        await query.delete()

    async def remove_filtered_policy(self, sec: str, ptype: str, field_index: int, *field_values):
        query = CasbinRules.objects.filter(CasbinRules.ptype == ptype)
        for i, value in enumerate(field_values):
            if value:
                field = getattr(CasbinRules, f"v{field_index + i}")
                query = query.filter(field == value)
        await query.delete()

    async def _save_policy_line(self, ptype: str, rule: list[str]):
        rule_obj = CasbinRules(
            ptype=ptype,
            v0=rule[0] if len(rule) > 0 else None,
            v1=rule[1] if len(rule) > 1 else None,
            v2=rule[2] if len(rule) > 2 else None,
            v3=rule[3] if len(rule) > 3 else None,
            v4=rule[4] if len(rule) > 4 else None,
            v5=rule[5] if len(rule) > 5 else None,
        )
        await rule_obj.save()

    def _rule_to_line(self, rule: CasbinRules) -> str:
        parts = [rule.ptype]
        for i in range(6):
            value = getattr(rule, f"v{i}")
            if value:
                parts.append(value)
        return ", ".join(parts)
