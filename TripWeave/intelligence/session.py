"""会话内槽位、最新候选和待补信息。只从当前用户输入及结构化工具结果更新。"""
import re
import time
from datetime import date, datetime, timedelta, timezone

from pydantic import Field
from typing import Literal
from .schemas import StrictModel, Ticket


class Turn(StrictModel):
    intents: list[Literal['weather', 'tickets', 'order', 'attractions']] = Field(default_factory=list)
    travel_date: str | None = None
    city: str | None = None
    departure_city: str | None = None
    arrival_city: str | None = None
    kind: Literal['train', 'flight', 'concert'] | None = None
    ticket_id: str | None = None
    candidate_index: int | None = Field(default=None, ge=1, le=20)
    quantity: int | None = Field(default=None, ge=1, le=5)
    previous: bool = False
    cancel: bool = False


CONTEXT_PROMPT = '''从当前 query 提取旅行意图和明确说出的字段，只输出 JSON，不补全未提到的信息。
schema: {intents:[weather|tickets|order|attractions],travel_date?:YYYY-MM-DD,city?:string,
departure_city?:string,arrival_city?:string,kind?:train|flight|concert,ticket_id?:string,
candidate_index?:int,quantity?:int,previous?:bool,cancel?:bool}。
意图可多个。只想查票不能标记order；用户要求订票/预订才是order。
"第二个"是candidate_index=2，"刚才那张"是previous=true；不能猜它的票号。
"1张"是quantity=1，缺失数量不能默认为1。"同一天"不填写日期，交程序继承。
仅支持ISO日期、月日、今天/明天/后天，其他日期表达先不填。城市值应是用户原文中的名称。
只补充字段时intents=[]，由程序决定是否接续追问。文档问答不属于本项目能力。'''


def parse_date(query, today=None):
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    if len(set(re.findall(r'\d{4}-\d{2}-\d{2}', query))) > 1:
        raise ValueError('当前只维护一组出行日期，请分开查询不同日期')
    match = re.search(r'\d{4}-\d{2}-\d{2}', query)
    if match:
        return date.fromisoformat(match.group()).isoformat()
    match = re.search(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]?', query)
    if match:
        return date(int(match[1] or today.year), int(match[2]), int(match[3])).isoformat()
    for word, offset in [('后天', 2), ('明天', 1), ('今天', 0)]:
        if word in query:
            return (today + timedelta(days=offset)).isoformat()
    return None


def number(value):
    digits = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    return int(value) if value.isdigit() else digits[value]


def parse_turn(query):
    """免密钥演示语法：有限城市和日期；不宣称通用自然语言理解。"""
    values = {'intents': [], 'travel_date': parse_date(query)}
    booking = any(w in query for w in ('预订', '订票', '下单', '订一', '订两', '订三', '订第', '订刚', '订这', '订那'))
    if any(w in query for w in ('天气', '温度', '下雨')):
        values['intents'].append('weather')
    if any(w in query for w in ('查票', '查车票', '查火车', '查机票', '查航班', '查演出票')) or (
        any(w in query for w in ('火车', '高铁', '机票', '航班', '演出票', '车票')) and
        (not booking or '查' in query or re.search(r'(北京|上海|广州|深圳)到(北京|上海|广州|深圳)', query))):
        values['intents'].append('tickets')
    if booking:
        values['intents'].append('order')
    if any(w in query for w in ('景点', '游玩', '攻略')):
        values['intents'].append('attractions')
    city_pattern = r'北京|上海|广州|深圳'
    cities = re.findall(city_pattern, query)
    if cities:
        values['city'] = cities[0]
    journey = re.search(rf'({city_pattern})\s*(?:到|至|→)\s*({city_pattern})', query)
    if journey:
        values.update(departure_city=journey[1], arrival_city=journey[2])
    else:
        departure = re.search(rf'从\s*({city_pattern})', query)
        arrival = re.search(rf'(?:去|到|至)\s*({city_pattern})', query)
        if departure:
            values['departure_city'] = departure[1]
        if arrival:
            values['arrival_city'] = arrival[1]
    if any(w in query for w in ('火车', '高铁', '车票')):
        values['kind'] = 'train'
    elif any(w in query for w in ('机票', '航班', '飞机')):
        values['kind'] = 'flight'
    elif '演出票' in query:
        values['kind'] = 'concert'
    ticket = re.search(r'DEMO-(?:TRAIN|FLIGHT|CONCERT)-\d{3}', query, re.I)
    if ticket:
        values['ticket_id'] = ticket.group().upper()
    index = re.search(r'第\s*(\d+|[一二三四五六七八九十])\s*[个张班条种]', query)
    quantity_query = query[:index.start()] + query[index.end():] if index else query
    quantity = re.search(r'(\d+|[一二两三四五六七八九十])\s*张', quantity_query)
    if index:
        values['candidate_index'] = number(index[1])
    if quantity:
        values['quantity'] = number(quantity[1])
    values['previous'] = any(w in query for w in ('刚才', '那张', '这张', '那个', '这个'))
    values['cancel'] = any(w in query for w in ('取消预订', '取消订票', '不订了', '不要订', '不预订', '不下单')) or query.strip() == '取消'
    return Turn.model_validate(values)


