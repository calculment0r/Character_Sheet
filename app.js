'use strict';

const TEMPLATE_URL = './character_generation_prompt_template_layout.txt';
const API_URL = 'https://api.anthropic.com/v1/messages';

const SHEET_FIELDS = [
  { section: 'CORE', key: 'character_name', label: 'name' },
  { section: 'CORE', key: 'alias', label: 'alias' },
  { section: 'CORE', key: 'gender', label: 'gender' },
  { section: 'CORE', key: 'age', label: 'age' },
  { section: 'CORE', key: 'height', label: 'height' },
  { section: 'CORE', key: 'body_type', label: 'body type' },
  { section: 'CORE', key: 'ethnicity', label: 'ethnicity' },
  { section: 'CORE', key: 'role', label: 'role' },
  { section: 'CORE', key: 'archetype', label: 'archetype' },
  { section: 'PSYCHE', key: 'personality_traits', label: 'personality' },
  { section: 'PSYCHE', key: 'core_theme', label: 'core theme' },
  { section: 'PSYCHE', key: 'emotional_range', label: 'emotional range' },
  { section: 'PSYCHE', key: 'behavior_notes', label: 'behavior' },
  { section: 'PSYCHE', key: 'speech_style', label: 'speech / accent' },
  { section: 'OUTFIT', key: 'default_outfit_description', label: 'outfit (overall)' },
  { section: 'OUTFIT', key: 'top_description', label: 'top' },
  { section: 'OUTFIT', key: 'bottom_description', label: 'bottom' },
  { section: 'OUTFIT', key: 'shoes_description', label: 'shoes' },
  { section: 'OUTFIT', key: 'accessories', label: 'accessories' },
  { section: 'EXTRA', key: 'color_palette', label: 'color palette' },
  { section: 'EXTRA', key: 'props', label: 'props' },
];

const SECTIONS = ['CORE', 'PSYCHE', 'OUTFIT', 'EXTRA'];
const SCALAR_KEYS = SHEET_FIELDS.map((f) => f.key);

const SYSTEM_PROMPT = `Tu es un assistant qui aide à construire une "character sheet" pour générer ensuite un prompt d'image (style cinematic concept art turnaround).

Ton rôle:
- Comprendre le perso que l'utilisateur décrit (en français ou anglais).
- Quand tu apprends une info concrète, appelle TOUT DE SUITE le tool "update_character_sheet" pour la stocker. Tu peux mettre plusieurs champs en un appel.
- Quand l'utilisateur t'envoie une image de référence, observe-la attentivement et extrais une description textuelle riche que tu peux ranger dans le bon champ (accessoires, top, bottom, props, color_palette, etc.).
- Quand un champ manque ou est vague, pose UNE question ciblée à la fois — ne submerge pas l'utilisateur.
- Tu peux extrapoler quand l'utilisateur le permet (ex: "à toi de voir") ou quand un champ est trivial à déduire (ex: si "accessoires: lunettes de soleil noires" → propose une couleur dans le palette).
- Réponds de manière concise et directe. Pas de blabla.
- Tu réponds dans la langue de l'utilisateur.

Champs disponibles (utilise EXACTEMENT ces noms):
- CORE: character_name, alias, gender, age, height, body_type, ethnicity, role, archetype
- PSYCHE: personality_traits, core_theme, emotional_range, behavior_notes, speech_style
- OUTFIT: default_outfit_description (description globale), top_description, bottom_description, shoes_description, accessories
- EXTRA: color_palette (liste de couleurs/swatches), props (objets que le perso porte/manipule)
- notes: utilise add_note pour ajouter une petite annotation visuelle (ex: "manches retroussées", "posture légèrement voûtée"). Plusieurs notes possibles.

Quand TOUS les champs CORE + au moins outfit + au moins une accessoire sont remplis, dis à l'utilisateur que la fiche est prête à générer.`;

