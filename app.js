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

PRINCIPES UX:
- L'utilisateur déteste taper. Pose tes questions via le tool "request_input" qui affiche des widgets cliquables (chips, slider, color picker) chaque fois que c'est possible.
- N'utilise une question ouverte (sans tool) QUE si la réponse est forcément descriptive et libre (ex: décris la cicatrice, le style de cheveux, etc.).
- UNE seule question / UN seul widget à la fois. Pas de surcharge.
- Tu peux pré-remplir plusieurs champs d'un coup avec "update_character_sheet" si l'utilisateur t'a déjà donné l'info ou si tu extrapoles depuis une image.
- Tu réponds toujours dans la langue de l'utilisateur (par défaut français).
- Sois concis. Pas de blabla, pas de "Bien sûr !".

QUEL WIDGET POUR QUEL CHAMP (recommandations):
- gender → chips ["femme", "homme", "non-binaire", "androgyne", "autre"]
- age → slider min=0 max=100 unit="ans"
- height → slider min=140 max=210 unit="cm"
- body_type → chips ["mince", "athlétique", "musclé", "rond", "élancé", "massif"]
- ethnicity → chips avec une liste raisonnable + allow_custom
- role → chips selon contexte (ex: "soldat", "détective", "magicien", "noble", "marchand", "rebelle")
- archetype → chips ["héros", "anti-héros", "mentor", "trickster", "ombre", "innocent", "explorateur", "rebelle"]
- emotional_range → chips ["stoïque", "expressif", "explosif", "réservé", "chaleureux", "froid"]
- speech_style → chips ["soutenu", "familier", "argot", "archaïque", "laconique", "verbeux"]
- color_palette → color_palette (le widget gère plusieurs swatches)
- accessories, props, top/bottom/shoes, personality_traits, core_theme, behavior_notes, character_name, alias, default_outfit_description → questions ouvertes en chat (texte libre), MAIS si tu peux raisonnablement proposer 4-6 directions, utilise chips avec allow_custom=true.

GESTION DES IMAGES:
- Quand l'utilisateur drop une image, observe-la et extrais une description. Range-la via "update_character_sheet" dans le bon champ (accessoires si c'est un objet, top/bottom si c'est un vêtement, color_palette si c'est une moodboard de couleurs, etc.). Demande à l'utilisateur dans quel champ ranger si ce n'est pas évident.

CHAMPS DISPONIBLES (utilise EXACTEMENT ces noms):
- CORE: character_name, alias, gender, age, height, body_type, ethnicity, role, archetype
- PSYCHE: personality_traits, core_theme, emotional_range, behavior_notes, speech_style
- OUTFIT: default_outfit_description, top_description, bottom_description, shoes_description, accessories
- EXTRA: color_palette, props
- notes: utilise add_note pour ajouter une annotation visuelle (ex: "manches retroussées").

