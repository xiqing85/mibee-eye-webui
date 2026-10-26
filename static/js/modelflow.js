// Model flow graph (SPEC §6 `model_call` + §3.1 `observability.model_flow`,
// appendix A #50): a live HUD of the device's AI pipeline, in the same
// visual language as the voiceprint hero — glowing orbs on dark, circular
// ripples while a model runs, energy dots travelling curved links. The
// dialogue chain is the hero row; the always-on perception models sit in
// a compact strip beneath. Concurrent models all animate at once; an
// elastic tier swap renames the orb on the next call.

import { store } from './store.js';
import { t } from './i18n.js';
import { $ } from './ui.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const REDUCED = typeof matchMedia === 'function'
  && matchMedia('(prefers-reduced-motion: reduce)').matches;

// The canvas fills the card width (the card itself sits in the page's
// content column like every other card); geometry below spreads content
// edge to edge with symmetric margins so nothing floats or clips.
const CHAIN_Y = 74;
const ALT_Y = 182;
const STRIP_Y = 260;
const ORB_R = 27;    // dialogue-chain orbs
const MINI_R = 20;   // ambient-strip orbs

const NODES = [
  { id: 'mic', x: 70, y: CHAIN_Y, r: 22, anchor: true, code: 'MIC' },
  { id: 'voice.asr', x: 226, y: CHAIN_Y, r: ORB_R, code: 'ASR' },
  { id: 'decision', x: 382, y: CHAIN_Y, r: ORB_R, code: 'DEC' },
  { id: 'llm', x: 538, y: CHAIN_Y, r: ORB_R, code: 'LLM' },
  { id: 'tts', x: 694, y: CHAIN_Y, r: ORB_R, code: 'TTS' },
  { id: 'out', x: 850, y: CHAIN_Y, r: 22, anchor: true, code: 'OUT' },
  { id: 'cloud.chat', x: 460, y: ALT_Y, r: ORB_R, code: 'CLD' },
  { id: 'vlm', x: 616, y: ALT_Y, r: ORB_R, code: 'VLM' },
  { id: 'audio_ai', x: 118, y: STRIP_Y, r: MINI_R, mini: true, code: 'SND' },
  { id: 'speaker', x: 258, y: STRIP_Y, r: MINI_R, mini: true, code: 'SPK' },
  { id: 'meeting', x: 398, y: STRIP_Y, r: MINI_R, mini: true, code: 'MTG' },
  { id: 'ai', x: 538, y: STRIP_Y, r: MINI_R, mini: true, code: 'DET' },
  { id: 'face.recog', x: 678, y: STRIP_Y, r: MINI_R, mini: true, code: 'FACE' },
  { id: 'ocr', x: 818, y: STRIP_Y, r: MINI_R, mini: true, code: 'OCR' },
];

// Curved energy links: [src, dst, sag] — sag bows the curve to the right
// of the travel direction.
const EDGES = [
  ['mic', 'voice.asr', 9],
  ['voice.asr', 'decision', 9],
  ['decision', 'llm', 9],
  ['llm', 'tts', 9],
  ['tts', 'out', 9],
  ['decision', 'cloud.chat', 22],
  ['cloud.chat', 'tts', 22],
  ['decision', 'vlm', 36],
];

const MODEL_TO_NODE = {
  'voice.asr': 'voice.asr',
  'decision': 'decision',
  'llm': 'llm',
  'vlm': 'vlm',
  'cloud.chat': 'cloud.chat',
  'cloud.vision': 'vlm',
  'tts.zh': 'tts',
  'tts.yue': 'tts',
  'tts.en': 'tts',
  'ai': 'ai',
  'audio_ai': 'audio_ai',
  'face.recog': 'face.recog',
  'speaker': 'speaker',
  'meeting': 'meeting',
  'ocr': 'ocr',
};

const RIPPLE_MIN_GAP_MS = 600;    // per-orb visual throttle (counters stay real)
const INFLIGHT_RIPPLE_MS = 1400;  // long calls keep pulsing while active

const state = {
  built: false,
  nodes: {},        // id → { g, metaEl }
  edges: {},        // 'src>dst' → path element
  inflight: {},     // id → count
  window: {},       // id → [timestamps] rolling 60s
  lastDur: {},      // id → ms of last end
  variant: {},      // id → concrete model name (live)
  lastRipple: {},   // id → ms of last ripple spawn
  rippleGen: {},    // id → generation (stops re-ripple loops)
};

export function initModelFlow() {
  // Labels re-resolve when the language flips (SVG text is built
  // dynamically, so the data-i18n scan in applyLang never sees it).
  window.addEventListener('langchange', renderLabels);
}