const TOOLS = [
  {
    name: 'update_character_sheet',
    description:
      "Met à jour un ou plusieurs champs de la character sheet. Utilise les noms de champs exacts. Appelle ce tool dès que tu apprends une info.",
    input_schema: {
      type: 'object',
      properties: {
        character_name: { type: 'string' },
        alias: { type: 'string' },
        gender: { type: 'string' },
        age: { type: 'string' },
        height: { type: 'string' },
        body_type: { type: 'string' },
        ethnicity: { type: 'string' },
        role: { type: 'string' },
        archetype: { type: 'string' },
        personality_traits: { type: 'string' },
        core_theme: { type: 'string' },
        emotional_range: { type: 'string' },
        behavior_notes: { type: 'string' },
        speech_style: { type: 'string' },
        default_outfit_description: { type: 'string' },
        top_description: { type: 'string' },
        bottom_description: { type: 'string' },
        shoes_description: { type: 'string' },
        accessories: { type: 'string' },
        color_palette: { type: 'string' },
        props: { type: 'string' },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'add_note',
    description: "Ajoute une petite annotation visuelle au panel notes (ex: 'manches retroussées', 'cheveux en bataille').",
    input_schema: {
      type: 'object',
      properties: { text: { type: 'string', description: 'Le texte de la note' } },
      required: ['text'],
      additionalProperties: false,
    },
  },
];

const state = {
  apiKey: localStorage.getItem('anthropic_api_key') || '',
  model: localStorage.getItem('claude_model') || 'claude-sonnet-4-6',
  conversation: [],
  sheet: Object.fromEntries(SCALAR_KEYS.map((k) => [k, ''])),
  notes: [],
  pendingImages: [],
  template: '',
  busy: false,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function setStatus(label, cls) {
  const el = $('#status-text');
  el.textContent = label;
  const wrap = el.parentElement;
  wrap.classList.remove('online', 'thinking', 'error');
  if (cls) wrap.classList.add(cls);
}

function renderSheet() {
  const container = $('#sheet-fields');
  container.innerHTML = '';

  for (const sectionName of SECTIONS) {
    const section = document.createElement('div');
    section.className = 'sheet-section';
    section.innerHTML = `<div class="sheet-section-title">${sectionName}</div>`;
    const fields = SHEET_FIELDS.filter((f) => f.section === sectionName);
    for (const f of fields) {
      const v = state.sheet[f.key];
      const row = document.createElement('div');
      row.className = 'sheet-row';
      row.dataset.key = f.key;
      row.innerHTML = `
        <div class="sheet-key">${escapeHtml(f.label)}</div>
        <div class="sheet-val ${v ? 'filled' : 'empty'}">${escapeHtml(v || '')}</div>
      `;
      section.appendChild(row);
    }
    container.appendChild(section);
  }

  if (state.notes.length) {
    const section = document.createElement('div');
    section.className = 'sheet-section';
    section.innerHTML = `<div class="sheet-section-title">NOTES (${state.notes.length})</div>`;
    state.notes.forEach((n, i) => {
      const row = document.createElement('div');
      row.className = 'sheet-row';
      row.innerHTML = `
        <div class="sheet-key">#${i + 1}</div>
        <div class="sheet-val filled">${escapeHtml(n)}</div>
      `;
      section.appendChild(row);
    });
    container.appendChild(section);
  }

  const filled = SCALAR_KEYS.filter((k) => state.sheet[k]).length;
  $('#sheet-progress').textContent = `${filled}/${SCALAR_KEYS.length}`;
}

function flashField(key) {
  const row = document.querySelector(`.sheet-row[data-key="${key}"] .sheet-val`);
  if (!row) return;
  row.classList.remove('flash');
  void row.offsetWidth;
  row.classList.add('flash');
}

function appendMessage(role, contentNodes, meta) {
  const log = $('#chat-log');
  const wrap = document.createElement('div');
  wrap.className = `msg msg-${role}`;
  if (meta) {
    const m = document.createElement('div');
    m.className = 'msg-meta';
    m.textContent = meta;
    wrap.appendChild(m);
  }
  const body = document.createElement('div');
  body.className = 'msg-body';
  if (typeof contentNodes === 'string') {
    body.textContent = contentNodes;
  } else if (Array.isArray(contentNodes)) {
    contentNodes.forEach((n) => body.appendChild(n));
  } else {
    body.appendChild(contentNodes);
  }
  wrap.appendChild(body);
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return wrap;
}

function appendThinking() {
  const log = $('#chat-log');
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-assistant';
  wrap.dataset.thinking = '1';
  wrap.innerHTML = `<div class="msg-meta">claude</div><div class="msg-body"><div class="thinking"><span></span><span></span><span></span></div></div>`;
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return wrap;
}

function removeThinking() {
  document.querySelectorAll('.msg[data-thinking]').forEach((n) => n.remove());
}

function renderImagePreviews() {
  const wrap = $('#image-preview');
  wrap.innerHTML = '';
  state.pendingImages.forEach((img, i) => {
    const div = document.createElement('div');
    div.className = 'preview-thumb';
    div.style.backgroundImage = `url("${img.dataUrl}")`;
    const btn = document.createElement('button');
    btn.textContent = '×';
    btn.title = 'remove';
    btn.onclick = () => {
      state.pendingImages.splice(i, 1);
      renderImagePreviews();
    };
    div.appendChild(btn);
    wrap.appendChild(div);
  });
}

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
  const [, mediaType, b64] = /^data:([^;]+);base64,(.+)$/.exec(dataUrl) || [];
  state.pendingImages.push({ dataUrl, mediaType, b64, name: file.name });
  renderImagePreviews();
}

function applyToolCall(name, input) {
  if (name === 'update_character_sheet') {
    const updated = [];
    for (const [k, v] of Object.entries(input || {})) {
      if (SCALAR_KEYS.includes(k) && typeof v === 'string' && v.trim()) {
        state.sheet[k] = v.trim();
        updated.push(k);
      }
    }
    renderSheet();
    updated.forEach(flashField);
    return `ok: ${updated.join(', ') || 'aucun champ valide'}`;
  }
  if (name === 'add_note') {
    const text = (input && input.text || '').trim();
    if (!text) return 'note vide';
    state.notes.push(text);
    renderSheet();
    return `note ajoutée (#${state.notes.length})`;
  }
  return `unknown tool: ${name}`;
}

async function callClaude(messages) {
  const res = await fetch(API_URL, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': state.apiKey,
      'anthropic-version': '2023-06-01',
      'anthropic-dangerous-direct-browser-access': 'true',
    },
    body: JSON.stringify({
      model: state.model,
      max_tokens: 4096,
      system: SYSTEM_PROMPT,
      tools: TOOLS,
      messages,
    }),
  });

  if (!res.ok) {
    let errMsg = `${res.status} ${res.statusText}`;
    try {
      const err = await res.json();
      if (err && err.error && err.error.message) errMsg = err.error.message;
    } catch (_) {}
    throw new Error(errMsg);
  }
  return res.json();
}

