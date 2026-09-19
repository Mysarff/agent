"""离线固定场景替身，用于演示编排；不模拟真实 LLM 准确率。"""
from .schemas import StepResult

EXAMPLES = ["项目票务数据是真的吗", "北京明天天气如何", "北京明天天气如何，项目票务数据是真的吗", "帮我模拟预订一张火车票"]


class DemoModel:
    def __init__(self):
        self.calls = []

    async def ask(self, purpose, system, payload):
        self.calls.append({"purpose": purpose, "mode": "offline_fixture"})
        if purpose == "route":
            question = payload["query"]
            fixtures = {
                EXAMPLES[0]: [{"id": "knowledge", "capability": "knowledge", "query": question}],
                EXAMPLES[1]: [{"id": "weather", "capability": "weather", "query": question}],
                EXAMPLES[2]: [{"id": "weather", "capability": "weather", "query": EXAMPLES[1]},
                              {"id": "knowledge", "capability": "knowledge", "query": EXAMPLES[0]}],
                EXAMPLES[3]: [{"id": "order", "capability": "order", "query": "演示：虚构车次 DEMO-001，1 张模拟票"}],
            }
            if question in fixtures:
                return {"action": "execute", "steps": fixtures[question]}
            return {"action": "clarify", "message": "离线演示使用固定场景，请输入页面列出的示例；自由问题需要连接真实模型。", "steps": []}
        if purpose == "knowledge":
            hit = payload["evidence"][0]
            quote = hit["text"].split("。", 1)[0] + "。"
            return {"supported": True, "claims": [{"text": quote, "evidence": [{"source_id": hit["id"], "quote": quote}]}]}
        return {"text": "离线演示不生成自由旅行建议。"}


class DemoTransport:
    async def call(self, capability, step, dependency_results):
        samples = {"weather": "离线虚构样例：北京，示例温度 20℃。不是当前天气预报。",
                   "tickets": "离线虚构样例：车次 DEMO-001，价格 100 元。不是实时票务。",
                   "order": "模拟工具已调用。未出票、未支付、未创建持久化订单。"}
        return StepResult(step_id=step.id, capability=step.capability, status="success", text=samples[step.capability])
