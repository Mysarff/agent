import asyncio
import unittest
from types import SimpleNamespace
from TripWeave.intelligence.model import JsonModel
from TripWeave.intelligence.registry import Registry
from TripWeave.intelligence.runtime import ROOT
from TripWeave.intelligence.schemas import Capability, Plan
from TripWeave.intelligence.transport import decode_task


def step(id='a', capability='tickets', dependencies=None):
    return {'id': id, 'capability': capability, 'query': '查询已有样例', 'depends_on': dependencies or []}


def plan(*steps):
    return {'action': 'execute', 'steps': list(steps)}


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
