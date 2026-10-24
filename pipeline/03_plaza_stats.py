"""03 — 24–28 Haziran için özet rasterlar ve Platz istatistikleri.

* Isınma günü (23 Haziran) atılır.
* Binaların üstü ve alan kenarındaki EDGE_CROP_M bant istatistiğe girmez.
* Çıktılar: outputs/<pilot>/results/period/*.tif, data/plaza_stats.json, data/plaza_stats.csv
"""
from __future__ import annotations

import json
from datetime import datetime

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio

import config as C

DAY_H = range(9, 19)     # gündüz analiz saatleri (yerel standart saat, CET)
NIGHT_H = [22, 23, 0, 1, 2, 3, 4, 5]


def stamp(p):
    return datetime.strptime(p.stem.split("_", 1)[1], "%Y%m%d_%H%M")


def frames(var):
    s, e = datetime.fromisoformat(C.START), datetime.fromisoformat(C.END).replace(hour=23)
    fs = sorted((C.RESULTS / var).glob(f"{var}_*.tif"))
    return [(stamp(f), f) for f in fs if s <= stamp(f) <= e]


def read(f):
    with rasterio.open(f) as r:
        return r.read(1).astype("float32"), r.profile


def main():
    with rasterio.open(C.INPUTS / "landcover.tif") as r:
        lc = r.read(1)
        prof = r.profile
    with rasterio.open(C.INPUTS / "plaza_id.tif") as r:
        pid = r.read(1)
    edge = int(C.EDGE_CROP_M)
    valid = lc != 2
    valid[:edge] = valid[-edge:] = False
    valid[:, :edge] = valid[:, -edge:] = False

    plazas = gpd.read_file(C.DATA / "plazas_altstadt.geojson")
    idx = {i + 1: row for i, row in plazas.iterrows()}

    tm, ut, sh = frames("tmrt"), frames("utci"), frames("shadow")
    assert len(tm) == len(ut) == len(sh) and tm, "saatlik çıktı eksik"
    n = len(tm)
    shape = lc.shape
    tmax = np.full(shape, -np.inf, "float32")
    tsum_day = np.zeros(shape, "float32"); nday = 0
    u32 = np.zeros(shape, "float32"); u38 = np.zeros(shape, "float32")
    unight = np.zeros(shape, "float32"); nnight = 0
    sun_day = np.zeros(shape, "float32")

    rows = []
    for (t, ft), (_, fu), (_, fs) in zip(tm, ut, sh):
        T, _ = read(ft); U, _ = read(fu); S, _ = read(fs)
        tmax = np.fmax(tmax, T)
        is_day = t.hour in DAY_H
        if is_day:
            tsum_day += np.nan_to_num(T); nday += 1
            u32 += U >= 32; u38 += U >= 38
            sun_day += S >= 0.99
        if t.hour in NIGHT_H:
            unight += np.nan_to_num(U); nnight += 1
        for k, row in idx.items():
            m = (pid == k) & valid & np.isfinite(T)
            if not m.any():
                continue
            rows.append({"pid": row.pid, "t": t.strftime("%Y-%m-%dT%H:00"),
                         "tmrt": float(np.mean(T[m])), "utci": float(np.mean(U[m])),
                         "tmrt_p90": float(np.percentile(T[m], 90)),
                         "sun": float(np.mean(S[m] >= 0.99)), "u32": float(np.mean(U[m] >= 32))})

    ndays = (datetime.fromisoformat(C.END) - datetime.fromisoformat(C.START)).days + 1
    period = {
        "tmrt_max": tmax,
        "tmrt_day_mean": tsum_day / max(nday, 1),
        "utci_hours32_per_day": u32 / ndays,
        "utci_hours38_per_day": u38 / ndays,
        "sun_hours_day_per_day": sun_day / ndays,
        "utci_night_mean": unight / max(nnight, 1),
    }
    pd_dir = C.RESULTS / "period"; pd_dir.mkdir(parents=True, exist_ok=True)
    prof.update(dtype="float32", nodata=-9999.0, compress="deflate")
    for name, a in period.items():
        a = np.where(valid, a, -9999.0).astype("float32")
        with rasterio.open(pd_dir / f"{name}.tif", "w", **prof) as dst:
            dst.write(a, 1)

    ts = pd.DataFrame(rows)
    stats = []
    for k, row in idx.items():
        m = (pid == k) & valid
        if not m.any():
            continue
        d = ts[ts.pid == row.pid]
        dd = d[pd.to_datetime(d.t).dt.hour.isin(DAY_H)]
        peak = d.loc[d.tmrt.idxmax()]
        s = {
            "pid": row.pid, "name": row["name"], "area_m2": int(row.area_m2),
            "geom_source": row.geom_source,
            "tmrt_day_mean": round(float(dd.tmrt.mean()), 1),
            "tmrt_peak": round(float(peak.tmrt), 1), "tmrt_peak_time": peak.t,
            "tmrt_max_px": round(float(np.max(tmax[m])), 1),
            "utci_day_mean": round(float(dd.utci.mean()), 1),
            "utci32_hours_per_day": round(float(np.mean(period["utci_hours32_per_day"][m])), 1),
            "utci38_hours_per_day": round(float(np.mean(period["utci_hours38_per_day"][m])), 1),
            "sun_share_day": round(float(dd.sun.mean() * 100), 0),
            "utci_night_mean": round(float(np.mean(period["utci_night_mean"][m])), 1),
            "tree_share": None, "lc_share": {},
        }
        stats.append(s)

    with rasterio.open(C.INPUTS / "cdsm.tif") as r:
        cd = r.read(1)
    for s in stats:
        k = int(s["pid"][1:]) + 1
        m = (pid == k)
        s["tree_share"] = round(float(np.mean(cd[m] > 0) * 100), 0)
        u, c = np.unique(lc[m], return_counts=True)
        s["lc_share"] = {int(a): round(float(b) / m.sum() * 100, 0) for a, b in zip(u, c)}

    # sıralama: çok güçlü ısı stresi (UTCI ≥ 38 °C). ≥32 eşiği bu dalgada gölgede de aşıldığı için ayırt etmiyor.
    stats.sort(key=lambda s: (-s["utci38_hours_per_day"], -s["tmrt_day_mean"]))
    for i, s in enumerate(stats, 1):
        s["rank"] = i
    meta = {"period": [C.START, C.END], "day_hours": [min(DAY_H), max(DAY_H)], "timezone": "CET (UTC+1)",
            "frames": n, "ndays": ndays}
    (C.DATA / "plaza_stats.json").write_text(json.dumps({"meta": meta, "plazas": stats}, ensure_ascii=False, indent=1))
    pd.DataFrame(stats).drop(columns=["lc_share"]).to_csv(C.DATA / "plaza_stats.csv", index=False)
    ts.round(2).to_csv(C.DATA / "plaza_timeseries.csv", index=False)
    print(pd.DataFrame(stats)[["rank", "name", "tmrt_day_mean", "tmrt_peak", "utci38_hours_per_day", "utci32_hours_per_day",
                               "sun_share_day", "tree_share"]].to_string(index=False))


if __name__ == "__main__":
    main()
