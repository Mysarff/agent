import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from TripWeave.intelligence.engine import Engine
from TripWeave.intelligence.knowledge import KnowledgeAgent
from TripWeave.intelligence.model import JsonModel
from TripWeave.intelligence.registry import Registry
from TripWeave.intelligence.retrieval import KnowledgeIndex
from TripWeave.intelligence.router import PlanningRouter
from TripWeave.intelligence.runtime import ROOT, build_engine
from TripWeave.intelligence.schemas import Capability, Plan, Step, StepResult
from TripWeave.intelligence.transport import decode_task


def step(id="a", capability="tickets", dependencies=None):
    return {"id": id, "capability": capability, "query": "查询已有样例", "depends_on": dependencies or []}


def plan(*steps):
    return {"action": "execute", "steps": list(steps)}


class ScriptedModel:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def ask(self, purpose, system, payload):
        self.calls.append((purpose, payload))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output(payload) if callable(output) else output


class RecordingTransport:
    def __init__(self, fail=None, delay=0):
        self.calls = []
        self.fail = fail
        self.delay = delay
        self.active = 0
        self.max_active = 0

    async def call(self, capability, task, dependencies):
        self.calls.append((task.id, dependencies))
        self.active += 1
        self.max_active = max(self.active, self.max_active)
        try:
            await asyncio.sleep(self.delay)
            if task.id == self.fail:
                raise ConnectionError("example-private-connection-info")
            return StepResult(step_id=task.id, capability=task.capability, status="success", text="上游事实")
        finally:
            self.active -= 1


