"""启动真实 MCP、3个领域 Agent 和可选页面；退出时回收自身子进程。"""
import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from dotenv import load_dotenv


def main():
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / '.env', override=False)
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', default=os.getenv('SMARTVOYAGE_MODEL_MODE') == 'llm', help='协调器及领域Agent都使用外部LLM')
    parser.add_argument('--no-ui', action='store_true')
    parser.add_argument('--host', default='127.0.0.1', help='仅页面监听地址；协议服务始终为本机')
    parser.add_argument('--port', type=int, default=8501)
    args = parser.parse_args()
    if args.host not in ('127.0.0.1', 'localhost') and len(os.getenv('SMARTVOYAGE_ACCESS_PASSWORD', '')) < 12:
        parser.error('非本机页面监听需要至少12字符的 SMARTVOYAGE_ACCESS_PASSWORD；公开访问还应配置HTTPS')
    if args.live and not (os.getenv('SMARTVOYAGE_API_KEY') and os.getenv('SMARTVOYAGE_MODEL')):
        parser.error('--live 需要 SMARTVOYAGE_API_KEY 和 SMARTVOYAGE_MODEL')
    for port in [8001, 5005, 5006, 5007] + ([] if args.no_ui else [args.port]):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                parser.error(f'端口 {port} 已占用；请停止自己的旧服务后再启动，不会结束其他进程')
    env = {**os.environ, 'SMARTVOYAGE_MODEL_MODE': 'llm' if args.live else 'rules',
           'SMARTVOYAGE_STACK': '1', 'PYTHONUNBUFFERED': '1'}
    children = []
    def shutdown(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, shutdown)
    try:
        commands = [['-m', 'SmartVoyage.services.mcp_tools']]
        commands += [['-m', 'SmartVoyage.services.domain_agent', '--kind', kind] for kind in ('weather', 'tickets', 'order')]
        if not args.no_ui:
            commands.append(['-m', 'streamlit', 'run', 'SmartVoyage/app.py', '--server.address', args.host,
                             '--server.port', str(args.port), '--server.headless', 'true', '--browser.gatherUsageStats', 'false'])
        for command in commands:
            children.append(subprocess.Popen([sys.executable, *command], cwd=root, env=env,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
        print('SmartVoyage 服务已启动；页面 http://127.0.0.1:' + str(args.port), flush=True)
        print('模型模式：' + env['SMARTVOYAGE_MODEL_MODE'] + '；票务与订单为本地模拟。按 Ctrl+C 关闭。', flush=True)
        while all(p.poll() is None for p in children):
            time.sleep(.5)
        raise RuntimeError('有服务提前退出，请检查上方错误')
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
