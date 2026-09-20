import asyncio
import json
import re
import time


class LoopIndependentChat:
    """Streamlit rerun/CLI 多次 asyncio.run 时复用同步 HTTP 客户端。

    避免缓存的异步连接绑定到已关闭的事件循环。底层 SDK 仍必须设置超时。
    """
    def __init__(self, llm):
        self.llm = llm

    async def ainvoke(self, messages):
        return await asyncio.to_thread(self.llm.invoke, messages)


class JsonModel:
    """可注入的模型边界；只记录耗时和 usage，不记录凭证或隐藏推理。"""
    def __init__(self, llm, timeout: float = 30):
        self.llm = llm
        self.timeout = timeout
        self.calls: list[dict] = []

    async def ask(self, purpose: str, system: str, payload: dict) -> dict:
        started = time.perf_counter()
        record = {"purpose": purpose, "status": "failed"}
        try:
            response = await asyncio.wait_for(self.llm.ainvoke([
                ("system", system), ("human", json.dumps(payload, ensure_ascii=False))
            ]), timeout=self.timeout)
            raw = response.content
            if not isinstance(raw, str):
                raise ValueError("模型响应不是文本")
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("模型响应必须是 JSON 对象")
            record.update(status="success", usage=getattr(response, "usage_metadata", None))
            return result
        finally:
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
            self.calls.append(record)


PLANNER_PROMPT = """你是旅行任务规划器，只输出合法JSON。不编造能力或地址。
session.requested 是本轮请求能力，session.queries 是程序补全的请求；以此为准，不从旧聊天重建票号。
只从可用catalog中选择本轮requested能力，每类一次；全部可用时不得遗漏。不可用则clarify说明。
天气与查票独立执行；同时查票和预订时，order必须depends_on票务步骤。
预订仅为模拟，程序会补参数、生成报价和要求确认；模型无权跳过。
景点仅一般生成建议，不核实实时价格政策。本项目无文档RAG能力。
单次最多六步，依赖无环，order只能终点。缺信息或不能执行则clarify。
schema: {action:execute|clarify|unsupported,message:string,
steps:[{id:string,capability:string,query:string,depends_on:[id]}]}。
"""