class SessionState:
    def __init__(self):
        self.slots = {}
        self.candidates = []
        self.candidate_at = 0.0
        self.awaiting = []
        self.selected_id = None
        self.selected_index = None
        self.quantity = None
        self.previous = False
        self.requested = []

    def clear_booking(self):
        self.selected_id = self.selected_index = self.quantity = None
        self.previous = False
        self.awaiting = []

    def clear_candidates(self):
        self.candidates = []
        self.candidate_at = 0.0
        self.selected_id = self.selected_index = None

    def snapshot(self):
        return {'slots': dict(self.slots), 'candidate_count': len(self.candidates),
                'selected_id': self.selected_id, 'quantity': self.quantity,
                'awaiting': list(self.awaiting)}

    def update(self, turn, query):
        if turn.cancel:
            self.clear_booking()
            self.requested = []
            return '已取消待处理的模拟预订。'
        supplied = turn.model_dump(exclude_none=True)
        meaningful = any(supplied.get(k) for k in ('travel_date', 'city', 'departure_city', 'arrival_city', 'kind',
                                                  'ticket_id', 'candidate_index', 'quantity', 'previous'))
        requested = list(dict.fromkeys(turn.intents or (self.awaiting if meaningful else [])))
        self.requested = requested
        if not requested:
            return '请说明要查天气、查票、模拟预订还是获取景点建议；本项目暂不提供文档问答。'
        # 只回答一个城市时，优先补上正在追问的查票城市，不能覆盖已知天气城市。
        if not turn.intents and 'tickets' in requested and turn.city and not turn.departure_city and not turn.arrival_city:
            if self.slots.get('departure_city') and not self.slots.get('arrival_city'):
                turn.arrival_city = turn.city
            elif not self.slots.get('departure_city'):
                turn.departure_city = turn.city
        # 新话题终止未完成订票，避免一条补充信息错误触发旧订单。
        if turn.intents and 'order' not in requested:
            self.clear_booking()
        changed = False
        for key in ('travel_date', 'city', 'departure_city', 'arrival_city', 'kind'):
            value = getattr(turn, key)
            if key == 'city' and 'weather' not in requested and (turn.arrival_city or turn.departure_city):
                continue
            if value is not None:
                if key != 'city' and value != self.slots.get(key):
                    changed = True
                self.slots[key] = value
        if 'tickets' in requested:
            if not self.slots.get('departure_city') and self.slots.get('city'):
                self.slots['departure_city'] = self.slots['city']
            if self.slots.get('kind') == 'concert' and self.slots.get('city'):
                self.slots['departure_city'] = self.slots['arrival_city'] = self.slots['city']
        if changed or 'tickets' in requested:
            self.clear_candidates()
            if changed:
                self.quantity = None
        if 'order' in requested:
            if turn.ticket_id:
                self.selected_id, self.selected_index = turn.ticket_id, None
            if turn.candidate_index is not None:
                self.selected_index, self.selected_id = turn.candidate_index, None
            if turn.quantity is not None:
                self.quantity = turn.quantity
            self.previous = turn.previous
        missing = []
        required = {'weather': ('city', 'travel_date'), 'tickets': ('kind', 'departure_city', 'arrival_city', 'travel_date')}
        labels = {'city': '天气城市', 'travel_date': '日期', 'kind': '票种（火车/机票/演出票）',
                  'departure_city': '出发城市', 'arrival_city': '到达城市'}
        for intent in requested:
            for key in required.get(intent, ()):
                if not self.slots.get(key) and labels[key] not in missing:
                    missing.append(labels[key])
        self.awaiting = requested if missing else []
        return '请补充：' + '、'.join(missing) + '。' if missing else ''

    def query_for(self, intent, original):
        s = self.slots
        if intent == 'weather':
            return f"查询{s['city']}{s['travel_date']}的天气"
        if intent == 'tickets':
            label = {'train': '火车票', 'flight': '机票', 'concert': '演出票'}[s['kind']]
            return f"查询{s['departure_city']}到{s['arrival_city']}{s['travel_date']}的{label}"
        return original

    def absorb_tickets(self, result):
        selected_id, selected_index = self.selected_id, self.selected_index
        self.clear_candidates()
        if result.status == 'success':
            rows = result.data.get('data', [])
            required = {'id', 'kind', 'departure_city', 'arrival_city', 'travel_date', 'price', 'remaining', 'service', 'seat'}
            if not isinstance(rows, list) or any(not isinstance(r, dict) or not required <= r.keys() for r in rows):
                raise ValueError('票务结构化结果缺字段')
            rows = [Ticket.model_validate(r).model_dump() for r in rows]
            if len({r['id'] for r in rows}) != len(rows):
                raise ValueError('候选票号重复')
            self.candidates = rows
            self.candidate_at = time.monotonic()
            self.selected_id, self.selected_index = selected_id, selected_index

    def booking_arguments(self):
        if self.candidates and time.monotonic() - self.candidate_at > 600:
            self.clear_candidates()
            return None, '候选列表已超过10分钟，请重新查票后再选择。'
        selected = self.selected_id
        if not selected and self.selected_index is not None:
            if not 1 <= self.selected_index <= len(self.candidates):
                return None, '该序号不在当前候选列表中，请重新查票或选择有效序号。'
            selected = self.candidates[self.selected_index - 1]['id']
        if not selected and len(self.candidates) == 1:
            selected = self.candidates[0]['id']
        if not selected:
            return None, '请先查票并选择第几个，或明确票号；多个候选不能仅用“刚才那张”确定。'
        self.selected_id = selected
        if self.quantity is None:
            return None, '已记住所选票，请补充预订数量（1至5张）。'
        return {'ticket_id': selected, 'quantity': self.quantity}, ''
