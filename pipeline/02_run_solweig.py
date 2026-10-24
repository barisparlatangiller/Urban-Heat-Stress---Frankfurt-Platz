"""02 — SOLWEIG'i pilot alan için koş (23–28 Haziran 2026; 23 Haziran ısınma günü).

Çıktılar: outputs/<pilot>/results/{tmrt,utci,shadow}/*.tif (saatlik) + summary/.
Sağlık kontrolü: açık gökyüzü öğle saatinde açık meydanların güneşte olması gerekir.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import rasterio
import solweig

import config as C

# Not: bulut/sanal makinede yazılım GPU'su çok yavaş. Apple Silicon Mac'te GPU açık kalabilir:
#   SOLWEIG_CPU=0 python 02_run_solweig.py
import os
if os.environ.get("SOLWEIG_CPU", "1") == "1":
    solweig.disable_gpu()
TILE = int(os.environ.get("SOLWEIG_TILE", "2048"))   # alanı tek döşemede tut (döşeme kenarı yükünü önler)
# 300 m'lik kuleler (Commerzbank) varsayılan 1000 m gölge tamponu açtırıyor → alan 4 kez hesaplanıyor.
# 500 m: güneş yüksekliği ~31°'nin üstündeyken kule gölgesinin tamamı içeride; daha alçak güneşte
# yalnızca 500 m'den uzak gölge ucu kesilir (o saatlerde ışınım zaten düşük).
MAX_SHADOW_M = float(os.environ.get("SOLWEIG_MAX_SHADOW", "500"))


def main(test_hours: int | None = None):
    t0 = time.time()
    surface = solweig.SurfaceData.prepare(
        dsm=str(C.INPUTS / "dsm.tif"),
        dem=str(C.INPUTS / "dem.tif"),
        cdsm=str(C.INPUTS / "cdsm.tif"),
        land_cover=str(C.INPUTS / "landcover.tif"),
        working_dir=str(C.CACHE),
        dsm_relative=False,
        cdsm_relative=True,      # kanopi göreli yükseklik (0 = ağaç yok)
        tdsm_relative=True,
        trunk_ratio=0.25,
        min_object_height=1.0,
        tile_size=TILE,
    )
    print(f"prepare: {time.time() - t0:.0f} s", flush=True)

    weather = solweig.Weather.from_epw(str(C.EPW_RUN), start=C.SPINUP_DAY, end=C.END)
    if test_hours:
        weather = [w for w in weather if w.datetime.day == 24 and w.datetime.hour == 14][:1] or weather[:test_hours]
    print(f"timesteps: {len(weather)}  {weather[0].datetime} → {weather[-1].datetime}", flush=True)

    loc = solweig.Location(latitude=C.LAT, longitude=C.LON, altitude=100.0, utc_offset=C.UTC_OFFSET)
    out = C.RESULTS if not test_hours else C.OUT / "test"
    t1 = time.time()
    solweig.calculate(
        surface=surface, weather=weather, location=loc, output_dir=str(out),
        outputs=["tmrt", "utci", "shadow"], tile_size=TILE, max_shadow_distance_m=MAX_SHADOW_M,
        heat_thresholds_day=[32, 38], heat_thresholds_night=[26],
        progress_callback=lambda i, n: print(f"  {i}/{n}", flush=True) if i % 12 == 0 else None,
    )
    print(f"calculate: {time.time() - t1:.0f} s", flush=True)

    # --- sağlık kontrolü (bir önceki koşudaki "her yer gölgede" hatasını yakalar)
    sh_files = sorted((out / "shadow").glob("*1400.tif"))
    if sh_files:
        with rasterio.open(sh_files[0]) as r:
            sh = r.read(1)
        with rasterio.open(C.INPUTS / "plaza_id.tif") as r:
            pid = r.read(1)
        lit = float(np.nanmean(sh[(pid > 0) & (pid < 255)] >= 0.99) * 100)
        print(f"CHECK {sh_files[0].name}: Platz piksellerinin %{lit:.0f}'i güneşte")
        (out / "check.json").write_text(json.dumps({"file": sh_files[0].name, "plaza_sunlit_pct": lit}))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
