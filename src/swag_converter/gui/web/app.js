'use strict';

/* The page holds no state the bridge does not also hold, so a poll is always
   the truth and a reload never loses anything. */

const $ = (id) => document.getElementById(id);

/* ----------------------------------------------------------- bridge calls */

// Every call to Python goes through here. A bridge method that throws used to
// reject a promise nobody was listening to, which is how "Add images" came to
// do nothing at all: the failure was real and completely invisible.
async function call(method, ...args) {
  try {
    return await window.pywebview.api[method](...args);
  } catch (err) {
    const detail = (err && (err.message || err.reason)) || String(err);
    say(`${method} failed — ${detail}`, 'bad');
    return null;
  }
}

let sayTimer = null;
function say(message, tone) {
  const box = $('notice');
  box.textContent = message;
  box.className = 'notice' + (tone ? ' ' + tone : '');
  box.hidden = false;
  clearTimeout(sayTimer);
  sayTimer = setTimeout(() => { box.hidden = true; }, tone === 'bad' ? 9000 : 4500);
}

function report(outcome) {
  // {added, skipped} from the bridge; neither may be dropped in silence.
  if (!outcome) return [];
  const skipped = outcome.skipped || [];
  if (skipped.length === 1) {
    say(`${skipped[0].name} ${skipped[0].reason}.`, 'bad');
  } else if (skipped.length > 1) {
    say(`${skipped.length} files skipped — ${skipped[0].name} ${skipped[0].reason}, and others.`, 'bad');
  }
  return outcome.added || [];
}

/* ------------------------------------------------------------------ state */

const PRESETS = {
  auto: ['Work it out', 'Measures the image and chooses.'],
  icon: ['Icon', 'Logos, flat artwork. Crisp edges.'],
  illustration: ['Illustration', 'Shaded drawings. Keeps gradients.'],
  photo: ['Photograph', 'A deliberate stylisation.'],
  poster: ['Poster', 'A few big flat shapes.'],
};

const QUALITY = {
  fast: ['Quick', 'Few shapes. Smallest file and the shortest wait.'],
  balanced: ['Balanced', 'As many shapes as the picture is worth. The default.'],
  max: ['Maximum', 'Most shapes. Slowest and largest; flat artwork barely changes.'],
};

const EDGES = [['640', 'Small'], ['1024', 'Normal'], ['1600', 'Large'], ['2400', 'Huge'], ['0', 'Full']];

// The one sentence that separates the two settings people confuse.
const EDGE_HINT =
  'The size the picture is scaled to before tracing starts. A larger one lets '
  + 'the tracer notice finer things; it does not on its own let it draw more — '
  + 'that is Detail above.';

const BACKGROUNDS = {
  auto: ['If flat', 'Cleared only when the image really has a plain backdrop.'],
  always: ['Always', 'Always try to cut the backdrop away.'],
  keep: ['Never', 'Trace the background along with everything else.'],
};

const state = {
  info: null,
  jobs: [],
  busy: false,
  outdated: 0,
  again: false,
  thumbs: new Map(),
  timer: null,
  view: null,          // {id, name, svg, source, result}
  order: [],           // ids of finished jobs, for ‹ ›
  zoom: 1,
  pan: { x: 0, y: 0 },
  split: 50,
  mode: 'wipe',
  natural: { w: 0, h: 0 },
};

/* ------------------------------------------------------------------- boot */

async function boot() {
  state.info = await call('describe');
  if (!state.info) {
    say('The converter did not start. Reopen the app.', 'bad');
    return;
  }
  const s = state.info.settings;

  buildCards($('presets'), state.info.presets, PRESETS, s.preset, (v) => {
    pushSettings({ preset: v });
  });
  buildSeg($('quality'), state.info.qualities.map((q) => [q, QUALITY[q][0]]), s.quality, (v) => {
    pushSettings({ quality: v });
    $('quality-hint').textContent = QUALITY[v][1];
  });
  buildSeg($('edge'), EDGES, String(s.max_edge), (v) => pushSettings({ max_edge: v }));
  $('edge-hint').textContent = EDGE_HINT;
  buildSeg($('background'), Object.entries(BACKGROUNDS).map(([k, v]) => [k, v[0]]), s.background, (v) => {
    pushSettings({ background: v });
    $('background-hint').textContent = BACKGROUNDS[v][1];
  });

  $('quality-hint').textContent = QUALITY[s.quality][1];
  $('background-hint').textContent = BACKGROUNDS[s.background][1];

  $('measure').checked = s.measure && state.info.scoring;
  $('measure').disabled = !state.info.scoring;
  $('measure-hint').textContent = state.info.scoring
    ? 'Renders the SVG back and compares it with the original. A little slower.'
    : 'Unavailable in this build — it needs a renderer that is not installed.';

  $('version').textContent = 'version ' + state.info.version;
  $('formats').textContent = state.info.suffixes.map((s) => s.slice(1).toUpperCase()).join(' · ');

  await refresh();
}

