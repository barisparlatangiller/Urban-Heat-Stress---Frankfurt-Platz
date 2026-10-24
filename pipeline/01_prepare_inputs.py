"""01 — SOLWEIG girdilerini hazırla (pilot alan).

Önceki koşudaki hatayı giderir ve eksik katmanları tamamlar:

* Kanopi ve yüzey rasterlarında NoData=0 tanımlıydı. SOLWEIG 0'ları "veri yok"
  sayıp zemin kotuyla dolduruyor, ardından göreli kanopiye DEM'i bir kez daha
  ekliyordu → ~100 m'lik sahte orman. Burada 0 geçerli değer olarak korunur ve
  NoData -9999 olarak yazılır.
* DSM: DGM1 + (ALKIS bina tabanı içinde DOM1). Ağaçlar DSM'de değil CDSM'de.
* Arazi örtüsü: Bars'ın yüzey sınıfları > binalar (2) > Main nehri (7) > varsayılan kaplama (0).
  Tanımsız/senaryo kodları ayrı bir maskeye yazılır.
* Platz poligonları: tamponsuz olan varsa o, yoksa 100 m tamponun -100 m geri alınmışı.
"""
from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import from_bounds
from scipy import ndimage as nd

import config as C

ND = -9999.0


def read_window(path, bounds):
    with rasterio.open(path) as r:
        win = from_bounds(*bounds, r.transform).round_offsets().round_lengths()
        arr = r.read(1, window=win)
        tr = r.window_transform(win)
        return arr, tr, r.crs


def write(path, arr, tr, crs, dtype="float32", nodata=ND):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                       dtype=dtype, crs=crs, transform=tr, nodata=nodata, compress="deflate",
                       tiled=True, blockxsize=256, blockysize=256) as dst:
        dst.write(arr.astype(dtype), 1)


def plaza_polygons(pilot: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    exact = gpd.read_file(C.SRC["plazas_exact"]).set_index("oid")
    rows = []
    for _, p in pilot.iterrows():
        if p.oid in exact.index:
            geom, src = exact.loc[p.oid].geometry, "ALKIS"
        else:
            geom, src = p.geometry.buffer(-C.BUFFER_M, join_style="mitre"), "tampon-geri"
        rows.append({"oid": p.oid, "name": p.unv_name_l or "Platz (isimsiz)",
                     "function": p.fkt_funkti, "geom_source": src, "geometry": geom})
    g = gpd.GeoDataFrame(rows, crs=pilot.crs)
    g = g[~g.geometry.is_empty].copy()
    # aynı isimli parçaları (ör. Willy-Brandt-Platz, An der Hauptwache) tek Platz olarak birleştir
    g = g.dissolve(by="name", aggfunc={"oid": "first", "function": "first", "geom_source": "first"}).reset_index()
    g["area_m2"] = g.area.round(0)
    g["pid"] = [f"p{i:02d}" for i in range(len(g))]
    return g


def woz_lag_hours(d):
    """WOZ ile UTC arasındaki fark (saat): boylam/15 + zaman denklemi. Frankfurt haziran sonu ≈ 0,53 h."""
    import math
    g = 2 * math.pi / 365 * (d.timetuple().tm_yday - 1)
    eot = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))   # dakika
    lon = 8.5213                                                              # DWD 01420
    return (4 * lon + eot) / 60.0


def cut_epw():
    """Dönemi (ısınma günü dahil) keser; dakika alanını 60→0 yapar.
    solweig 0.1.0b96 EPW okuyucusu minute=60 satırlarını sessizce atlıyor."""
    from datetime import date
    lines = C.SRC["epw"].read_text(encoding="utf-8").splitlines()
    head, rows = lines[:8], lines[8:]
    s, e = date.fromisoformat(C.SPINUP_DAY), date.fromisoformat(C.END)
    P = [ln.split(",") for ln in rows]
    keep = []
    for i, p in enumerate(P):
        d = date(int(p[0]), int(p[1]), int(p[2]))
        if s <= d <= e:
            p = list(p)
            p[4] = "0"
            if C.RAD_WOZ_FIX and i + 1 < len(P):
                # Işınım WOZ (gerçek güneş zamanı) ile etiketlenmiş ama UTC sanılmış: değer, olması
                # gerekenden `lag` saat geç. Doğru aralığı komşu iki değerin ağırlıklı ortalamasıyla kur.
                lag = woz_lag_hours(d)
                for col in (13, 14, 15):                       # GHI, DNI, DHI
                    v0, v1 = float(p[col]), float(P[i + 1][col])
                    if v0 < 9999 and v1 < 9999:
                        p[col] = f"{(1 - lag) * v0 + lag * v1:.2f}"
            keep.append(",".join(p))
    head[7] = f"DATA PERIODS,1,1,Data,{s.strftime('%A')},{s.month}/{s.day},{e.month}/{e.day}"
    C.EPW_RUN.parent.mkdir(parents=True, exist_ok=True)
    C.EPW_RUN.write_text("\n".join(head + keep) + "\n", encoding="utf-8")
    return len(keep)


