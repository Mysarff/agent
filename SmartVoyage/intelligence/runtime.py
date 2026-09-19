import os
from pathlib import Path

from .engine import Engine
from .knowledge import KnowledgeAgent
from .registry import Registry
from .retrieval import KnowledgeIndex
from .router import PlanningRouter

ROOT = Path(__file__).resolve().parents[1]


def build_engine(demo: bool = True, network: bool = False) -> Engine:
    registry = Registry.load(ROOT / "intelligence" / "capabilities.json")
    index = KnowledgeIndex(ROOT / "knowledge")
    if demo and not network:
        from .demo import DemoModel, DemoTransport
        model, transport = DemoModel(), DemoTransport()
    elif network:
        from SmartVoyage.services.settings import agent_url, load_llm
        from SmartVoyage.services.rules import ProtocolDemoModel
        from .transport import A2ATransport
        for capability in registry.configured:
            if capability.handler == 'a2a':
                capability.endpoint = agent_url(capability.id)
                capability.structured_request = True
                capability.data_notice = '真实 A2A/MCP 调用；请查看返回的数据来源。样例票务与模拟订单不代表真实出票。'
        registry.discovery_enabled = True
        model = ProtocolDemoModel() if demo else load_llm()
        transport = A2ATransport(timeout=55)
    else:
        from langchain_openai import ChatOpenAI
        from .model import JsonModel, LoopIndependentChat
        from .transport import A2ATransport
        key = os.getenv("SMARTVOYAGE_API_KEY")
        name = os.getenv("SMARTVOYAGE_MODEL")
        if not key or not name:
            raise ValueError("请先设置自己的 SMARTVOYAGE_API_KEY 和 SMARTVOYAGE_MODEL；可选 SMARTVOYAGE_BASE_URL。")
        options = {"model": name, "api_key": key, "temperature": 0, "timeout": 25, "max_retries": 0}
        if os.getenv("SMARTVOYAGE_BASE_URL"):
            options["base_url"] = os.environ["SMARTVOYAGE_BASE_URL"]
        model, transport = JsonModel(LoopIndependentChat(ChatOpenAI(**options))), A2ATransport()
    return Engine(PlanningRouter(model, registry), KnowledgeAgent(model, index), transport, model,
                  timeout=60 if network else 35)