function buildCards(host, values, labels, chosen, onPick) {
  host.innerHTML = '';
  values.forEach((value, index) => {
    const [title, note] = labels[value] || [value, ''];
    const b = document.createElement('button');
    b.className = 'pcard' + (index === 0 ? ' wide' : '');
    b.innerHTML = `<b></b><span></span>`;
    b.querySelector('b').textContent = title;
    b.querySelector('span').textContent = note;
    b.setAttribute('aria-pressed', value === chosen);
    b.onclick = () => {
      host.querySelectorAll('.pcard').forEach((o) => o.setAttribute('aria-pressed', 'false'));
      b.setAttribute('aria-pressed', 'true');
      onPick(value);
    };
    host.appendChild(b);
  });
}

function buildSeg(host, pairs, chosen, onPick) {
  host.innerHTML = '';
  for (const [value, label] of pairs) {
    const b = document.createElement('button');
    b.textContent = label;
    b.setAttribute('aria-pressed', String(value) === String(chosen));
    b.onclick = () => {
      host.querySelectorAll('button').forEach((o) => o.setAttribute('aria-pressed', 'false'));
      b.setAttribute('aria-pressed', 'true');
      onPick(value);
    };
    host.appendChild(b);
  }
}

async function pushSettings(patch) {
  await call('update_settings', patch);
  // Changing a setting makes finished results out of date; the button and
  // the cards say so straight away rather than at the next poll.
  await refresh();
}

/* ------------------------------------------------------------------- grid */

function render() {
  const jobs = state.jobs;
  $('empty').hidden = jobs.length > 0;
  $('grid').hidden = jobs.length === 0;

  const grid = $('grid');
  grid.innerHTML = '';
  for (const job of jobs) grid.appendChild(card(job));

  const done = jobs.filter((j) => j.status === 'done');
  const failed = jobs.filter((j) => j.status === 'failed').length;
  const stale = done.filter((j) => j.outdated).length;
  state.order = done.map((j) => j.id);

  $('tally').innerHTML = jobs.length
    ? `<b>${jobs.length}</b> image${jobs.length > 1 ? 's' : ''}` +
      (done.length ? ` · <b>${done.length}</b> converted` : '') +
      (failed ? ` · <b>${failed}</b> failed` : '') +
      (stale && !state.busy ? ` · settings changed since` : '')
    : '';

  const finished = done.length + failed;
  $('batch').hidden = !state.busy;
  $('batch-fill').style.width = jobs.length ? (100 * finished / jobs.length) + '%' : '0';

  // Nothing out of date means every result already matches these settings.
  // The button then offers the only thing left worth doing: do it again.
  state.again = jobs.length > 0 && state.outdated === 0;
  $('convert').disabled = state.busy || jobs.length === 0;
  $('convert').textContent = state.busy
    ? 'Converting…'
    : state.again ? 'Convert again' : 'Convert';
  $('cancel').hidden = !state.busy;
  $('clear').hidden = !jobs.length || state.busy;
}

