// Model flow graph (SPEC §6 `model_call` + §3.1 `observability.model_flow`,
// appendix A #50): a lane-structured circuit board of the device's AI
// pipeline — chips with the mono code + localized role inside, one meta
// line under each (model variant · rate · duration), a distribution bus
// per source, data packets on the traces. Concurrent models all animate
// at once; an elastic tier swap renames the chip on the next call.

import { store } from './store.js';
import { t } from './i18n.js';
import { $ } from './ui.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const REDUCED = typeof matchMedia === 'function'
  && matchMedia('(prefers-reduced-motion: reduce)').matches;

// Two-tier composition: the dialogue chain is the hero (full chips on
// two rows), the always-on perception models are a compact status strip
// (small badges, no trace spaghetti) — the card stays ~half the height
// so it never dominates the assistant page.
const CHAIN_Y = 64;
const ALT_Y = 150;
const STRIP_Y = 224;
const CHIP_W = 96;
const CHIP_H = 36;
const BADGE_W = 84;
const BADGE_H = 30;

const NODES = [
  { id: 'mic', x: 66, y: CHAIN_Y, w: CHIP_W, h: CHIP_H, anchor: true, code: 'MIC' },
  { id: 'voice.asr', x: 220, y: CHAIN_Y, w: CHIP_W, h: CHIP_H, code: 'ASR' },
  { id: 'decision', x: 374, y: CHAIN_Y, w: CHIP_W, h: CHIP_H, code: 'DEC' },
  { id: 'llm', x: 528, y: CHAIN_Y, w: CHIP_W, h: CHIP_H, code: 'LLM' },
  { id: 'tts', x: 682, y: CHAIN_Y, w: CHIP_W, h: CHIP_H, code: 'TTS' },
  { id: 'out', x: 848, y: CHAIN_Y, w: CHIP_W, h: CHIP_H, anchor: true, code: 'OUT' },
  { id: 'cloud.chat', x: 528, y: ALT_Y, w: CHIP_W, h: CHIP_H, code: 'CLD' },
  { id: 'vlm', x: 682, y: ALT_Y, w: CHIP_W, h: CHIP_H, code: 'VLM' },
  { id: 'audio_ai', x: 120, y: STRIP_Y, w: BADGE_W, h: BADGE_H, badge: true, code: 'SND' },
  { id: 'speaker', x: 268, y: STRIP_Y, w: BADGE_W, h: BADGE_H, badge: true, code: 'SPK' },
  { id: 'meeting', x: 416, y: STRIP_Y, w: BADGE_W, h: BADGE_H, badge: true, code: 'MTG' },
  { id: 'ai', x: 564, y: STRIP_Y, w: BADGE_W, h: BADGE_H, badge: true, code: 'DET' },
  { id: 'face.recog', x: 712, y: STRIP_Y, w: BADGE_W, h: BADGE_H, badge: true, code: 'FACE' },
  { id: 'ocr', x: 860, y: STRIP_Y, w: BADGE_W, h: BADGE_H, badge: true, code: 'OCR' },
];

// Orthogonal PCB traces. 'h'/'v' straight; 'elbow:<midY>' routes
// bottom(src) → midY → top(dst) — sharing a midY merges the overlapping
// segments into one visual bus; 'low:<midY>' routes below the
// destination row and rises into its bottom edge.
const EDGES = [
  ['mic', 'voice.asr', 'h'],
  ['voice.asr', 'decision', 'h'],
  ['decision', 'llm', 'h'],
  ['llm', 'tts', 'h'],
  ['tts', 'out', 'h'],
  ['decision', 'cloud.chat', 'elbow:107'],
  ['cloud.chat', 'tts', 'elbow:120'],
  ['decision', 'vlm', 'elbow:128'],
];

// Via where the two alternate traces cross the chain column.
const PADS = [];
const VIAS = [[528, 120]];

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
  nodes: {},        // id → { g, metaEl }
  edges: {},        // 'src>dst' → path element
  inflight: {},     // id → count
  window: {},       // id → [timestamps] rolling 60s
  lastDur: {},      // id → ms of last end
  variant: {},      // id → concrete model name (live)
  lastRipple: {},   // id → ms of last ripple spawn
  rippleGen: {},    // id → generation (stops re-ripple loops)
  laneCaps: [],     // [{el, key}] lane band captions
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
  renderMeta(id);
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
  const w = Number(node.g.dataset.w || CHIP_W);
  const h = Number(node.g.dataset.h || CHIP_H);
  const ring = document.createElementNS(SVG_NS, 'rect');
  ring.setAttribute('class', 'mf-ripple');
  ring.setAttribute('x', Number(node.g.dataset.x) - w / 2);
  ring.setAttribute('y', Number(node.g.dataset.y) - h / 2);
  ring.setAttribute('width', w);
  ring.setAttribute('height', h);
  ring.setAttribute('rx', 5);
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

/// A short data packet sliding along the trace, oriented by the local
/// tangent — PCB data-flow feel.
function travelPacket(path) {
  const svg = $('modelflow-svg');
  if (!svg) return;
  const len = path.getTotalLength();
  const packet = document.createElementNS(SVG_NS, 'rect');
  packet.setAttribute('class', 'mf-pulse');
  packet.setAttribute('width', '10');
  packet.setAttribute('height', '2.6');
  packet.setAttribute('rx', '1');
  svg.appendChild(packet);
  const t0 = performance.now();
  const DUR = 480;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / DUR);
    const l = k * len;
    const p = path.getPointAtLength(l);
    const q = path.getPointAtLength(Math.min(len, l + 1));
    const a = (Math.atan2(q.y - p.y, q.x - p.x) * 180) / Math.PI;
    packet.setAttribute('transform', `translate(${p.x - 5} ${p.y - 1.3}) rotate(${a.toFixed(1)} 5 1.3)`);
    packet.setAttribute('opacity', String(0.95 - k * 0.35));
    if (k < 1) requestAnimationFrame(step);
    else packet.remove();
  };
  requestAnimationFrame(step);
}

