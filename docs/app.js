/* Frankfurt Platz Heat — interactive viewer
 * Hourly SOLWEIG frames are 8-bit grayscale PNGs: 0 = no data, 1..255 = lo..hi (data/manifest.json).
 * Colouring happens here, so the same frames can feed a later in-browser design sandbox.
 * Model time is CET (UTC+1, as in the weather file); the UI shows summer time (CEST = CET + 1 h).
 */
(async function () {
  const $ = (s) => document.querySelector(s);
  const [M, STATS, PLAZAS, TSraw] = await Promise.all([
    fetch("data/manifest.json").then((r) => r.json()),
    fetch("data/plaza_stats.json").then((r) => r.json()),
    fetch("data/plazas_altstadt_wgs84.geojson").then((r) => r.json()),
    fetch("data/plaza_timeseries.csv").then((r) => r.text()),
  ]);
  const TS = parseCSV(TSraw);
  const W = M.width, H = M.height;
  proj4.defs("EPSG:25832", "+proj=utm +zone=32 +ellps=GRS80 +units=m +no_defs");
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;

  // ================================================================ colour scales
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const ramp = (stops) => (x) => {
    if (x <= stops[0][0]) return hex(stops[0][1]);
    for (let i = 1; i < stops.length; i++) {
      const [x1, c1] = stops[i], [x0, c0] = stops[i - 1];
      if (x <= x1) { const t = (x - x0) / (x1 - x0), a = hex(c0), b = hex(c1); return a.map((v, k) => v + (b[k] - v) * t); }
    }
    return hex(stops[stops.length - 1][1]);
  };
  const css = (c) => `rgb(${c.map(Math.round).join(",")})`;

  // UTCI heat-stress classes (Bröde et al. 2012). One colour per class; the map blends smoothly between them.
  const UTCI_STOPS = [[18, "#6fb2b4"], [23, "#94ccbf"], [29, "#f0de92"], [35, "#f5ad62"], [38.5, "#ec8550"], [42, "#d9573f"], [45, "#b1323f"], [48, "#7d1d3c"], [52, "#4e1230"]];
  const UTCI_RAMP = ramp(UTCI_STOPS);
  const UTCI_CLASSES = [   // swatch = map colour at the class middle, so legend and map match exactly
    { hi: 26, mid: 23, name: "Comfortable", sub: "no heat stress" },
    { hi: 32, mid: 29, name: "Warm", sub: "moderate stress" },
    { hi: 38, mid: 35, name: "Hot", sub: "strong stress" },
    { hi: 46, mid: 42, name: "Very hot", sub: "very strong stress" },
    { hi: 99, mid: 48, name: "Extreme", sub: "extreme stress" },
  ];
  UTCI_CLASSES.forEach((k) => (k.c = css(UTCI_RAMP(k.mid))));
  const utciClass = (v) => UTCI_CLASSES.findIndex((k) => v < k.hi);
  const HEAT = [[0, dark ? "#2d2b27" : "#f3eee4"], [2, "#f1df94"], [4, "#f3a964"], [6, "#dc5a45"], [8.5, "#86203d"]];

  const SCALES = {
    utci: { f: UTCI_RAMP, kind: "utci", title: "How hot it feels", sub: "UTCI felt temperature, °C. Shade, sun, hot paving and walls all count.", unit: "°C" },
    utci_night_mean: { f: UTCI_RAMP, kind: "utci", title: "How warm the nights feel", sub: "Average felt temperature 23–06 h. Above 26 °C, sleep and recovery suffer.", unit: "°C" },
    tmrt: { f: ramp([[20, "#5f8fb2"], [32, "#9fcac4"], [42, "#f1df94"], [52, "#f3a964"], [62, "#dc5a45"], [72, "#86203d"]]), kind: "bar",
      title: "Heat from sun & surfaces", sub: "Mean radiant temperature (Tmrt), °C: what your skin receives from sun, sky, ground and walls.", ticks: [20, 35, 50, 65], ends: ["shade", "full sun"], unit: "°C" },
    utci_hours38_per_day: { f: ramp(HEAT), kind: "bar", title: "Hours of very strong heat stress", sub: "Per day, 09–19 h: how long it feels like 38 °C or more.", ticks: [0, 2, 4, 6, 8, 10], ends: ["never", "all day"], unit: " h" },
    utci_hours32_per_day: { f: ramp(HEAT), kind: "bar", title: "Hours of strong heat stress", sub: "Per day, 09–19 h: how long it feels like 32 °C or more. In this heatwave even most shade is above 32 °C.", ticks: [0, 2, 4, 6, 8, 10], ends: ["never", "all day"], unit: " h" },
    sun_hours_day_per_day: { f: ramp([[0, "#4b5d7e"], [3, "#8c9bb5"], [6, "#e9cf78"], [10, "#f6b53b"]]), kind: "bar", title: "Hours of direct sun", sub: "Per day, 09–19 h. Shade from buildings and trees is the main cooling lever.", ticks: [0, 2, 4, 6, 8, 10], ends: ["always shaded", "always sunny"], unit: " h" },
  };
  SCALES.tmrt_max = { ...SCALES.tmrt, title: "Hottest moment", sub: "Highest radiant temperature reached in the 5 days, °C." };
  SCALES.tmrt_day_mean = { ...SCALES.tmrt, title: "Average daytime radiant heat", sub: "Mean Tmrt 09–19 h over the 5 days, °C." };
  const HINTS = {
    utci: "Felt temperature — air, sun, surfaces and wind combined.",
    tmrt: "Only the radiation part: where sun and hot surfaces load the body.",
    summary: "Five days condensed into one map.",
  };

  const luts = {};
  function lut(key) {
    if (luts[key]) return luts[key];
    const [lo, hi] = M.ranges[key], f = SCALES[key].f, L = new Uint8ClampedArray(256 * 4);
    for (let v = 1; v < 256; v++) { const c = f(lo + ((v - 1) / 254) * (hi - lo)); L.set([c[0], c[1], c[2], 240], v * 4); }
    return (luts[key] = L);
  }
  const decode = (key, v) => { const [lo, hi] = M.ranges[key]; return lo + ((v - 1) / 254) * (hi - lo); };

  // ================================================================ frames
  const cache = new Map();
  const scratch = Object.assign(document.createElement("canvas"), { width: W, height: H });
  const sctx = scratch.getContext("2d", { willReadFrequently: true });
  function loadGray(url) {
    if (cache.has(url)) return cache.get(url);
    const p = new Promise((res, rej) => {
      const img = new Image();
      img.onload = () => {
        sctx.clearRect(0, 0, W, H); sctx.drawImage(img, 0, 0);
        const d = sctx.getImageData(0, 0, W, H).data, g = new Uint8Array(W * H);
        for (let i = 0; i < g.length; i++) g[i] = d[i * 4];
        res(g);
      };
      img.onerror = rej; img.src = url;
    });
    cache.set(url, p);
    return p;
  }
  const frameURL = (layer, iso) => `data/frames/${layer}_${iso.slice(0, 10).replaceAll("-", "")}_${iso.slice(11, 13)}.png`;

  const view = Object.assign(document.createElement("canvas"), { width: W, height: H });
  const vctx = view.getContext("2d");
  const out = vctx.createImageData(W, H);
  let current = null;
  function paint(key, gray) {
    const L = lut(key), o = out.data;
    for (let i = 0, j = 0; i < gray.length; i++, j += 4) {
      const v = gray[i] * 4; o[j] = L[v]; o[j + 1] = L[v + 1]; o[j + 2] = L[v + 2]; o[j + 3] = L[v + 3];
    }
    vctx.putImageData(out, 0, 0);
    current = { key, gray };
    const src = map.getSource("heat");
    if (src) { src.play(); requestAnimationFrame(() => src.pause()); }
  }

  // ================================================================ state
  const days = [...new Set(M.times.map((t) => t.slice(0, 10)))];
  const S = { layer: "utci", summary: "utci_hours38_per_day", day: Math.max(0, days.indexOf("2026-06-27")), hour: 14, playing: null, plaza: null };
  const iso = () => `${days[S.day]}T${String(S.hour).padStart(2, "0")}:00`;
  const cest = (h) => String((h + 1) % 24).padStart(2, "0") + ":00";

  // ================================================================ controls
  $("#days").innerHTML = days.map((d, i) => {
    const info = (M.days || []).find((x) => x.date === d);
    const dt = new Date(d + "T12:00");
    return `<button data-i="${i}" role="tab">${dt.toLocaleDateString("en-GB", { weekday: "short", day: "numeric" })}<small>${info ? Math.round(info.tmax) + "° air" : ""}</small></button>`;
  }).join("");
  $("#days").onclick = (e) => { const b = e.target.closest("button"); if (b) { S.day = +b.dataset.i; update(); } };
  $("#hour").oninput = (e) => { S.hour = +e.target.value; update(); };
  $("#layerSeg").onclick = (e) => { const b = e.target.closest("button"); if (b) { S.layer = b.dataset.layer; update(); } };
  $("#summarySel").onchange = (e) => { S.summary = e.target.value; update(); };
  const PLAY = "M8 5v14l11-7z", PAUSE = "M7 5h4v14H7zM13 5h4v14h-4z";
  $("#play").onclick = () => {
    if (S.playing) { clearInterval(S.playing); S.playing = null; $("#playIcon").setAttribute("d", PLAY); return; }
    if (S.layer === "summary") { S.layer = "utci"; }
    $("#playIcon").setAttribute("d", PAUSE);
    S.playing = setInterval(() => { S.hour = (S.hour + 1) % 24; if (S.hour === 0) S.day = (S.day + 1) % days.length; update(); }, 700);
  };
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select")) return;
    if (e.key === "ArrowRight") { S.hour = (S.hour + 1) % 24; update(); }
    if (e.key === "ArrowLeft") { S.hour = (S.hour + 23) % 24; update(); }
    if (e.key === " ") { e.preventDefault(); $("#play").click(); }
  });
  try { if (!localStorage.getItem("fph-seen")) { $("#howto").open = true; localStorage.setItem("fph-seen", "1"); } } catch { $("#howto").open = true; }

  let token = 0;
  async function update() {
    document.querySelectorAll("#layerSeg button").forEach((b) => b.classList.toggle("on", b.dataset.layer === S.layer));
    document.querySelectorAll("#days button").forEach((b) => b.classList.toggle("on", +b.dataset.i === S.day));
    $("#summarySel").hidden = S.layer !== "summary";
    $("#layerHint").textContent = HINTS[S.layer];
    $("#player").classList.toggle("disabled", S.layer === "summary");
    $("#hour").value = S.hour;
    $("#hourOut").textContent = cest(S.hour);
    $("#sunState").textContent = sunText();
    const key = S.layer === "summary" ? S.summary : S.layer;
    const url = S.layer === "summary" ? `data/period/${S.summary}.png` : frameURL(S.layer, iso());
    legend(key);
    const my = ++token;
    try {
      const g = await loadGray(url);
      if (my === token) paint(key, g);
      $("#loading").classList.add("done");
    } catch { $("#loading").textContent = "Could not load this hour"; }
    if (S.layer !== "summary") prefetch();
    if (S.plaza) detail(S.plaza);
  }
  function prefetch() {
    for (let h = 0; h < 24; h++) loadGray(frameURL(S.layer, `${days[S.day]}T${String(h).padStart(2, "0")}:00`));
  }

  // simple solar altitude for the label under the clock (interval mid-point, CET)
  function sunAlt() {
    const d = new Date(`${days[S.day]}T00:00:00Z`), doy = Math.floor((d - Date.UTC(d.getUTCFullYear(), 0, 0)) / 864e5);
    const utc = S.hour - 1 - 0.5, g = (2 * Math.PI / 365) * (doy - 1 + (utc - 12) / 24);
    const eot = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g) - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
    const dec = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g) + 0.000907 * Math.sin(2 * g);
    const ha = ((utc * 60 + eot + 4 * 8.68) / 4 - 180) * Math.PI / 180, lat = 50.11 * Math.PI / 180;
    return Math.asin(Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(ha)) * 180 / Math.PI;
  }
  function sunText() {
    const a = sunAlt();
    if (a < -2) return "night";
    if (a < 12) return "sun low";
    if (a < 45) return `sun ${Math.round(a)}° high`;
    return `high sun · ${Math.round(a)}°`;
  }

  // ================================================================ legend
  let legendKey = null;
  function legend(key) {
    if (key === legendKey) return;
    legendKey = key;
    const sc = SCALES[key], el = $("#legend");
    if (sc.kind === "utci") {
      const A = 18, Z = 52, n = 34, pct = (v) => ((v - A) / (Z - A)) * 100;
      const grad = Array.from({ length: n + 1 }, (_, i) => css(sc.f(A + ((Z - A) * i) / n))).join(",");
      const seg = UTCI_CLASSES.map((k, i) => {
        const lo = i ? UTCI_CLASSES[i - 1].hi : A, hi = Math.min(k.hi, Z);
        return `<div data-c="${i}" style="width:${pct(hi) - pct(lo)}%"><b>${k.name}</b></div>`;
      }).join("");
      el.innerHTML = `<div class="lg-title">${sc.title}</div><div class="lg-sub">${sc.sub}</div>
        <div class="lg-bar" style="background:linear-gradient(90deg,${grad})"></div>
        <div class="lg-marks mono">${[26, 32, 38, 46].map((v) => `<span style="left:${pct(v)}%">${v}°</span>`).join("")}</div>
        <div class="lg-classes">${seg}</div>`;
      return;
    }
    const [lo, hi] = M.ranges[key], n = 24;
    const grad = Array.from({ length: n + 1 }, (_, i) => css(sc.f(lo + ((hi - lo) * i) / n))).join(",");
    el.innerHTML = `<div class="lg-title">${sc.title}</div><div class="lg-sub">${sc.sub}</div>
      <div class="lg-bar" style="background:linear-gradient(90deg,${grad})"></div>
      <div class="lg-ticks mono">${sc.ticks.map((t) => `<span>${t}</span>`).join("")}</div>
      <div class="lg-ends"><span>← ${sc.ends[0]}</span><span>${sc.ends[1]} →</span></div>`;
  }
  function highlightClass(i) {
    document.querySelectorAll(".lg-classes > div").forEach((d) => d.classList.toggle("dim", i !== null && +d.dataset.c !== i));
  }

  // ================================================================ ranking & detail
  const P = STATS.plazas, maxH = Math.max(...P.map((p) => p.utci38_hours_per_day), 0.1), minH = Math.min(...P.map((p) => p.utci38_hours_per_day));
  const hoursCol = ramp(HEAT);
  $("#rank").innerHTML = P.map((p) => `<li data-pid="${p.pid}"><span class="n mono">${p.rank}</span><span>${p.name}</span>
    <span><div class="bar" style="width:${(p.utci38_hours_per_day / maxH) * 100}%;background:${css(hoursCol(p.utci38_hours_per_day))}"></div></span>
    <span class="v mono">${p.utci38_hours_per_day.toFixed(1)} h</span></li>`).join("");
  $("#rank").onclick = (e) => { const li = e.target.closest("li"); if (li) select(li.dataset.pid, true); };

  const LC = { 0: ["Paving", "#cdc4b4"], 1: ["Asphalt", "#5b5750"], 2: ["Building", "#b8a48c"], 5: ["Grass", "#8cc06a"], 6: ["Soil", "#b08850"], 7: ["Water", "#6aa6d6"] };
  function select(pid, fly) {
    S.plaza = pid;
    document.querySelectorAll("#rank li").forEach((li) => li.classList.toggle("on", li.dataset.pid === pid));
    if (map.getLayer("plaza-sel")) map.setFilter("plaza-sel", ["==", ["get", "pid"], pid]);
    if (fly) {
      const f = PLAZAS.features.find((x) => x.properties.pid === pid);
      map.fitBounds(bbox(f.geometry), { padding: { top: 120, bottom: 140, left: innerWidth > 860 ? 420 : 40, right: 60 }, maxZoom: 17.4, duration: 800 });
    }
    detail(pid);
    if (innerWidth <= 860) $("#detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function verdict(p) {
    const ratio = p.utci38_hours_per_day / minH;
    const why = p.tree_share < 5 && p.sun_share_day > 65 ? "almost no tree shade and long sun exposure"
      : p.tree_share >= 30 ? `${p.tree_share}% tree cover keeps much of it in shade`
      : p.sun_share_day < 45 ? "surrounding buildings shade it for much of the day" : "partly shaded by trees and buildings";
    return `${p.rank <= 8 ? "One of the hottest squares" : p.rank >= P.length - 4 ? "One of the cooler squares" : "Mid-range"}: ${why}. ` +
      (ratio > 1.15 ? `It spends ${ratio.toFixed(1)}× as long in very strong heat stress as the coolest square.` : "");
  }
  function detail(pid) {
    const p = P.find((x) => x.pid === pid), el = $("#detail");
    const rows = TS.filter((r) => r.pid === pid);
    const lc = Object.entries(p.lc_share).filter(([k]) => LC[k]).map(([k, v]) => `<span title="${LC[k][0]} ${v}%" style="width:${v}%;background:${LC[k][1]}"></span>`).join("");
    const lcTxt = Object.entries(p.lc_share).filter(([k, v]) => LC[k] && v >= 3).map(([k, v]) => `${LC[k][0]} ${v}%`).join(" · ");
    el.hidden = false;
    el.innerHTML = `<button class="close" aria-label="Close">×</button><h3>${p.name}</h3>
      <p class="hint" style="margin:0">Rank ${p.rank} of ${P.length} · ${p.area_m2.toLocaleString("en")} m² · trees cover ${p.tree_share}%</p>
      <div class="kpis">
        <div class="kpi"><b>${p.utci38_hours_per_day.toFixed(1)} h</b><span>a day feeling 38 °C or more</span></div>
        <div class="kpi"><b>${Math.round(p.utci_day_mean)}°</b><span>average daytime felt temp.</span></div>
        <div class="kpi"><b>${p.sun_share_day}%</b><span>of the day in direct sun</span></div>
      </div>
      ${chart(rows)}
      <p class="verdict">${verdict(p)}</p>
      <div class="lcbar">${lc}</div><p class="hint" style="margin-top:2px">Ground: ${lcTxt}</p>`;
    el.querySelector(".close").onclick = () => {
      el.hidden = true; S.plaza = null; map.setFilter("plaza-sel", ["==", ["get", "pid"], ""]);
      document.querySelectorAll("#rank li").forEach((li) => li.classList.remove("on"));
    };
  }
  function chart(rows) {
    const w = 316, h = 132, l = 24, r = 4, t = 6, b = 18, n = rows.length;
    const y0 = 15, y1 = 75;
    const X = (i) => l + (i / (n - 1)) * (w - l - r), Y = (v) => t + (1 - (v - y0) / (y1 - y0)) * (h - t - b);
    const line = (k) => rows.map((q, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(Math.min(y1, Math.max(y0, q[k]))).toFixed(1)}`).join("");
    let g = "";
    // background bands = UTCI classes, same colours as the map legend
    let prev = y0;
    UTCI_CLASSES.forEach((k) => { const top = Math.min(k.hi, y1); if (top > prev) g += `<rect x="${l}" width="${w - l - r}" y="${Y(top)}" height="${Y(prev) - Y(top)}" fill="${k.c}" opacity=".28"/>`; prev = top; });
    for (let v = 20; v <= 70; v += 10) g += `<text x="${l - 4}" y="${Y(v) + 3}" text-anchor="end">${v}</text>`;
    days.forEach((d) => { const k = rows.findIndex((q) => q.t.startsWith(d)); if (k >= 0) g += `<text x="${X(k + 12)}" y="${h - 4}" text-anchor="middle">${+d.slice(8)} Jun</text>`; });
    const ci = rows.findIndex((q) => q.t === iso());
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="Hourly felt and radiant temperature on this square">${g}
      <path d="${line("tmrt")}" fill="none" stroke="#dc5a45" stroke-width="1.3" opacity=".75"/>
      <path d="${line("utci")}" fill="none" stroke="${dark ? "#efece6" : "#2b2925"}" stroke-width="1.8"/>
      ${ci >= 0 && S.layer !== "summary" ? `<line x1="${X(ci)}" x2="${X(ci)}" y1="${t}" y2="${h - b}" stroke="var(--accent)" stroke-width="1.5"/><circle cx="${X(ci)}" cy="${Y(rows[ci].utci)}" r="3.5" fill="var(--accent)"/>` : ""}
      </svg><p class="hint chart-key"><b style="color:var(--ink)">━</b> feels like (UTCI) &nbsp; <b style="color:#dc5a45">━</b> sun &amp; surfaces (Tmrt) &nbsp;· hourly average over the square, °C</p>`;
  }

  // ================================================================ map
  const DOP = "https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-images.ows?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=he_dop20_rgb&STYLES=&CRS=EPSG:3857&BBOX={bbox-epsg-3857}&WIDTH=512&HEIGHT=512&FORMAT=image/jpeg";
  const map = new maplibregl.Map({
    container: "map",
    style: `https://basemaps.cartocdn.com/gl/${dark ? "dark-matter" : "positron"}-gl-style/style.json`,
    bounds: [M.corners[3], M.corners[1]],
    fitBoundsOptions: { padding: innerWidth > 860 ? { top: 40, bottom: 130, left: 400, right: 40 } : 20 },
    maxPitch: 70,
    attributionControl: { compact: innerWidth <= 860, customAttribution: "Datenbasis: Deutscher Wetterdienst, eigene Bearbeitung · Geobasisdaten © HVBG Hessen · Baumkataster © Stadt Frankfurt am Main" },
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
  const V = { base: "map", b3d: false, opacity: 0.8 };

  map.on("load", () => {
    const firstSymbol = map.getStyle().layers.find((l) => l.type === "symbol")?.id;
    map.addSource("aerial", { type: "raster", tiles: [DOP], tileSize: 512, maxzoom: 20, attribution: "Luftbild DOP20 © HVBG Hessen" });
    map.addLayer({ id: "aerial", type: "raster", source: "aerial", layout: { visibility: "none" }, paint: { "raster-fade-duration": 200, "raster-saturation": -0.15 } }, firstSymbol);
    let dopErrors = 0;
    map.on("error", (e) => {
      if (e.sourceId === "aerial" && ++dopErrors === 4) $("#loading").textContent = "Aerial photo service not reachable right now", $("#loading").classList.remove("done");
    });

    map.addSource("heat", { type: "canvas", canvas: view, coordinates: M.corners, animate: false });
    map.addLayer({ id: "heat", type: "raster", source: "heat", paint: { "raster-resampling": "linear", "raster-opacity": V.opacity, "raster-fade-duration": 0 } }, firstSymbol);
    for (const [id, url, op, vis] of [["trees", "data/trees.png", 0.75, "visible"], ["scenario", "data/scenario.png", 1, "none"]]) {
      map.addSource(id, { type: "image", url, coordinates: M.corners });
      map.addLayer({ id, type: "raster", source: id, paint: { "raster-opacity": op, "raster-resampling": "linear", "raster-fade-duration": 0 }, layout: { visibility: vis } }, firstSymbol);
    }

    // buildings — ALKIS footprints, roof height from DOM1 − DGM1
    map.addSource("bld", { type: "geojson", data: "data/buildings.geojson" });
    map.addLayer({ id: "bld-2d", type: "fill", source: "bld", paint: { "fill-color": dark ? "#3b3934" : "#f4f0e8", "fill-opacity": 0.97 } }, firstSymbol);
    map.addLayer({ id: "bld-line", type: "line", source: "bld", paint: { "line-color": dark ? "#4b4842" : "#ddd5c7", "line-width": 0.6 } }, firstSymbol);
    map.addLayer({ id: "bld-3d", type: "fill-extrusion", source: "bld", layout: { visibility: "none" },
      paint: { "fill-extrusion-height": ["get", "h"],
        // ALKIS Bauteile (passages, arcades, overhangs) float above street level instead of standing on it
        "fill-extrusion-base": ["case", ["in", ["get", "use"], ["literal", ["Durchfahrt im Gebäude", "Arkade", "Auskragender Geschossteil"]]], ["min", 4, ["*", 0.5, ["get", "h"]]], 0], "fill-extrusion-opacity": 0.9, "fill-extrusion-vertical-gradient": true,
        "fill-extrusion-color": ["interpolate", ["linear"], ["get", "h"], 0, dark ? "#4a4842" : "#f3efe7", 40, dark ? "#58554e" : "#e4ddd0", 150, dark ? "#6e6a61" : "#cfc6b6"] } });

    // trees — register height & crown diameter; trunk + three stacked crown discs
    map.addSource("tr3d", { type: "geojson", data: "data/trees3d.geojson" });
    map.addLayer({ id: "trees-3d", type: "fill-extrusion", source: "tr3d", layout: { visibility: "none" },
      paint: { "fill-extrusion-base": ["get", "b"], "fill-extrusion-height": ["get", "t"], "fill-extrusion-opacity": 0.95,
        "fill-extrusion-color": ["case", ["==", ["get", "part"], "trunk"], "#7d6450",
          ["interpolate", ["linear"], ["get", "h"], 4, "#9cc27a", 12, "#6fa35a", 25, "#4d8448"]] } });

    map.addSource("plazas", { type: "geojson", data: PLAZAS });
    map.addLayer({ id: "plaza-fill", type: "fill", source: "plazas", paint: { "fill-color": "#000", "fill-opacity": 0 } });
    map.addLayer({ id: "plaza-line", type: "line", source: "plazas", paint: { "line-color": dark ? "#efece6" : "#2b2925", "line-width": 1.2, "line-opacity": 0.55, "line-dasharray": [2, 2] } });
    map.addLayer({ id: "plaza-sel", type: "line", source: "plazas", filter: ["==", ["get", "pid"], ""], paint: { "line-color": "#d0613f", "line-width": 3, "line-blur": 0.5 } });
    map.addLayer({ id: "plaza-label", type: "symbol", source: "plazas", minzoom: 15.3,
      layout: { "text-field": ["get", "name"], "text-size": 11.5, "text-font": ["Montserrat Medium"], "text-allow-overlap": false },
      paint: { "text-color": dark ? "#efece6" : "#2b2925", "text-halo-color": dark ? "rgba(0,0,0,.7)" : "rgba(255,255,255,.85)", "text-halo-width": 1.6 } });

    map.on("click", "plaza-fill", (e) => select(e.features[0].properties.pid, false));
    map.on("mousemove", (e) => hover(e));
    map.on("click", (e) => hover(e));                       // touch screens: tap shows the value
    map.on("dragstart", () => ($("#tip").hidden = true));
    map.on("mouseout", () => { $("#tip").hidden = true; highlightClass(null); });
    update();
  });

  // ================================================================ hover tooltip
  function hover(e) {
    const tip = $("#tip");
    const layers = V.b3d ? ["trees-3d", "bld-3d"] : ["bld-2d"];
    const f = map.queryRenderedFeatures(e.point, { layers })[0];
    const onPlaza = map.queryRenderedFeatures(e.point, { layers: ["plaza-fill"] }).length > 0;
    map.getCanvas().style.cursor = onPlaza ? "pointer" : "";
    let html = null;
    highlightClass(null);
    if (f && f.layer.id === "trees-3d") {
      const p = f.properties;
      html = `<b>${p.sp || "Tree"}</b><small>${p.lat ? `<i>${p.lat}</i> · ` : ""}${Math.round(p.h)} m tall · crown ${p.cd} m${p.yr ? ` · planted ${p.yr}` : ""}</small>`;
    } else if (f) {
      const p = f.properties;
      html = `<b>${(p.name || p.use || "Building").replaceAll(";", " ")}</b><small>${Math.round(p.h)} m${p.addr ? " · " + p.addr : ""}</small>`;
    } else if (current) {
      const [x, y] = proj4("EPSG:4326", "EPSG:25832", [e.lngLat.lng, e.lngLat.lat]);
      const [L, , , T] = M.bounds_utm, c = Math.floor(x - L), r = Math.floor(T - y);
      const v = c >= 0 && r >= 0 && c < W && r < H ? current.gray[r * W + c] : 0;
      if (v) {
        const val = decode(current.key, v), sc = SCALES[current.key];
        const col = css(sc.f(val));
        if (sc.kind === "utci") {
          const k = utciClass(val); highlightClass(k);
          html = `<span class="dot" style="background:${col}"></span><b>Feels like ${Math.round(val)} °C</b><small>${UTCI_CLASSES[k].name} · ${UTCI_CLASSES[k].sub}</small>`;
        } else {
          html = `<span class="dot" style="background:${col}"></span><b>${val.toFixed(sc.unit === " h" ? 1 : 0)}${sc.unit}</b><small>${sc.title}</small>`;
        }
      }
    }
    if (!html) { tip.hidden = true; return; }
    tip.innerHTML = html; tip.hidden = false;
    const box = map.getContainer().getBoundingClientRect();
    tip.style.left = `${Math.min(e.point.x, box.width - 260)}px`; tip.style.top = `${e.point.y}px`;
  }

  // ================================================================ view controls
  $("#baseSeg").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    V.base = b.dataset.base;
    document.querySelectorAll("#baseSeg button").forEach((x) => x.classList.toggle("on", x === b));
    const aerial = V.base === "aerial";
    map.setLayoutProperty("aerial", "visibility", aerial ? "visible" : "none");
    map.setPaintProperty("bld-2d", "fill-opacity", aerial ? 0 : 0.97);
    map.setPaintProperty("bld-line", "line-color", aerial ? "rgba(255,255,255,.7)" : (dark ? "#4b4842" : "#ddd5c7"));
    map.setLayoutProperty("trees", "visibility", aerial || V.b3d ? "none" : "visible");
    if (aerial && V.opacity > 0.65) setOpacity(0.6);
    if (!aerial && V.opacity < 0.8) setOpacity(0.8);
  };
  $("#b3dTog").onchange = (e) => {
    V.b3d = e.target.checked;
    for (const id of ["bld-3d", "trees-3d"]) map.setLayoutProperty(id, "visibility", V.b3d ? "visible" : "none");
    map.setLayoutProperty("bld-line", "visibility", V.b3d ? "none" : "visible");   // flat footprints stay under the 3D blocks to fill small gaps
    map.setLayoutProperty("trees", "visibility", V.b3d || V.base === "aerial" ? "none" : "visible");
    map.easeTo({ pitch: V.b3d ? 58 : 0, bearing: V.b3d ? -24 : 0, zoom: V.b3d ? Math.max(map.getZoom(), 16.2) : map.getZoom(), duration: 1000 });
  };
  $("#scenTog").onchange = (e) => map.setLayoutProperty("scenario", "visibility", e.target.checked ? "visible" : "none");
  function setOpacity(v) { V.opacity = v; $("#opac").value = Math.round(v * 100); map.setPaintProperty("heat", "raster-opacity", v); }
  $("#opac").oninput = (e) => setOpacity(+e.target.value / 100);

  // link to the repository when served from GitHub Pages (user.github.io/repo/)
  if (location.hostname.endsWith("github.io")) {
    const user = location.hostname.split(".")[0], repo = location.pathname.split("/").filter(Boolean)[0] || "";
    $("#repoLink").href = `https://github.com/${user}/${repo}`;
  }

  // ================================================================ helpers
  function parseCSV(txt) {
    const [head, ...lines] = txt.trim().split("\n"), k = head.split(",");
    return lines.map((l) => { const v = l.split(","), o = {}; k.forEach((n, i) => (o[n] = isNaN(+v[i]) ? v[i] : +v[i])); return o; });
  }
  function bbox(g) {
    const cs = g.type === "Polygon" ? g.coordinates.flat() : g.coordinates.flat(2);
    const xs = cs.map((c) => c[0]), ys = cs.map((c) => c[1]);
    return [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]];
  }
})();