function card(job) {
  const li = document.createElement('li');
  li.className = 'card' + (job.status === 'done' ? ' ready' : '') + (job.status === 'failed' ? ' failed' : '');

  const shot = document.createElement('div');
  shot.className = 'shot';
  const img = document.createElement('img');
  img.alt = '';
  if (state.thumbs.has(job.id)) img.src = state.thumbs.get(job.id);
  shot.appendChild(img);

  if (job.status === 'running') {
    const scrim = document.createElement('div');
    scrim.className = 'scrim';
    scrim.appendChild(ring(job.progress));
    const label = document.createElement('div');
    label.className = 'stage-name';
    label.textContent = job.stage || 'starting';
    scrim.appendChild(label);
    shot.appendChild(scrim);
  }

  if (job.status !== 'running') {
    const [label, tone] = badge(job);
    const pill = document.createElement('div');
    pill.className = 'pill ' + tone;
    pill.textContent = label;
    shot.appendChild(pill);
  }

  const meta = document.createElement('div');
  meta.className = 'meta';
  const name = document.createElement('div');
  name.className = 'name';
  name.textContent = job.name;
  name.title = job.name;
  const sub = document.createElement('div');
  sub.className = 'sub'
    + (job.status === 'failed' ? ' bad' : (job.status === 'done' && !job.outdated) ? ' good' : '');
  sub.textContent = describe(job);
  meta.append(name, sub);

  if (job.status !== 'running') {
    const kill = document.createElement('button');
    kill.className = 'kill';
    kill.textContent = '×';
    kill.title = 'Remove';
    kill.onclick = async (e) => {
      e.stopPropagation();
      state.thumbs.delete(job.id);
      apply(await call('remove', job.id));
    };
    li.appendChild(kill);
  }

  if (job.status === 'done') li.onclick = () => openViewer(job.id);
  li.append(shot, meta);
  return li;
}

function ring(fraction) {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('class', 'ring');
  svg.setAttribute('viewBox', '0 0 48 48');
  const circumference = 2 * Math.PI * 20;
  for (const kind of ['rail', 'run']) {
    const c = document.createElementNS(NS, 'circle');
    c.setAttribute('class', kind);
    c.setAttribute('cx', '24'); c.setAttribute('cy', '24'); c.setAttribute('r', '20');
    if (kind === 'run') {
      c.setAttribute('stroke-dasharray', String(circumference));
      c.setAttribute('stroke-dashoffset', String(circumference * (1 - Math.max(0.03, fraction))));
    }
    svg.appendChild(c);
  }
  return svg;
}

function badge(job) {
  // "waiting" is only true of a queue that is moving. Before Convert is
  // pressed nothing is waiting on anything, and saying so reads as a stall.
  if (job.status === 'queued') return state.busy ? ['waiting', 'wait'] : ['ready', 'wait'];
  if (job.status === 'done') return job.outdated ? ['out of date', 'wait'] : ['done', 'good'];
  if (job.status === 'failed') return ['failed', 'bad'];
  if (job.status === 'cancelled') return ['stopped', 'wait'];
  return [job.status, 'wait'];
}

function describe(job) {
  switch (job.status) {
    case 'queued': return state.busy ? 'in the queue' : 'not converted yet';
    case 'running': return job.stage ? job.stage + '…' : 'starting…';
    case 'cancelled': return 'stopped before it finished';
    case 'failed': return job.error || 'failed';
    case 'done': {
      const r = job.result || {};
      const bits = [`${size(r.svg_bytes)}`, `${r.regions} shapes`];
      if (r.similarity != null) bits.push(`${(r.similarity * 100).toFixed(1)}% match`);
      if (job.outdated) bits.push('settings changed');
      return bits.join(' · ');
    }
    default: return '';
  }
}

function size(bytes) {
  if (bytes == null) return '';
  return bytes >= 1024 * 1024
    ? (bytes / 1024 / 1024).toFixed(1) + ' MB'
    : Math.round(bytes / 1024) + ' KB';
}

function apply(snapshot) {
  if (!snapshot) return;
  state.jobs = snapshot.jobs;
  state.busy = snapshot.busy;
  state.outdated = snapshot.outdated || 0;
  render();
  pace();
}

async function refresh() { apply(await call('poll')); }

function pace() {
  // Poll only while something is moving; an idle window should be idle.
  clearInterval(state.timer);
  state.timer = state.busy ? setInterval(refresh, 350) : null;
}

/* ----------------------------------------------------------------- viewer */