/// Show the card when the device streams model_call (SPEC §3.1
/// observability.model_flow, or the event listed in capabilities.events).
export function updateModelFlowVisibility() {
  const card = $('modelflow-card');
  if (!card) return;
  const caps = store.caps || {};
  const obs = caps.observability || {};
  const on = !!obs.model_flow || (caps.events || []).includes('model_call');
  card.classList.toggle('hidden', !on);
  if (on) buildGraph();
}

/// One §6 model_call payload: start lights the orb (circular ripples +
/// energy dots on the incoming links + the concrete variant name), end
/// clears one inflight slot and refreshes the counters. Unknown model
/// names are ignored (SPEC fail-open).
export function handleModelCall(ev) {
  if (!ev || !ev.model) return;
  const id = MODEL_TO_NODE[ev.model];
  const node = id && state.nodes[id];
  if (!node) return;
  const now = Date.now();
  if (ev.phase === 'end') {
    state.inflight[id] = Math.max(0, (state.inflight[id] || 0) - 1);
    state.window[id] = (state.window[id] || []).filter((ts) => now - ts < 60_000);
    state.window[id].push(now);
    if (typeof ev.duration_ms === 'number') state.lastDur[id] = ev.duration_ms;
    if (ev.ok === false) node.g.classList.add('errored');
    setTimeout(() => node.g.classList.remove('errored'), 1200);
    if (state.inflight[id] === 0) {
      node.g.classList.remove('active');
      state.rippleGen[id] = (state.rippleGen[id] || 0) + 1;
    }
  } else {
    state.inflight[id] = (state.inflight[id] || 0) + 1;
    if (ev.variant) state.variant[id] = variantShort(ev.variant);
    node.g.classList.add('active');
    ripple(id);
    flowInto(id);
    if (state.inflight[id] > 0) scheduleInflightRipple(id);
  }
  renderMeta(id);
}

/// Collapse a variant string to the concrete model name shown under the
/// orb: basename without paths/extensions, cut at a natural boundary
/// (hyphen/underscore) when too long.
/// "models/llm/Qwen3-4B-Instruct-2507-Q4_K_M.gguf" → "Qwen3-4B",
/// "paraformer-trilingual" → "paraformer".
function variantShort(variant) {
  let v = String(variant).split('/').pop() || String(variant);
  v = v.replace(/\.(gguf|onnx|bin|int8|q8_0|q4_k_m)$/i, '');
  v = v.split(/-[Ii]nstruct/)[0];
  if (v.length <= 14) return v;
  const cut = (sep) => {
    const i = v.indexOf(sep);
    return i >= 4 && i <= 14 ? v.slice(0, i) : null;
  };
  return cut('-') || cut('_') || v.slice(0, 14);
}

function scheduleInflightRipple(id) {
  const gen = (state.rippleGen[id] = (state.rippleGen[id] || 0) + 1);
  setTimeout(() => {
    if (state.rippleGen[id] !== gen) return;
    if (!state.inflight[id]) return;
    ripple(id);
    scheduleInflightRipple(id);
  }, INFLIGHT_RIPPLE_MS);
}

function ripple(id) {
  const node = state.nodes[id];
  const svg = $('modelflow-svg');
  if (!node || !svg || REDUCED) return;
  const now = performance.now();
  if (now - (state.lastRipple[id] || 0) < RIPPLE_MIN_GAP_MS) return;
  state.lastRipple[id] = now;
  const ring = document.createElementNS(SVG_NS, 'circle');
  ring.setAttribute('class', 'mf-ripple');
  ring.setAttribute('cx', Number(node.g.dataset.x));
  ring.setAttribute('cy', Number(node.g.dataset.y));
  ring.setAttribute('r', Number(node.g.dataset.r));
  ring.addEventListener('animationend', () => ring.remove());
  svg.appendChild(ring);
}

function flowInto(id) {
  for (const [src, dst] of EDGES) {
    if (dst !== id) continue;
    const path = state.edges[`${src}>${dst}`];
    if (!path) continue;
    path.classList.add('flowing');
    setTimeout(() => path.classList.remove('flowing'), 700);
    if (!REDUCED) travelPacket(path);
  }
}

/// A glowing energy dot (bright core + soft halo) sliding along the link.
function travelPacket(path) {
  const svg = $('modelflow-svg');
  if (!svg) return;
  const len = path.getTotalLength();
  const halo = document.createElementNS(SVG_NS, 'circle');
  halo.setAttribute('class', 'mf-pulse-halo');
  halo.setAttribute('r', '7');
  const dot = document.createElementNS(SVG_NS, 'circle');
  dot.setAttribute('class', 'mf-pulse');
  dot.setAttribute('r', '3.2');
  svg.appendChild(halo);
  svg.appendChild(dot);
  const t0 = performance.now();
  const DUR = 480;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / DUR);
    const p = path.getPointAtLength(k * len);
    for (const el of [halo, dot]) {
      el.setAttribute('cx', p.x);
      el.setAttribute('cy', p.y);
      el.setAttribute('opacity', String((el === halo ? 0.5 : 1) - k * 0.3));
    }
    if (k < 1) requestAnimationFrame(step);
    else {
      halo.remove();
      dot.remove();
    }
  };
  requestAnimationFrame(step);
}

