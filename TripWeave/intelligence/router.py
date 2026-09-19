from datetime import datetime, timezone, timedelta

from .model import PLANNER_PROMPT
from .registry import Registry
from .schemas import Plan


class PlanningRouter:
    def __init__(self, model, registry: Registry):
        self.model = model
        self.registry = registry

    async def route(self, query: str, history: list[dict]) -> tuple[Plan, dict]:
        await self.registry.refresh()
        preferred = self.registry.candidates(query)
        payload = {"query": query, "history": history[-8:], "preferred": preferred,
                   "catalog": self.registry.catalog(self.registry.ids),
                   "current_date": datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
                   "service_discovery": self.registry.discovery_trace}
        # 全量能力目录保留给模型，避免词法召回失败直接导致能力不可达。
        for attempt in range(2):
            try:
                raw = await self.model.ask("route", PLANNER_PROMPT, payload)
                plan = self.registry.validate(Plan.model_validate(raw))
                return plan, {"preferred": preferred, "attempts": attempt + 1,
                              "selected": [s.capability for s in plan.steps],
                              "services": self.registry.discovery_trace}
            except (ValueError, TypeError, KeyError):
                payload["validation_feedback"] = "上一计划不符合 schema 或注册能力/依赖约束，请重新输出合法计划。"
        raise ValueError("两次路由结果均未通过校验，请明确查询对象和日期后重试")
