"""协议演示用规则模型：明确区别于真实 LLM，不伪造模型效果指标。"""
import re
from SmartVoyage.intelligence.demo import DemoModel


class ProtocolDemoModel(DemoModel):
    async def ask(self, purpose, system, payload):
        if purpose != 'route':
            return await super().ask(purpose, system, payload)
        query = payload['query']
        steps = []
        for capability, words in [('weather', ('天气', '温度', '下雨')), ('tickets', ('火车', '高铁', '机票', '航班', '演出票')),
                                   ('knowledge', ('资料', '项目', '来源', '说明'))]:
            if any(w in query for w in words):
                steps.append({'id': capability, 'capability': capability, 'query': query})
        if any(w in query for w in ('预订', '订票', '下单')):
            # 演示明确票号可直接下单，其余情况要求先查票并传递结果。
            deps = [s['id'] for s in steps if s['capability'] == 'tickets']
            steps.append({'id': 'order', 'capability': 'order', 'query': query, 'depends_on': deps})
        if not steps:
            return {'action': 'clarify', 'message': '协议演示支持天气、票务、项目资料和模拟预订；自由理解请配置LLM。', 'steps': []}
        return {'action': 'execute', 'steps': steps}


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
    if kind == 'order':
        ticket = re.search(r'DEMO-(?:TRAIN|FLIGHT|CONCERT)-\d{3}', query, re.I)
        quantity = re.search(r'(\d+)\s*张', query)
        ids = re.findall(r'DEMO-(?:TRAIN|FLIGHT|CONCERT)-\d{3}', str(dependencies))
        ticket_id = ticket.group().upper() if ticket else next(iter(set(ids))) if len(set(ids)) == 1 else None
        if ticket_id and quantity:
            return {'action': 'call', 'tool': 'book_simulated_ticket', 'arguments': {'ticket_id': ticket_id, 'quantity': int(quantity.group(1))}}
    return missing
