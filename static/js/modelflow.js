// Model flow graph (SPEC §6 `model_call` + §3.1 `observability.model_flow`,
// appendix A #50): a live circuit-board view of the device's AI pipeline.
// Chips light up while their model runs, data pulses travel the traces,
// concurrent models all animate at once. Each chip shows its concrete
// model variant (live — an elastic tier swap renames the chip on the
// next call). Ambient detectors (~1-2 Hz) keep firing; the visuals are
// throttled per chip, the counters stay honest.

import { store } from './store.js';
import { t } from './i18n.js';
import { $ } from './ui.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const REDUCED = typeof matchMedia === 'function'
  && matchMedia('(prefers-reduced-motion: reduce)').matches;

// Strict grid: six columns × three rows. Row 0 is the dialogue chain,
// row 1 the mic-fed extras and the chat alternates, row 2 the camera-fed
// perception band. Chips are 88×34, centered on the grid points.
const COLS = [70, 230, 390, 550, 710, 880];
const ROWS = [70, 190, 310];
const CHIP_W = 88;
const CHIP_H = 34;

const NODES = [
  { id: 'mic', x: COLS[0], y: ROWS[0], anchor: true, code: 'MIC' },
  { id: 'voice.asr', x: COLS[1], y: ROWS[0], code: 'ASR' },
  { id: 'decision', x: COLS[2], y: ROWS[0], code: 'DEC' },
  { id: 'llm', x: COLS[3], y: ROWS[0], code: 'LLM' },
  { id: 'tts', x: COLS[4], y: ROWS[0], code: 'TTS' },
  { id: 'out', x: COLS[5], y: ROWS[0], anchor: true, code: 'OUT' },
  { id: 'audio_ai', x: COLS[0], y: ROWS[1], code: 'SND' },
  { id: 'speaker', x: COLS[1], y: ROWS[1], code: 'SPK' },
  { id: 'meeting', x: COLS[2], y: ROWS[1], code: 'MTG' },
  { id: 'cloud.chat', x: COLS[3], y: ROWS[1], code: 'CLD' },
  { id: 'vlm', x: COLS[4], y: ROWS[1], code: 'VLM' },
  { id: 'cam', x: COLS[0], y: ROWS[2], anchor: true, code: 'CAM' },
  { id: 'ai', x: COLS[1], y: ROWS[2], code: 'DET' },
  { id: 'face.recog', x: COLS[2], y: ROWS[2], code: 'FACE' },
  { id: 'ocr', x: COLS[3], y: ROWS[2], code: 'OCR' },
];

// Trace shapes: 'h'/'v' are straight grid segments; 'elbow:<midY>' is the
// PCB vertical-horizontal-vertical route between rows.
const EDGES = [
  ['mic', 'voice.asr', 'h'],
  ['voice.asr', 'decision', 'h'],
  ['decision', 'llm', 'h'],
  ['llm', 'tts', 'h'],
  ['tts', 'out', 'h'],
  ['mic', 'audio_ai', 'v'],
  ['mic', 'speaker', 'elbow:146'],
  ['mic', 'meeting', 'elbow:162'],
  ['decision', 'cloud.chat', 'elbow:135'],
  ['cloud.chat', 'tts', 'elbow:135'],
  ['decision', 'vlm', 'elbow:150'],
  ['cam', 'ai', 'h'],
  ['ai', 'face.recog', 'h'],
  ['cam', 'ocr', 'h'],
  ['ai', 'vlm', 'elbow:255'],
];

// PCB vias where two traces cross (decision→vlm's horizontal crosses the
// decision→cloud.chat vertical): a ring keeps the crossing readable
// instead of looking like a junction.
const VIAS = [[550, 150]];

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

const RIPPLE_MIN_GAP_MS = 600;    // per-chip visual throttle (counters stay real)
const INFLIGHT_RIPPLE_MS = 1400;  // long calls keep pulsing while active

const state = {
  built: false,
  nodes: {},        // id → { g, codeEl, labelEl, varEl, subEl }
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

/// One §6 model_call payload: start lights the chip (pulse rings + data
/// packets on the incoming traces + the concrete variant name), end
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
  renderSub(id);
}

