import json
import asyncio
import httpx
from pathlib import Path
from urllib.parse import urlparse

from .retrieval import BM25
from .schemas import Capability, Plan


class Registry:
    def __init__(self, capabilities: list[Capability]):
        self.discovery_enabled = False
        self.configured = capabilities
        self.discovery_trace = []
        self.items = {c.id: c for c in capabilities}
        if len(self.items) != len(capabilities):
            raise ValueError("能力 ID 重复")
        for c in capabilities:
            if c.handler == "a2a":
                url = urlparse(c.endpoint or "")
                if url.scheme not in ("http", "https") or not url.hostname or url.username:
                    raise ValueError(f"能力 {c.id} 的服务地址无效")
            elif c.endpoint:
                raise ValueError("本地能力不能配置远程地址")
        self.ids = list(self.items)
        self.index = BM25([c.description + " " + " ".join(c.examples) for c in capabilities])

    async def refresh(self):
        if not self.discovery_enabled:
            return
        async def inspect(cap):
            if cap.handler != 'a2a':
                return cap, {'capability': cap.id, 'status': 'local'}
            try:
                async with httpx.AsyncClient(timeout=3, follow_redirects=False) as client:
                    response = await client.get(cap.endpoint.rstrip('/') + '/.well-known/agent.json')
                    response.raise_for_status()
                    card = response.json()
                if card.get('url', '').rstrip('/') != cap.endpoint.rstrip('/'):
                    raise ValueError('AgentCard 地址与可信配置不符')
                if cap.id not in {s.get('id') for s in card.get('skills', [])}:
                    raise ValueError('AgentCard 未声明所需能力')
                return cap, {'capability': cap.id, 'status': 'available', 'agent': card.get('name'), 'endpoint': cap.endpoint}
            except Exception as exc:
                return None, {'capability': cap.id, 'status': 'unavailable', 'reason': type(exc).__name__}
        found = await asyncio.gather(*(inspect(c) for c in self.configured))
        self.discovery_trace = [status for _, status in found]
        self.items = {cap.id: cap for cap, _ in found if cap is not None}
        self.ids = list(self.items)
        self.index = BM25([self.items[i].description + ' ' + ' '.join(self.items[i].examples) for i in self.ids])

    @classmethod
    def load(cls, path: Path):
        return cls([Capability.model_validate(c) for c in json.loads(path.read_text(encoding="utf-8"))])

    def candidates(self, query: str, limit: int = 4) -> list[str]:
        hits = self.index.search(query, limit)
        return [self.ids[i] for i, _ in hits] or self.ids

    def catalog(self, ids: list[str]) -> list[dict]:
        # 地址与 handler 留在可信执行层，模型只选择已注册 ID。
        return [{"id": c.id, "description": c.description, "examples": c.examples,
                 "requires_confirmation": c.requires_confirmation}
                for c in (self.items[i] for i in ids)]

    def validate(self, plan: Plan) -> Plan:
        ids = {s.id for s in plan.steps}
        write_steps = set()
        for step in plan.steps:
            if step.capability not in self.items:
                raise ValueError(f"未知能力 {step.capability}")
            if len(set(step.depends_on)) != len(step.depends_on):
                raise ValueError("依赖重复")
            if not set(step.depends_on) <= ids or step.id in step.depends_on:
                raise ValueError("依赖不存在或依赖自身")
            if self.items[step.capability].requires_confirmation:
                write_steps.add(step.id)
        if len(write_steps) > 1:
            raise ValueError("单次计划最多一个需确认操作")
        if any(write_steps & set(s.depends_on) for s in plan.steps):
            raise ValueError("需确认操作必须是终点")
        done = set()
        while len(done) < len(ids):
            ready = {s.id for s in plan.steps if s.id not in done and set(s.depends_on) <= done}
            if not ready:
                raise ValueError("任务依赖存在环")
            done.update(ready)
        return plan
