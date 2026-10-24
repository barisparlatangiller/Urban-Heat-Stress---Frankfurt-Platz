"""Proje yapılandırması — Frankfurt Platz Heat (SOLWEIG pilot: Altstadt).

Tüm yollar `1_SHP Files` klasörüne göredir. Bu dosya 6_Uygulama/pipeline/ içinde
durduğu için kök otomatik bulunur; farklı bir yerde çalıştırmak için
FFM_ROOT ortam değişkenini `1_SHP Files` klasörüne ayarlayın.
"""
from __future__ import annotations

import os
from pathlib import Path

APP = Path(__file__).resolve().parents[1]                     # .../6_Uygulama
ROOT = Path(os.environ.get("FFM_ROOT", APP.parent))          # .../1_SHP Files

# ---------------------------------------------------------------- kaynak veriler
SRC = {
    "dem": ROOT / "2_Raster" / "DGM 1 Merged.tiff",                       # HVBG DGM1 (mutlak, m ü.NN)
    "dom": ROOT / "2_Raster" / "DOM 1 Merged.tiff",                       # HVBG DOM1 (mutlak)
    "buildings": ROOT / "3_SHPs" / "Hessen ALKIS Buildings clipped.shp",  # ALKIS bina tabanları
    "trees": ROOT / "3_SHPs" / "Frankfurt_Baeume_QGIS_crown radius.shp",  # Baumkataster: yükseklik, taç çapı
    "canopy": ROOT / "3_SHPs" / "Plaza100meter_Canopy_Height_1m.tiff.tif",  # göreli ağaç yüksekliği (m)
    "surface": ROOT / "2_Raster" / "Surface_Materials.tiff.tif",          # Bars'ın yüzey sınıfları (UMEP kodları)
    "plazas_buffer": ROOT / "2_Raster" / "Plazas 100 M Buffer.shp",       # 98 Platz, 100 m tampon
    "plazas_exact": ROOT / "2_Raster" / "Plazas + Surfaces.shp",          # 43 Platz, tamponsuz
    "pilot_buffer": ROOT / "5_SOLWEIG" / "0_Altstadt.shp",                # 26 Altstadt Platz, 100 m tampon
    "epw": ROOT / "5_SOLWEIG" / "Frankfurt_2026_01Mar_01Sep_DWD_PROVISIONAL.epw",
}

# ---------------------------------------------------------------- çalışma alanı
PILOT = "altstadt"
OUT = APP / "outputs" / PILOT            # büyük rasterlar (git'e girmez)
INPUTS = OUT / "inputs"
CACHE = OUT / "solweig_cache"
EPW_RUN = INPUTS / "weather_heatwave_2026.epw"   # 01 tarafından kesilmiş/düzeltilmiş EPW
RESULTS = OUT / "results"
DATA = APP / "data"                       # küçük vektör/istatistik (git'e girer)
WEB = APP / "docs"                        # GitHub Pages kökü

BUFFER_M = 100.0          # Platz çevresindeki hesaplama tamponu
EDGE_CROP_M = 30.0        # kenar etkisi: istatistik/webde alan kenarından kırpılacak bant

# ---------------------------------------------------------------- dönem & konum
SPINUP_DAY = "2026-06-23"                 # termal durum ısınması (sonuçlardan atılır)
START, END = "2026-06-24", "2026-06-28"   # 2026 yazının en sıcak 5 günlük dönemi
# EPW'deki ışınım sütunları DWD'nin gerçek güneş zamanı (WOZ) ile etiketli ama UTC gibi işlenmiş;
# açık gökyüzü eğrisine göre ~32 dk geç. True → 01 adımında düzeltilir (bkz. README › Limitations).
RAD_WOZ_FIX = True
LAT, LON = 50.1109, 8.6821               # Römerberg (EPW istasyon koordinatı yerine)
UTC_OFFSET = 1.0                          # EPW yerel standart saat (CET); yaz saati değil

# ---------------------------------------------------------------- arazi örtüsü
# SOLWEIG / UMEP kodları: 0 taş-kaplama, 1 asfalt, 2 bina, 5 çim, 6 toprak, 7 su
LC_DEFAULT = 0            # yüzey bilgisi olmayan açık alan (sokak/avlu) → kaplama
LC_VALID = {0, 1, 2, 5, 6, 7}
LC_SCENARIO = {3, 4, 8, 9, 10, 99}   # tanımsız/senaryo sınıfları → modelde LC_DEFAULT, ayrıca maskelenir
RIVER_DEM_MAX = 92.3      # DGM1'de Main su yüzeyi bu kotun altında (en büyük bağlı bölge)
BUILDING_MIN_H = 2.5      # ALKIS tabanında DOM−DGM bu değerden yüksekse bina

# ---------------------------------------------------------------- konfor eşikleri (UTCI, °C)
UTCI_CLASSES = [(9, "Isı stresi yok"), (26, "Orta"), (32, "Güçlü"), (38, "Çok güçlü"), (46, "Aşırı")]
