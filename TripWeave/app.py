"""行知 TripWeave 页面入口。"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
runpy.run_module("TripWeave.intelligence.ui", run_name="__main__")
