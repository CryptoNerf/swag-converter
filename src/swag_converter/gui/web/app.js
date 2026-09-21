'use strict';

// Everything Python can do for us lives behind pywebview.api; the page holds
// no state the backend does not also hold, so a poll is always the truth.
const api = () => window.pywebview.api;

const $ = (id) => document.getElementById(id);

// Every call to Python goes through here. A bridge method that throws used to
// reject a promise nobody was listening to, which is how "Add images" came to
// do nothing at all: the failure was real and completely invisible.
async function call(method, ...args) {
  try {
    return await api()[method](...args);
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
  sayTimer = setTimeout(() => { box.hidden = true; }, tone === 'bad' ? 9000 : 5000);
}

function report(outcome) {
  // {added, skipped} from the bridge; the page must not silently drop either.
  if (!outcome) return [];
  const skipped = outcome.skipped || [];
  if (skipped.length === 1) {
    say(`${skipped[0].name} ${skipped[0].reason}.`, 'bad');
  } else if (skipped.length > 1) {
    say(`${skipped.length} files skipped — ${skipped[0].name} ${skipped[0].reason}, and others.`, 'bad');
  }
  return outcome.added || [];
}

const PRESET_NOTES = {
  auto: 'Looks at the image and picks for you.',
  icon: 'Logos, UI icons, flat artwork. Crisp edges, few shapes.',
  illustration: 'Shaded drawings, stickers, game art. Keeps gradients.',
  photo: 'Photographs. A deliberate stylisation, not a reproduction.',
  poster: 'A few big flat shapes, screen-print look.',
};

const QUALITY_NOTES = {
  fast: 'Fewest shapes. Quickest, smallest file.',
  balanced: 'The default. Detail worth keeping, without the wait.',
  max: 'Most shapes. Slowest, largest file; flat artwork barely differs.',
};

const state = {
  jobs: [],
  busy: false,
  info: null,
  thumbs: new Map(),
  viewing: null,
  timer: null,
};

/* ------------------------------------------------------------------ setup */

async function boot() {
  state.info = await call('describe');
  if (!state.info) {
    say('The converter did not start. Reopen the app.', 'bad');
    return;
  }
  const s = state.info.settings;

  fill($('preset'), state.info.presets, s.preset);
  fill($('quality'), state.info.qualities, s.quality);
  $('max-edge').value = String(s.max_edge);
  $('background').value = s.background;
  $('measure').checked = s.measure && state.info.scoring;
  $('measure').disabled = !state.info.scoring;
  $('measure-note').textContent = state.info.scoring
    ? 'Renders the SVG back and compares it. Slower.'
    : 'Needs CairoSVG, which is not installed here.';
  $('version').textContent = 'version ' + state.info.version;
  $('formats').textContent = 'PNG, JPEG, WebP, HEIC, GIF, BMP, TIFF and more';
  notes();
  await refresh();
}

function fill(select, values, chosen) {
  select.innerHTML = '';
  for (const value of values) {
    const option = new Option(value[0].toUpperCase() + value.slice(1), value);
    select.add(option);
  }
  select.value = chosen;
}

function notes() {
  $('preset-note').textContent = PRESET_NOTES[$('preset').value] || '';
  $('quality-note').textContent = QUALITY_NOTES[$('quality').value] || '';
}

async function pushSettings(patch) {
  await call('update_settings', patch);
  notes();
}

/* ------------------------------------------------------------------ queue */

function render() {
  const list = $('list');
  const jobs = state.jobs;

  $('empty').hidden = jobs.length > 0;
  list.hidden = jobs.length === 0;

  list.innerHTML = '';
  for (const job of jobs) {
    const li = document.createElement('li');
    li.className = 'item' + (job.status === 'done' ? ' done' : '');

    const img = document.createElement('img');
    img.className = 'thumb';
    img.alt = '';
    if (state.thumbs.has(job.id)) img.src = state.thumbs.get(job.id);

    const middle = document.createElement('div');
    const name = document.createElement('div');
    name.className = 'item-name';
    name.textContent = job.name;
    const note = document.createElement('div');
    note.className = 'item-note' + noteTone(job);
    note.textContent = describe(job);
    middle.append(name, note);

    if (job.status === 'running') {
      const bar = document.createElement('div');
      bar.className = 'bar';
      const fill = document.createElement('i');
      fill.style.width = Math.round(job.progress * 100) + '%';
      bar.appendChild(fill);
      middle.appendChild(bar);
    }

    const actions = document.createElement('div');
    actions.className = 'item-actions';
    if (job.status === 'done') {
      actions.appendChild(button('Show', (e) => { e.stopPropagation(); call('reveal', job.destination); }));
    }
    if (job.status !== 'running') {
      actions.appendChild(button('Remove', async (e) => {
        e.stopPropagation();
        state.thumbs.delete(job.id);
        apply(await call('remove', job.id));
      }));
    }

    if (job.status === 'done') li.onclick = () => openViewer(job.id);
    li.append(img, middle, actions);
    list.appendChild(li);
  }

  const done = jobs.filter((j) => j.status === 'done').length;
  const failed = jobs.filter((j) => j.status === 'failed').length;
  $('summary').textContent = jobs.length
    ? `${jobs.length} image${jobs.length > 1 ? 's' : ''}` +
      (done ? ` · ${done} done` : '') + (failed ? ` · ${failed} failed` : '')
    : '';

  $('convert').disabled = state.busy || jobs.length === 0;
  $('convert').textContent = state.busy ? 'Converting…' : 'Convert';
  $('cancel').hidden = !state.busy;
  $('clear-done').hidden = !(done + failed) || state.busy;
  $('clear-all').hidden = !jobs.length || state.busy;
}

function noteTone(job) {
  if (job.status === 'failed') return ' bad';
  if (job.status === 'done') return ' good';
  return '';
}

function describe(job) {
  switch (job.status) {
    case 'queued': return 'waiting';
    case 'running': return job.stage ? job.stage + '…' : 'starting…';
    case 'cancelled': return 'stopped';
    case 'failed': return job.error || 'failed';
    case 'done': {
      const r = job.result || {};
      const bits = [`${kb(r.svg_bytes)} · ${r.regions} shapes`];
      if (r.similarity != null) bits.push(`${(r.similarity * 100).toFixed(1)}% match`);
      if (job.elapsed != null) bits.push(`${job.elapsed}s`);
      return bits.join(' · ');
    }
    default: return '';
  }
}

function kb(bytes) {
  if (!bytes && bytes !== 0) return '';
  return bytes >= 1024 * 1024
    ? (bytes / 1024 / 1024).toFixed(1) + ' MB'
    : Math.round(bytes / 1024) + ' KB';
}

function button(label, onclick) {
  const b = document.createElement('button');
  b.className = 'quiet';
  b.textContent = label;
  b.onclick = onclick;
  return b;
}

function apply(snapshot) {
  if (!snapshot) return;
  state.jobs = snapshot.jobs;
  state.busy = snapshot.busy;
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
  state.viewing = data;
  $('viewer-name').textContent = data.name;
  $('viewer-source').src = data.source;
  $('viewer-vector').innerHTML = data.svg;
  side('vector');

  const r = data.result || {};
  const stats = [
    ['Source', `${r.source_size ? r.source_size.join('×') : ''} · ${kb(r.source_bytes)}`],
    ['Vector', `${kb(r.svg_bytes)} · ${r.regions} shapes · ${r.gradients} gradients`],
    ['Read as', r.content || ''],
    ['Settings', `${r.preset} · ${r.quality}`],
    ['Took', `${r.seconds}s`],
  ];
  if (r.similarity != null) stats.push(['Match', `${(r.similarity * 100).toFixed(1)}%`]);
  $('viewer-stats').innerHTML = stats
    .map(([k, v]) => `<div><b>${k}</b>${escapeHtml(String(v))}</div>`)
    .join('');
  $('viewer').hidden = false;
}

function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function side(which) {
  $('viewer-source').classList.toggle('on', which === 'source');
  $('viewer-vector').classList.toggle('on', which === 'vector');
  for (const b of $('viewer-toggle').querySelectorAll('button')) {
    b.setAttribute('aria-pressed', b.dataset.side === which);
  }
}

function closeViewer() {
  $('viewer').hidden = true;
  $('viewer-vector').innerHTML = '';
  state.viewing = null;
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

function forgetFinished() {
  for (const job of state.jobs) {
    if (job.status !== 'running' && job.status !== 'queued') state.thumbs.delete(job.id);
  }
}

/* ------------------------------------------------------------------ wiring */

function wire() {
  $('add').onclick = async () => { remember(report(await call('choose_images'))); await refresh(); };
  $('convert').onclick = async () => apply(await call('start'));
  $('cancel').onclick = async () => apply(await call('cancel'));
  $('clear-done').onclick = async () => { forgetFinished(); apply(await call('clear_finished')); };
  $('clear-all').onclick = async () => { state.thumbs.clear(); apply(await call('clear_all')); };

  $('preset').onchange = (e) => pushSettings({ preset: e.target.value });
  $('quality').onchange = (e) => pushSettings({ quality: e.target.value });
  $('max-edge').onchange = (e) => pushSettings({ max_edge: e.target.value });
  $('background').onchange = (e) => pushSettings({ background: e.target.value });
  $('measure').onchange = (e) => pushSettings({ measure: e.target.checked });

  $('pick-out').onclick = async () => setOut(await call('choose_output_dir'));
  $('out-beside').onclick = async () => setOut(await call('use_source_folder'));

  $('viewer-close').onclick = closeViewer;
  $('viewer-reveal').onclick = () => state.viewing && call('reveal', state.viewing.result.destination);
  $('viewer-save').onclick = async () => {
    if (!state.viewing) return;
    const saved = await call('save_copy', state.viewing.id);
    if (saved) say('Saved to ' + saved);
  };
  for (const b of $('viewer-toggle').querySelectorAll('button')) {
    b.onclick = () => side(b.dataset.side);
  }

  addEventListener('keydown', (e) => {
    if ($('viewer').hidden) return;
    if (e.key === 'Escape') closeViewer();
    if (e.key === '1') side('source');
    if (e.key === '2') side('vector');
  });

  dropTargets();
}

function setOut(path) {
  $('outdir').textContent = path || 'Beside each original';
}

window.addEventListener('pywebviewready', () => { wire(); boot(); });