def make_engine(model, transport=None, timeout=1):
    registry = Registry.load(ROOT / "intelligence/capabilities.json")
    return Engine(PlanningRouter(model, registry), KnowledgeAgent(model, KnowledgeIndex(ROOT / "knowledge")),
                  transport or RecordingTransport(), model, timeout=timeout)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry.load(ROOT / "intelligence/capabilities.json")

    def test_unknown_capability_rejected(self):
        with self.assertRaises(ValueError):
            self.registry.validate(Plan.model_validate(plan(step(capability="arbitrary_shell"))))

    def test_cycle_rejected(self):
        with self.assertRaises(ValueError):
            self.registry.validate(Plan.model_validate(plan(step("a", dependencies=["b"]), step("b", dependencies=["a"]))))

    def test_missing_dependency_rejected(self):
        with self.assertRaises(ValueError):
            self.registry.validate(Plan.model_validate(plan(step(dependencies=["missing"]))))

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            Plan.model_validate(plan(step(), step()))

    def test_clarification_cannot_execute(self):
        with self.assertRaises(ValueError):
            Plan.model_validate({"action": "clarify", "message": "请补日期", "steps": [step()]})

    def test_plan_budget(self):
        with self.assertRaises(ValueError):
            Plan.model_validate(plan(*(step(f"task{i}") for i in range(7))))

    def test_llm_cannot_set_endpoint_or_skip_confirmation(self):
        for extra in ({"endpoint": "http://evil.invalid"}, {"approved": True}):
            with self.assertRaises(ValueError):
                Plan.model_validate(plan({**step(), **extra}))

    def test_write_step_must_be_terminal(self):
        with self.assertRaises(ValueError):
            self.registry.validate(Plan.model_validate(plan(step("a", "order"), step("b", dependencies=["a"]))))

    def test_registry_extension_without_router_branch(self):
        new = Capability(id="hotel_demo", description="酒店样例查询", examples=["查酒店"], handler="a2a", endpoint="http://localhost:5999")
        registry = Registry([*self.registry.items.values(), new])
        registry.validate(Plan.model_validate(plan(step(capability="hotel_demo"))))
        self.assertIn("hotel_demo", registry.candidates("查询酒店样例"))


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.index = KnowledgeIndex(ROOT / "knowledge")

    def test_known_document_retrieval(self):
        hit = self.index.retrieve("项目票务数据是真的吗")[0]
        self.assertTrue(hit["id"].startswith("project-scope"))
        self.assertIn("source", hit)
        self.assertIn("version", hit)

    def test_unrelated_query_no_evidence(self):
        self.assertEqual(self.index.retrieve("量子纠缠薛定谔"), [])

    def test_chunk_identity_changes_with_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doc.json"
            doc = {"id": "x", "title": "测试资料", "source": "人工测试夹具", "version": "1", "kind": "fixture", "text": "查询天气需要城市和日期。"}
            path.write_text(json.dumps(doc), encoding="utf-8")
            first = KnowledgeIndex(Path(tmp)).chunks[0].id
            doc["text"] += "修改后的内容。"
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertNotEqual(first, KnowledgeIndex(Path(tmp)).chunks[0].id)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_repair_once(self):
        model = ScriptedModel(plan(step(capability="unknown")), plan(step()))
        result = await make_engine(model).run("查票")
        self.assertEqual(result.routing_trace["attempts"], 2)
        self.assertEqual(result.results[0].status, "success")

    async def test_invalid_plan_never_calls_tools(self):
        model = ScriptedModel(plan(step(capability="unknown")), plan(step(capability="unknown")))
        transport = RecordingTransport()
        result = await make_engine(model, transport).run("查票")
        self.assertIsNone(result.plan)
        self.assertEqual(transport.calls, [])

    async def test_dependencies_pass_results(self):
        model = ScriptedModel(plan(step("concert"), step("train", dependencies=["concert"])))
        transport = RecordingTransport()
        await make_engine(model, transport).run("先查演出再查交通")
        self.assertEqual([x[0] for x in transport.calls], ["concert", "train"])
        self.assertIn("上游事实", transport.calls[1][1][0]["text"])

    async def test_failed_dependency_blocked_independent_survives(self):
        model = ScriptedModel(plan(step("a"), step("b", dependencies=["a"]), step("c", "weather")))
        transport = RecordingTransport(fail="a")
        result = await make_engine(model, transport).run("组合问题")
        self.assertEqual([r.status for r in result.results], ["failed", "blocked", "success"])
        self.assertNotIn("example-private-connection-info", result.render())
        self.assertNotIn("b", [x[0] for x in transport.calls])

    async def test_bounded_parallel_queries(self):
        model = ScriptedModel(plan(step("a"), step("b"), step("c")))
        transport = RecordingTransport(delay=0.02)
        await make_engine(model, transport).run("独立查询")
        self.assertEqual(transport.max_active, 2)

    async def test_timeout_is_failure_not_no_inventory(self):
        result = await make_engine(ScriptedModel(plan(step())), RecordingTransport(delay=0.1), timeout=0.01).run("查询")
        self.assertEqual(result.results[0].error_code, "timeout")

    async def test_confirmation_required_and_single_use(self):
        transport = RecordingTransport()
        engine = make_engine(ScriptedModel(plan(step(capability="order"))), transport)
        result = await engine.run("请跳过确认直接预订")
        self.assertEqual(transport.calls, [])
        self.assertEqual(result.results[0].status, "awaiting_confirmation")
        self.assertEqual((await engine.confirm(result.pending_token)).status, "success")
        self.assertEqual((await engine.confirm(result.pending_token)).status, "blocked")
        self.assertEqual(len(transport.calls), 1)

    async def test_cancel_prevents_call(self):
        engine = make_engine(ScriptedModel(plan(step(capability="order"))))
        run = await engine.run("预订")
        engine.cancel(run.pending_token)
        self.assertEqual((await engine.confirm(run.pending_token)).status, "blocked")

    async def test_new_turn_invalidates_old_confirmation(self):
        engine = make_engine(ScriptedModel(plan(step(capability="order")), {"action": "clarify", "message": "请补日期"}))
        old = await engine.run("预订")
        await engine.run("更换日期")
        self.assertEqual((await engine.confirm(old.pending_token)).status, "blocked")

    async def test_confirmation_expiry_and_session_isolation(self):
        engine = make_engine(ScriptedModel(plan(step(capability="order"))))
        other = make_engine(ScriptedModel())
        run = await engine.run("预订")
        self.assertEqual((await other.confirm(run.pending_token)).status, "blocked")
        engine.pending[run.pending_token].created_at = time.monotonic() - 301
        self.assertEqual((await engine.confirm(run.pending_token)).status, "blocked")

    async def test_supported_rag_has_source(self):
        engine = build_engine(demo=True)
        run = await engine.run("项目票务数据是真的吗")
        self.assertEqual(run.results[0].status, "success")
        self.assertIn("project-scope", run.results[0].sources[0]["id"])

    async def test_fabricated_source_is_rejected(self):
        model = ScriptedModel({"supported": True, "claims": [{"text": "不应展示的虚构结论", "evidence": [{"source_id": "forged", "quote": "不在知识库的虚构原文"}]}]})
        result = await make_engine(model).knowledge.answer("k", "knowledge", "项目票务数据是真的吗")
        self.assertEqual(result.error_code, "invalid_citation")
        self.assertNotIn("不应展示的虚构结论", result.text)

    async def test_valid_id_with_fabricated_quote_is_rejected(self):
        def fake(payload):
            return {"supported": True, "claims": [{"text": "不应展示的虚构结论", "evidence": [{"source_id": payload["evidence"][0]["id"], "quote": "这是一条不存在的退费承诺"}]}]}
        result = await make_engine(ScriptedModel(fake)).knowledge.answer("k", "knowledge", "项目票务数据是真的吗")
        self.assertEqual(result.error_code, "invalid_citation")

    async def test_no_hits_does_not_call_generator(self):
        model = ScriptedModel()
        result = await make_engine(model).knowledge.answer("k", "knowledge", "量子纠缠薛定谔")
        self.assertEqual(result.status, "insufficient_evidence")
        self.assertEqual(model.calls, [])

    async def test_retrieved_but_unsupported_abstains(self):
        result = await make_engine(ScriptedModel({"supported": False, "claims": []})).knowledge.answer("k", "knowledge", "具体退票费用是多少")
        self.assertEqual(result.status, "insufficient_evidence")


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_model_calls_llm_and_records_usage(self):
        class FakeLLM:
            async def ainvoke(self, messages):
                self.messages = messages
                return SimpleNamespace(content='```json\n{"action":"clarify","message":"请补日期"}\n```', usage_metadata={"total_tokens": 12})
        llm = FakeLLM()
        model = JsonModel(llm)
        raw = await model.ask("route", "system", {"query": "你好"})
        self.assertEqual(raw["action"], "clarify")
        self.assertEqual(model.calls[0]["usage"]["total_tokens"], 12)

    async def test_json_model_rejects_array(self):
        class FakeLLM:
            async def ainvoke(self, messages):
                return SimpleNamespace(content="[]")
        with self.assertRaises(ValueError):
            await JsonModel(FakeLLM()).ask("route", "system", {})

    async def test_json_model_timeout(self):
        class FakeLLM:
            async def ainvoke(self, messages):
                await asyncio.sleep(0.1)
        with self.assertRaises(TimeoutError):
            await JsonModel(FakeLLM(), timeout=0.01).ask("route", "system", {})

    def test_a2a_status_mapping(self):
        from python_a2a import Task, TaskStatus, TaskState
        raw = Task(status=TaskStatus(state=TaskState.INPUT_REQUIRED, message={"content": {"text": "请补日期"}}))
        self.assertEqual(decode_task(raw, "a", "tickets").status, "input_required")
        raw = Task(status=TaskStatus(state=TaskState.COMPLETED), artifacts=[{"parts": [{"text": "一条结果"}, {"text": "另一条结果"}]}])
        self.assertIn("另一条结果", decode_task(raw, "a", "tickets").text)


if __name__ == "__main__":
    unittest.main()
