'use strict';

import { cfg, ANTHROPIC_MODELS } from './config.js';
import { converse, probeDgx } from './llm.js';
import { SCALAR_KEYS, SYSTEM_PREAMBLE, TOOLS, STAGES } from './schema.js';
import { renderTemplate, buildRef2VA, ref2vaToText } from './promptbuilder.js';
import * as ui from './ui.js';

const { $, $$, el } = ui;

const TEMPLATE_URL = './data/sheet_template.txt';
const METHODOLOGY_URL = './data/methodology.md';
const MAX_TURNS = 10;

/* ── état ───────────────────────────────────────────────── */

const state = {
  view: 'home',
  stage: 'identity',
  progress: {},                 // stageId → 'run' | 'done' | 'err'
  conversation: [],
  sheet: Object.fromEntries(SCALAR_KEYS.map((k) => [k, ''])),
  notes: [],
  pendingImages: [],
  template: '',
  methodology: '',
  system: SYSTEM_PREAMBLE + '(méthodologie non chargée — utilise ton bon sens en attendant)',
  busy: false,
};

/* ── outils : ce que le modèle a le droit de changer ────── */

function applyToolCall(name, input) {
  if (name === 'update_character_sheet') {
    const touched = [];
    for (const [k, v] of Object.entries(input || {})) {
      if (SCALAR_KEYS.includes(k) && typeof v === 'string' && v.trim()) {
        state.sheet[k] = v.trim();
        touched.push(k);
      }
    }
    ui.renderSheet(state.sheet, state.notes);
    touched.forEach(ui.flashField);
    return `ok: ${touched.join(', ') || 'aucun champ valide'}`;
  }

  if (name === 'add_note') {
    const text = (input?.text || '').trim();
    if (!text) return 'note vide';
    state.notes.push(text);
    ui.renderSheet(state.sheet, state.notes);
    return `note ajoutée (#${state.notes.length})`;
  }

  if (name === 'request_input') return 'widget affiché — en attente de la réponse utilisateur via UI';

  return `outil inconnu: ${name}`;
}

/* ── la boucle d'agent ──────────────────────────────────── */

async function runAgent() {
  if (state.busy) return;
  if (!cfg.ready()) { openSettings(); return; }

  state.busy = true;
  ui.setStatus('en cours…', 'work');
  ui.appendThinking();

  let lastVia = null;
  try {
    for (let turn = 0; turn < MAX_TURNS; turn++) {
      const r = await converse({
        system: state.system,
        messages: state.conversation,
        tools: TOOLS,
        onFallback: (from, to, why) => {
          ui.toast(`${from} indisponible → repli ${to}`);
          ui.appendMessage('system', `// ${from} : ${why}\n// repli sur ${to}`, 'moteur');
        },
      });

      lastVia = r.via;

      // On réécrit la réponse dans le dialecte à blocs pour que le
      // tour suivant reparte d'un historique cohérent, quel que soit
      // le moteur qui a répondu.
      const blocks = [];
      if (r.text) blocks.push({ type: 'text', text: r.text });
      for (const tc of r.toolCalls) blocks.push({ type: 'tool_use', id: tc.id, name: tc.name, input: tc.input });
      state.conversation.push({ role: 'assistant', content: blocks });

      ui.removeThinking();
      if (r.text.trim()) ui.appendMessage('assistant', r.text.trim(), `modèle · ${r.via}`);

      if (!r.toolCalls.length) break;

      const results = [];
      let waiting = false;
      for (const tc of r.toolCalls) {
        const out = applyToolCall(tc.name, tc.input);
        results.push({ type: 'tool_result', tool_use_id: tc.id, content: out });
        if (tc.name === 'request_input') { ui.renderInputWidget(tc); waiting = true; }
        else ui.appendMessage('tool', `▸ ${tc.name} → ${out}`, null);
      }
      state.conversation.push({ role: 'user', content: results });

      if (waiting) { ui.setStatus('attente réponse', 'work'); return; }
      ui.appendThinking();
    }
    ui.setStatus(engineLabel(lastVia), 'on');
  } catch (e) {
    ui.removeThinking();
    ui.appendMessage('err', `// erreur : ${e.message}`, 'moteur');
    ui.setStatus('erreur', 'err');
  } finally {
    state.busy = false;
  }
}

async function submitWidget(field, value, lock) {
  if (state.busy) return;
  if (SCALAR_KEYS.includes(field)) {
    state.sheet[field] = value;
    ui.renderSheet(state.sheet, state.notes);
    ui.flashField(field);
  }
  lock(value);
  state.conversation.push({ role: 'user', content: `[via widget] ${field} = ${value}` });
  await runAgent();
}

