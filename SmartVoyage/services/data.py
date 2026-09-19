"""可独立运行的样例库。参数化查询；模拟订单与库存同一事务更新。"""
import json
import sqlite3
import uuid
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
            # 固定日期及虚构编号，避免自建数据冒充实时票价和库存。
            db.executemany('INSERT OR IGNORE INTO tickets VALUES (?,?,?,?,?,?,?,?,?)', [
                ('DEMO-TRAIN-001', 'train', '北京', '上海', '2026-10-01', 'DEMO-G101', '二等座', 500, 20),
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
            rows = db.execute('SELECT * FROM tickets WHERE kind=? AND departure_city=? AND arrival_city=? AND travel_date=? ORDER BY price LIMIT 20',
                              (kind, departure_city, arrival_city, travel_date)).fetchall()
        return {'status': 'success' if rows else 'no_data', 'source': '自建演示 SQLite 票务库，不是实时票务',
                'data': [dict(row) for row in rows]}

    def book(self, ticket_id, quantity, request_id):
        if not 1 <= quantity <= 5:
            raise ValueError('单次模拟订单数量必须为1至5')
        with self.connection(write=True) as db:
            previous = db.execute('SELECT * FROM orders WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                if previous['ticket_id'] != ticket_id or previous['quantity'] != quantity:
                    raise ValueError('相同请求ID不能用于不同订单参数')
                return {**json.loads(previous['payload']), 'replayed': True}
            row = db.execute('SELECT * FROM tickets WHERE id=?', (ticket_id,)).fetchone()
            if not row or row['remaining'] < quantity:
                return {'status': 'no_data', 'message': '样例票不存在或模拟库存不足，未创建订单'}
            order_id = 'SIM-' + uuid.uuid4().hex[:12].upper()
            result = {'status': 'success', 'order_id': order_id, 'ticket_id': ticket_id, 'quantity': quantity,
                      'amount': row['price'] * quantity, 'source': '本地模拟订单，无支付、无真实出票'}
            db.execute('UPDATE tickets SET remaining=remaining-? WHERE id=?', (quantity, ticket_id))
            db.execute('INSERT INTO orders VALUES (?,?,?,?,?)',
                       (order_id, request_id, ticket_id, quantity, json.dumps(result, ensure_ascii=False)))
            return {**result, 'replayed': False}