async function openViewer(id) {
  const data = await call('preview', id);
  if (!data || !data.svg) {
    say('That result is no longer on disk.', 'bad');
    return;
  }
  state.view = data;
  $('v-name').textContent = data.name;
  const at = state.order.indexOf(id);
  $('v-pos').textContent = state.order.length > 1 ? `${at + 1} of ${state.order.length}` : '';
  $('v-prev').disabled = $('v-next').disabled = state.order.length < 2;

  $('v-source').src = data.source;
  $('v-vector').innerHTML = data.svg;

  const r = data.result || {};
  state.natural = { w: r.width || 800, h: r.height || 600 };
  $('v-stats').innerHTML = '';
  const chips = [
    ['Original', `${(r.source_size || []).join('×')} · ${size(r.source_bytes)}`],
    ['Vector', `${size(r.svg_bytes)} · ${r.regions} shapes · ${r.gradients} gradients`],
    ['Read as', r.content || ''],
    ['Settings', `${r.preset} · ${r.quality}`],
    ['Took', `${r.seconds}s`],
  ];
  if (r.similarity != null) chips.push(['Match', `${(r.similarity * 100).toFixed(1)}%`]);
  for (const [k, v] of chips) {
    const chip = document.createElement('div');
    chip.className = 'chip' + (k === 'Match' && r.similarity > 0.9 ? ' win' : '');
    const b = document.createElement('b');
    b.textContent = k;
    chip.append(b, document.createTextNode(v));
    $('v-stats').appendChild(chip);
  }

  state.split = 50;
  setMode('wipe');
  $('viewer').hidden = false;
  requestAnimationFrame(fit);
}

function closeViewer() {
  $('viewer').hidden = true;
  $('v-vector').innerHTML = '';
  state.view = null;
}

function setMode(mode) {
  state.mode = mode;
  const canvas = $('v-canvas');
  canvas.classList.toggle('wipe', mode === 'wipe');
  canvas.classList.toggle('only-source', mode === 'source');
  canvas.classList.toggle('only-vector', mode === 'vector');
  for (const b of $('v-mode').querySelectorAll('button')) {
    b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
  }
  $('v-tag-left').hidden = mode !== 'wipe';
  $('v-tag-right').hidden = mode !== 'wipe';
  setSplit(state.split);
}

function setSplit(percent) {
  state.split = Math.max(0, Math.min(100, percent));
  $('v-canvas').style.setProperty('--split', state.split + '%');
}

function fit() {
  const stage = $('v-stage').getBoundingClientRect();
  const { w, h } = state.natural;
  if (!w || !h) return;
  const scale = Math.min((stage.width - 48) / w, (stage.height - 48) / h, 4);
  state.zoom = Math.max(0.05, scale);
  state.pan = { x: 0, y: 0 };
  const canvas = $('v-canvas');
  canvas.style.width = w + 'px';
  canvas.style.height = h + 'px';
  applyTransform();
}

function applyTransform() {
  $('v-canvas').style.transform =
    `translate(${state.pan.x}px, ${state.pan.y}px) scale(${state.zoom})`;
  $('v-zoom').textContent = Math.round(state.zoom * 100) + '%';
}

function zoomBy(factor) {
  state.zoom = Math.max(0.05, Math.min(16, state.zoom * factor));
  applyTransform();
}

function viewerInteractions() {
  const stage = $('v-stage');
  const wipe = $('v-wipe');
  let dragging = null;

  wipe.addEventListener('mousedown', (e) => {
    e.preventDefault(); e.stopPropagation();
    dragging = 'split';
  });

  stage.addEventListener('mousedown', (e) => {
    if (dragging) return;   // the handle claimed it first
    stage.classList.add('panning');
    dragging = { kind: 'pan', x: e.clientX - state.pan.x, y: e.clientY - state.pan.y };
  });

  addEventListener('mousemove', (e) => {
    if (!dragging) return;
    if (dragging === 'split') {
      const box = $('v-canvas').getBoundingClientRect();
      setSplit(((e.clientX - box.left) / box.width) * 100);
    } else if (dragging.kind === 'pan') {
      state.pan = { x: e.clientX - dragging.x, y: e.clientY - dragging.y };
      applyTransform();
    }
  });

  addEventListener('mouseup', () => { dragging = null; stage.classList.remove('panning'); });

  stage.addEventListener('wheel', (e) => {
    e.preventDefault();
    zoomBy(e.deltaY < 0 ? 1.12 : 1 / 1.12);
  }, { passive: false });
}

function step(delta) {
  if (!state.view || state.order.length < 2) return;
  const at = state.order.indexOf(state.view.id);
  const next = state.order[(at + delta + state.order.length) % state.order.length];
  openViewer(next);
}

