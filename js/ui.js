'use strict';

import { SHEET_FIELDS, SECTIONS, SCALAR_KEYS, STAGES } from './schema.js';

/* ============================================================
   Tout ce qui touche au DOM. La logique ne connaît pas le HTML,
   elle appelle ces fonctions ; l'affichage ne connaît pas le
   modèle, il remonte les réponses par les rappels enregistrés.
   ============================================================ */

const $  = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

const handlers = {
  onWidgetSubmit: async () => {},
  onStagePick: () => {},
};
function setHandlers(h) { Object.assign(handlers, h); }

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

/* ── bandeau flottant ───────────────────────────────────── */

let toastTimer = null;
function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), ms);
}

/* ── état du moteur, en haut à droite ───────────────────── */

function setStatus(label, cls) {
  const pill = $('#engine-pill');
  $('#engine-text').textContent = label;
  pill.className = 'pill' + (cls ? ` ${cls}` : '');
}

/* ── le rack des étages ─────────────────────────────────── */

function renderRack(activeId, progress = {}) {
  const list = $('#rack');
  list.innerHTML = '';

  let lot = null;
  for (const st of STAGES) {
    if (st.lot !== lot) {
      lot = st.lot;
      const sep = el('li', 'serie');
      sep.appendChild(el('span', null, lot));
      const n = STAGES.filter((s) => s.lot === lot).length;
      sep.appendChild(el('span', 'n', `${n} étage${n > 1 ? 's' : ''}`));
      list.appendChild(sep);
    }

    const li = el('li');
    const btn = el('button', 'item');
    btn.type = 'button';
    if (st.id === activeId) btn.classList.add('sel');
    if (!st.ready) btn.classList.add('locked');
    if (progress[st.id] === 'done') btn.classList.add('done');

    const dot = el('span', 'st');
    if (progress[st.id]) dot.classList.add(progress[st.id] === 'done' ? 'ok' : progress[st.id]);
    btn.appendChild(dot);

    const txt = el('span', 'txt');
    txt.appendChild(el('span', 'ref', st.ref));
    txt.appendChild(el('span', 'nm', st.name));
    txt.appendChild(el('span', 'sub', st.ready ? st.sub : `verrouillé — ${st.needs}`));
    btn.appendChild(txt);
    btn.appendChild(el('span', 'dots'));

    btn.onclick = () => handlers.onStagePick(st.id);
    li.appendChild(btn);
    list.appendChild(li);
  }
}

/* ── la fiche d'identité ────────────────────────────────── */

function renderSheet(sheet, notes) {
  const container = $('#sheet-fields');
  container.innerHTML = '';

  for (const sectionName of SECTIONS) {
    const section = el('div', 'sheet-section');
    section.appendChild(el('div', 'sheet-section-title', sectionName));
    for (const f of SHEET_FIELDS.filter((x) => x.section === sectionName)) {
      const v = sheet[f.key];
      const row = el('div', 'sheet-row');
      row.dataset.key = f.key;
      row.appendChild(el('div', 'sheet-key', f.label));
      const val = el('div', `sheet-val ${v ? 'filled' : 'empty'}`, v || '');
      row.appendChild(val);
      section.appendChild(row);
    }
    container.appendChild(section);
  }

  if (notes.length) {
    const section = el('div', 'sheet-section');
    section.appendChild(el('div', 'sheet-section-title', `NOTES (${notes.length})`));
    notes.forEach((n, i) => {
      const row = el('div', 'sheet-row');
      row.appendChild(el('div', 'sheet-key', `#${i + 1}`));
      row.appendChild(el('div', 'sheet-val filled', n));
      section.appendChild(row);
    });
    container.appendChild(section);
  }

  const filled = SCALAR_KEYS.filter((k) => sheet[k]).length;
  $('#sheet-progress').textContent = `${filled}/${SCALAR_KEYS.length}`;
  $('#sheet-meter').style.width = `${Math.round((filled / SCALAR_KEYS.length) * 100)}%`;
}

function flashField(key) {
  const cell = $(`.sheet-row[data-key="${key}"] .sheet-val`);
  if (!cell) return;
  cell.classList.remove('flash');
  void cell.offsetWidth;
  cell.classList.add('flash');
}

/* ── la conversation ────────────────────────────────────── */

function appendMessage(role, content, meta) {
  const log = $('#chat-log');
  const wrap = el('div', `msg msg-${role}`);
  if (meta) wrap.appendChild(el('div', 'msg-meta', meta));

  const body = el('div', 'msg-body');
  if (typeof content === 'string') body.textContent = content;
  else if (Array.isArray(content)) content.forEach((n) => body.appendChild(n));
  else body.appendChild(content);

  wrap.appendChild(body);
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return wrap;
}

function appendThinking() {
  const log = $('#chat-log');
  const wrap = el('div', 'msg msg-assistant');
  wrap.dataset.thinking = '1';
  wrap.appendChild(el('div', 'msg-meta', 'modèle'));
  const body = el('div', 'msg-body');
  const dots = el('div', 'thinking');
  dots.append(el('span'), el('span'), el('span'));
  body.appendChild(dots);
  wrap.appendChild(body);
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return wrap;
}

function removeThinking() {
  $$('.msg[data-thinking]').forEach((n) => n.remove());
}

function clearChat() { $('#chat-log').innerHTML = ''; }

/* ── les widgets interactifs ────────────────────────────── */

