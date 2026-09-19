import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = {'weather': ('WeatherAgent', 5005), 'tickets': ('TicketAgent', 5006), 'order': ('OrderAgent', 5007)}


def agent_url(kind):
    return os.getenv(f'SMARTVOYAGE_{kind.upper()}_URL', f'http://127.0.0.1:{AGENTS[kind][1]}').rstrip('/')


def mcp_url():
    return os.getenv('SMARTVOYAGE_MCP_URL', 'http://127.0.0.1:8001/mcp')


def db_path():
    return os.getenv('SMARTVOYAGE_DB', str(ROOT / 'var/travel.sqlite3'))


def load_llm():
    from langchain_openai import ChatOpenAI
    from SmartVoyage.intelligence.model import JsonModel, LoopIndependentChat
    key, name = os.getenv('SMARTVOYAGE_API_KEY'), os.getenv('SMARTVOYAGE_MODEL')
    if not key or not name:
        raise ValueError('模型模式需要自己的 SMARTVOYAGE_API_KEY 和 SMARTVOYAGE_MODEL')
    options = dict(api_key=key, model=name, temperature=0, timeout=25, max_retries=0)
    if os.getenv('SMARTVOYAGE_BASE_URL'):
        options['base_url'] = os.environ['SMARTVOYAGE_BASE_URL']
    return JsonModel(LoopIndependentChat(ChatOpenAI(**options)))