ORDRE SUGGÉRÉ (mais adapte-toi à l'utilisateur):
1. nom + alias (chat libre) 2. gender (chips) 3. age (slider) 4. role (chips) 5. archetype (chips) 6. body_type (chips) 7. height (slider) 8. ethnicity (chips) 9. personality (chat) 10. outfit en bloc puis détaillé 11. color_palette (color picker) 12. accessoires (chat) 13. notes finales.

Quand TOUS les champs CORE + au moins default_outfit_description + accessories sont remplis, dis à l'utilisateur que la fiche est prête à générer.`;

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
  {
    name: 'request_input',
    description:
      "Affiche un widget interactif (chips à cliquer, slider, color picker) pour que l'utilisateur réponde sans taper. Préfère TOUJOURS ce tool aux questions ouvertes quand le champ a un set fini de réponses raisonnables. Après l'appel, ATTENDS la réponse — n'appelle pas d'autre tool dans la même réponse.",
    input_schema: {
      type: 'object',
      properties: {
        field: {
          type: 'string',
          description: 'Nom du champ ciblé (ex: gender, age, role, color_palette).',
          enum: SCALAR_KEYS,
        },
        question: { type: 'string', description: "Question affichée au-dessus du widget. Concise." },
        input_type: {
          type: 'string',
          enum: ['chips', 'multi_chips', 'slider', 'color_palette', 'text'],
          description: "chips=choix unique, multi_chips=plusieurs choix, slider=numérique, color_palette=picker de couleurs, text=zone de texte courte.",
        },
        options: {
          type: 'array',
          items: { type: 'string' },
          description: 'Options pour chips/multi_chips (4-8 max).',
        },
        allow_custom: {
          type: 'boolean',
          description: "Si true, ajoute une option 'autre…' qui ouvre un champ texte. Recommandé pour chips quand la liste n'est pas exhaustive.",
        },
        slider_min: { type: 'number' },
        slider_max: { type: 'number' },
        slider_default: { type: 'number' },
        slider_unit: { type: 'string', description: "Suffixe (ex: 'ans', 'cm')." },
        placeholder: { type: 'string', description: "Pour input_type=text, placeholder du champ." },
      },
      required: ['field', 'question', 'input_type'],
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

function renderInputWidget(toolUse) {
  const { field, question, input_type, options, allow_custom, slider_min, slider_max, slider_default, slider_unit, placeholder } = toolUse.input || {};

  const log = $('#chat-log');
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-widget';
  wrap.dataset.field = field;

  const meta = document.createElement('div');
  meta.className = 'msg-meta';
  meta.textContent = `claude → ${field}`;
  wrap.appendChild(meta);

  const body = document.createElement('div');
  body.className = 'msg-body widget';
  if (question) {
    const q = document.createElement('div');
    q.className = 'widget-question';
    q.textContent = question;
    body.appendChild(q);
  }

  const widgetArea = document.createElement('div');
  widgetArea.className = 'widget-area';

  const lock = (value) => {
    wrap.classList.add('widget-locked');
    widgetArea.querySelectorAll('button, input, select').forEach((el) => (el.disabled = true));
    const result = document.createElement('div');
    result.className = 'widget-result';
    result.textContent = `→ ${value}`;
    widgetArea.appendChild(result);
  };

  if (input_type === 'chips' || input_type === 'multi_chips') {
    const isMulti = input_type === 'multi_chips';
    const opts = Array.isArray(options) ? [...options] : [];
    const picks = new Set();

    const chipsRow = document.createElement('div');
    chipsRow.className = 'chips-row';

    const renderChip = (label) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip';
      chip.textContent = label;
      chip.onclick = () => {
        if (isMulti) {
          if (picks.has(label)) {
            picks.delete(label);
            chip.classList.remove('chip-selected');
          } else {
            picks.add(label);
            chip.classList.add('chip-selected');
          }
        } else {
          submitWidget(field, label, lock);
        }
      };
      chipsRow.appendChild(chip);
    };

    opts.forEach(renderChip);

    if (allow_custom) {
      const otherChip = document.createElement('button');
      otherChip.type = 'button';
      otherChip.className = 'chip chip-other';
      otherChip.textContent = '+ autre…';
      otherChip.onclick = () => {
        otherChip.remove();
        const customRow = document.createElement('div');
        customRow.className = 'chips-custom';
        const inp = document.createElement('input');
        inp.className = 'input';
        inp.placeholder = 'tape ta réponse…';
        inp.onkeydown = (e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            submitBtn.click();
          }
        };
        const submitBtn = document.createElement('button');
        submitBtn.className = 'btn btn-primary btn-sm';
        submitBtn.textContent = 'OK';
        submitBtn.onclick = () => {
          const v = inp.value.trim();
          if (v) submitWidget(field, v, lock);
        };
        customRow.appendChild(inp);
        customRow.appendChild(submitBtn);
        widgetArea.appendChild(customRow);
        inp.focus();
      };
      chipsRow.appendChild(otherChip);
    }

    widgetArea.appendChild(chipsRow);

    if (isMulti) {
      const confirmBtn = document.createElement('button');
      confirmBtn.className = 'btn btn-primary btn-sm widget-confirm';
      confirmBtn.textContent = '✓ valider';
      confirmBtn.onclick = () => {
        if (picks.size === 0) return;
        submitWidget(field, [...picks].join(', '), lock);
      };
      widgetArea.appendChild(confirmBtn);
    }
  } else if (input_type === 'slider') {
    const min = Number.isFinite(slider_min) ? slider_min : 0;
    const max = Number.isFinite(slider_max) ? slider_max : 100;
    const def = Number.isFinite(slider_default) ? slider_default : Math.round((min + max) / 2);
    const unit = slider_unit || '';

    const sliderWrap = document.createElement('div');
    sliderWrap.className = 'slider-wrap';
    const valLabel = document.createElement('div');
    valLabel.className = 'slider-value';
    valLabel.textContent = `${def} ${unit}`.trim();

    const range = document.createElement('input');
    range.type = 'range';
    range.min = min;
    range.max = max;
    range.value = def;
    range.className = 'slider';
    range.oninput = () => (valLabel.textContent = `${range.value} ${unit}`.trim());

    const ticks = document.createElement('div');
    ticks.className = 'slider-ticks';
    ticks.innerHTML = `<span>${min}</span><span>${max}</span>`;

    const confirmBtn = document.createElement('button');
    confirmBtn.className = 'btn btn-primary btn-sm widget-confirm';
    confirmBtn.textContent = '✓ valider';
    confirmBtn.onclick = () => {
      const v = `${range.value} ${unit}`.trim();
      submitWidget(field, v, lock);
    };

    sliderWrap.appendChild(valLabel);
    sliderWrap.appendChild(range);
    sliderWrap.appendChild(ticks);
    widgetArea.appendChild(sliderWrap);
    widgetArea.appendChild(confirmBtn);
  } else if (input_type === 'color_palette') {
    const colors = [];

    const swatchesRow = document.createElement('div');
    swatchesRow.className = 'swatches-row';

    const renderSwatches = () => {
      swatchesRow.innerHTML = '';
      colors.forEach((c, i) => {
        const sw = document.createElement('div');
        sw.className = 'swatch';
        sw.style.background = c;
        sw.title = c;
        const lbl = document.createElement('span');
        lbl.textContent = c;
        const x = document.createElement('button');
        x.textContent = '×';
        x.className = 'swatch-x';
        x.onclick = () => {
          colors.splice(i, 1);
          renderSwatches();
        };
        sw.appendChild(lbl);
        sw.appendChild(x);
        swatchesRow.appendChild(sw);
      });
    };

    const pickerRow = document.createElement('div');
    pickerRow.className = 'picker-row';
    const colorInp = document.createElement('input');
    colorInp.type = 'color';
    colorInp.value = '#1ed8e8';
    colorInp.className = 'color-picker';
    const addBtn = document.createElement('button');
    addBtn.className = 'btn btn-ghost btn-sm';
    addBtn.textContent = '+ ajouter';
    addBtn.onclick = () => {
      colors.push(colorInp.value);
      renderSwatches();
    };
    pickerRow.appendChild(colorInp);
    pickerRow.appendChild(addBtn);

    const confirmBtn = document.createElement('button');
    confirmBtn.className = 'btn btn-primary btn-sm widget-confirm';
    confirmBtn.textContent = '✓ valider la palette';
    confirmBtn.onclick = () => {
      if (colors.length === 0) return;
      submitWidget(field, colors.join(', '), lock);
    };

    widgetArea.appendChild(swatchesRow);
    widgetArea.appendChild(pickerRow);
    widgetArea.appendChild(confirmBtn);
  } else {
    // text fallback
    const row = document.createElement('div');
    row.className = 'chips-custom';
    const inp = document.createElement('input');
    inp.className = 'input';
    inp.placeholder = placeholder || 'tape ta réponse…';
    inp.onkeydown = (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        sub.click();
      }
    };
    const sub = document.createElement('button');
    sub.className = 'btn btn-primary btn-sm';
    sub.textContent = 'OK';
    sub.onclick = () => {
      const v = inp.value.trim();
      if (v) submitWidget(field, v, lock);
    };
    row.appendChild(inp);
    row.appendChild(sub);
    widgetArea.appendChild(row);
    setTimeout(() => inp.focus(), 50);
  }

  body.appendChild(widgetArea);
  wrap.appendChild(body);
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
}