/* ── envoi d'un tour utilisateur ────────────────────────── */

async function handleSend() {
  const text = $('#input-text').value.trim();
  if (!text && !state.pendingImages.length) return;
  if (state.busy) return;

  const blocks = state.pendingImages.map((img) => ({
    type: 'image',
    source: { type: 'base64', media_type: img.mediaType, data: img.b64 },
  }));
  if (text) blocks.push({ type: 'text', text });
  state.conversation.push({ role: 'user', content: blocks });

  const node = el('div');
  if (state.pendingImages.length) {
    const strip = el('div', 'msg-images');
    state.pendingImages.forEach((img) => {
      const i = el('img');
      i.src = img.dataUrl;
      i.alt = img.name || 'référence';
      strip.appendChild(i);
    });
    node.appendChild(strip);
  }
  if (text) node.appendChild(el('div', null, text));
  ui.appendMessage('user', node, 'toi');

  $('#input-text').value = '';
  state.pendingImages = [];
  paintPreviews();

  await runAgent();
}

/* ── références ─────────────────────────────────────────── */

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

async function addImageFile(file) {
  if (!file || !file.type.startsWith('image/')) return;
  const dataUrl = await fileToDataUrl(file);
  const m = /^data:([^;]+);base64,(.+)$/.exec(dataUrl);
  if (!m) return;
  state.pendingImages.push({ dataUrl, mediaType: m[1], b64: m[2], name: file.name });
  paintPreviews();
}

function paintPreviews() {
  ui.renderPreviews(state.pendingImages, (i) => {
    state.pendingImages.splice(i, 1);
    paintPreviews();
  });
}

function setupDragDrop() {
  const zone = document.body;
  let depth = 0;
  const drop = $('#drop');

  zone.addEventListener('dragenter', (e) => { e.preventDefault(); if (++depth === 1) drop.classList.add('over'); });
  zone.addEventListener('dragover', (e) => e.preventDefault());
  zone.addEventListener('dragleave', (e) => { e.preventDefault(); if (--depth <= 0) { depth = 0; drop.classList.remove('over'); } });
  zone.addEventListener('drop', async (e) => {
    e.preventDefault();
    depth = 0;
    drop.classList.remove('over');
    for (const f of e.dataTransfer.files) await addImageFile(f);
  });

  document.addEventListener('paste', async (e) => {
    for (const item of e.clipboardData?.items || []) {
      if (item.type.startsWith('image/')) await addImageFile(item.getAsFile());
    }
  });
}

/* ── sortie texte ───────────────────────────────────────── */

function currentOutput() { return $('#output-text').textContent; }

