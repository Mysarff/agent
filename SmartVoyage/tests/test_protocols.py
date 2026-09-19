"""本地 HTTP 集成测试：真实 SDK / JSON-RPC，模型和业务结果为固定替身。"""
import asyncio
import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from SmartVoyage.intelligence.knowledge import KnowledgeAgent
from SmartVoyage.intelligence.model import JsonModel, LoopIndependentChat
from SmartVoyage.intelligence.registry import Registry
from SmartVoyage.intelligence.retrieval import KnowledgeIndex
from SmartVoyage.intelligence.router import PlanningRouter
from SmartVoyage.intelligence.runtime import ROOT
from SmartVoyage.intelligence.schemas import Capability, Step
from SmartVoyage.intelligence.transport import A2ATransport


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
                answer = {'action': 'execute', 'steps': [{'id': 'k', 'capability': 'knowledge', 'query': payload['query']}]}
            else:
                hit = payload['evidence'][0]
                quote = hit['text'].split('。')[0] + '。'
                answer = {'supported': True, 'claims': [{'text': quote, 'evidence': [{'source_id': hit['id'], 'quote': quote}]}]}
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
    def test_real_chat_sdk_router_and_rag_across_event_loops(self):
        from langchain_openai import ChatOpenAI
        with fixture_server() as (server, url):
            llm = ChatOpenAI(model='fixture-model', api_key='local-test-placeholder', base_url=url + '/v1', max_retries=0, timeout=3)
            model = JsonModel(LoopIndependentChat(llm), timeout=5)
            registry = Registry.load(ROOT / 'intelligence/capabilities.json')
            router = PlanningRouter(model, registry)
            knowledge = KnowledgeAgent(model, KnowledgeIndex(ROOT / 'knowledge'))
            try:
                for _ in range(2):
                    plan, trace = asyncio.run(router.route('项目票务数据是真的吗', []))
                    result = asyncio.run(knowledge.answer('k', 'knowledge', plan.steps[0].query))
                    self.assertEqual(result.status, 'success')
                    self.assertEqual(trace['selected'], ['knowledge'])
                self.assertEqual(len(server.calls), 4)
                self.assertEqual(model.calls[-1]['usage']['total_tokens'], 20)
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
