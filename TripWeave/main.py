"""行知 TripWeave 命令行入口。"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from TripWeave.intelligence.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