/* ------------------------------------------------------------------- drop */

function dropTargets() {
  let depth = 0;
  const veil = $('drop');
  addEventListener('dragenter', (e) => { e.preventDefault(); if (++depth === 1) veil.hidden = false; });
  addEventListener('dragover', (e) => e.preventDefault());
  addEventListener('dragleave', (e) => { e.preventDefault(); if (--depth <= 0) { depth = 0; veil.hidden = true; } });
  addEventListener('drop', async (e) => {
    e.preventDefault();
    depth = 0;
    veil.hidden = true;
    const files = Array.from(e.dataTransfer?.files || []);
    if (!files.length) return;
    const cap = (state.info && state.info.max_drop_bytes) || Infinity;
    const tooBig = files.filter((f) => f.size > cap);
    if (tooBig.length) {
      // Refuse before reading: a dropped file crosses the bridge as base64,
      // which is a third larger again than the file itself.
      say(`${tooBig[0].name} is larger than ${Math.round(cap / 1024 / 1024)} MB.`, 'bad');
    }
    for (const file of files) {
      if (file.size <= cap) await handOver(file);
    }
    await refresh();
  });
}

// The system webview will not tell us where a dropped file lives, so the
// bytes travel through the bridge and Python writes them somewhere it owns.
function handOver(file) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onerror = () => resolve();
    reader.onload = async () => {
      const base64 = String(reader.result).split(',', 2)[1] || '';
      remember(report(await call('accept_drop', file.name, base64)));
      resolve();
    };
    reader.readAsDataURL(file);
  });
}

function remember(added) {
  for (const job of added || []) {
    if (job.thumbnail) state.thumbs.set(job.id, job.thumbnail);
  }
}

/* ----------------------------------------------------------------- wiring */

function wire() {
  const add = async () => { remember(report(await call('choose_images'))); await refresh(); };
  $('add').onclick = add;
  $('add-inline').onclick = add;

  $('convert').onclick = async () =>
    apply(await call(state.again ? 'convert_again' : 'start'));
  $('cancel').onclick = async () => apply(await call('cancel'));
  $('clear').onclick = async () => { state.thumbs.clear(); apply(await call('clear_all')); };

  $('settings-toggle').onclick = (e) => {
    const shown = $('panel').hidden;
    $('panel').hidden = !shown;
    e.currentTarget.setAttribute('aria-pressed', String(shown));
  };

  $('measure').onchange = (e) => pushSettings({ measure: e.target.checked });
  $('pick-out').onclick = async () => setOut(await call('choose_output_dir'));
  $('out-beside').onclick = async () => setOut(await call('use_source_folder'));

  $('v-close').onclick = closeViewer;
  $('v-prev').onclick = () => step(-1);
  $('v-next').onclick = () => step(1);
  $('v-fit').onclick = fit;
  $('v-zoom-in').onclick = () => zoomBy(1.25);
  $('v-zoom-out').onclick = () => zoomBy(1 / 1.25);
  $('v-reveal').onclick = () => state.view && call('reveal', state.view.result.destination);
  $('v-save').onclick = async () => {
    if (!state.view) return;
    const saved = await call('save_copy', state.view.id);
    if (saved) say('Saved to ' + saved, 'good');
  };
  for (const b of $('v-mode').querySelectorAll('button')) {
    b.onclick = () => setMode(b.dataset.mode);
  }

  addEventListener('keydown', (e) => {
    if ($('viewer').hidden) return;
    if (e.key === 'Escape') closeViewer();
    else if (e.key === 'ArrowLeft') step(-1);
    else if (e.key === 'ArrowRight') step(1);
    else if (e.key === '1') setMode('source');
    else if (e.key === '2') setMode('vector');
    else if (e.key === '3') setMode('wipe');
    else if (e.key === '0') fit();
    else if (e.key === '+' || e.key === '=') zoomBy(1.25);
    else if (e.key === '-') zoomBy(1 / 1.25);
  });

  addEventListener('resize', () => { if (!$('viewer').hidden) fit(); });

  viewerInteractions();
  dropTargets();
}

function setOut(path) {
  if (path === null) return;
  $('outdir').textContent = path || 'Beside each original';
  $('out-beside').hidden = !path;
}

window.addEventListener('pywebviewready', () => { wire(); boot(); });