async function runAgent() {
  if (state.busy) return;
  if (!state.apiKey) {
    openModal();
    return;
  }
  state.busy = true;
  setStatus('thinking…', 'thinking');

  const thinkNode = appendThinking();

  try {
    let safety = 0;
    while (safety++ < 10) {
      const response = await callClaude(state.conversation);
      state.conversation.push({ role: 'assistant', content: response.content });

      const toolUses = response.content.filter((b) => b.type === 'tool_use');

      if (toolUses.length === 0) {
        removeThinking();
        const text = response.content.filter((b) => b.type === 'text').map((b) => b.text).join('\n').trim();
        if (text) appendMessage('assistant', text, 'claude');
        break;
      }

      const toolResults = [];
      for (const tu of toolUses) {
        const out = applyToolCall(tu.name, tu.input);
        toolResults.push({ type: 'tool_result', tool_use_id: tu.id, content: out });
        appendMessage('tool', `▸ ${tu.name}(${JSON.stringify(tu.input)}) → ${out}`, 'tool');
      }
      state.conversation.push({ role: 'user', content: toolResults });

      if (response.stop_reason !== 'tool_use') {
        removeThinking();
        const text = response.content.filter((b) => b.type === 'text').map((b) => b.text).join('\n').trim();
        if (text) appendMessage('assistant', text, 'claude');
        break;
      }
    }
    setStatus('online', 'online');
  } catch (e) {
    removeThinking();
    appendMessage('system', `// erreur: ${e.message}`, 'system');
    setStatus('error', 'error');
  } finally {
    state.busy = false;
  }
}

async function handleSend() {
  const text = $('#input-text').value.trim();
  if (!text && state.pendingImages.length === 0) return;
  if (state.busy) return;

  const blocks = [];
  for (const img of state.pendingImages) {
    blocks.push({
      type: 'image',
      source: { type: 'base64', media_type: img.mediaType, data: img.b64 },
    });
  }
  if (text) blocks.push({ type: 'text', text });

  state.conversation.push({ role: 'user', content: blocks });

  const userNode = document.createElement('div');
  if (state.pendingImages.length) {
    const imgsWrap = document.createElement('div');
    imgsWrap.className = 'msg-images';
    state.pendingImages.forEach((img) => {
      const i = document.createElement('img');
      i.src = img.dataUrl;
      imgsWrap.appendChild(i);
    });
    userNode.appendChild(imgsWrap);
  }
  if (text) {
    const p = document.createElement('div');
    p.textContent = text;
    userNode.appendChild(p);
  }
  appendMessage('user', userNode, 'you');

  $('#input-text').value = '';
  state.pendingImages = [];
  renderImagePreviews();

  await runAgent();
}

