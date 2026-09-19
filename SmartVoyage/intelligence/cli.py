import argparse
import asyncio
import json

from .runtime import build_engine


def main():
    parser = argparse.ArgumentParser(description="SmartVoyage：能力路由与有据问答")
    parser.add_argument("--live", action="store_true", help="连接自己配置的模型与原 A2A/MCP 服务")
    parser.add_argument("--demo", action="store_true", help="离线固定样例（默认）")
    parser.add_argument("--network", action="store_true", help="真实A2A/MCP协议链路；默认规则模型，配合--live使用LLM")
    parser.add_argument("--question", help="执行单个问题后退出")
    args = parser.parse_args()
    if args.live and args.demo:
        parser.error("--live 和 --demo 不能同时使用")
    try:
        engine = build_engine(demo=not args.live, network=args.network)
    except ValueError as exc:
        print(str(exc))
        return 2
    print("SmartVoyage · " + ("连接模式" if args.live else "离线固定场景演示，不代表真实模型表现"))
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
