"""新版页面。原课程页面保存在 legacy/app_original.py。"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
runpy.run_module("SmartVoyage.intelligence.ui", run_name="__main__")