function renderTemplate() {
  let out = state.template;

  out = out.replace(/\{\{#if\s+(\w+)\}\}([\s\S]*?)\{\{\/if\}\}/g, (_, key, body) => {
    const val = state.sheet[key];
    return val ? body : '';
  });

  out = out.replace(/\{\{#each\s+notes\}\}([\s\S]*?)\{\{\/each\}\}/g, (_, body) => {
    if (!state.notes.length) return '';
    return state.notes.map((n) => body.replace(/\{\{this\}\}/g, n)).join('');
  });

  out = out.replace(/\{\{(\w+)\}\}/g, (_, key) => {
    return state.sheet[key] || `(à compléter: ${key})`;
  });

  return out.trim();
}

function handleGenerate() {
  if (!state.template) {
    appendMessage('system', '// template introuvable, tente de recharger la page', 'system');
    return;
  }
  const out = renderTemplate();
  $('#output-text').textContent = out;
  $('#output-block').classList.remove('hidden');
  $('#output-text').scrollTop = 0;
}

function handleCopy() {
  const txt = $('#output-text').textContent;
  navigator.clipboard.writeText(txt).then(
    () => {
      const btn = $('#copy-btn');
      const old = btn.textContent;
      btn.textContent = 'OK ✓';
      setTimeout(() => (btn.textContent = old), 1200);
    },
    () => alert('clipboard refusé'),
  );
}

function handleDownload() {
  const txt = $('#output-text').textContent;
  const name = (state.sheet.character_name || 'character').toLowerCase().replace(/[^a-z0-9]+/g, '_');
  const blob = new Blob([txt], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${name}_sheet.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function openModal() {
  $('#key-input').value = state.apiKey;
  $('#key-modal').classList.remove('hidden');
  setTimeout(() => $('#key-input').focus(), 50);
}

function closeModal() {
  $('#key-modal').classList.add('hidden');
}

function handleSaveKey() {
  const v = $('#key-input').value.trim();
  if (!v) return;
  state.apiKey = v;
  localStorage.setItem('anthropic_api_key', v);
  setStatus('online', 'online');
  closeModal();
}

function handleClearKey() {
  state.apiKey = '';
  localStorage.removeItem('anthropic_api_key');
  $('#key-input').value = '';
  setStatus('offline');
}

function handleReset() {
  if (!confirm('reset session — tu perds la conversation et la fiche. ok ?')) return;
  state.conversation = [];
  state.sheet = Object.fromEntries(SCALAR_KEYS.map((k) => [k, '']));
  state.notes = [];
  state.pendingImages = [];
  $('#chat-log').innerHTML = `
    <div class="msg msg-system">
      <div class="msg-body">
        <p>// session reset.</p>
        <p>// décris-moi un nouveau perso.</p>
      </div>
    </div>`;
  $('#output-block').classList.add('hidden');
  $('#output-text').textContent = '';
  renderSheet();
  renderImagePreviews();
}

async function loadTemplate() {
  try {
    const res = await fetch(TEMPLATE_URL);
    if (!res.ok) throw new Error(res.statusText);
    state.template = await res.text();
  } catch (e) {
    appendMessage('system', `// impossible de charger le template (${e.message}). la génération finale ne marchera pas tant que le fichier n'est pas servi.`, 'system');
  }
}

function setupDragDrop() {
  const composer = $('#composer');
  ['dragenter', 'dragover'].forEach((ev) =>
    composer.addEventListener(ev, (e) => {
      e.preventDefault();
      composer.classList.add('dragging');
    }),
  );
  ['dragleave', 'drop'].forEach((ev) =>
    composer.addEventListener(ev, (e) => {
      e.preventDefault();
      composer.classList.remove('dragging');
    }),
  );
  composer.addEventListener('drop', async (e) => {
    const files = Array.from(e.dataTransfer.files || []);
    for (const f of files) await addImageFile(f);
  });

  document.addEventListener('paste', async (e) => {
    const items = Array.from(e.clipboardData?.items || []);
    for (const it of items) {
      if (it.type.startsWith('image/')) {
        const f = it.getAsFile();
        if (f) await addImageFile(f);
      }
    }
  });
}

function init() {
  $('#model-select').value = state.model;
  $('#model-select').addEventListener('change', (e) => {
    state.model = e.target.value;
    localStorage.setItem('claude_model', state.model);
  });

  $('#key-btn').addEventListener('click', openModal);
  $('#key-save').addEventListener('click', handleSaveKey);
  $('#key-clear').addEventListener('click', handleClearKey);
  $$('[data-close-modal]').forEach((b) => b.addEventListener('click', closeModal));
  $('#key-modal').addEventListener('click', (e) => {
    if (e.target === $('#key-modal')) closeModal();
  });

  $('#send-btn').addEventListener('click', handleSend);
  $('#input-text').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSend();
    }
  });

  $('#input-image').addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    for (const f of files) await addImageFile(f);
    e.target.value = '';
  });

  $('#generate-btn').addEventListener('click', handleGenerate);
  $('#copy-btn').addEventListener('click', handleCopy);
  $('#download-btn').addEventListener('click', handleDownload);
  $('#reset-btn').addEventListener('click', handleReset);

  setupDragDrop();
  renderSheet();
  loadTemplate();

  if (state.apiKey) setStatus('online', 'online');
  else setStatus('offline');
}

document.addEventListener('DOMContentLoaded', init);
