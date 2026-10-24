"""04 — Web görüntüleyici için veri dışa aktarımı (docs/data/).

Her saat için Tmrt ve UTCI, 8-bit gri tonlu PNG olarak kodlanır:
    0 = veri yok (bina, alan kenarı), 1..255 = lo..hi aralığında doğrusal değer.
Renklendirme tarayıcıda yapılır; böylece aynı dosyalar ileride sandbox
hesapları için ham değer olarak da kullanılabilir.
"""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd
import rasterio
from PIL import Image
from pyproj import Transformer

import config as C

RANGES = {"tmrt": (10.0, 75.0), "utci": (10.0, 52.0)}
PERIOD = {  # özet katmanlar ve kodlama aralıkları
    "tmrt_max": (10.0, 75.0),
    "tmrt_day_mean": (10.0, 75.0),
    "utci_hours38_per_day": (0.0, 10.0),
    "utci_hours32_per_day": (0.0, 10.0),
    "sun_hours_day_per_day": (0.0, 10.0),
    "utci_night_mean": (10.0, 52.0),
}


def encode(a, lo, hi, valid):
    v = np.clip((a - lo) / (hi - lo), 0, 1) * 254 + 1
    v = np.where(valid & np.isfinite(a), np.round(v), 0).astype("uint8")
    return Image.fromarray(v, mode="L")


def export_buildings(out):
    """ALKIS bina tabanları + DOM−DGM'den çatı yüksekliği → 3B görüntüleme için GeoJSON."""
    import geopandas as gpd
    from rasterstats import zonal_stats

    with rasterio.open(C.INPUTS / "dsm.tif") as r:
        dsm, tr, bb = r.read(1), r.transform, r.bounds
    with rasterio.open(C.INPUTS / "dem.tif") as r:
        dem = r.read(1)
    ndsm = np.clip(dsm - dem, 0, None)
    g = gpd.read_file(C.SRC["buildings"]).cx[bb.left:bb.right, bb.bottom:bb.top].copy()
    if "rellage" in g:                                          # yer altı yapıları (Tiefgarage) çıkar — 01 ile aynı kural
        g = g[g["rellage"].fillna("") != "Unter der Erdoberfläche"]
    from shapely.geometry import box
    g["geometry"] = g.geometry.intersection(box(bb.left, bb.bottom, bb.right, bb.top))
    zs = zonal_stats(list(g.geometry), ndsm, affine=tr, stats=["percentile_75", "max"], nodata=-9999, all_touched=True)
    # modelin bina maskesiyle aynı ölçüt: tabanda nDSM ≥ BUILDING_MIN_H olan en az bir piksel
    g["hmax"] = [float(z["max"] or 0) for z in zs]
    g["h"] = [round(max(float(z["percentile_75"] or 0), C.BUILDING_MIN_H + 0.5), 1) for z in zs]
    g = g[g.hmax >= C.BUILDING_MIN_H]
    g["use"] = g["funktion"].fillna("")
    g["addr"] = g["lagebeztxt"].fillna("").str.split(";").str[0]
    g["name"] = g["name"].fillna("")
    g = g[["h", "use", "addr", "name", "geometry"]]
    # modelde bina sayılan ama ALKIS'te yer üstü karşılığı olmayan küçük yapılar (merdiven/otopark girişleri vb.)
    from rasterio.features import rasterize, shapes
    from shapely.geometry import shape
    with rasterio.open(C.INPUTS / "landcover.tif") as r:
        lc = r.read(1)
    cover = rasterize(((geom, 1) for geom in g.geometry), out_shape=lc.shape, transform=tr, dtype="uint8", all_touched=True)
    rest = ((lc == 2) & (cover == 0)).astype("uint8")
    extra = []
    for geom, v in shapes(rest, mask=rest.astype(bool), transform=tr):
        poly = shape(geom)
        if poly.area >= 2:
            zs2 = zonal_stats([poly], ndsm, affine=tr, stats=["percentile_75"], nodata=-9999)[0]
            extra.append({"h": round(max(float(zs2["percentile_75"] or 0), C.BUILDING_MIN_H + 0.5), 1), "use": "Small structure",
                          "addr": "", "name": "", "geometry": poly})
    if extra:
        g = pd.concat([g, gpd.GeoDataFrame(extra, crs=g.crs)], ignore_index=True)
    g["geometry"] = g.geometry.simplify(0.3)
    g.to_crs(4326).to_file(out / "buildings.geojson", driver="GeoJSON", COORDINATE_PRECISION=6)
    print(f"binalar: {len(g)}  (yükseklik p50 {g.h.median():.0f} m, maks {g.h.max():.0f} m)")