function buildGraph() {
  if (state.built) return;
  const svg = $('modelflow-svg');
  if (!svg) return;
  state.built = true;
  const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));
  for (const [src, dst, sag] of EDGES) {
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', curvePath(byId[src], byId[dst], sag));
    path.setAttribute('class', 'mf-edge');
    svg.appendChild(path);
    state.edges[`${src}>${dst}`] = path;
  }
  for (const n of NODES) {
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', `mf-node${n.anchor ? ' anchor' : ''}${n.mini ? ' mini' : ''}`);
    g.dataset.node = n.id;
    g.dataset.x = n.x;
    g.dataset.y = n.y;
    g.dataset.r = n.r;
    const halo = document.createElementNS(SVG_NS, 'circle');
    halo.setAttribute('cx', n.x);
    halo.setAttribute('cy', n.y);
    halo.setAttribute('r', n.r + 6);
    halo.setAttribute('class', 'halo');
    g.appendChild(halo);
    const core = document.createElementNS(SVG_NS, 'circle');
    core.setAttribute('cx', n.x);
    core.setAttribute('cy', n.y);
    core.setAttribute('r', n.r);
    core.setAttribute('class', 'core');
    g.appendChild(core);
    const code = document.createElementNS(SVG_NS, 'text');
    code.setAttribute('x', n.x);
    code.setAttribute('y', n.y + 4);
    code.setAttribute('class', 'mf-code');
    code.textContent = n.code;
    g.appendChild(code);
    const label = document.createElementNS(SVG_NS, 'text');
    label.setAttribute('x', n.x);
    label.setAttribute('y', n.y + n.r + 14);
    label.setAttribute('class', 'mf-label');
    g.appendChild(label);
    const meta = document.createElementNS(SVG_NS, 'text');
    meta.setAttribute('x', n.x);
    meta.setAttribute('y', n.y + n.r + 27);
    meta.setAttribute('class', 'mf-meta');
    g.appendChild(meta);
    svg.appendChild(g);
    state.nodes[n.id] = { g, meta };
  }
  renderLabels();
}

/// Energy link between two orbs: leaves/enters at the orb borders,
/// bowed by `sag` to the right of the travel direction.
function curvePath(a, b, sag) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const dist = Math.hypot(dx, dy) || 1;
  const ux = dx / dist;
  const uy = dy / dist;
  const x1 = a.x + ux * (a.r + 3);
  const y1 = a.y + uy * (a.r + 3);
  const x2 = b.x - ux * (b.r + 3);
  const y2 = b.y - uy * (b.r + 3);
  const mx = (x1 + x2) / 2 - uy * sag;
  const my = (y1 + y2) / 2 + ux * sag;
  return `M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`;
}

function renderLabels() {
  for (const [id, node] of Object.entries(state.nodes)) {
    const labelEl = node.g.querySelector('.mf-label');
    if (labelEl) labelEl.textContent = t(labelKey(id));
    renderMeta(id);
  }
}

function labelKey(id) {
  return {
    'mic': 'mfMic',
    'cam': 'mfCam',
    'out': 'mfOut',
    'voice.asr': 'mfAsr',
    'decision': 'mfDecision',
    'llm': 'mfLlm',
    'tts': 'mfTts',
    'cloud.chat': 'mfCloud',
    'vlm': 'mfVlm',
    'meeting': 'mfMeeting',
    'speaker': 'mfSpeaker',
    'audio_ai': 'mfAudioAi',
    'ai': 'mfAi',
    'face.recog': 'mfFace',
    'ocr': 'mfOcr',
  }[id] || id;
}

function renderMeta(id) {
  const node = state.nodes[id];
  if (!node) return;
  const recent = (state.window[id] || []).filter((ts) => Date.now() - ts < 60_000);
  state.window[id] = recent;
  const parts = [];
  if (state.variant[id]) parts.push(state.variant[id]);
  if (recent.length) parts.push(`n=${recent.length}`);
  if (state.lastDur[id] != null) parts.push(fmtDur(state.lastDur[id]));
  node.meta.textContent = parts.join(' · ');
}

function fmtDur(ms) {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 1000)}s`;
}

/// Test helper: reset per-orb activity so visual assertions are
/// deterministic across runs.
export function resetModelFlowForTests() {
  state.inflight = {};
  state.window = {};
  state.lastDur = {};
  state.variant = {};
  for (const node of Object.values(state.nodes)) {
    node.g.classList.remove('active', 'errored');
    node.meta.textContent = '';
  }
}
