"""有限语法规则模型，仅用于无需API的可重复演示。"""
import re
from TripWeave.intelligence.session import parse_turn


class ProtocolDemoModel:
    async def ask(self, purpose, system, payload):
        if purpose == 'context':
            return parse_turn(payload['query']).model_dump(exclude_none=True)
        if purpose == 'route':
            context = payload['session']
            available = {c['id'] for c in payload['catalog']}
            if not set(context['requested']) <= available:
                return {'action': 'clarify', 'message': '所需服务不可用，请检查启动状态。'}
            steps = [{'id': c, 'capability': c, 'query': context['queries'][c],
                      'depends_on': ['tickets'] if c == 'order' and 'tickets' in context['requested'] else []}
                     for c in context['requested']]
            return {'action': 'execute', 'steps': steps}
        return {'text': '规则模式不生成自由景点建议；请配置自己的LLM。建议不包含实时核实的信息。'}


def domain_plan(kind, query, dependencies):
    day = re.search(r'\d{4}-\d{2}-\d{2}', query)
    cities = [m.group() for m in re.finditer('北京|上海|广州|深圳', query)]
    missing = {'action': 'clarify', 'message': '协议演示请明确日期(YYYY-MM-DD)、城市；预订需明确唯一票号与数量。'}
    if kind == 'weather' and day and cities:
        return {'action': 'call', 'tool': 'query_weather', 'arguments': {'city': cities[0], 'travel_date': day.group()}}
    journey = re.search(r'(北京|上海|广州|深圳)\s*(?:到|至|→)\s*(北京|上海|广州|深圳)', query)
    if kind == 'tickets' and day and journey:
        ticket_kind = 'flight' if any(w in query for w in ('机票', '航班')) else 'concert' if '演出票' in query else 'train'
        return {'action': 'call', 'tool': 'query_tickets', 'arguments': {'kind': ticket_kind, 'departure_city': journey.group(1),
                'arrival_city': journey.group(2), 'travel_date': day.group()}}
    return missing