/// Collapse a variant string to the concrete model name shown on the
/// chip: basename without paths/extensions, cut at a natural boundary
/// (hyphen/underscore) when too long for the chip width.
/// "models/llm/Qwen3-4B-Instruct-2507-Q4_K_M.gguf" → "Qwen3-4B",
/// "paraformer-trilingual" → "paraformer".
function variantShort(variant) {
  let v = String(variant).split('/').pop() || String(variant);
  v = v.replace(/\.(gguf|onnx|bin|int8|q8_0|q4_k_m)$/i, '');
  v = v.split(/-[Ii]nstruct/)[0];
  if (v.length <= 12) return v;
  const cut = (sep) => {
    const i = v.indexOf(sep);
    return i >= 4 && i <= 12 ? v.slice(0, i) : null;
  };
  return cut('-') || cut('_') || v.slice(0, 12);
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
  const ring = document.createElementNS(SVG_NS, 'rect');
  ring.setAttribute('class', 'mf-ripple');
  ring.setAttribute('x', Number(node.g.dataset.x) - CHIP_W / 2);
  ring.setAttribute('y', Number(node.g.dataset.y) - CHIP_H / 2);
  ring.setAttribute('width', CHIP_W);
  ring.setAttribute('height', CHIP_H);
  ring.setAttribute('rx', 4);
  ring.addEventListener('animationend', () => ring.remove());
  svg.appendChild(ring);
}

function flowInto(id) {
  for (const [src, dst] of EDGES) {
    if (dst !== id) continue;
    const path = state.edges[`${src}>${dst}`];
    if (!path) continue;
    path.classList.add('flowing');
    setTimeout(() => path.classList.remove('flowing'), 650);
    if (!REDUCED) travelPacket(path);
  }
}

/// A short data packet sliding along the trace, oriented by the local
/// tangent — PCB data-flow feel.
function travelPacket(path) {
  const svg = $('modelflow-svg');
  if (!svg) return;
  const len = path.getTotalLength();
  const packet = document.createElementNS(SVG_NS, 'rect');
  packet.setAttribute('class', 'mf-pulse');
  packet.setAttribute('width', '9');
  packet.setAttribute('height', '2.4');
  packet.setAttribute('rx', '1');
  svg.appendChild(packet);
  const t0 = performance.now();
  const DUR = 460;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / DUR);
    const l = k * len;
    const p = path.getPointAtLength(l);
    const q = path.getPointAtLength(Math.min(len, l + 1));
    const a = (Math.atan2(q.y - p.y, q.x - p.x) * 180) / Math.PI;
    packet.setAttribute('transform', `translate(${p.x - 4.5} ${p.y - 1.2}) rotate(${a.toFixed(1)} 4.5 1.2)`);
    packet.setAttribute('opacity', String(0.95 - k * 0.35));
    if (k < 1) requestAnimationFrame(step);
    else packet.remove();
  };
  requestAnimationFrame(step);
}

/// Chip body: 88×34 rounded rect with the code tag inside, corner
/// brackets that appear on activity, and three annotation lines below —
/// localized name, concrete variant (live), counter/duration.
function buildGraph() {
  if (state.built) return;
  const svg = $('modelflow-svg');
  if (!svg) return;
  state.built = true;
  svg.appendChild(dotGrid());

  const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));
  for (const [src, dst, shape] of EDGES) {
    const a = byId[src];
    const b = byId[dst];
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', tracePath(a, b, shape));
    path.setAttribute('class', 'mf-edge');
    svg.appendChild(path);
    state.edges[`${src}>${dst}`] = path;
  }
  for (const [vx, vy] of VIAS) {
    const via = document.createElementNS(SVG_NS, 'circle');
    via.setAttribute('cx', vx);
    via.setAttribute('cy', vy);
    via.setAttribute('r', '4');
    via.setAttribute('class', 'mf-via');
    svg.appendChild(via);
  }
  for (const n of NODES) {
    const g = document.createElementNS(SVG_NS, 'g');
    g.setAttribute('class', `mf-node${n.anchor ? ' anchor' : ''}`);
    g.dataset.node = n.id;
    g.dataset.x = n.x;
    g.dataset.y = n.y;
    g.appendChild(chipFrame(n));
    const code = document.createElementNS(SVG_NS, 'text');
    code.setAttribute('x', n.x);
    code.setAttribute('y', n.y + 4);
    code.setAttribute('class', 'mf-code');
    code.textContent = n.code;
    g.appendChild(code);
    const label = document.createElementNS(SVG_NS, 'text');
    label.setAttribute('x', n.x);
    label.setAttribute('y', n.y + CHIP_H / 2 + 12);
    label.setAttribute('class', 'mf-label');
    g.appendChild(label);
    const variant = document.createElementNS(SVG_NS, 'text');
    variant.setAttribute('x', n.x);
    variant.setAttribute('y', n.y + CHIP_H / 2 + 24);
    variant.setAttribute('class', 'mf-variant');
    g.appendChild(variant);
    const sub = document.createElementNS(SVG_NS, 'text');
    sub.setAttribute('x', n.x);
    sub.setAttribute('y', n.y + CHIP_H / 2 + 36);
    sub.setAttribute('class', 'mf-sub');
    g.appendChild(sub);
    svg.appendChild(g);
    state.nodes[n.id] = { g, label, variant, sub };
  }
  renderLabels();
}

