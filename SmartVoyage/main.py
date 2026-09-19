"""新版入口。原课程 CLI 保存在 legacy/main_original.py。"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from SmartVoyage.intelligence.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
