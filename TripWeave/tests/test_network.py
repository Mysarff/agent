"""真实本地 HTTP A2A + Streamable HTTP MCP + SQLite，模型采用可重复的规则。"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from TripWeave.intelligence.runtime import build_engine
from TripWeave.services.data import TravelStore
from TripWeave.services.domain_agent import DomainAgent
from TripWeave.services.stack import stop_children


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / 'travel.db'
        ports = []
        while len(ports) < 4:
            port = free_port()
            if port not in ports:
                ports.append(port)
        cls.env = {'TRIPWEAVE_MODEL_MODE': 'rules', 'TRIPWEAVE_DB': str(cls.path),
                   'TRIPWEAVE_WEATHER_PROVIDER': 'sample', 'TRIPWEAVE_MCP_URL': f'http://127.0.0.1:{ports[0]}/mcp'}
        for kind, port in zip(('weather', 'tickets', 'order'), ports[1:]):
            cls.env[f'TRIPWEAVE_{kind.upper()}_URL'] = f'http://127.0.0.1:{port}'
        cls.children, cls.logs = [], []
        commands = [['-m', 'TripWeave.services.mcp_tools', '--port', str(ports[0])]]
        commands += [['-m', 'TripWeave.services.domain_agent', '--kind', kind, '--port', str(port)]
                     for kind, port in zip(('weather', 'tickets', 'order'), ports[1:])]
        try:
            for i, command in enumerate(commands):
                log = open(Path(cls.temp.name) / f'service-{i}.log', 'w', encoding='utf-8')
                cls.logs.append(log)
                cls.children.append(subprocess.Popen([sys.executable, *command], cwd=Path(__file__).resolve().parents[2],
                    env={**os.environ, **cls.env}, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if any(p.poll() is not None for p in cls.children):
                    raise RuntimeError('网络测试服务提前退出')
                try:
                    for port in ports:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2):
                            pass
                    return
                except OSError:
                    time.sleep(.2)
            raise TimeoutError('测试服务未就绪')
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        stop_children(cls.children)
        for log in cls.logs:
            log.close()
        cls.temp.cleanup()

    def test_cli_queries_current_network_services(self):
        result = subprocess.run([sys.executable, '-m', 'TripWeave.main', '--network', '--question',
                                 '北京2026-10-01的天气'],
            cwd=Path(__file__).resolve().parents[2],
            env={**os.environ, **self.env, 'PYTHONIOENCODING': 'utf-8'},
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('真实 A2A/MCP 协议演示（规则模型）', result.stdout)
        self.assertIn('24', result.stdout)
        self.assertIn('available', result.stdout)
        self.assertNotIn('路由未完成', result.stdout)

    def test_full_multi_agent_handoff_and_confirmation(self):
        with patch.dict(os.environ, self.env):
            engine = build_engine(demo=True, network=True)
            result = asyncio.run(engine.run('查询北京2026-10-01的天气和北京到上海2026-10-01的火车票，帮我模拟预订第二个，1张'))
            self.assertEqual([r.status for r in result.results], ['success', 'success', 'awaiting_confirmation'])
            self.assertEqual(result.routing_trace['selected'], ['weather', 'tickets', 'order'])
            self.assertEqual(sum(s['status'] == 'available' for s in result.routing_trace['services']), 3)
            for r in result.results[:2]:
                self.assertEqual([e['protocol'] for e in r.trace], ['A2A', 'MCP', 'MCP'])
            store = TravelStore(self.path)
            with store.connection() as db:
                before = db.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
            confirmed = asyncio.run(engine.confirm(result.pending_token))
            self.assertEqual(confirmed.status, 'success')
            self.assertIn('SIM-', confirmed.text)
            self.assertEqual(confirmed.trace[0]['dependencies'], ['tickets'])
            self.assertEqual(confirmed.data['ticket_id'], 'DEMO-TRAIN-002')
            self.assertEqual(confirmed.trace[-1]['tool'], 'book_simulated_ticket')
            self.assertEqual(asyncio.run(engine.confirm(result.pending_token)).status, 'blocked')
            with store.connection() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0], before + 1)
            evidence = Path(__file__).resolve().parents[2] / 'reports/network_execution_example.json'
            evidence.parent.mkdir(exist_ok=True)
            evidence.write_text(json.dumps({'scope': 'Real local protocols with rule model and sample data',
                'run': result.model_dump(), 'confirmed': confirmed.model_dump()}, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_page_network_mode_and_confirmation(self):
        from streamlit.testing.v1 import AppTest
        with patch.dict(os.environ, {**self.env, 'TRIPWEAVE_STACK': '1', 'TRIPWEAVE_ACCESS_PASSWORD': ''}):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=25)
            self.assertEqual(app.sidebar.selectbox[0].value, '真实协议演示（规则模型）')
            app.chat_input[0].set_value('北京到上海2026-10-01的火车票，帮我模拟预订第二个，1张').run(timeout=25)
            self.assertFalse(app.exception)
            self.assertTrue(any('MCP' in str(item.value) for item in app.json))
            next(b for b in app.button if b.label == '确认模拟操作').click().run(timeout=25)
            self.assertFalse(app.exception)
            self.assertTrue(any('SIM-' in m.value for m in app.markdown))

    def test_real_mcp_discovery_and_parameterized_query(self):
        async def check():
            async with streamablehttp_client(self.env['TRIPWEAVE_MCP_URL']) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.assertEqual({t.name for t in (await session.list_tools()).tools},
                                     {'query_weather', 'query_tickets', 'prepare_simulated_booking', 'book_simulated_ticket'})
                    result = await session.call_tool('query_tickets', {'kind': 'train', 'departure_city': "北京' OR 1=1 --",
                        'arrival_city': '上海', 'travel_date': '2026-10-01'})
                    self.assertFalse(result.isError)
                    self.assertIn('no_data', str(result))
        asyncio.run(check())

    def test_domain_model_selects_discovered_tool(self):
        class Model:
            async def ask(self, purpose, system, payload):
                self.catalog = payload['tools']
                return {'action': 'call', 'tool': 'query_tickets', 'arguments': {'kind': 'train',
                        'departure_city': '北京', 'arrival_city': '上海', 'travel_date': '2026-10-01'}}
        model = Model()
        agent = DomainAgent('tickets', model=model, tool_url=self.env['TRIPWEAVE_MCP_URL'])
        result = asyncio.run(agent.run('测试结构化工具选择', [], 'read-test'))
        self.assertEqual(result['status'], 'success')
        self.assertEqual([t['name'] for t in model.catalog], ['query_tickets'])

    def test_tool_injection_blocked(self):
        class Model:
            async def ask(self, *args):
                return {'action': 'call', 'tool': 'book_simulated_ticket', 'arguments': {}}
        agent = DomainAgent('tickets', model=Model(), tool_url=self.env['TRIPWEAVE_MCP_URL'])
        with self.assertRaises(Exception):
            asyncio.run(agent.run('不允许查询Agent下单', [], 'blocked'))

    def test_offline_agent_removed_from_route_catalog(self):
        with patch.dict(os.environ, {**self.env, 'TRIPWEAVE_TICKETS_URL': f'http://127.0.0.1:{free_port()}'}):
            engine = build_engine(demo=True, network=True)
            result = asyncio.run(engine.run('北京到上海2026-10-01的火车票'))
            self.assertNotIn('tickets', engine.registry.items)
            self.assertEqual(result.results, [])
            self.assertFalse(engine.pending)

    def test_missing_parameters_does_not_create_order(self):
        with patch.dict(os.environ, self.env):
            engine = build_engine(demo=True, network=True)
            result = asyncio.run(engine.run('模拟预订DEMO-TRAIN-001'))
            self.assertIsNone(result.pending_token)
            self.assertEqual(result.results[-1].status, 'input_required')
            self.assertEqual(result.results[-1].trace, [])

    def test_multi_turn_weather_ticket_selection_quantity_and_confirmation(self):
        with patch.dict(os.environ, self.env):
            engine = build_engine(demo=True, network=True)
            for query in ('北京2026-10-01的天气', '查同一天去上海的火车票'):
                result = asyncio.run(engine.run(query))
                self.assertEqual(result.results[0].status, 'success')
            result = asyncio.run(engine.run('订第二个'))
            self.assertIsNone(result.pending_token)
            self.assertIn('数量', result.render())
            result = asyncio.run(engine.run('1张'))
            self.assertTrue(result.pending_token)
            quote = result.results[-1].data['quote']
            self.assertEqual(quote['ticket']['id'], 'DEMO-TRAIN-002')
            self.assertEqual(quote['quantity'], 1)
            confirmed = asyncio.run(engine.confirm(result.pending_token))
            self.assertEqual(confirmed.status, 'success')
            self.assertEqual(confirmed.trace[-1]['arguments'], {'quote_id': quote['quote_id']})

    def test_network_price_change_rejected_after_confirmation(self):
        with patch.dict(os.environ, self.env):
            engine = build_engine(demo=True, network=True)
            run = asyncio.run(engine.run('预订 DEMO-FLIGHT-001，1张'))
            self.assertTrue(run.pending_token)
            store = TravelStore(self.path)
            with store.connection(write=True) as db:
                db.execute("UPDATE tickets SET price=price+1 WHERE id='DEMO-FLIGHT-001'")
            try:
                reply = asyncio.run(engine.confirm(run.pending_token))
                self.assertEqual(reply.status, 'input_required')
                self.assertIn('变化', reply.text)
            finally:
                with store.connection(write=True) as db:
                    db.execute("UPDATE tickets SET price=price-1 WHERE id='DEMO-FLIGHT-001'")


class BookingTests(unittest.TestCase):
    def test_private_page_requires_password_before_chat(self):
        from streamlit.testing.v1 import AppTest
        with patch.dict(os.environ, {'TRIPWEAVE_ACCESS_PASSWORD': 'test-only-private-password', 'TRIPWEAVE_STACK': '0'}):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=20)
            self.assertFalse(app.chat_input)
            app.text_input[0].set_value('wrong')
            next(b for b in app.button if b.label == '进入').click().run()
            self.assertFalse(app.chat_input)
            app.text_input[0].set_value('test-only-private-password')
            next(b for b in app.button if b.label == '进入').click().run()
            self.assertTrue(app.chat_input)