async function submitWidget(field, value, lockFn) {
  if (state.busy) return;
  if (SCALAR_KEYS.includes(field)) {
    state.sheet[field] = value;
    renderSheet();
    flashField(field);
  }
  lockFn(value);
  state.conversation.push({
    role: 'user',
    content: `[via widget] ${field} = ${value}`,
  });
  await runAgent();
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
  if (name === 'request_input') {
    return 'widget affiché — en attente de la réponse utilisateur via UI';
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

      removeThinking();
      const text = response.content.filter((b) => b.type === 'text').map((b) => b.text).join('\n').trim();
      if (text) appendMessage('assistant', text, 'claude');

      const toolResults = [];
      let waitingForWidget = false;
      for (const tu of toolUses) {
        const out = applyToolCall(tu.name, tu.input);
        toolResults.push({ type: 'tool_result', tool_use_id: tu.id, content: out });
        if (tu.name === 'request_input') {
          renderInputWidget(tu);
          waitingForWidget = true;
        } else {
          appendMessage('tool', `▸ ${tu.name} → ${out}`, 'tool');
        }
      }
      state.conversation.push({ role: 'user', content: toolResults });

      if (waitingForWidget) {
        setStatus('await input', 'thinking');
        return;
      }
      if (response.stop_reason !== 'tool_use') break;
      appendThinking();
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
