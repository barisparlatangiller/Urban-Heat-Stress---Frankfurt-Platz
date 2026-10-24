"""Tüm adımları sırayla çalıştırır: python pipeline/run_all.py"""
import runpy
import sys
from pathlib import Path

here = Path(__file__).parent
sys.path.insert(0, str(here))
for step in ["01_prepare_inputs", "02_run_solweig", "03_plaza_stats", "04_export_web"]:
    print(f"\n=== {step} ===", flush=True)
    runpy.run_path(str(here / f"{step}.py"), run_name="__main__")
