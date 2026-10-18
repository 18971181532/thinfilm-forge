/* ThinFilmForge - front-end controller and hand-rolled canvas charts.
   No build step, no dependencies: open the served page and everything works. */

(() => {
  'use strict';

  // ---------------------------------------------------------------- helpers
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const fmt = (v, d) => (v === null || v === undefined || !isFinite(v) ? '—' : Number(v).toFixed(d === undefined ? 2 : d));
  const pct = (v, d) => fmt(v * 100, d === undefined ? 2 : d) + '%';

  function debounce(fn, ms) {
    let t = null;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  let toastTimer = null;
  function toast(msg) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('on'), 2200);
  }

  async function api(path, body) {
    const options = body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    };
    const res = await fetch(path, options);
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok || (data && data.ok === false)) {
      throw new Error((data && data.error) || ('HTTP ' + res.status));
    }
    return data;
  }

  // ------------------------------------------------------------------ state
  const state = {
    tab: 'sim',
    materials: [],
    presets: [],
    stack: { incident: 'Air', substrate: 'BK7', layers: [] },
    theta: 0,
    pol: 's',
    range: [380, 780],
    points: 241,
    sim: null,
    bands: [{ lo: 420, hi: 680, kind: 'max', value: 0.01, weight: 1 }],
    design: { bands: [{ lo: 420, hi: 680, kind: 'max', value: 0.005, weight: 1 }], result: null, poll: null },
    tol: { bands: [{ lo: 420, hi: 680, kind: 'max', value: 0.02, weight: 1 }], result: null, touched: false },
    selectedMaterial: 'TiO2',
    hover: null,
    log: false,
  };

  // ------------------------------------------------------------- canvas util
  function setup(canvas) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(20, Math.round(rect.width));
    const h = Math.max(20, Math.round(rect.height));
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
    ctx.textBaseline = 'middle';
    return { ctx, w, h };
  }

  function niceTicks(min, max, count) {
    count = count || 5;
    const span = max - min;
    if (!(span > 0)) return [min];
    const rough = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(rough)));
    const norm = rough / mag;
    let step = mag * (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10);
    const out = [];
    const start = Math.ceil(min / step - 1e-9) * step;
    for (let v = start; v <= max + step * 0.001; v += step) out.push(+v.toFixed(10));
    return out;
  }

  function makePlot(ctx, w, h, opt) {
    const pad = opt.pad || { l: 48, r: 16, t: 14, b: 28 };
    const box = { x: pad.l, y: pad.t, w: Math.max(10, w - pad.l - pad.r), h: Math.max(10, h - pad.t - pad.b) };
    const xd = opt.xd, yd = opt.yd;
    const sx = (v) => box.x + ((v - xd[0]) / (xd[1] - xd[0])) * box.w;
    const sy = (v) => box.y + box.h - ((v - yd[0]) / (yd[1] - yd[0])) * box.h;
    const invX = (px) => xd[0] + ((px - box.x) / box.w) * (xd[1] - xd[0]);
    const invY = (py) => yd[1] - ((py - box.y) / box.h) * (yd[1] - yd[0]);

    ctx.save();
    ctx.strokeStyle = cssVar('--grid');
    ctx.lineWidth = 1;
    ctx.fillStyle = cssVar('--muted');
    ctx.textAlign = 'right';
    const yticks = opt.yticks || niceTicks(yd[0], yd[1], opt.yCount || 5);
    yticks.forEach((v) => {
      if (v < yd[0] - 1e-9 || v > yd[1] + 1e-9) return;
      const y = Math.round(sy(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(box.x, y); ctx.lineTo(box.x + box.w, y); ctx.stroke();
      ctx.fillText(opt.yfmt ? opt.yfmt(v) : fmt(v, 0), box.x - 8, y);
    });
    ctx.textAlign = 'center';
    const xticks = opt.xticks || niceTicks(xd[0], xd[1], opt.xCount || 6);
    xticks.forEach((v) => {
      if (v < xd[0] - 1e-9 || v > xd[1] + 1e-9) return;
      const x = Math.round(sx(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(x, box.y); ctx.lineTo(x, box.y + box.h); ctx.stroke();
      ctx.fillText(opt.xfmt ? opt.xfmt(v) : fmt(v, 0), x, box.y + box.h + 14);
    });
    ctx.strokeStyle = cssVar('--line');
    ctx.strokeRect(box.x + 0.5, box.y + 0.5, box.w, box.h);
    if (opt.xlabel) {
      ctx.fillStyle = cssVar('--muted');
      ctx.textAlign = 'right';
      ctx.fillText(opt.xlabel, box.x + box.w, box.y + box.h + 14);
    }
    if (opt.ylabel) {
      ctx.save();
      ctx.translate(11, box.y + 2);
      ctx.textAlign = 'left';
      ctx.fillStyle = cssVar('--muted');
      ctx.fillText(opt.ylabel, 0, 0);
      ctx.restore();
    }
    ctx.restore();
    return { sx, sy, invX, invY, box };
  }

  function drawLine(ctx, pts, color, width, dash) {
    if (!pts.length) return;
    ctx.save();
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = width || 1.6;
    ctx.lineJoin = 'round';
    if (dash) ctx.setLineDash(dash);
    pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
    ctx.stroke();
    ctx.restore();
  }

  // ------------------------------------------------------------------- tabs
  function switchTab(name) {
    if (!['sim', 'design', 'tol', 'mat'].includes(name)) name = 'sim';
    state.tab = name;
    if (history.replaceState) history.replaceState(null, '', '#' + name);
    $$('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
    $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + name));
    if (name === 'sim') {
      if (!state.sim || !state.sim.angleMap) refreshSim(); else renderSim();
    }
    if (name === 'design') drawConvergence();
    if (name === 'tol') {
      maybeSyncTolBand();
      if (state.tol.result) { drawHistogram(state.tol.result); drawEnvelope(); }
    }
    if (name === 'mat') drawDispersion();
  }

  // -------------------------------------------------------------- materials
  async function loadCatalog() {
    const [mat, pre] = await Promise.all([api('/api/materials'), api('/api/presets')]);
    const rank = { dielectric: 0, polymer: 1, metal: 2, substrate: 3, ambient: 4 };
    state.materials = mat.materials.sort((a, b) =>
      (rank[a.category] === undefined ? 9 : rank[a.category]) -
      (rank[b.category] === undefined ? 9 : rank[b.category]) || b.n550 - a.n550);
    state.presets = pre.presets;
    $('#matCount').textContent = state.materials.length + ' materials';
    fillSelect($('#presetSelect'),
      [{ value: 'custom', label: '自定义膜系' }].concat(state.presets.map((p) => ({ value: p.id, label: p.name }))),
      'ar_broadband');
    const ambient = state.materials.filter((m) => m.category === 'ambient');
    const subs = state.materials.filter((m) => ['substrate', 'metal', 'dielectric'].includes(m.category));
    fillSelect($('#incidentSelect'), ambient.map((m) => ({ value: m.key, label: m.label })), 'Air');
    fillSelect($('#substrateSelect'), subs.map((m) => ({ value: m.key, label: m.label })), 'BK7');
    const films = state.materials.filter((m) => ['dielectric', 'metal', 'polymer'].includes(m.category));
    fillSelect($('#designHigh'), films.map((m) => ({ value: m.key, label: m.label })), 'TiO2');
    fillSelect($('#designLow'), films.map((m) => ({ value: m.key, label: m.label })), 'SiO2');
    renderMaterialList();
  }

  function fillSelect(sel, items, value) {
    sel.innerHTML = '';
    items.forEach((it) => {
      const o = document.createElement('option');
      o.value = it.value;
      o.textContent = it.label;
      sel.appendChild(o);
    });
    if (value) sel.value = value;
  }

  function materialByKey(key) { return state.materials.find((m) => m.key === key); }

  function materialColor(key) {
    const m = materialByKey(key);
    if (!m) return '#94a3b8';
    if (m.category === 'metal') {
      if (key === 'Au') return '#d4a017';
      if (key === 'Ag') return '#b9c2cc';
      return '#8e99a6';
    }
    const t = clamp((m.n550 - 1.3) / (2.5 - 1.3), 0, 1);
    const h = 182 - 128 * t;
    const s = 46 + 14 * t;
    const l = 62 - 20 * t;
    return 'hsl(' + h.toFixed(0) + ' ' + s.toFixed(0) + '% ' + l.toFixed(0) + '%)';
  }

  function renderMaterialOptions(select, value) {
    const groups = {};
    state.materials.forEach((m) => {
      if (m.category === 'ambient' || m.category === 'substrate') return;
      (groups[m.category] = groups[m.category] || []).push(m);
    });
    select.innerHTML = '';
    const labels = { dielectric: '介质薄膜', metal: '金属', polymer: '聚合物' };
    Object.keys(groups).forEach((cat) => {
      const og = document.createElement('optgroup');
      og.label = labels[cat] || cat;
      groups[cat].forEach((m) => {
        const o = document.createElement('option');
        o.value = m.key;
        o.textContent = m.label;
        og.appendChild(o);
      });
      select.appendChild(og);
    });
    if (value) select.value = value;
  }

  // ------------------------------------------------------------ layer editor
  function markCustom() {
    const sel = $('#presetSelect');
    if (sel && sel.value !== 'custom') sel.value = 'custom';
  }

  function renderLayerList() {
    const host = $('#layerList');
    host.innerHTML = '';
    state.stack.layers.forEach((layer, i) => {
      const row = document.createElement('div');
      row.className = 'layer-row';

      const idx = document.createElement('span');
      idx.className = 'idx';
      idx.textContent = i + 1;
      row.appendChild(idx);

      const sel = document.createElement('select');
      renderMaterialOptions(sel, layer.material);
      sel.addEventListener('change', () => {
        state.stack.layers[i].material = sel.value;
        markCustom();
        refreshSim();
      });
      row.appendChild(sel);

      const thick = document.createElement('input');
      thick.type = 'number';
      thick.min = '0';
      thick.step = '1';
      thick.value = fmt(layer.thickness, 1);
      thick.title = '物理厚度 / nm';
      const commit = () => {
        const v = parseFloat(thick.value);
        if (!isFinite(v) || v < 0) { thick.value = fmt(state.stack.layers[i].thickness, 1); return; }
        state.stack.layers[i].thickness = v;
        markCustom();
        refreshSim();
      };
      thick.addEventListener('change', commit);
      thick.addEventListener('keydown', (e) => { if (e.key === 'Enter') commit(); });
      row.appendChild(thick);

      const up = document.createElement('button');
      up.className = 'mini';
      up.textContent = '↑';
      up.title = '上移';
      up.disabled = i === 0;
      up.addEventListener('click', () => moveLayer(i, -1));
      row.appendChild(up);

      const del = document.createElement('button');
      del.className = 'mini del';
      del.textContent = '×';
      del.title = '删除该层';
      del.addEventListener('click', () => {
        state.stack.layers.splice(i, 1);
        markCustom();
        renderLayerList();
        refreshSim();
      });
      row.appendChild(del);
      host.appendChild(row);
    });

    $('#stackMeta').textContent = state.stack.layers.length + ' 层 · ' +
      fmt(state.stack.layers.reduce((a, l) => a + l.thickness, 0), 0) + ' nm';
  }

  function moveLayer(i, dir) {
    const j = i + dir;
    if (j < 0 || j >= state.stack.layers.length) return;
    const tmp = state.stack.layers[i];
    state.stack.layers[i] = state.stack.layers[j];
    state.stack.layers[j] = tmp;
    renderLayerList();
    refreshSim();
  }

  function alternateMaterial(i) {
    const hi = $('#designHigh').value || 'TiO2';
    const lo = $('#designLow').value || 'SiO2';
    const last = state.stack.layers[state.stack.layers.length - 1];
    if (!last) return hi;
    return last.material === hi ? lo : hi;
  }

  // ------------------------------------------------------------------ bands
  const KIND_LABEL = { max: 'R ≤', min: 'R ≥', target: 'R ≈' };

  function renderBandEditor(host, bands, onChange) {
    host.innerHTML = '';
    bands.forEach((band, i) => {
      const row = document.createElement('div');
      row.className = 'band-row';

      const lo = document.createElement('input');
      lo.type = 'number'; lo.value = band.lo; lo.title = '起始波长 / nm';
      lo.addEventListener('change', () => { band.lo = parseFloat(lo.value) || band.lo; onChange(); });

      const hi = document.createElement('input');
      hi.type = 'number'; hi.value = band.hi; hi.title = '终止波长 / nm';
      hi.addEventListener('change', () => { band.hi = parseFloat(hi.value) || band.hi; onChange(); });

      const kind = document.createElement('select');
      [['max', 'R ≤'], ['min', 'R ≥'], ['target', 'R ≈']].forEach(([v, t]) => {
        const o = document.createElement('option'); o.value = v; o.textContent = t; kind.appendChild(o);
      });
      kind.value = band.kind;
      kind.addEventListener('change', () => { band.kind = kind.value; onChange(); });

      const val = document.createElement('input');
      val.type = 'number'; val.step = '0.001'; val.value = band.value; val.title = '目标反射率 (0-1)';
      val.addEventListener('change', () => { band.value = parseFloat(val.value) || 0; onChange(); });

      const del = document.createElement('button');
      del.className = 'mini'; del.textContent = '×'; del.title = '删除';
      del.addEventListener('click', () => {
        if (bands.length <= 1) { toast('至少保留一条判据'); return; }
        bands.splice(i, 1);
        renderBandEditor(host, bands, onChange);
        onChange();
      });

      row.append(lo, hi, kind, val, del);
      host.appendChild(row);
    });
  }

  // ------------------------------------------------------------- simulation
  let simToken = 0;

  async function refreshSim() {
    const token = ++simToken;
    state.range = [
      clamp(parseFloat($('#lamLo').value) || 380, 200, 2000),
      clamp(parseFloat($('#lamHi').value) || 780, 200, 2000),
    ];
    if (state.range[1] <= state.range[0] + 10) state.range[1] = state.range[0] + 10;
    $('#thetaOut').textContent = state.theta + '°';
    const payload = {
      stack: state.stack,
      range: state.range,
      points: state.points,
      theta: state.theta,
      pol: state.pol,
      bands: state.bands,
      angleMap: state.tab === 'sim',
      admittance: true,
    };
    try {
      const data = await api('/api/simulate', payload);
      if (token !== simToken) return;
      state.sim = data;
      $('#serverStatus').textContent = '本地服务 · 就绪';
      $('#serverStatus').classList.remove('offline');
      renderSim();
    } catch (err) {
      $('#serverStatus').textContent = '服务异常';
      $('#serverStatus').classList.add('offline');
      toast('仿真失败：' + err.message);
    }
  }

  const refreshSimDebounced = debounce(refreshSim, 160);

  function renderSim() {
    if (!state.sim) return;
    renderMetrics();
    drawSpectrum();
    drawAngleMap();
    drawGDD();
    drawAdmittance();
    drawStack();
  }

  // ------------------------------------------------------------- chart: spec
  function drawSpectrumOn(canvas, sim, bands, opts) {
    opts = opts || {};
    const { ctx, w, h } = setup(canvas);
    if (!sim || !sim.spectra) return null;
    const lam = sim.spectra.wavelengths;
    const log = !!opts.log;
    const xd = [lam[0], lam[lam.length - 1]];
    const yv = (v) => log
      ? clamp(Math.log10(Math.max(v, 1e-4)), -4, 0)
      : clamp(v, 0, 1) * 100;
    const yfmt = log
      ? (v) => {
          const p = Math.pow(10, v) * 100;
          if (p >= 10) return p.toFixed(0) + '%';
          if (p >= 1) return p.toFixed(1) + '%';
          if (p >= 0.1) return p.toFixed(2) + '%';
          return p.toFixed(3) + '%';
        }
      : (v) => v.toFixed(0) + '%';

    const plot = makePlot(ctx, w, h, {
      xd, yd: log ? [-4, 0] : [0, 100],
      pad: { l: 54, r: 16, t: 16, b: 30 },
      xfmt: (v) => v.toFixed(0), yfmt,
      yticks: log ? [-4, -3, -2, -1, 0] : undefined,
      xlabel: 'λ / nm',
    });

    (bands || []).forEach((band) => {
      const x0 = plot.sx(Math.max(band.lo, xd[0]));
      const x1 = plot.sx(Math.min(band.hi, xd[1]));
      if (x1 <= x0) return;
      ctx.save();
      ctx.fillStyle = cssVar('--accent-soft');
      ctx.fillRect(x0, plot.box.y, x1 - x0, plot.box.h);
      ctx.fillStyle = cssVar('--muted');
      ctx.font = '10px ui-monospace, monospace';
      ctx.textAlign = 'center';
      ctx.fillText(KIND_LABEL[band.kind] + ' ' + (band.value * 100).toFixed(1) + '%',
        (x0 + x1) / 2, plot.box.y + 9);
      ctx.restore();
    });

    const series = [
      ['R', cssVar('--r'), 2.1, null],
      ['T', cssVar('--t'), 1.7, null],
      ['A', cssVar('--a'), 1.4, [4, 3]],
    ];
    series.forEach(([key, color, width, dash]) => {
      const pts = lam.map((l, i) => [plot.sx(l), plot.sy(yv(sim.spectra[key][i]))]);
      drawLine(ctx, pts, color, width, dash);
    });

    if (state.hover && opts.interactive) {
      const x = plot.sx(state.hover.lam);
      ctx.save();
      ctx.strokeStyle = cssVar('--muted');
      ctx.globalAlpha = 0.45;
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, plot.box.y); ctx.lineTo(x, plot.box.y + plot.box.h); ctx.stroke();
      ctx.restore();
      ['R', 'T', 'A'].forEach((key, i) => {
        const v = state.hover[key];
        if (v === undefined) return;
        ctx.beginPath();
        ctx.fillStyle = [cssVar('--r'), cssVar('--t'), cssVar('--a')][i];
        ctx.arc(x, plot.sy(yv(v)), 3, 0, Math.PI * 2);
        ctx.fill();
      });
    }
    return plot;
  }

  function drawSpectrum() {
    const sim = state.sim;
    const plot = drawSpectrumOn($('#spectrumCanvas'), sim, state.bands,
      { interactive: true, log: state.log });
    if (plot) state._specGeom = plot;
  }

  // --------------------------------------------------------------- heat map
  const VIRIDIS = [
    [68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142],
    [31, 158, 137], [53, 183, 121], [109, 205, 89], [180, 222, 44], [253, 231, 37],
  ];

  function viridis(t) {
    t = clamp(t, 0, 1) * (VIRIDIS.length - 1);
    const i = Math.floor(t);
    const f = t - i;
    const a = VIRIDIS[i];
    const b = VIRIDIS[Math.min(VIRIDIS.length - 1, i + 1)];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
  }

  function drawAngleMap() {
    const { ctx, w, h } = setup($('#angleCanvas'));
    const map = state.sim && state.sim.angleMap;
    if (!map) {
      ctx.fillStyle = cssVar('--muted');
      ctx.textAlign = 'center';
      ctx.fillText('角度图谱不可用', w / 2, h / 2);
      return;
    }
    const rows = map.theta.length;
    const cols = map.lambda.length;
    const off = document.createElement('canvas');
    off.width = cols; off.height = rows;
    const octx = off.getContext('2d');
    const img = octx.createImageData(cols, rows);
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const v = map.R[r][c];
        const col = viridis(v);
        const k = 4 * ((rows - 1 - r) * cols + c);
        img.data[k] = col[0]; img.data[k + 1] = col[1]; img.data[k + 2] = col[2]; img.data[k + 3] = 255;
      }
    }
    octx.putImageData(img, 0, 0);

    const plot = makePlot(ctx, w, h, {
      xd: [map.lambda[0], map.lambda[map.lambda.length - 1]],
      yd: [0, map.theta[map.theta.length - 1]],
      pad: { l: 44, r: 54, t: 10, b: 30 },
      xfmt: (v) => v.toFixed(0), yfmt: (v) => v.toFixed(0) + '°',
      xlabel: 'λ / nm', ylabel: 'θ',
    });
    ctx.save();
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(off, plot.box.x, plot.box.y, plot.box.w, plot.box.h);
    ctx.restore();

    // colour bar
    const cbx = plot.box.x + plot.box.w + 12;
    const cbw = 9;
    const grad = ctx.createLinearGradient(0, plot.box.y + plot.box.h, 0, plot.box.y);
    for (let i = 0; i <= 10; i++) {
      const c = viridis(i / 10);
      grad.addColorStop(i / 10, 'rgb(' + c.map((v) => v.toFixed(0)).join(',') + ')');
    }
    ctx.fillStyle = grad;
    ctx.fillRect(cbx, plot.box.y, cbw, plot.box.h);
    ctx.strokeStyle = cssVar('--line');
    ctx.strokeRect(cbx + 0.5, plot.box.y + 0.5, cbw, plot.box.h);
    ctx.fillStyle = cssVar('--muted');
    ctx.textAlign = 'left';
    ctx.font = '10px ui-monospace, monospace';
    [0, 0.5, 1].forEach((t) => {
      const y = plot.box.y + plot.box.h * (1 - t);
      ctx.fillText((t * 100).toFixed(0) + '%', cbx + cbw + 4, y);
    });
    state._angleGeom = { plot, map };
  }

  // ------------------------------------------------------------------- GDD
  function drawGDD() {
    const { ctx, w, h } = setup($('#gddCanvas'));
    const g = state.sim && state.sim.gdd;
    if (!g || !g.lambda) {
      ctx.fillStyle = cssVar('--muted');
      ctx.textAlign = 'center';
      ctx.fillText('无数据', w / 2, h / 2);
      return;
    }
    let lo = Math.min.apply(null, g.gdd);
    let hi = Math.max.apply(null, g.gdd);
    const padY = Math.max(1, (hi - lo) * 0.12);
    lo -= padY; hi += padY;
    if (hi - lo < 2) { lo -= 1; hi += 1; }
    const plot = makePlot(ctx, w, h, {
      xd: [g.lambda[0], g.lambda[g.lambda.length - 1]],
      yd: [lo, hi], pad: { l: 56, r: 16, t: 16, b: 30 },
      xfmt: (v) => v.toFixed(0), yfmt: compact,
      xlabel: 'λ / nm',
    });
    if (lo < 0 && hi > 0) {
      ctx.save();
      ctx.strokeStyle = cssVar('--line');
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(plot.box.x, plot.sy(0));
      ctx.lineTo(plot.box.x + plot.box.w, plot.sy(0));
      ctx.stroke();
      ctx.restore();
    }
    const pts = g.lambda.map((l, i) => [plot.sx(l), plot.sy(g.gdd[i])]);
    const grad = ctx.createLinearGradient(0, plot.box.y, 0, plot.box.y + plot.box.h);
    grad.addColorStop(0, cssVar('--accent-2'));
    grad.addColorStop(1, cssVar('--a'));
    drawLine(ctx, pts, grad, 1.8);
    $('#gddHint').textContent = 'GD ' + fmt(g.gd0, 2) + ' fs · GDD₀ ' + fmt(g.gdd0, 1) + ' fs²';
  }

  // ------------------------------------------------------------- admittance
  // A conventional Cartesian admittance diagram becomes useless for a
  // quarter-wave stack, whose admittance spirals out by (nH/nL)^2 per pair.
  // Plotting r = log10(1+|Y|) with the phase kept linear keeps both a 1% AR
  // coating and a 99.99% mirror readable on the same axes.
  function compact(v) {
    const av = Math.abs(v);
    if (av >= 1000) return (v / 1000).toFixed(av >= 10000 ? 0 : 1) + 'k';
    if (av >= 10) return v.toFixed(0);
    if (av >= 1) return v.toFixed(1);
    return v.toFixed(2);
  }

  function drawAdmittance() {
    const { ctx, w, h } = setup($('#admitCanvas'));
    const a = state.sim && state.sim.admittance;
    if (!a) {
      ctx.fillStyle = cssVar('--muted');
      ctx.textAlign = 'center';
      ctx.fillText('无数据', w / 2, h / 2);
      return;
    }
    $('#admitHint').textContent = 'log|Y| @ ' + a.lambda.toFixed(0) + ' nm';
    const pts = a.re.map((v, i) => ({ re: v, im: a.im[i] }));
    const radius = (p) => Math.log10(1 + Math.hypot(p.re, p.im));
    const maxR = Math.max.apply(null, pts.map(radius).concat([Math.log10(1 + Math.abs(a.eta0[0]))]));
    const lim = Math.max(0.8, Math.ceil(maxR * 4) / 4);
    const cx = w / 2;
    const cy = h / 2 + 2;
    const rad = Math.min(w, h) / 2 - 24;
    const scale = rad / lim;
    const pos = (p) => {
      const r = radius(p) * scale;
      const ang = Math.atan2(p.im, p.re);
      return [cx + r * Math.cos(ang), cy - r * Math.sin(ang)];
    };

    ctx.save();
    // radial spokes
    ctx.strokeStyle = cssVar('--grid');
    ctx.fillStyle = cssVar('--muted');
    ctx.textAlign = 'center';
    for (let k = 0; k < 8; k++) {
      const ang = (k * Math.PI) / 4;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + rad * Math.cos(ang), cy - rad * Math.sin(ang));
      ctx.stroke();
    }
    // constant-|Y| circles
    ctx.setLineDash([2, 3]);
    [1, 3, 10, 30, 100, 300, 1000, 3000, 10000].forEach((v) => {
      const r = Math.log10(1 + v) * scale;
      if (r > rad) return;
      ctx.strokeStyle = v === 1 ? cssVar('--accent') : cssVar('--grid');
      ctx.globalAlpha = v === 1 ? 0.5 : 1;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
      const ang = -Math.PI * 0.75;
      ctx.fillStyle = cssVar('--muted');
      ctx.font = '9px ui-monospace, monospace';
      ctx.fillText(v >= 1000 ? (v / 1000) + 'k' : String(v),
        cx + r * Math.cos(ang) - 6, cy - r * Math.sin(ang));
    });
    ctx.setLineDash([]);
    ctx.fillStyle = cssVar('--muted');
    ctx.font = '9.5px ui-monospace, monospace';
    ctx.textAlign = 'left';
    ctx.fillText('Re Y', cx + rad - 8, cy + 12);
    ctx.textAlign = 'center';
    ctx.fillText('Im Y', cx, cy - rad - 2);
    ctx.restore();

    // locus
    const xy = pts.map(pos);
    ctx.save();
    ctx.strokeStyle = cssVar('--accent');
    ctx.globalAlpha = 0.6;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    xy.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
    ctx.stroke();
    ctx.restore();

    xy.forEach((p, i) => {
      const last = i === xy.length - 1;
      ctx.beginPath();
      ctx.fillStyle = i === 0 ? cssVar('--muted') : last ? cssVar('--r') : cssVar('--accent');
      ctx.arc(p[0], p[1], i === 0 || last ? 3.2 : 1.9, 0, Math.PI * 2);
      ctx.fill();
    });

    ctx.fillStyle = cssVar('--muted');
    ctx.font = '9.5px ui-monospace, monospace';
    ctx.textAlign = 'left';
    ctx.fillText('○ substrate   ● input', 6, h - 6);
  }

  // -------------------------------------------------------------- stack view
  function drawStack() {
    const { ctx, w, h } = setup($('#stackCanvas'));
    const layers = state.stack.layers;
    if (!layers.length) {
      ctx.fillStyle = cssVar('--muted');
      ctx.textAlign = 'center';
      ctx.fillText('尚未添加膜层', w / 2, h / 2);
      return;
    }
    const labelW = 96;
    const x0 = 10;
    const x1 = w - labelW;
    const top = 16;
    const bottom = h - 22;
    const usable = bottom - top;
    const total = layers.reduce((a, l) => a + Math.max(6, l.thickness), 0);
    const airH = 12;
    const subH = 16;
    let y = top + airH;

    ctx.font = '10px ui-monospace, monospace';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = cssVar('--muted');
    ctx.textAlign = 'left';
    ctx.fillText('air  n = ' + fmt(materialByKey(state.stack.incident) ? materialByKey(state.stack.incident).n550 : 1, 3), x0, top + 4);

    layers.forEach((layer, i) => {
      const hgt = Math.max(4, (Math.max(6, layer.thickness) / total) * (usable - airH - subH - 6));
      ctx.fillStyle = materialColor(layer.material);
      ctx.globalAlpha = 0.85;
      ctx.fillRect(x0, y, x1 - x0, hgt);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = cssVar('--panel');
      ctx.lineWidth = 0.8;
      ctx.strokeRect(x0, y, x1 - x0, hgt);

      if (hgt > 9) {
        ctx.fillStyle = cssVar('--ink-2');
        ctx.textAlign = 'left';
        ctx.font = '9.5px ui-monospace, monospace';
        ctx.fillText('L' + (i + 1) + ' ' + layer.material + ' ' + fmt(layer.thickness, 0), x1 + 5, y + hgt / 2);
      }
      y += hgt;
    });

    // substrate
    ctx.fillStyle = cssVar('--bg-2');
    ctx.fillRect(x0, y, x1 - x0, subH);
    ctx.strokeStyle = cssVar('--line');
    ctx.strokeRect(x0, y, x1 - x0, subH);
    ctx.fillStyle = cssVar('--muted');
    ctx.textAlign = 'left';
    ctx.fillText('substrate ' + state.stack.substrate, x1 + 6, y + subH / 2);


  }

  // ---------------------------------------------------------------- metrics
  function renderMetrics() {
    const sim = state.sim;
    const host = $('#metricsCard');
    if (!sim) { host.innerHTML = '<div class="empty">无数据</div>'; return; }
    const m = sim.metrics;
    const color = sim.color;
    const gd = sim.gdd || {};
    const total = sim.stack.totalThickness;
    const cells = [
      ['平均反射 R̄', pct(m.R.avg, 2), '400–700 nm'],
      ['最大反射 Rmax', pct(m.R.max, 2), '带内最差'],
      ['平均透射 T̄', pct(m.T.avg, 2), '400–700 nm'],
      ['总厚度', fmt(total, 1) + ' nm', sim.stack.layers.length + ' 层'],
    ];
    host.innerHTML = '<div class="panel-head"><h3>关键指标</h3><span class="hint">D65 · 2°</span></div>' +
      '<div class="metric-grid">' +
      cells.map(([k, v, s]) => '<div class="metric"><div class="k">' + k + '</div><div class="v">' + v +
        '</div><div class="k">' + s + '</div></div>').join('') +
      '</div>' +
      '<div class="color-line"><div class="color-chip" style="background:' + color.hex + '"></div>' +
      '<div><div class="k">反射色（D65）</div><div class="mono">' + color.hex.toUpperCase() +
      ' · L* ' + fmt(color.lab[0], 1) + '</div><div class="k">GD ' + fmt(gd.gd0, 2) + ' fs · 穿透 ' +
      fmt(gd.penetrationDepth, 0) + ' nm</div></div></div>';
  }

  // -------------------------------------------------------------- convergence
  function drawConvergence() {
    const { ctx, w, h } = setup($('#convergeCanvas'));
    const res = state.design.result;
    if (!res || !res.history || res.history.length < 2) {
      ctx.fillStyle = cssVar('--muted');
      ctx.textAlign = 'center';
      ctx.fillText('运行优化后显示收敛过程', w / 2, h / 2);
      return;
    }
    const hist = res.history.map((v) => Math.max(v, 1e-12));
    let lo = Math.log10(Math.min.apply(null, hist));
    let hi = Math.log10(Math.max.apply(null, hist));
    if (hi - lo < 1) { hi = lo + 1; }
    const plot = makePlot(ctx, w, h, {
      xd: [0, hist.length - 1], yd: [lo - 0.15, hi + 0.15],
      pad: { l: 56, r: 16, t: 14, b: 30 },
      xfmt: (v) => v.toFixed(0), yfmt: (v) => '1e' + v.toFixed(0),
      yCount: 4, xlabel: 'generation', ylabel: 'merit (log)',
    });
    const pts = hist.map((v, i) => [plot.sx(i), plot.sy(Math.log10(v))]);
    drawLine(ctx, pts, cssVar('--accent'), 1.8);
    ctx.beginPath();
    ctx.fillStyle = cssVar('--r');
    const last = pts[pts.length - 1];
    ctx.arc(last[0], last[1], 3.4, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = cssVar('--muted');
    ctx.font = '10px ui-monospace, monospace';
    ctx.textAlign = 'left';
    ctx.fillText('final ' + res.merit.toExponential(2) + ' · ' + res.evaluations + ' evals · ' + fmt(res.elapsed, 2) + ' s',
      plot.box.x + 6, plot.box.y + 10);
  }

  // ---------------------------------------------------------------- histogram
  function drawHistogram(report) {
    const { ctx, w, h } = setup($('#histCanvas'));
    const useMin = report.thresholdMin !== null && report.thresholdMin !== undefined && !!report.histogramMinR;
    const useMaxR = !useMin && !!report.histogramMaxR;
    const hist = useMin ? report.histogramMinR : (useMaxR ? report.histogramMaxR : report.histogram);
    if (!hist || !hist.counts.length) return;
    const isPct = useMin || useMaxR;
    const threshold = useMin ? report.thresholdMin : report.threshold;
    const stats = useMin ? report.minR : report.maxR;
    $('#histTitle').textContent = useMin ? '带内最小反射率分布' : '带内最大反射率分布';
    $('#histHint').textContent = isPct
      ? (useMin ? '停带最差点 Rmin' : '通带最差点 Rmax') + ' · ' + report.runs + ' 次'
      : '蒙特卡洛直方图';
    const maxCount = Math.max.apply(null, hist.counts);
    const plot = makePlot(ctx, w, h, {
      xd: [hist.edges[0], hist.edges[hist.edges.length - 1]],
      yd: [0, maxCount * 1.12],
      pad: { l: 42, r: 16, t: 16, b: 30 },
      xfmt: (v) => {
        if (!isPct) return v.toExponential(1);
        const span = hist.edges[hist.edges.length - 1] - hist.edges[0];
        const dec = span > 0.2 ? 0 : span > 0.02 ? 1 : span > 0.002 ? 2 : 3;
        return (v * 100).toFixed(dec) + '%';
      },
      yfmt: (v) => v.toFixed(0),
      yCount: 4,
    });
    const bw = plot.box.w / hist.counts.length;
    hist.counts.forEach((c, i) => {
      if (!c) return;
      const x = plot.box.x + i * bw;
      const y = plot.sy(c);
      const grad = ctx.createLinearGradient(0, y, 0, plot.box.y + plot.box.h);
      grad.addColorStop(0, cssVar('--accent'));
      grad.addColorStop(1, cssVar('--t'));
      ctx.fillStyle = grad;
      ctx.globalAlpha = 0.55;
      ctx.fillRect(x + 1, y, Math.max(1, bw - 2), plot.box.y + plot.box.h - y);
      ctx.globalAlpha = 1;
    });
    if (isPct && threshold !== null && threshold !== undefined) {
      const x = plot.sx(threshold);
      if (x > plot.box.x && x < plot.box.x + plot.box.w) {
        ctx.save();
        ctx.strokeStyle = cssVar('--r');
        ctx.setLineDash([4, 3]);
        ctx.beginPath(); ctx.moveTo(x, plot.box.y); ctx.lineTo(x, plot.box.y + plot.box.h); ctx.stroke();
        ctx.restore();
        ctx.fillStyle = cssVar('--r');
        ctx.textAlign = 'right';
        ctx.font = '10px ui-monospace, monospace';
        ctx.fillText('判据 ' + pct(threshold, 2), x - 5, plot.box.y + 10);
      }
    }
    const p95 = isPct ? (useMin ? stats.p05 : stats.p95) : report.merit.p95;
    if (p95 >= hist.edges[0] && p95 <= hist.edges[hist.edges.length - 1]) {
      const x = plot.sx(p95);
      ctx.save();
      ctx.strokeStyle = cssVar('--accent-2');
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, plot.box.y); ctx.lineTo(x, plot.box.y + plot.box.h); ctx.stroke();
      ctx.restore();
      ctx.fillStyle = cssVar('--accent-2');
      ctx.textAlign = x > plot.box.x + plot.box.w - 30 ? 'right' : 'left';
      ctx.font = '10px ui-monospace, monospace';
      ctx.fillText('P95', x + (x > plot.box.x + plot.box.w - 30 ? -4 : 4), plot.box.y + 10);
    }
  }

  // ---------------------------------------------------------------- dispersion
  function drawDispersion() {
    const { ctx, w, h } = setup($('#dispersionCanvas'));
    const mat = materialByKey(state.selectedMaterial) || state.materials[0];
    if (!mat) return;
    $('#matHint').textContent = mat.key + ' · n(λ), k(λ)';
    const samples = mat.samples;
    const ns = mat.n;
    const ks = mat.k;
    const nLo = Math.min.apply(null, ns);
    const nHi = Math.max.apply(null, ns);
    const kMax = Math.max.apply(null, ks.concat([1e-4]));
    const useK = kMax > 1e-3;

    let lo = nLo - Math.max(0.02, (nHi - nLo) * 0.15);
    let hi = nHi + Math.max(0.02, (nHi - nLo) * 0.15);
    const plot = makePlot(ctx, w, h, {
      xd: [samples[0], samples[samples.length - 1]],
      yd: [lo, hi],
      pad: { l: 52, r: useK ? 54 : 16, t: 16, b: 30 },
      xfmt: (v) => v.toFixed(0), yfmt: (v) => v.toFixed(3),
      xlabel: 'λ / nm', ylabel: 'n',
    });
    const pts = samples.map((l, i) => [plot.sx(l), plot.sy(ns[i])]);
    drawLine(ctx, pts, cssVar('--accent'), 2.2);
    ctx.save();
    ctx.globalAlpha = 0.10;
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
    ctx.lineTo(pts[pts.length - 1][0], plot.box.y + plot.box.h);
    ctx.lineTo(pts[0][0], plot.box.y + plot.box.h);
    ctx.closePath();
    ctx.fillStyle = cssVar('--accent');
    ctx.fill();
    ctx.restore();

    if (useK) {
      const kPts = samples.map((l, i) => [plot.sx(l), plot.sy(lo + (ks[i] / kMax) * (hi - lo) * 0.9)]);
      drawLine(ctx, kPts, cssVar('--r'), 1.5, [4, 3]);
      ctx.save();
      ctx.translate(w - 12, plot.box.y + 2);
      ctx.fillStyle = cssVar('--r');
      ctx.textAlign = 'right';
      ctx.font = '10px ui-monospace, monospace';
      ctx.fillText('k → ' + fmt(kMax, 2), 0, 0);
      ctx.restore();
    }
    ctx.fillStyle = cssVar('--muted');
    ctx.textAlign = 'left';
    ctx.font = '11px ui-monospace, monospace';
    ctx.fillText(mat.label + '   n(550) = ' + fmt(mat.n550, 4) + '   k(550) = ' + fmt(mat.k550, 4),
      plot.box.x + 2, plot.box.y - 4);
    $('#matDetail').classList.remove('empty');
    $('#matDetail').innerHTML =
      '<table><tbody>' +
      '<tr><td>键名</td><td class="num">' + mat.key + '</td></tr>' +
      '<tr><td>类别</td><td class="num">' + mat.category + '</td></tr>' +
      '<tr><td>色散模型</td><td class="num">' + mat.model + '</td></tr>' +
      '<tr><td>n @ 550 nm</td><td class="num">' + fmt(mat.n550, 4) + '</td></tr>' +
      '<tr><td>k @ 550 nm</td><td class="num">' + fmt(mat.k550, 4) + '</td></tr>' +
      '<tr><td>有效范围</td><td class="num">' + mat.range[0] + '–' + mat.range[1] + ' nm</td></tr>' +
      '</tbody></table><p class="muted" style="margin:8px 0 0">' + (mat.note || '') + '</p>';
  }

  function renderMaterialList() {
    const host = $('#matList');
    const query = ($('#matSearch').value || '').trim().toLowerCase();
    host.innerHTML = '';
    state.materials
      .filter((m) => !query || m.key.toLowerCase().includes(query) || m.label.toLowerCase().includes(query) ||
        String(m.n550).includes(query))
      .forEach((m) => {
        const item = document.createElement('div');
        item.className = 'mat-item' + (m.key === state.selectedMaterial ? ' active' : '');
        item.innerHTML = '<span class="swatch" style="background:' + materialColor(m.key) + '"></span>' +
          '<span><b>' + m.key + '</b><br><span class="cat">' + m.label + '</span></span>' +
          '<span class="n">' + fmt(m.n550, 3) + '</span>';
        item.addEventListener('click', () => {
          state.selectedMaterial = m.key;
          renderMaterialList();
          drawDispersion();
        });
        host.appendChild(item);
      });
    if (!host.children.length) host.innerHTML = '<div class="empty">没有匹配的材料</div>';
  }

  // ------------------------------------------------------------------ design
  function buildDesignStack() {
    const hi = $('#designHigh').value;
    const lo = $('#designLow').value;
    const count = clamp(parseInt($('#designLayers').value, 10) || 6, 2, 40);
    const start = $('#designStart').value;
    const layers = [];
    for (let i = 0; i < count; i++) {
      const isH = (i % 2 === 0) === (start === 'H');
      layers.push({ material: isH ? hi : lo, thickness: 60 });
    }
    return { incident: 'Air', substrate: $('#substrateSelect').value || 'BK7', layers };
  }

  async function runDesign() {
    const stack = buildDesignStack();
    const body = {
      stack,
      bands: state.design.bands,
      theta: parseFloat($('#designTheta').value) || 0,
      pol: $('#designPol').value,
      popsize: parseInt($('#designPop').value, 10) || 26,
      maxiter: parseInt($('#designIter').value, 10) || 160,
      timeBudget: 40,
      seed: 1,
      bounds: stack.layers.map(() => [
        parseFloat($('#designMin').value) || 4,
        parseFloat($('#designMax').value) || 400,
      ]),
    };
    $('#runDesign').disabled = true;
    $('#designStatus').textContent = '提交任务…';
    $('#designProgress').style.width = '2%';
    try {
      const { jobId } = await api('/api/design', body);
      pollDesign(jobId);
    } catch (err) {
      $('#runDesign').disabled = false;
      $('#designStatus').textContent = '失败：' + err.message;
      toast('优化任务提交失败：' + err.message);
    }
  }

  function pollDesign(jobId) {
    clearTimeout(state.design.poll);
    const tick = async () => {
      try {
        const job = await api('/api/design/' + jobId);
        const pctDone = Math.round((job.progress || 0) * 100);
        $('#designProgress').style.width = Math.max(2, pctDone) + '%';
        $('#designStatus').textContent = job.state === 'running'
          ? 'gen ' + (job.gen || 0) + '/' + (job.maxiter || '?') +
            ' · merit ' + (job.best !== null ? job.best.toExponential(2) : '—') +
            ' · ' + fmt(job.elapsed || 0, 1) + ' s'
          : job.state === 'done' ? '完成 · ' + fmt(job.result.elapsed, 1) + ' s' : job.state;
        if (job.state === 'running' || job.state === 'queued') {
          state.design.poll = setTimeout(tick, 450);
          return;
        }
        $('#runDesign').disabled = false;
        if (job.state === 'error') {
          $('#designStatus').textContent = '错误：' + job.error;
          toast('优化失败：' + job.error);
          return;
        }
        state.design.result = job.result;
        renderDesignResult();
        toast('优化完成：merit ' + job.result.merit.toExponential(2));
      } catch (err) {
        $('#runDesign').disabled = false;
        $('#designStatus').textContent = '轮询失败';
        toast(err.message);
      }
    };
    tick();
  }

  function renderDesignResult() {
    const res = state.design.result;
    if (!res) return;
    // An antireflection result lives between 0.1% and 1%: unreadable on a
    // linear axis, so switch to the log scale automatically.
    if (res.metrics && res.metrics.R.max < 0.05 && !state.log) {
      state.log = true;
      $('#scaleBtn').textContent = '对数';
    }
    drawConvergence();
    drawSpectrumOn($('#designSpectrum'), res, state.design.bands, { log: state.log });
    $('#designMetrics').textContent = 'R̄ ' + pct(res.metrics.R.avg, 2) + ' · Rmax ' + pct(res.metrics.R.max, 2);
    const rows = res.materials.map((m, i) => {
      const opt = res.thicknesses[i];
      return '<tr><td class="num">' + (i + 1) + '</td><td>' + m + '</td>' +
        '<td class="num">' + fmt(opt, 1) + '</td></tr>';
    }).join('');
    $('#designLayersTable').innerHTML =
      '<table><thead><tr><th class="num">#</th><th>材料</th><th class="num">厚度 / nm</th></tr></thead><tbody>' +
      rows + '</tbody></table>' +
      '<p class="muted" style="margin:8px 0 0">总计 ' + fmt(res.thicknesses.reduce((a, b) => a + b, 0), 1) +
      ' nm · ' + res.evaluations + ' 次评价 · ' + res.method + '</p>';
    $('#designStatus').textContent = '完成 · merit ' + res.merit.toExponential(2);
    const bd = res.breakdown || [];
    $('#designBreakdown').innerHTML = bd.length
      ? '<table><thead><tr><th>判据</th><th class="num">平均 R</th><th class="num">最差 R</th>' +
        '<th class="num">罚函数</th></tr></thead><tbody>' +
        bd.map((b) => '<tr><td>' + b.label + '</td><td class="num">' + pct(b.avg, 3) +
          '</td><td class="num">' + pct(b.max, 3) + '</td><td class="num">' +
          b.penalty.toExponential(2) + '</td></tr>').join('') + '</tbody></table>'
      : '<div class="empty">无判据</div>';
  }

  function applyDesign() {
    const res = state.design.result;
    if (!res || !res.stack) { toast('还没有可应用的结果'); return; }
    state.stack = {
      incident: res.stack.incident,
      substrate: res.stack.substrate,
      layers: res.stack.layers.map((l) => ({ material: l.material, thickness: l.thickness })),
    };
    $('#substrateSelect').value = res.stack.substrate;
    $('#incidentSelect').value = res.stack.incident;
    renderLayerList();
    switchTab('sim');
    refreshSim();
    toast('已应用到当前膜系');
  }

  // --------------------------------------------------------------- tolerance
  // A broadband AR wants "R <= 2%", a mirror wants "R >= 95%".  Pick a sane
  // default from the current spectrum instead of showing a 0% yield.
  function maybeSyncTolBand() {
    if (state.tol.touched || !state.sim || !state.tol.bands.length) return;
    const avg = state.sim.metrics.R.avg;
    const band = state.tol.bands[0];
    if (avg > 0.5 && band.kind === 'max') {
      band.kind = 'min';
      band.value = 0.95;
    } else if (avg <= 0.5 && band.kind === 'min') {
      band.kind = 'max';
      band.value = 0.02;
    }
    renderBandEditor($('#tolBandList'), state.tol.bands, () => { state.tol.touched = true; });
  }

  async function runTolerance() {
    maybeSyncTolBand();
    $('#tolStatus').textContent = '计算中…';
    $('#runTolerance').disabled = true;
    try {
      const report = await api('/api/tolerance', {
        stack: state.stack,
        bands: state.tol.bands,
        runs: clamp(parseInt($('#tolRuns').value, 10) || 300, 20, 2000),
        thicknessSigma: parseFloat($('#tolSigma').value) || 0,
        thicknessMode: 'percent',
        indexSigma: parseFloat($('#tolIndex').value) || 0,
        angleSigma: parseFloat($('#tolAngle').value) || 0,
        theta: state.theta,
        pol: state.pol,
        seed: parseInt($('#tolSeed').value, 10) || 7,
      });
      state.tol.result = report;
      renderTolerance();
      $('#tolStatus').textContent = '完成 · ' + report.runs + ' 次抽样 · ' + fmt(report.elapsed, 2) + ' s';
    } catch (err) {
      $('#tolStatus').textContent = '失败：' + err.message;
      toast(err.message);
    } finally {
      $('#runTolerance').disabled = false;
    }
  }

  function drawEnvelope() {
    const { ctx, w, h } = setup($('#envelopeCanvas'));
    const env = state.tol.result && state.tol.result.envelope;
    if (!env) {
      ctx.fillStyle = cssVar('--muted');
      ctx.textAlign = 'center';
      ctx.fillText('运行蒙特卡洛后显示反射率包络', w / 2, h / 2);
      return;
    }
    const lam = env.lambda;
    const top = Math.max(0.02, Math.min(1, Math.max.apply(null, env.max.concat(env.nominal)) * 1.15));
    const plot = makePlot(ctx, w, h, {
      xd: [lam[0], lam[lam.length - 1]], yd: [0, top * 100],
      pad: { l: 50, r: 16, t: 16, b: 30 },
      xfmt: (v) => v.toFixed(0), yfmt: (v) => v.toFixed(0) + '%',
      xlabel: 'λ / nm',
    });

    ctx.save();
    ctx.beginPath();
    lam.forEach((l, i) => {
      const x = plot.sx(l);
      const y = plot.sy(clamp(env.max[i], 0, top) * 100);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    for (let i = lam.length - 1; i >= 0; i--) {
      ctx.lineTo(plot.sx(lam[i]), plot.sy(clamp(env.min[i], 0, top) * 100));
    }
    ctx.closePath();
    ctx.fillStyle = cssVar('--accent');
    ctx.globalAlpha = 0.16;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.restore();

    drawLine(ctx, lam.map((l, i) => [plot.sx(l), plot.sy(clamp(env.max[i], 0, top) * 100)]), cssVar('--r'), 1.1, [5, 3]);
    drawLine(ctx, lam.map((l, i) => [plot.sx(l), plot.sy(clamp(env.min[i], 0, top) * 100)]), cssVar('--r'), 1.1, [5, 3]);
    drawLine(ctx, lam.map((l, i) => [plot.sx(l), plot.sy(clamp(env.nominal[i], 0, top) * 100)]), cssVar('--ink-2'), 1.8);

    ctx.fillStyle = cssVar('--muted');
    ctx.font = '10px ui-monospace, monospace';
    ctx.textAlign = 'left';
    ctx.fillText('— nominal    ⋯ min / max', plot.box.x + 4, plot.box.y + 10);
  }

  function renderTolerance() {
    const r = state.tol.result;
    if (!r) return;
    const cls = r.yield >= 95 ? 'good' : r.yield >= 80 ? 'ok' : 'bad';
    $('#yieldCard').innerHTML =
      '<div class="panel-head"><h3>工艺良率</h3><span class="hint">' + r.runs + ' 次抽样</span></div>' +
      '<div class="yield-hero"><div class="yield-num ' + cls + '">' + fmt(r.yield, 1) + '%</div>' +
      '<div class="yield-meta"><span>失败 ' + r.failures + ' 次</span>' +
      '<span>merit P50 ' + r.merit.p50.toExponential(2) + '</span>' +
      (r.thresholdMin !== null && r.thresholdMin !== undefined
        ? '<span>Rmin 均值 ' + pct(r.minR.mean, 2) + '</span><span>Rmin P05 ' + pct(r.minR.p05, 2) + '</span>'
        : '<span>Rmax 均值 ' + pct(r.maxR.mean, 2) + '</span><span>Rmax P95 ' + pct(r.maxR.p95, 2) + '</span>') +
      '<span>膜厚 σ ' + fmt(r.config.thicknessSigma, 2) + '%</span></div></div>';
    drawHistogram(r);
    drawEnvelope();

    const maxSens = Math.max.apply(null, r.sensitivity.map((s) => Math.abs(s.deltaR)).concat([1e-12]));
    $('#sensitivityTable').innerHTML =
      '<table><thead><tr><th>层 / 材料</th><th>相对灵敏度</th><th class="num">ΔR̄</th></tr></thead><tbody>' +
      r.sensitivity.slice(0, 10).map((row) =>
        '<tr><td>L' + row.layer + ' · ' + row.material + '<br><span class="muted mono">' +
        fmt(row.thickness, 1) + ' nm</span></td>' +
        '<td><div class="bar-cell"><i style="width:' +
        Math.max(2, (Math.abs(row.deltaR) / maxSens) * 100) + '%"></i></div></td>' +
        '<td class="num">' + pct(row.deltaR, 2) + '</td></tr>').join('') +
      '</tbody></table>';

    $('#bandPassTable').innerHTML =
      '<table><thead><tr><th>判据</th><th class="num">通过率</th></tr></thead><tbody>' +
      r.bands.map((b) => '<tr><td>' + b.label + '</td><td class="num">' + fmt(b.passRate, 1) + '%</td></tr>').join('') +
      '</tbody></table>' +
      '<p class="muted" style="margin:8px 0 0">最差样本 merit ' + r.merit.max.toExponential(2) +
      '，反射范围 ' + pct(r.range.minR, 2) + ' – ' + pct(r.range.maxR, 2) + '</p>';
  }

  // ------------------------------------------------------------------ export
  function exportJson() {
    let payload;
    let name;
    if (state.tab === 'design' && state.design.result) {
      payload = state.design.result;
      name = 'thinfilm-design.json';
    } else if (state.tab === 'tol' && state.tol.result) {
      payload = state.tol.result;
      name = 'thinfilm-tolerance.json';
    } else {
      payload = state.sim;
      name = 'thinfilm-simulation.json';
    }
    if (!payload) { toast('暂无内容可导出'); return; }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast('已导出 ' + name);
  }

  // ------------------------------------------------------------------- wiring
  function bind() {
    $$('.tab').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));

    $('#presetSelect').addEventListener('change', (e) => {
      if (e.target.value === 'custom') return;
      const preset = state.presets.find((p) => p.id === e.target.value);
      if (!preset) return;
      const st = preset.stack;
      state.stack = {
        incident: st.incident, substrate: st.substrate,
        layers: st.layers.map((l) => ({ material: l.material, thickness: l.thickness })),
      };
      $('#incidentSelect').value = st.incident;
      $('#substrateSelect').value = st.substrate;
      state.selectedMaterial = st.layers[0] ? st.layers[0].material : state.selectedMaterial;
      renderLayerList();
      refreshSim();
      toast(preset.name + ' · ' + preset.description);
    });

    $('#incidentSelect').addEventListener('change', (e) => {
      state.stack.incident = e.target.value;
      refreshSim();
    });
    $('#substrateSelect').addEventListener('change', (e) => {
      state.stack.substrate = e.target.value;
      refreshSim();
    });

    $('#addLayer').addEventListener('click', () => {
      state.stack.layers.push({ material: alternateMaterial(), thickness: 60 });
      markCustom();
      renderLayerList();
      refreshSim();
    });

    $('#thetaRange').addEventListener('input', (e) => {
      state.theta = parseInt(e.target.value, 10);
      $('#thetaOut').textContent = state.theta + '°';
      refreshSimDebounced();
    });

    $$('#polGroup button').forEach((b) => b.addEventListener('click', () => {
      $$('#polGroup button').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      state.pol = b.dataset.pol;
      refreshSim();
    }));

    ['#lamLo', '#lamHi'].forEach((sel) => {
      $(sel).addEventListener('change', refreshSim);
    });

    $('#addSimBand').addEventListener('click', () => {
      state.bands.push({ lo: 500, hi: 600, kind: 'max', value: 0.01, weight: 1 });
      renderBandEditor($('#simBandList'), state.bands, refreshSim);
      refreshSim();
    });

    $('#addDesignBand').addEventListener('click', () => {
      state.design.bands.push({ lo: 500, hi: 600, kind: 'min', value: 0.95, weight: 1 });
      renderBandEditor($('#designBandList'), state.design.bands, () => {});
    });

    $('#addTolBand').addEventListener('click', () => {
      state.tol.bands.push({ lo: 500, hi: 600, kind: 'max', value: 0.02, weight: 1 });
      renderBandEditor($('#tolBandList'), state.tol.bands, () => { state.tol.touched = true; });
    });

    $('#seedFromCurrent').addEventListener('click', () => {
      const first = state.stack.layers[0];
      if (first) {
        $('#designHigh').value = first.material;
        const second = state.stack.layers.find((l) => l.material !== first.material);
        if (second) $('#designLow').value = second.material;
      }
      toast('已把当前材料填入设计参数');
    });

    $('#runDesign').addEventListener('click', runDesign);
    $('#applyDesign').addEventListener('click', applyDesign);
    $('#runTolerance').addEventListener('click', runTolerance);
    $('#scaleBtn').addEventListener('click', () => {
      state.log = !state.log;
      $('#scaleBtn').textContent = state.log ? '对数' : '线性';
      renderSim();
      if (state.design.result) {
        drawSpectrumOn($('#designSpectrum'), state.design.result, state.design.bands, { log: state.log });
      }
    });

    $('#exportBtn').addEventListener('click', exportJson);
    $('#matSearch').addEventListener('input', debounce(renderMaterialList, 120));

    $('#themeBtn').addEventListener('click', () => {
      const root = document.documentElement;
      const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
      root.dataset.theme = next;
      localStorage.setItem('tff-theme', next);
      setTimeout(() => { renderSim(); drawConvergence(); drawDispersion(); if (state.tol.result) drawHistogram(state.tol.result); }, 30);
    });

    const spec = $('#spectrumCanvas');
    spec.addEventListener('mousemove', (e) => {
      const g = state._specGeom;
      if (!g || !state.sim) return;
      const rect = spec.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      if (x < g.box.x || x > g.box.x + g.box.w || y < g.box.y || y > g.box.y + g.box.h) {
        if (state.hover) { state.hover = null; drawSpectrum(); }
        hideTooltip();
        return;
      }
      const lam = g.invX(x);
      const lams = state.sim.spectra.wavelengths;
      let idx = 0;
      let best = Infinity;
      for (let i = 0; i < lams.length; i++) {
        const d = Math.abs(lams[i] - lam);
        if (d < best) { best = d; idx = i; }
      }
      state.hover = { lam: lams[idx], R: state.sim.spectra.R[idx], T: state.sim.spectra.T[idx], A: state.sim.spectra.A[idx] };
      drawSpectrum();
      showTooltip(e.clientX, e.clientY,
        'λ ' + fmt(state.hover.lam, 1) + ' nm<br>R ' + pct(state.hover.R, 2) +
        '<br>T ' + pct(state.hover.T, 2) + '<br>A ' + pct(state.hover.A, 3));
    });
    spec.addEventListener('mouseleave', () => {
      state.hover = null;
      hideTooltip();
      drawSpectrum();
    });

    window.addEventListener('resize', debounce(() => {
      if (state.tab === 'sim') renderSim();
      if (state.tab === 'design') { drawConvergence(); if (state.design.result) drawSpectrumOn($('#designSpectrum'), state.design.result, state.design.bands, { log: state.log }); }
      if (state.tab === 'tol' && state.tol.result) drawHistogram(state.tol.result);
      if (state.tab === 'mat') drawDispersion();
    }, 160));
  }

  function showTooltip(x, y, html) {
    const tip = $('#tooltip');
    tip.innerHTML = html;
    tip.classList.add('on');
    const rect = tip.getBoundingClientRect();
    tip.style.left = Math.min(window.innerWidth - rect.width - 12, x + 14) + 'px';
    tip.style.top = Math.max(8, y - rect.height - 12) + 'px';
  }

  function hideTooltip() { $('#tooltip').classList.remove('on'); }

  // -------------------------------------------------------------------- boot
  async function boot() {
    const saved = localStorage.getItem('tff-theme');
    if (saved) document.documentElement.dataset.theme = saved;
    bind();
    renderBandEditor($('#simBandList'), state.bands, refreshSim);
    renderBandEditor($('#designBandList'), state.design.bands, () => {});
    renderBandEditor($('#tolBandList'), state.tol.bands, () => { state.tol.touched = true; });
    if (location.hash) switchTab(location.hash.slice(1));

    try {
      await loadCatalog();
      const health = await api('/api/health');
      $('#serverStatus').textContent = '本地服务 · v' + health.version;
      const preset = state.presets.find((p) => p.id === 'hr_mirror') || state.presets[0];
      if (preset) {
        $('#presetSelect').value = preset.id;
        const st = preset.stack;
        state.stack = {
          incident: st.incident, substrate: st.substrate,
          layers: st.layers.map((l) => ({ material: l.material, thickness: l.thickness })),
        };
        $('#incidentSelect').value = st.incident;
        $('#substrateSelect').value = st.substrate;
        state.selectedMaterial = st.layers[0].material;
      }
      renderLayerList();
      renderMaterialList();
      drawDispersion();
      await refreshSim();
    } catch (err) {
      $('#serverStatus').textContent = '无法连接本地服务';
      $('#serverStatus').classList.add('offline');
      toast('请通过 python run.py serve 启动服务后访问');
      console.error(err);
    }
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
