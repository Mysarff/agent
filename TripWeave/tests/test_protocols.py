"""本地 HTTP 集成测试：真实 SDK / JSON-RPC，模型和业务结果为固定替身。"""
import asyncio
import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from TripWeave.intelligence.model import JsonModel, LoopIndependentChat
from TripWeave.intelligence.registry import Registry
from TripWeave.intelligence.router import PlanningRouter
from TripWeave.intelligence.runtime import ROOT
from TripWeave.intelligence.schemas import Capability, Step
from TripWeave.intelligence.transport import A2ATransport


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.calls.append((self.path, body))
        code = 200
        if self.path == '/v1/chat/completions':
            payload = json.loads(body['messages'][-1]['content'])
            if 'catalog' in payload:
                answer = {'action': 'execute', 'steps': [{'id': 'w', 'capability': 'weather', 'query': payload['query']}]}
            else:
                answer = {'intents': ['weather'], 'city': '北京', 'travel_date': '2026-10-01'}
            response = {'id': 'fixture-chat', 'object': 'chat.completion', 'created': 0, 'model': 'fixture-model',
                        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(answer, ensure_ascii=False)}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}
        elif self.server.fail_a2a:
            code = 503
            response = {'error': 'fixture unavailable'}
        else:
            from python_a2a import Task, TaskStatus, TaskState
            result = Task(id=body['params']['id'], status=TaskStatus(state=TaskState.COMPLETED),
                          artifacts=[{'parts': [{'type': 'text', 'text': '本地协议替身结果'}]}])
            response = {'jsonrpc': '2.0', 'id': body['id'], 'result': result.to_dict()}
            if self.server.wrong_id:
                response['id'] = 'wrong-id'
        data = json.dumps(response).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@contextmanager
def fixture_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
    server.calls = []
    server.fail_a2a = False
    server.wrong_id = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class ProtocolTests(unittest.TestCase):
    def test_live_runtime_uses_structured_tripweave_services_by_default(self):
        from TripWeave.intelligence.runtime import build_engine
        with patch('TripWeave.services.settings.load_llm', return_value=object()):
            engine = build_engine(demo=False)
        self.assertTrue(engine.registry.discovery_enabled)
        self.assertEqual(engine.timeout, 60)
        remote = [c for c in engine.registry.configured if c.handler == 'a2a']
        self.assertEqual(len(remote), 3)
        self.assertTrue(all(c.structured_request for c in remote))

    def test_cli_live_loads_env_and_selects_current_network(self):
        from TripWeave.intelligence import cli
        with patch('sys.argv', ['TripWeave', '--live']), patch.object(cli, 'load_dotenv') as load, \
             patch.object(cli, 'build_engine') as build, patch('builtins.input', return_value='/quit'):
            self.assertEqual(cli.main(), 0)
        build.assert_called_once_with(demo=False, network=True)
        load.assert_called_once_with(ROOT.parent / '.env', override=False)

    def test_real_chat_sdk_context_and_router_across_event_loops(self):
        from langchain_openai import ChatOpenAI
        from TripWeave.intelligence.session import Turn, CONTEXT_PROMPT
        with fixture_server() as (server, url):
            llm = ChatOpenAI(model='fixture-model', api_key='local-test-placeholder', base_url=url + '/v1', max_retries=0, timeout=3)
            model = JsonModel(LoopIndependentChat(llm), timeout=5)
            router = PlanningRouter(model, Registry.load(ROOT / 'intelligence/capabilities.json'))
            try:
                for _ in range(2):
                    turn = Turn.model_validate(asyncio.run(model.ask('context', CONTEXT_PROMPT, {'query':'北京2026-10-01天气'})))
                    result, trace = asyncio.run(router.route('北京2026-10-01天气', [], {'requested':turn.intents}))
                    self.assertEqual(trace['selected'], ['weather'])
                self.assertEqual(len(server.calls), 4)
            finally:
                llm.root_client.close()

    def test_a2a_http_roundtrip_passes_dependency_context(self):
        with fixture_server() as (server, url):
            cap = Capability(id='tickets', description='测试', examples=[], handler='a2a', endpoint=url)
            task = Step(id='t', capability='tickets', query='查询测试票')
            result = asyncio.run(A2ATransport(timeout=3).call(cap, task, [{'text': '上游演出时间'}]))
            self.assertEqual(result.text, '本地协议替身结果')
            self.assertEqual(server.calls[0][0], '/tasks/send')
            self.assertIn('上游演出时间', server.calls[0][1]['params']['message']['content']['text'])

    def test_failed_write_request_is_not_automatically_retried(self):
        with fixture_server() as (server, url):
            server.fail_a2a = True
            cap = Capability(id='order', description='测试', examples=[], handler='a2a', endpoint=url, requires_confirmation=True)
            task = Step(id='t', capability='order', query='模拟预订')
            with self.assertRaises(Exception):
                asyncio.run(A2ATransport(timeout=3).call(cap, task, []))
            self.assertEqual(len(server.calls), 1)

    def test_mismatched_response_id_rejected(self):
        with fixture_server() as (server, url):
            server.wrong_id = True
            cap = Capability(id='tickets', description='测试', examples=[], handler='a2a', endpoint=url)
            with self.assertRaises(ValueError):
                asyncio.run(A2ATransport(timeout=3).call(cap, Step(id='a', capability='tickets', query='查询'), []))


if __name__ == '__main__':
    unittest.main()
