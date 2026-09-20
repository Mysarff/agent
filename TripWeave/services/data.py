"""可独立运行的样例库。参数化查询；模拟订单与库存同一事务更新。"""
import json
import sqlite3
import uuid
import time
from contextlib import contextmanager
from pathlib import Path


class TravelStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tickets (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, departure_city TEXT NOT NULL,
                  arrival_city TEXT NOT NULL, travel_date TEXT NOT NULL, service TEXT NOT NULL,
                  seat TEXT NOT NULL, price REAL NOT NULL, remaining INTEGER NOT NULL CHECK(remaining>=0));
                CREATE TABLE IF NOT EXISTS orders (
                  id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, ticket_id TEXT NOT NULL,
                  quantity INTEGER NOT NULL, payload TEXT NOT NULL);
            ''')
            db.execute('CREATE TABLE IF NOT EXISTS booking_quotes (id TEXT PRIMARY KEY, expires_at REAL NOT NULL, payload TEXT NOT NULL, used_order_id TEXT)')
            # 固定日期及虚构编号，避免自建数据冒充实时票价和库存。
            db.executemany('INSERT OR IGNORE INTO tickets VALUES (?,?,?,?,?,?,?,?,?)', [
                ('DEMO-TRAIN-001', 'train', '北京', '上海', '2026-10-01', 'DEMO-G101', '二等座', 500, 20),
                ('DEMO-TRAIN-002', 'train', '北京', '上海', '2026-10-01', 'DEMO-G102', '一等座', 650, 6),
                ('DEMO-FLIGHT-001', 'flight', '北京', '上海', '2026-10-01', 'DEMO-CA101', '经济舱', 800, 10),
                ('DEMO-CONCERT-001', 'concert', '上海', '上海', '2026-10-01', '示例音乐会', '看台', 300, 8),
            ])

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def tickets(self, kind, departure_city, arrival_city, travel_date):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM tickets WHERE kind=? AND departure_city=? AND arrival_city=? AND travel_date=? ORDER BY price,id LIMIT 20',
                              (kind, departure_city, arrival_city, travel_date)).fetchall()
        return {'status': 'success' if rows else 'no_data', 'source': '自建演示 SQLite 票务库，不是实时票务',
                'data': [dict(row) for row in rows]}

    def prepare(self, ticket_id, quantity):
        if type(quantity) is not int or not 1 <= quantity <= 5:
            raise ValueError('数量必须为1至5的整数')
        with self.connection(write=True) as db:
            row = db.execute('SELECT * FROM tickets WHERE id=?', (ticket_id,)).fetchone()
            if not row or row['remaining'] < quantity:
                return {'status': 'no_data', 'message': '票号不存在或当前库存不足，请重新查询；未创建订单'}
            quote_id = 'QUOTE-' + uuid.uuid4().hex
            expires = time.time() + 300
            quote = {'quote_id': quote_id, 'ticket': dict(row), 'quantity': quantity,
                     'amount': row['price'] * quantity, 'expires_at': expires}
            db.execute('INSERT INTO booking_quotes(id,expires_at,payload) VALUES (?,?,?)',
                       (quote_id, expires, json.dumps(quote, ensure_ascii=False)))
            return {'status': 'success', 'quote': quote,
                    'source': '本地模拟报价，5分钟有效；未锁库存、未扣款、未创建订单'}

    def book(self, quote_id, request_id):
        if not request_id or len(request_id) > 128:
            raise ValueError('无效请求ID')
        with self.connection(write=True) as db:
            previous = db.execute('SELECT * FROM orders WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                payload = json.loads(previous['payload'])
                if payload.get('quote_id') != quote_id:
                    raise ValueError('相同请求ID不能用于不同报价单')
                return {**payload, 'replayed': True}
            record = db.execute('SELECT * FROM booking_quotes WHERE id=?', (quote_id,)).fetchone()
            if not record:
                return {'status': 'no_data', 'message': '确认单不存在，请重新发起预订'}
            if record['used_order_id']:
                row = db.execute('SELECT payload FROM orders WHERE id=?', (record['used_order_id'],)).fetchone()
                return {**json.loads(row['payload']), 'replayed': True}
            if record['expires_at'] <= time.time():
                return {'status': 'no_data', 'message': '确认单已过期，请重新查询并确认；未创建订单'}
            quote = json.loads(record['payload'])
            ticket, quantity = quote['ticket'], quote['quantity']
            row = db.execute('SELECT * FROM tickets WHERE id=?', (ticket['id'],)).fetchone()
            if not row or row['remaining'] < quantity:
                return {'status': 'no_data', 'message': '确认时库存不足或票已移除，请重新查询；未创建订单'}
            fields = ('price', 'kind', 'departure_city', 'arrival_city', 'travel_date', 'service', 'seat')
            if any(row[k] != ticket[k] for k in fields):
                return {'status': 'no_data', 'message': '价格或行程已变化，请重新查询并确认；未创建订单'}
            order_id = 'SIM-' + uuid.uuid4().hex[:12].upper()
            result = {'status': 'success', 'order_id': order_id, 'quote_id': quote_id,
                      'ticket_id': ticket['id'], 'quantity': quantity, 'amount': row['price'] * quantity,
                      'source': '本地模拟订单，无支付、无真实出票'}
            changed = db.execute('UPDATE tickets SET remaining=remaining-? WHERE id=? AND remaining>=?',
                                 (quantity, ticket['id'], quantity)).rowcount
            if changed != 1:
                raise ValueError('库存更新失败')
            db.execute('INSERT INTO orders VALUES (?,?,?,?,?)',
                       (order_id, request_id, ticket['id'], quantity, json.dumps(result, ensure_ascii=False)))
            db.execute('UPDATE booking_quotes SET used_order_id=? WHERE id=?', (order_id, quote_id))
            return {**result, 'replayed': False}


def render_business(kind, value):
    if value.get('status') != 'success':
        return value.get('message', '当前条件没有匹配记录。')
    if 'quote' in value:
        q = value['quote']; t = q['ticket']
        return (f"请确认模拟预订：{t['travel_date']} {t['departure_city']} → {t['arrival_city']}，"
                f"{t['service']} / {t['seat']}，票号 {t['id']}，{q['quantity']} 张，"
                f"单价 {t['price']} 元，总价 {q['amount']} 元。\n"
                '确认单5分钟有效，尚未锁库存；确认时会复查库存和价格。无真实出票或支付。')
    if 'order_id' in value:
        return f"模拟订单 {value['order_id']}：{value['ticket_id']}，{value['quantity']} 张，合计 {value['amount']} 元。无真实出票或支付。"
    if kind == 'tickets':
        lines = [f"{i}. {t['service']} / {t['seat']} | {t['departure_city']} → {t['arrival_city']} | {t['travel_date']} | {t['price']} 元 | 余量 {t['remaining']} | 票号 {t['id']}"
                 for i,t in enumerate(value['data'],1)]
        return '\n\n'.join(lines) + '\n\n可回复“订第二个，1张”等；序号仅对应本次列表。数据均为虚构样例。'
    if kind == 'weather' and 'temperature_max' in value:
        return f"{value['city']} {value['date']}：{value['temperature_min']}–{value['temperature_max']}℃。{value['source']}"
    return json.dumps(value, ensure_ascii=False)