/// Chip: 104×42 rect with two lines inside (mono code + localized role)
/// and ONE meta line below (variant · rate · duration) — a uniform
/// rhythm across all chips.
function buildGraph() {
  if (state.built) return;
  const svg = $('modelflow-svg');
  if (!svg) return;
  state.built = true;
  svg.appendChild(dotGrid());
  svg.appendChild(lanes());

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
  for (const [px, py] of PADS) {
    const pad = document.createElementNS(SVG_NS, 'rect');
    pad.setAttribute('x', px - 2.6);
    pad.setAttribute('y', py - 2.6);
    pad.setAttribute('width', 5.2);
    pad.setAttribute('height', 5.2);
    pad.setAttribute('class', 'mf-pad');
    svg.appendChild(pad);
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
    g.setAttribute('class', `mf-node${n.anchor ? ' anchor' : ''}${n.badge ? ' badge' : ''}`);
    g.dataset.node = n.id;
    g.dataset.x = n.x;
    g.dataset.y = n.y;
    g.dataset.w = n.w;
    g.dataset.h = n.h;
    g.appendChild(chipFrame(n));
    const code = document.createElementNS(SVG_NS, 'text');
    code.setAttribute('x', n.x);
    code.setAttribute('y', n.y + (n.badge ? 1 : -1));
    code.setAttribute('class', 'mf-code');
    code.textContent = n.code;
    g.appendChild(code);
    const label = document.createElementNS(SVG_NS, 'text');
    label.setAttribute('x', n.x);
    label.setAttribute('y', n.y + n.h / 2 + 11);
    label.setAttribute('class', 'mf-label');
    g.appendChild(label);
    const meta = document.createElementNS(SVG_NS, 'text');
    meta.setAttribute('x', n.x);
    meta.setAttribute('y', n.y + n.h / 2 + 23);
    meta.setAttribute('class', 'mf-meta');
    g.appendChild(meta);
    svg.appendChild(g);
    state.nodes[n.id] = { g, meta };
  }
  renderLabels();
}

/// The chip rect plus four HUD corner brackets (visible when active).
function chipFrame(n) {
  const frag = document.createDocumentFragment();
  const rect = document.createElementNS(SVG_NS, 'rect');
  rect.setAttribute('x', n.x - n.w / 2);
  rect.setAttribute('y', n.y - n.h / 2);
  rect.setAttribute('width', n.w);
  rect.setAttribute('height', n.h);
  rect.setAttribute('rx', 5);
  rect.setAttribute('class', 'core');
  frag.appendChild(rect);
  const b = n.badge ? 6 : 8; // bracket arm length
  const x0 = n.x - n.w / 2 - 3;
  const x1 = n.x + n.w / 2 + 3;
  const y0 = n.y - n.h / 2 - 3;
  const y1 = n.y + n.h / 2 + 3;
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

/// PCB trace geometry: 'h'/'v' straight between chip borders;
/// 'elbow:<midY>' bottom(src) → midY → top(dst); 'low:<midY>' routes
/// below the destination row and rises into its bottom edge.
function tracePath(a, b, shape) {
  const [ax, ay, bx, by] = [a.x, a.y, b.x, b.y];
  if (shape === 'h') {
    const x1 = ax < bx ? ax + a.w / 2 : ax - a.w / 2;
    const x2 = ax < bx ? bx - b.w / 2 : bx + b.w / 2;
    return `M ${x1} ${ay} H ${x2}`;
  }
  if (shape === 'v') {
    const y1 = ay < by ? ay + a.h / 2 : ay - a.h / 2;
    const y2 = ay < by ? by - b.h / 2 : by + b.h / 2;
    return `M ${ax} ${y1} V ${y2}`;
  }
  const midY = Number(shape.split(':')[1]);
  const fromTop = by < ay;
  const y1 = fromTop ? ay - a.h / 2 : ay + a.h / 2;
  const y2 = fromTop ? by + b.h / 2 : by - b.h / 2;
  return `M ${ax} ${y1} V ${midY} H ${bx} V ${y2}`;
}

/// Three lane bands (dialogue / audio & parallel / vision) — the board's
/// structure, so the chips sit in a designed panel instead of a void.
function lanes() {
  const frag = document.createDocumentFragment();
  const bands = [
    [24, 176, 'mfLane1'],
    [202, 74, 'mfLane3'],
  ];
  for (const [y, h, key] of bands) {
    const rect = document.createElementNS(SVG_NS, 'rect');
    rect.setAttribute('x', 8);
    rect.setAttribute('y', y);
    rect.setAttribute('width', 904);
    rect.setAttribute('height', h);
    rect.setAttribute('rx', 8);
    rect.setAttribute('class', 'mf-lane');
    frag.appendChild(rect);
    const cap = document.createElementNS(SVG_NS, 'text');
    cap.setAttribute('x', 16);
    cap.setAttribute('y', y + 15);
    cap.setAttribute('class', 'mf-lane-cap');
    frag.appendChild(cap);
    state.laneCaps = state.laneCaps || [];
    state.laneCaps.push({ el: cap, key });
  }
  return frag;
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
  for (const { el, key } of state.laneCaps || []) el.textContent = t(key);
  const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));
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

/// Test helper: reset per-chip activity so visual assertions are
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
