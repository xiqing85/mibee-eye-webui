// Model flow graph (SPEC §6 `model_call` + §3.1 `observability.model_flow`,
// appendix A #50): a live topology of the device's AI pipeline. Nodes
// ripple while their model runs, pulses travel the edges as the call
// chain advances, and concurrent models all animate at once. Ambient
// detectors (ai/audio_ai/face ~1-2 Hz) keep firing — the ripple visuals
// are throttled per node, the counters stay honest.

import { store } from './store.js';
import { t } from './i18n.js';
import { $ } from './ui.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const REDUCED = typeof matchMedia === 'function'
  && matchMedia('(prefers-reduced-motion: reduce)').matches;

// Fixed layout (viewBox 920×340): mic/cam anchors on the left, the
// dialogue chain across the top, perception models in the bands below.
// tts.zh is the canonical tts node (tts.yue/tts.en light it too);
// cloud.vision lights the vlm node.
const NODES = [
  { id: 'mic', x: 46, y: 168, anchor: true, glyph: '🎤', label: 'mfMic' },
  { id: 'cam', x: 46, y: 264, anchor: true, glyph: '📷', label: 'mfCam' },
  { id: 'out', x: 874, y: 72, anchor: true, glyph: '📢', label: 'mfOut' },
  { id: 'voice.asr', x: 190, y: 72, glyph: '👂', label: 'mfAsr' },
  { id: 'decision', x: 370, y: 72, glyph: '⚡', label: 'mfDecision' },
  { id: 'llm', x: 550, y: 72, glyph: '🧠', label: 'mfLlm' },
  { id: 'tts', x: 730, y: 72, glyph: '💬', label: 'mfTts' },
  { id: 'meeting', x: 190, y: 168, glyph: '🗣', label: 'mfMeeting' },
  { id: 'speaker', x: 370, y: 168, glyph: '🧬', label: 'mfSpeaker' },
  { id: 'cloud.chat', x: 550, y: 168, glyph: '☁', label: 'mfCloud' },
  { id: 'vlm', x: 730, y: 168, glyph: '👁', label: 'mfVlm' },
  { id: 'audio_ai', x: 190, y: 264, glyph: '🎵', label: 'mfAudioAi' },
  { id: 'ai', x: 370, y: 264, glyph: '🔍', label: 'mfAi' },
  { id: 'face.recog', x: 550, y: 264, glyph: '😀', label: 'mfFace' },
  { id: 'ocr', x: 730, y: 264, glyph: '🔤', label: 'mfOcr' },
];

const EDGES = [
  ['mic', 'voice.asr'],
  ['voice.asr', 'decision'],
  ['decision', 'llm'],
  ['llm', 'tts'],
  ['cloud.chat', 'tts'],
  ['decision', 'cloud.chat'],
  ['mic', 'meeting'],
  ['mic', 'speaker'],
  ['mic', 'audio_ai'],
  ['cam', 'ai'],
  ['ai', 'face.recog'],
  ['ai', 'vlm'],
  ['decision', 'vlm'],
  ['cam', 'ocr'],
  ['tts', 'out'],
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

const RIPPLE_MIN_GAP_MS = 600;    // per-node visual throttle (counters stay real)
const INFLIGHT_RIPPLE_MS = 1400;  // long calls keep rippling while active

const state = {
  built: false,
  nodes: {},        // id → { g, countEl, durEl }
  edges: {},        // 'src>dst' → path element
  inflight: {},     // id → count
  window: {},       // id → [timestamps] rolling 60s
  lastDur: {},      // id → ms of last end
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

/// One §6 model_call payload: start lights the node (ripples + edge
/// pulses into it), end clears one inflight slot and refreshes the
/// counters. Unknown model names are ignored (SPEC fail-open).
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
    node.g.classList.add('active');
    ripple(id);
    flowInto(id);
    if (state.inflight[id] > 0) scheduleInflightRipple(id);
  }
  renderSub(id);
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
  const c = document.createElementNS(SVG_NS, 'circle');
  c.setAttribute('class', 'mf-ripple');
  c.setAttribute('cx', node.g.dataset.x);
  c.setAttribute('cy', node.g.dataset.y);
  c.setAttribute('r', node.g.dataset.r);
  c.addEventListener('animationend', () => c.remove());
  svg.appendChild(c);
}