def export_trees(out):
    """Frankfurt ağaç kaydı → sembolik 3B ağaç: gövde + iki kademeli taç (fill-extrusion için daireler)."""
    import geopandas as gpd
    from shapely.geometry import Point

    with rasterio.open(C.INPUTS / "dsm.tif") as r:
        bb = r.bounds
    t = gpd.read_file(C.SRC["trees"]).cx[bb.left:bb.right, bb.bottom:bb.top].copy()
    t["x"] = t["X_25832"].fillna(t.geometry.centroid.x)
    t["y"] = t["Y_25832"].fillna(t.geometry.centroid.y)
    rad = t["crown_radi"].clip(lower=1.0).fillna(2.5)
    h = t["tree_heigh"].where(t["tree_heigh"] > 0, rad * 2 + 3).clip(3, 35)
    feats = []
    for (x, y, r_, h_, sp, yr, d) in zip(t.x, t.y, rad, h, t["Lateinisch"].fillna(""), t["Pflanzjahr"], t["Stamm Durc"]):
        trunk_r = max(0.25, (d or 30) / 200 * 1.4)                 # gövde (biraz kalınlaştırılmış, görünsün diye)
        base = 0.3 * h_                                            # taç tabanı (model trunk_ratio 0.25 ile uyumlu)
        props = {"sp": sp.split(",")[-1].strip() or sp, "lat": sp.split(",")[0].strip(), "h": round(float(h_), 1),
                 "cd": round(float(r_ * 2), 1), "yr": int(yr) if yr == yr else None}
        for part, rr, b0, b1 in (("trunk", trunk_r, 0, base + 0.5),
                                 ("crown", r_ * 0.82, base, base + (h_ - base) * 0.45),
                                 ("crown", r_, base + (h_ - base) * 0.2, base + (h_ - base) * 0.8),
                                 ("crown", r_ * 0.6, base + (h_ - base) * 0.6, h_)):
            feats.append({"geometry": Point(x, y).buffer(rr, quad_segs=4), "part": part, "b": round(b0, 1),
                          "t": round(b1, 1), **props})
    g = gpd.GeoDataFrame(feats, crs=t.crs).to_crs(4326)
    g.to_file(out / "trees3d.geojson", driver="GeoJSON", COORDINATE_PRECISION=6)
    print(f"ağaçlar: {len(t)}  (yükseklik p50 {h.median():.0f} m, taç çapı p50 {rad.median() * 2:.0f} m)")


def main():
    out = C.WEB / "data"
    (out / "frames").mkdir(parents=True, exist_ok=True)
    (out / "period").mkdir(parents=True, exist_ok=True)

    with rasterio.open(C.INPUTS / "landcover.tif") as r:
        lc = r.read(1)
        b, crs = r.bounds, r.crs
        h, w = r.height, r.width
    edge = int(C.EDGE_CROP_M)
    valid = lc != 2
    valid[:edge] = valid[-edge:] = False
    valid[:, :edge] = valid[:, -edge:] = False

    tf = Transformer.from_crs(crs, 4326, always_xy=True)
    corners = [list(tf.transform(x, y)) for x, y in
               [(b.left, b.top), (b.right, b.top), (b.right, b.bottom), (b.left, b.bottom)]]

    s, e = datetime.fromisoformat(C.START), datetime.fromisoformat(C.END).replace(hour=23)
    times = []
    for var, (lo, hi) in RANGES.items():
        for f in sorted((C.RESULTS / var).glob(f"{var}_*.tif")):
            t = datetime.strptime(f.stem.split("_", 1)[1], "%Y%m%d_%H%M")
            if not s <= t <= e:
                continue
            with rasterio.open(f) as r:
                a = r.read(1)
            key = t.strftime("%Y%m%d_%H")
            encode(a, lo, hi, valid).save(out / "frames" / f"{var}_{key}.png", optimize=True)
            if var == "tmrt":
                times.append(t.strftime("%Y-%m-%dT%H:00"))

    for name, (lo, hi) in PERIOD.items():
        with rasterio.open(C.RESULTS / "period" / f"{name}.tif") as r:
            a = r.read(1)
        encode(a, lo, hi, valid & (a > -9998)).save(out / "period" / f"{name}.png", optimize=True)

    # bina ve ağaç maskeleri (bağlam katmanı) — RGBA
    with rasterio.open(C.INPUTS / "cdsm.tif") as r:
        cd = r.read(1)
    tr = np.zeros((h, w, 4), "uint8")
    tr[cd > 0] = (40, 110, 60, 110)
    Image.fromarray(tr, "RGBA").save(out / "trees.png", optimize=True)
    with rasterio.open(C.INPUTS / "scenario_mask.tif") as r:
        sc = r.read(1)
    sm = np.zeros((h, w, 4), "uint8")
    sm[sc == 1] = (255, 0, 160, 150)
    Image.fromarray(sm, "RGBA").save(out / "scenario.png", optimize=True)

    # günlük maksimum hava sıcaklığı (EPW, kolon 7 = kuru termometre)
    tmax = {}
    for line in C.SRC["epw"].read_text().splitlines()[8:]:
        p = line.split(",")
        d = f"{int(p[0]):04d}-{int(p[1]):02d}-{int(p[2]):02d}"
        tmax[d] = max(tmax.get(d, -99.0), float(p[6]))
    days = sorted({t[:10] for t in times})

    manifest = {
        "pilot": C.PILOT, "width": w, "height": h, "pixel_m": 1.0, "crs": "EPSG:25832",
        "bounds_utm": [b.left, b.bottom, b.right, b.top],
        "days": [{"date": d, "tmax": tmax.get(d)} for d in days],
        "corners": corners, "times": times, "timezone": "CET (UTC+1)",
        "ranges": {**RANGES, **PERIOD}, "period": [C.START, C.END],
        "source": "SOLWEIG (umep-dev solweig 0.1.0b96), DWD 01420 (provisional), HVBG DGM1/DOM1, ALKIS",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    for f in ["plaza_stats.json", "plaza_timeseries.csv", "plazas_altstadt_wgs84.geojson"]:
        (out / f).write_bytes((C.DATA / f).read_bytes())
    export_buildings(out)
    export_trees(out)
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"web verisi: {len(times)} saat × {len(RANGES)} değişken, toplam {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