function renderInputWidget(toolUse) {
  const {
    field, question, input_type, options, allow_custom,
    slider_min, slider_max, slider_default, slider_unit, placeholder,
  } = toolUse.input || {};

  const log = $('#chat-log');
  const wrap = el('div', 'msg msg-widget');
  wrap.dataset.field = field;
  wrap.appendChild(el('div', 'msg-meta', `modèle → ${field}`));

  const body = el('div', 'msg-body');
  if (question) body.appendChild(el('div', 'widget-question', question));

  const area = el('div', 'widget-area');

  const lock = (value) => {
    wrap.classList.add('widget-locked');
    $$('button, input, select', area).forEach((n) => (n.disabled = true));
    area.appendChild(el('div', 'widget-result', `→ ${value}`));
  };
  const submit = (value) => handlers.onWidgetSubmit(field, value, lock);

  if (input_type === 'chips' || input_type === 'multi_chips') {
    const isMulti = input_type === 'multi_chips';
    const picks = new Set();
    const row = el('div', 'chips-row');

    for (const label of Array.isArray(options) ? options : []) {
      const chip = el('button', 'chip', label);
      chip.type = 'button';
      chip.onclick = () => {
        if (!isMulti) return submit(label);
        if (picks.has(label)) { picks.delete(label); chip.classList.remove('chip-selected'); }
        else { picks.add(label); chip.classList.add('chip-selected'); }
      };
      row.appendChild(chip);
    }

    if (allow_custom) {
      const other = el('button', 'chip chip-other', '+ autre…');
      other.type = 'button';
      other.onclick = () => {
        other.remove();
        const custom = el('div', 'chips-custom');
        const inp = el('input', 'fld');
        inp.placeholder = 'tape ta réponse…';
        const ok = el('button', 'tb go sm', 'OK');
        ok.onclick = () => { const v = inp.value.trim(); if (v) submit(v); };
        inp.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); ok.click(); } };
        custom.append(inp, ok);
        area.appendChild(custom);
        inp.focus();
      };
      row.appendChild(other);
    }

    area.appendChild(row);

    if (isMulti) {
      const confirm = el('button', 'tb go sm widget-confirm', '✓ valider');
      confirm.onclick = () => { if (picks.size) submit([...picks].join(', ')); };
      area.appendChild(confirm);
    }

  } else if (input_type === 'slider') {
    const min = Number.isFinite(slider_min) ? slider_min : 0;
    const max = Number.isFinite(slider_max) ? slider_max : 100;
    const def = Number.isFinite(slider_default) ? slider_default : Math.round((min + max) / 2);
    const unit = slider_unit || '';

    const box = el('div', 'slider-wrap');
    const readout = el('div', 'slider-value', `${def} ${unit}`.trim());
    const range = el('input', 'slider');
    range.type = 'range'; range.min = min; range.max = max; range.value = def;
    range.oninput = () => (readout.textContent = `${range.value} ${unit}`.trim());
    const ticks = el('div', 'slider-ticks');
    ticks.append(el('span', null, String(min)), el('span', null, String(max)));
    box.append(readout, range, ticks);

    const confirm = el('button', 'tb go sm widget-confirm', '✓ valider');
    confirm.onclick = () => submit(`${range.value} ${unit}`.trim());
    area.append(box, confirm);

  } else if (input_type === 'color_palette') {
    const colors = [];
    const row = el('div', 'swatches-row');

    const paint = () => {
      row.innerHTML = '';
      colors.forEach((c, i) => {
        const sw = el('div', 'swatch');
        sw.style.background = c;
        sw.title = c;
        sw.appendChild(el('span', null, c));
        const x = el('button', 'swatch-x', '×');
        x.onclick = () => { colors.splice(i, 1); paint(); };
        sw.appendChild(x);
        row.appendChild(sw);
      });
    };

    const picker = el('div', 'picker-row');
    const input = el('input', 'color-picker');
    input.type = 'color';
    input.value = '#e0674a';
    const add = el('button', 'tb ghost sm', '+ ajouter');
    add.onclick = () => { colors.push(input.value); paint(); };
    picker.append(input, add);

    const confirm = el('button', 'tb go sm widget-confirm', '✓ valider la palette');
    confirm.onclick = () => { if (colors.length) submit(colors.join(', ')); };
    area.append(row, picker, confirm);

  } else {
    const row = el('div', 'chips-custom');
    const inp = el('input', 'fld');
    inp.placeholder = placeholder || 'tape ta réponse…';
    const ok = el('button', 'tb go sm', 'OK');
    ok.onclick = () => { const v = inp.value.trim(); if (v) submit(v); };
    inp.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); ok.click(); } };
    row.append(inp, ok);
    area.appendChild(row);
    setTimeout(() => inp.focus(), 50);
  }

  body.appendChild(area);
  wrap.appendChild(body);
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
}

/* ── vignettes des références en attente ────────────────── */

function renderPreviews(images, onRemove) {
  const wrap = $('#previews');
  wrap.innerHTML = '';
  images.forEach((img, i) => {
    const thumb = el('div', 'preview-thumb');
    thumb.style.backgroundImage = `url("${img.dataUrl}")`;
    thumb.title = img.name || '';
    const x = el('button', null, '×');
    x.title = 'retirer';
    x.onclick = () => onRemove(i);
    thumb.appendChild(x);
    wrap.appendChild(thumb);
  });
}

export {
  $, $$, el, setHandlers, toast, setStatus,
  renderRack, renderSheet, flashField,
  appendMessage, appendThinking, removeThinking, clearChat,
  renderInputWidget, renderPreviews,
};