function showOutput(text, label) {
  $('#output-text').textContent = text;
  $('#output-label').textContent = label;
  $('#output').hidden = false;
  $('#output-text').scrollTop = 0;
  $('#output').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function handleGenerate() {
  if (!state.template) { ui.toast('gabarit introuvable — recharge la page'); return; }
  showOutput(renderTemplate(state.template, state.sheet, state.notes), 'prompt_sheet.txt');
}

function handleRef2VA() {
  const refs = [];
  if (state.pendingImages.length) {
    refs.push({ role: 'the face, hair, age and identity of the character', what: state.pendingImages[0].name });
    state.pendingImages.slice(1).forEach((img, i) => {
      refs.push({ role: `costume reference ${i + 1}`, what: img.name });
    });
  }
  const p = buildRef2VA({
    sheet: state.sheet,
    notes: state.notes,
    refs,
    target: 'plate',
    maskFace: $('#mask-face').checked,
  });
  showOutput(ref2vaToText(p), 'ref2va_plate.txt');
}

function handleCopy() {
  navigator.clipboard.writeText(currentOutput()).then(
    () => ui.toast('copié'),
    () => ui.toast('presse-papiers refusé'),
  );
}

function handleDownload() {
  const name = (state.sheet.character_name || 'character').toLowerCase().replace(/[^a-z0-9]+/g, '_');
  const label = $('#output-label').textContent.replace(/[^a-z0-9_.]+/gi, '_');
  const blob = new Blob([currentOutput()], { type: 'text/plain;charset=utf-8' });
  const a = el('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${name}_${label}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function handleExportJson() {
  const payload = {
    schema: 'character-factory/identity@1',
    exported_at: new Date().toISOString(),
    sheet: state.sheet,
    notes: state.notes,
  };
  const name = (state.sheet.character_name || 'character').toLowerCase().replace(/[^a-z0-9]+/g, '_');
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const a = el('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${name}_identity.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  ui.toast('identité exportée');
}

/* ── étages ─────────────────────────────────────────────── */

function showHome() {
  state.view = 'home';
  $('#home').hidden = false;
  $('#work').hidden = true;
  $('#console-btn').hidden = true;
  ui.renderConsole(state.progress);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function pickStage(id) {
  const st = STAGES.find((s) => s.id === id);
  if (!st) return;
  state.stage = id;
  state.view = 'work';
  $('#home').hidden = true;
  $('#work').hidden = false;
  $('#console-btn').hidden = false;

  $('#stage-ref').textContent = st.ref;
  $('#stage-name').textContent = st.name;
  $('#stage-blurb').textContent = st.blurb;

  const live = st.ready;
  $('#stage-live').hidden = !live;
  $('#stage-pending').hidden = live;
  if (!live) $('#stage-needs').textContent = st.needs;

  // Le plan de planche ne concerne que les deux étages qui le
  // produisent : la planche elle-même et les vues qu'on en tire.
  $('#plate-plan').hidden = live || !(id === 'sheet' || id === 'views');

  ui.renderRack(id, state.progress);
}

/* ── réglages ───────────────────────────────────────────── */

function engineLabel(via) {
  const chain = cfg.plan();
  if (!chain.length) return 'non configuré';
  const active = via || chain[0];
  const model = active === 'dgx' ? cfg.read('dgxModel') : cfg.read('anthropicModel');
  return `${active} · ${model}`;
}

function openSettings() {
  const c = cfg.all();
  $('#set-dgx-url').value = c.dgxUrl;
  $('#set-dgx-model').value = c.dgxModel;
  $('#set-dgx-token').value = c.dgxToken;
  $('#set-key').value = c.anthropicKey;
  $('#set-engine').value = c.engine;

  const sel = $('#set-anthropic-model');
  sel.innerHTML = '';
  for (const m of ANTHROPIC_MODELS) {
    const o = el('option', null, m);
    o.value = m;
    sel.appendChild(o);
  }
  sel.value = c.anthropicModel;

  $('#set-probe').textContent = '';
  $('#settings').hidden = false;
  setTimeout(() => $('#set-dgx-url').focus(), 50);
}

function closeSettings() { $('#settings').hidden = true; }

function saveSettings() {
  cfg.write('dgxUrl', $('#set-dgx-url').value.trim());
  cfg.write('dgxModel', $('#set-dgx-model').value.trim() || 'local-model');
  cfg.write('dgxToken', $('#set-dgx-token').value.trim());
  cfg.write('anthropicKey', $('#set-key').value.trim());
  cfg.write('anthropicModel', $('#set-anthropic-model').value);
  cfg.write('engine', $('#set-engine').value);
  closeSettings();
  refreshEngine();
  ui.toast('réglages enregistrés');
}

async function runProbe() {
  const out = $('#set-probe');
  // On sonde l'URL qui est dans le champ, pas celle qui est
  // enregistrée : sinon on teste l'ancienne valeur.
  const previous = cfg.read('dgxUrl');
  cfg.write('dgxUrl', $('#set-dgx-url').value.trim());
  cfg.write('dgxToken', $('#set-dgx-token').value.trim());

  out.textContent = 'test en cours…';
  const r = await probeDgx();
  cfg.write('dgxUrl', previous);

  if (r.ok) {
    out.textContent = r.models.length
      ? `OK — modèles servis : ${r.models.join(', ')}`
      : 'OK — le DGX répond, aucun modèle listé';
    if (r.models.length && !$('#set-dgx-model').value.trim()) $('#set-dgx-model').value = r.models[0];
  } else {
    out.textContent = `échec — ${r.reason}`;
  }
}

async function refreshEngine() {
  const chain = cfg.plan();
  const settle = (label, cls, node) => {
    ui.setStatus(label, cls);
    ui.setNode(node);
    if (state.view === 'home') ui.renderConsole(state.progress);
  };

  if (!chain.length) return settle('non configuré', 'err', 'aucun moteur');

  if (chain[0] === 'dgx') {
    ui.setStatus('test DGX…', 'work');
    const r = await probeDgx();
    if (r.ok) return settle(`dgx · ${cfg.read('dgxModel')}`, 'on', `dgx · ${cfg.read('dgxModel')}`);
    if (chain[1]) return settle(`dgx muet → ${chain[1]}`, 'err', `repli ${chain[1]}`);
    return settle(`dgx muet — ${r.reason}`, 'err', 'dgx muet');
  }

  settle(`anthropic · ${cfg.read('anthropicModel')}`, 'on', `anthropic · ${cfg.read('anthropicModel')}`);
}

/* ── méthode ────────────────────────────────────────────── */

function openMethod() {
  $('#method-body').textContent = state.methodology || '(methodology.md non chargé)';
  $('#method').hidden = false;
}
function closeMethod() { $('#method').hidden = true; }

/* ── remise à zéro ──────────────────────────────────────── */

function handleReset() {
  if (!confirm('Vider la fiche et la conversation ? Les réglages du moteur sont conservés.')) return;
  state.conversation = [];
  state.sheet = Object.fromEntries(SCALAR_KEYS.map((k) => [k, '']));
  state.notes = [];
  state.pendingImages = [];
  state.progress = {};
  ui.clearChat();
  ui.renderSheet(state.sheet, state.notes);
  paintPreviews();
  $('#output').hidden = true;
  bootMessage();
  pickStage('identity');
  showHome();
  ui.toast('session vidée');
}

/* ── chargements ────────────────────────────────────────── */

async function loadTemplate() {
  try {
    const res = await fetch(TEMPLATE_URL);
    if (!res.ok) throw new Error(`${res.status}`);
    state.template = await res.text();
  } catch (e) {
    ui.appendMessage('err', `// gabarit illisible (${e.message}) — le bouton PROMPT restera inerte`, 'boot');
  }
}

async function loadMethodology() {
  try {
    const res = await fetch(METHODOLOGY_URL);
    if (!res.ok) throw new Error(`${res.status}`);
    state.methodology = await res.text();
    state.system = SYSTEM_PREAMBLE + state.methodology;
  } catch (e) {
    ui.appendMessage('err', `// methodology.md illisible (${e.message}) — le modèle travaille sans méthode`, 'boot');
  }
}

function bootMessage() {
  ui.appendMessage(
    'system',
    [
      '// character factory — étage identité.',
      '// décris le personnage que tu as en tête, en français ou en anglais.',
      '// dépose une image n\'importe où sur la page pour la donner en référence.',
      '// la fiche se remplit à droite ; le modèle pose des widgets pour combler les blancs.',
    ].join('\n'),
    'boot',
  );
}

/* ── horloge ────────────────────────────────────────────── */

function tickClock() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  $('#clock').textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/* ── amorçage ───────────────────────────────────────────── */

function wire() {
  ui.setHandlers({ onWidgetSubmit: submitWidget, onStagePick: pickStage });

  $('#send').onclick = handleSend;
  $('#input-text').onkeydown = (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); handleSend(); }
  };
  $('#pick-image').onchange = async (e) => {
    for (const f of e.target.files) await addImageFile(f);
    e.target.value = '';
  };

  $('#generate').onclick = handleGenerate;
  $('#ref2va').onclick = handleRef2VA;
  $('#export-json').onclick = handleExportJson;
  $('#copy').onclick = handleCopy;
  $('#download').onclick = handleDownload;

  $('#home-btn').onclick = showHome;
  $('#console-btn').onclick = showHome;
  $('#settings-btn').onclick = openSettings;
  $('#set-save').onclick = saveSettings;
  $('#set-probe-btn').onclick = runProbe;
  $('#method-btn').onclick = openMethod;
  $('#reset-btn').onclick = handleReset;

  $$('[data-close]').forEach((b) => {
    b.onclick = () => { closeSettings(); closeMethod(); };
  });
  $$('.scrim').forEach((s) => {
    s.onclick = (e) => { if (e.target === s) { closeSettings(); closeMethod(); } };
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeSettings(); closeMethod(); }
  });

  setupDragDrop();
}

async function init() {
  wire();
  ui.renderSheet(state.sheet, state.notes);
  pickStage('identity');   // prépare le banc sans l'afficher
  showHome();
  bootMessage();
  tickClock();
  setInterval(tickClock, 1000);

  await Promise.all([loadTemplate(), loadMethodology()]);
  await refreshEngine();

  if (!cfg.ready()) {
    ui.appendMessage('system', '// aucun moteur configuré. ouvre MOTEUR et pose l\'URL du DGX, ou une clé Anthropic de repli.', 'boot');
  }
}

document.addEventListener('DOMContentLoaded', init);
