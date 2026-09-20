"""运行限定范围测试并保存可审计报告，不导入旧的交互测试或调用外部 API。"""
import hashlib
import importlib.metadata
import json
import platform
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.append(test.id())


def main():
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'TripWeave/tests'), top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
    hashes = {}
    for directory in ('TripWeave/intelligence', 'TripWeave/tests', 'TripWeave/services'):
        for path in sorted((ROOT / directory).rglob('*')):
            if path.suffix in ('.py', '.json'):
                hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {
        'created_at': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
        'test_count': result.testsRun, 'passed_count': len(result.passed),
        'success': result.wasSuccessful(), 'duration_seconds': round(time.perf_counter() - started, 3),
        'passed_tests': result.passed, 'failures': [(t.id(), e) for t, e in result.failures],
        'errors': [(t.id(), e) for t, e in result.errors],
        'versions': {name: importlib.metadata.version(name) for name in ('pydantic', 'langchain-openai', 'python-a2a', 'streamlit', 'httpx')},
        'scope': 'Unit/UI tests, local HTTP model fixtures, real local A2A -> Streamable HTTP MCP -> SQLite services with rule model. No external LLM quality or live ticket benchmark.',
        'source_sha256': hashes,
    }
    path = ROOT / 'reports/intelligence_verification.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Report: {path}')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
