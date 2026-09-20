import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from .runtime import build_engine


def main():
    load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)
    parser = argparse.ArgumentParser(description="TripWeave：能力路由与有据问答")
    parser.add_argument("--live", action="store_true", help="使用自己的模型与 TripWeave A2A/MCP 服务；请先以 --live 启动服务栈")
    parser.add_argument("--demo", action="store_true", help="离线固定样例（默认）")
    parser.add_argument("--network", action="store_true", help="真实A2A/MCP协议链路；默认规则模型，配合--live使用LLM")
    parser.add_argument("--question", help="执行单个问题后退出")
    args = parser.parse_args()
    if args.demo and (args.live or args.network):
        parser.error("--demo 不能与 --live 或 --network 同时使用")
    try:
        engine = build_engine(demo=not args.live, network=args.network or args.live)
    except ValueError as exc:
        print(str(exc))
        return 2
    mode = "LLM + TripWeave A2A/MCP 服务" if args.live else "真实 A2A/MCP 协议演示（规则模型）" if args.network else "离线固定场景演示，不代表真实模型表现"
    print("TripWeave · " + mode)
    history = []
    pending = None
    while True:
        query = args.question or input("问题（/confirm 确认模拟操作，/cancel 取消，/quit 退出）：").strip()
        if query == "/quit":
            break
        if query == "/confirm":
            print(asyncio.run(engine.confirm(pending or "")).text)
            pending = None
        elif query == "/cancel":
            engine.cancel(pending or "")
            pending = None
            print("已取消。")
        else:
            result = asyncio.run(engine.run(query, history))
            print(result.render())
            print("路由记录：", json.dumps(result.routing_trace, ensure_ascii=False))
            pending = result.pending_token
            history.extend([{"role": "user", "content": query}, {"role": "assistant", "content": result.render()}])
        if args.question:
            break
    return 0
