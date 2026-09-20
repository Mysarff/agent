from pathlib import Path

from .engine import Engine
from .registry import Registry
from .router import PlanningRouter

ROOT = Path(__file__).resolve().parents[1]


def build_engine(demo: bool = True, network: bool = False) -> Engine:
    # 真实模型统一使用 TripWeave 的结构化 A2A/MCP 服务。
    network = network or not demo
    registry = Registry.load(ROOT / "intelligence" / "capabilities.json")
    if demo and not network:
        from .demo import DemoModel, DemoTransport
        model, transport = DemoModel(), DemoTransport()
    else:
        from TripWeave.services.settings import agent_url, load_llm
        from TripWeave.services.rules import ProtocolDemoModel
        from .transport import A2ATransport
        for capability in registry.configured:
            if capability.handler == 'a2a':
                capability.endpoint = agent_url(capability.id)
                capability.structured_request = True
                capability.data_notice = '真实 A2A/MCP 调用；请查看返回的数据来源。样例票务与模拟订单不代表真实出票。'
        registry.discovery_enabled = True
        model = ProtocolDemoModel() if demo else load_llm()
        transport = A2ATransport(timeout=55)
    return Engine(PlanningRouter(model, registry), transport, model,
                  timeout=60 if network else 35)