def main():
    n_epw = cut_epw()
    pilot = gpd.read_file(C.SRC["pilot_buffer"], encoding="utf-8")
    b = pilot.total_bounds
    bounds = (np.floor(b[0]), np.floor(b[1]), np.ceil(b[2]), np.ceil(b[3]))

    dem, tr, crs = read_window(C.SRC["dem"], bounds)
    dom, _, _ = read_window(C.SRC["dom"], bounds)
    can, _, _ = read_window(C.SRC["canopy"], bounds)
    srf, _, _ = read_window(C.SRC["surface"], bounds)
    shape = dem.shape

    # --- DSM: bina tabanlarında DOM, diğer her yerde DGM
    bld = gpd.read_file(C.SRC["buildings"]).to_crs(crs)
    bld = bld.cx[bounds[0]:bounds[2], bounds[1]:bounds[3]]
    # yer altı yapıları (Tiefgarage vb.) çıkar: aksi halde üstlerindeki ağaç/kiosk DOM'dan 'bina' olur
    if "rellage" in bld:
        bld = bld[bld["rellage"].fillna("") != "Unter der Erdoberfläche"]
    foot = rasterize(((g, 1) for g in bld.geometry), out_shape=shape, transform=tr, dtype="uint8").astype(bool)
    ndsm = np.clip(dom - dem, 0, None)
    is_bld = foot & (ndsm >= C.BUILDING_MIN_H)
    dsm = np.where(is_bld, dom, dem).astype("float32")

    # --- CDSM: göreli ağaç yüksekliği; 0 = ağaç yok (geçerli değer), binaların üstündeki kanopi silinir
    cdsm = np.nan_to_num(can, nan=0.0)
    cdsm[(cdsm < 1.0) | is_bld] = 0.0

    # --- Arazi örtüsü
    river = dem < C.RIVER_DEM_MAX
    lab, n = nd.label(river)
    if n:
        sizes = nd.sum(river, lab, range(1, n + 1))
        river = nd.binary_closing(lab == (np.argmax(sizes) + 1), iterations=3)
    lc = np.full(shape, C.LC_DEFAULT, dtype="int16")
    lc[river] = 7
    lc[is_bld] = 2
    user = srf.astype("int16")
    has_user = user > 0                       # 0 = Bars'ın rasterında boş
    valid_user = has_user & np.isin(user, list(C.LC_VALID))
    lc[valid_user & ~is_bld] = user[valid_user & ~is_bld]
    scenario = (has_user & np.isin(user, list(C.LC_SCENARIO)) & ~is_bld).astype("uint8")

    plazas = plaza_polygons(pilot)
    pmask = rasterize(((g, i + 1) for i, g in enumerate(plazas.geometry)), out_shape=shape,
                      transform=tr, dtype="uint8")

    write(C.INPUTS / "dsm.tif", dsm, tr, crs)
    write(C.INPUTS / "dem.tif", dem, tr, crs)
    write(C.INPUTS / "cdsm.tif", cdsm, tr, crs)
    write(C.INPUTS / "landcover.tif", lc, tr, crs, dtype="int16", nodata=-1)
    write(C.INPUTS / "scenario_mask.tif", scenario, tr, crs, dtype="uint8", nodata=255)
    write(C.INPUTS / "plaza_id.tif", pmask, tr, crs, dtype="uint8", nodata=255)

    C.DATA.mkdir(parents=True, exist_ok=True)
    plazas.to_file(C.DATA / "plazas_altstadt.geojson", driver="GeoJSON")
    plazas.to_crs(4326).to_file(C.DATA / "plazas_altstadt_wgs84.geojson", driver="GeoJSON")

    u, c = np.unique(lc, return_counts=True)
    report = {
        "bounds": bounds, "shape": shape,
        "landcover_pct": {int(k): round(float(v) / lc.size * 100, 2) for k, v in zip(u, c)},
        "building_pct": round(float(is_bld.mean() * 100), 2),
        "tree_pct": round(float((cdsm > 0).mean() * 100), 2),
        "tree_height_p50_p99": [round(float(x), 1) for x in np.percentile(cdsm[cdsm > 0], [50, 99])],
        "river_pct": round(float(river.mean() * 100), 2),
        "scenario_px": int(scenario.sum()),
        "plazas": len(plazas),
        "epw_hours": n_epw,
    }
    (C.INPUTS / "prepare_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