/// The chip rect plus four HUD corner brackets (visible when active).
function chipFrame(n) {
  const frag = document.createDocumentFragment();
  const rect = document.createElementNS(SVG_NS, 'rect');
  rect.setAttribute('x', n.x - CHIP_W / 2);
  rect.setAttribute('y', n.y - CHIP_H / 2);
  rect.setAttribute('width', CHIP_W);
  rect.setAttribute('height', CHIP_H);
  rect.setAttribute('rx', 4);
  rect.setAttribute('class', 'core');
  frag.appendChild(rect);
  const b = 7; // bracket arm length
  const x0 = n.x - CHIP_W / 2 - 4;
  const x1 = n.x + CHIP_W / 2 + 4;
  const y0 = n.y - CHIP_H / 2 - 4;
  const y1 = n.y + CHIP_H / 2 + 4;
  const corners = [
    `M ${x0} ${y0 + b} V ${y0} H ${x0 + b}`,
    `M ${x1 - b} ${y0} H ${x1} V ${y0 + b}`,
    `M ${x1} ${y1 - b} V ${y1} H ${x1 - b}`,
    `M ${x0 + b} ${y1} H ${x0} V ${y1 - b}`,
  ];
  for (const d of corners) {
    const p = document.createElementNS(SVG_NS, 'path');
    p.setAttribute('d', d);
    p.setAttribute('class', 'mf-bracket');
    frag.appendChild(p);
  }
  return frag;
}

/// PCB trace geometry: 'h'/'v' are straight segments between chip
/// borders; 'elbow:<midY>' routes bottom(src) → midY → top/bottom(dst).
function tracePath(a, b, shape) {
  const [ax, ay, bx, by] = [a.x, a.y, b.x, b.y];
  if (shape === 'h') {
    const x1 = ax < bx ? ax + CHIP_W / 2 : ax - CHIP_W / 2;
    const x2 = ax < bx ? bx - CHIP_W / 2 : bx + CHIP_W / 2;
    return `M ${x1} ${ay} H ${x2}`;
  }
  if (shape === 'v') {
    const y1 = ay < by ? ay + CHIP_H / 2 : ay - CHIP_H / 2;
    const y2 = ay < by ? by - CHIP_H / 2 : by + CHIP_H / 2;
    return `M ${ax} ${y1} V ${y2}`;
  }
  const midY = Number(shape.split(':')[1]);
  const fromTop = by < ay;
  const y1 = fromTop ? ay - CHIP_H / 2 : ay + CHIP_H / 2;
  const y2 = fromTop ? by + CHIP_H / 2 : by - CHIP_H / 2;
  return `M ${ax} ${y1} V ${midY} H ${bx} V ${y2}`;
}

/// Subtle dot-grid background — the board the chips sit on.
function dotGrid() {
  const defs = document.createElementNS(SVG_NS, 'defs');
  const pattern = document.createElementNS(SVG_NS, 'pattern');
  pattern.setAttribute('id', 'mf-grid');
  pattern.setAttribute('width', '22');
  pattern.setAttribute('height', '22');
  pattern.setAttribute('patternUnits', 'userSpaceOnUse');
  const dot = document.createElementNS(SVG_NS, 'circle');
  dot.setAttribute('cx', '1');
  dot.setAttribute('cy', '1');
  dot.setAttribute('r', '0.9');
  dot.setAttribute('class', 'mf-grid-dot');
  pattern.appendChild(dot);
  defs.appendChild(pattern);
  const bg = document.createElementNS(SVG_NS, 'rect');
  bg.setAttribute('width', '100%');
  bg.setAttribute('height', '100%');
  bg.setAttribute('fill', 'url(#mf-grid)');
  return bg;
}

function renderLabels() {
  const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));
  for (const [id, node] of Object.entries(state.nodes)) {
    node.label.textContent = t(byId[id].anchor ? byId[id].code === 'MIC' ? 'mfMic' : byId[id].code === 'CAM' ? 'mfCam' : 'mfOut' : labelKey(id));
    renderSub(id);
  }
}

function labelKey(id) {
  return {
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

function renderSub(id) {
  const node = state.nodes[id];
  if (!node) return;
  const recent = (state.window[id] || []).filter((ts) => Date.now() - ts < 60_000);
  state.window[id] = recent;
  node.variant.textContent = state.variant[id] || '';
  const parts = [];
  if (recent.length) parts.push(`n=${recent.length}`);
  if (state.lastDur[id] != null) parts.push(fmtDur(state.lastDur[id]));
  node.sub.textContent = parts.join(' ');
}

function fmtDur(ms) {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 1000)}s`;
}

/// Test helper: reset per-chip activity so visual assertions are
/// deterministic across runs.
export function resetModelFlowForTests() {
  state.inflight = {};
  state.window = {};
  state.lastDur = {};
  state.variant = {};
  for (const node of Object.values(state.nodes)) {
    node.g.classList.remove('active', 'errored');
    node.sub.textContent = '';
    node.variant.textContent = '';
  }
}
