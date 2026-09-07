// AI model picker (extension: ai + ai_models, SPEC v1 §4.6).
//
// Capability-gated: only devices that advertise `ai_models` show the
// selector. Switching POSTs the activate endpoint (applied immediately,
// rollback server-side); `ai_model_changed` SSE events keep every open
// client in sync without a reload.

import { api } from './api.js';
import { store, hasCap } from './store.js';
import { $, toast } from './ui.js';
import { t } from './i18n.js';

export function aiModelsEnabled() {
  return hasCap('ai') && hasCap('ai_models');
}

async function refreshModels() {
  if (!aiModelsEnabled()) return;
  const data = await api.get('/api/ai/models').catch(() => null);
  if (!data) return;
  renderModelSelect(data);
}

export function renderModelSelect(data) {
  const field = $('ai-model-field');
  const select = $('ai-model-select');
  if (!field || !select) return;
  field.classList.toggle('hidden', !aiModelsEnabled());
  if (!aiModelsEnabled()) return;

  store.aiModels = data;
  const active = data.active || '';
  select.innerHTML = '';
  for (const m of (data.models || [])) {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = m.id + (m.input ? ' · ' + m.input : '');
    opt.selected = m.id === active;
    if (!m.available) {
      opt.disabled = true;
      opt.title = t('aiModelUnavailable');
    }
    if (m.id === 'custom') opt.title = t('aiModelCustom');
    select.appendChild(opt);
  }
  if (!select.querySelector('option[selected]') && active) {
    // Active model missing from the list (e.g. custom override resolved at
    // boot) — show it as a locked extra entry so the UI never lies.
    const opt = document.createElement('option');
    opt.value = active;
    opt.textContent = active;
    opt.selected = true;
    opt.disabled = true;
    select.appendChild(opt);
  }
}

async function activateModel(id) {
  try {
    const data = await api.post(`/api/ai/models/${encodeURIComponent(id)}/activate`);
    toast(t('aiModelSwitched', { model: data.active || id }), 'success');
    // SSE ai_model_changed refreshes the select for everyone; refresh now
    // anyway so single-client setups update even without a stream.
    await refreshModels();
  } catch (e) {
    toast(t('aiModelSwitchFail') + (e && e.message ? ': ' + e.message : ''), 'error');
    await refreshModels();
  }
}

export function handleModelChanged(payload) {
  if (!payload || !payload.model) return;
  toast(t('aiModelSwitched', { model: payload.model }), 'info');
  refreshModels();
}

export function initAi() {
  const select = $('ai-model-select');
  if (!select) return;
  select.addEventListener('change', () => {
    if (select.value) activateModel(select.value);
  });
  refreshModels();
}