function flowInto(id) {
  for (const [src, dst] of EDGES) {
    if (dst !== id) continue;
    const path = state.edges[`${src}>${dst}`];
    if (!path) continue;
    path.classList.add('flowing');
    setTimeout(() => path.classList.remove('flowing'), 650);
    if (!REDUCED) travelPulse(path);
  }
}

function travelPulse(path) {
  const svg = $('modelflow-svg');
  if (!svg) return;
  const len = path.getTotalLength();
  const dot = document.createElementNS(SVG_NS, 'circle');
  dot.setAttribute('class', 'mf-pulse');
  dot.setAttribute('r', '4');
  svg.appendChild(dot);
  const t0 = performance.now();
  const DUR = 480;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / DUR);
    const p = path.getPointAtLength(k * len);
    dot.setAttribute('cx', p.x);
    dot.setAttribute('cy', p.y);
    dot.setAttribute('opacity', String(1 - k * 0.3));
    if (k < 1) requestAnimationFrame(step);
    else dot.remove();
  };
  requestAnimationFrame(step);
}

function buildGraph() {
  if (state.built) return;
  const svg = $('modelflow-svg');
  if (!svg) return;
  state.built = true;
  const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));
  for (const [src, dst] of EDGES) {
    const a = byId[src];
    const b = byId[dst];
    const path = document.createElementNS(SVG_NS, 'path');
    // Gentle curve: control point offset perpendicular to the chord.
    const mx = (a.x + b.x) / 2;
    const my = (a.y + b.y) / 2;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const off = 0.18;
    path.setAttribute('d', `M ${a.x} ${a.y} Q ${mx - dy * off} ${my + dx * off} ${b.x} ${b.y}`);
    path.setAttribute('class', 'mf-edge');
    svg.appendChild(path);
    state.edges[`${src}>${dst}`] = path;
  }
  for (const n of NODES) {
    const r = n.anchor ? 17 : 24;
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', `mf-node${n.anchor ? ' anchor' : ''}`);
    g.dataset.node = n.id;
    g.dataset.x = n.x;
    g.dataset.y = n.y;
    g.dataset.r = r;
    const core = document.createElementNS(SVG_NS, 'circle');
    core.setAttribute('cx', n.x);
    core.setAttribute('cy', n.y);
    core.setAttribute('r', r);
    core.setAttribute('class', 'core');
    g.appendChild(core);
    const glyph = document.createElementNS(SVG_NS, 'text');
    glyph.setAttribute('x', n.x);
    glyph.setAttribute('y', n.y + 6);
    glyph.setAttribute('class', 'glyph');
    glyph.textContent = n.glyph;
    g.appendChild(glyph);
    const label = document.createElementNS(SVG_NS, 'text');
    label.setAttribute('x', n.x);
    label.setAttribute('y', n.y + r + 13);
    label.setAttribute('class', 'mf-label');
    g.appendChild(label);
    const sub = document.createElementNS(SVG_NS, 'text');
    sub.setAttribute('x', n.x);
    sub.setAttribute('y', n.y + r + 26);
    sub.setAttribute('class', 'mf-sub');
    g.appendChild(sub);
    svg.appendChild(g);
    state.nodes[n.id] = { g, label, sub };
  }
  renderLabels();
}

function renderLabels() {
  const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));
  for (const [id, node] of Object.entries(state.nodes)) {
    node.label.textContent = t(byId[id].label);
    renderSub(id);
  }
}

function renderSub(id) {
  const node = state.nodes[id];
  if (!node) return;
  const recent = (state.window[id] || []).filter((ts) => Date.now() - ts < 60_000);
  state.window[id] = recent;
  if (!recent.length && state.lastDur[id] == null) {
    node.sub.textContent = '';
    return;
  }
  const parts = [];
  if (recent.length) parts.push(t('mfRate', { n: recent.length }));
  if (state.lastDur[id] != null) parts.push(fmtDur(state.lastDur[id]));
  node.sub.textContent = parts.join(' · ');
}

function fmtDur(ms) {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 1000)}s`;
}

/// Test helper: reset per-node activity so visual assertions are
/// deterministic across runs.
export function resetModelFlowForTests() {
  state.inflight = {};
  state.window = {};
  state.lastDur = {};
  for (const node of Object.values(state.nodes)) {
    node.g.classList.remove('active', 'errored');
    node.sub.textContent = '';
  }
}
