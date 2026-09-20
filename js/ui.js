'use strict';

import { SHEET_FIELDS, SECTIONS, SCALAR_KEYS, STAGES, LOTS } from './schema.js';

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

/* ── la console d'accueil ───────────────────────────────── */

/* Quelques pictogrammes au trait, dans l'esprit des marques de la
   pile NL. Rien de figuratif : ils servent de repère, pas d'illustration. */
const ICONS = {
  identity: 'M4 18c0-3.3 2.7-6 6-6s6 2.7 6 6M10 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6',
  face:     'M3 10a7 7 0 1 0 14 0 7 7 0 0 0-14 0M7 9v1M13 9v1M7 13c1.8 1.4 4.2 1.4 6 0',
  costume:  'M7 3 4 6v11h12V6l-3-3M7 3c0 1.7 1.3 3 3 3s3-1.3 3-3',
  sheet:    'M3 3h5v14H3zM9.5 3h5v14h-5zM16 3h1v14h-1',
  views:    'M3 3h6v6H3zM11 3h6v6h-6zM3 11h6v6H3zM11 11h6v6h-6',
  mesh:     'M10 2 3 6v8l7 4 7-4V6zM3 6l7 4 7-4M10 10v8',
  rig:      'M10 3v5m0 0-4 4m4-4 4 4m-8 4v-4m8 4v-4M10 3a1 1 0 1 0 0-.01',
  anim:     'M2 10h3l2-6 3 12 3-9 2 3h3',
};

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 20 20');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', ICONS[name] || ICONS.identity);
  path.setAttribute('stroke-linecap', 'round');
  path.setAttribute('stroke-linejoin', 'round');
  svg.appendChild(path);
  const box = el('span', 'ico');
  box.appendChild(svg);
  return box;
}

const CORAL = ['t3', 't2', 't1'];
const VERD = ['lk', 'lk2'];

function renderConsole(progress = {}) {
  const host = $('#console-sections');
  host.innerHTML = '';

  const lots = [...new Set(STAGES.map((s) => s.lot))];

  lots.forEach((lot, lotIndex) => {
    const rows = STAGES.filter((s) => s.lot === lot);

    const sect = el('section', 'sect');
    const head = el('div', 'sect-head');
    head.appendChild(el('span', 'k', lot.replace(' ', '\u2014')));
    head.appendChild(el('h2', null, LOTS[lot] || lot));
    head.appendChild(el('span', 'cnt', `${rows.length} étage${rows.length > 1 ? 's' : ''}`));
    sect.appendChild(head);

    rows.forEach((st, i) => {
      const bar = el('button', 'slab');
      bar.type = 'button';
      // Le lot de tête prend la famille corail, l'aval reste en vert :
      // la couleur dit où on travaille, pas ce qui est fini.
      bar.classList.add(lotIndex === 0 ? CORAL[i % CORAL.length] : VERD[i % VERD.length]);

      bar.appendChild(icon(st.id));

      const body = el('div', 'body');
      const line = el('div', 'line');
      line.appendChild(el('span', 'ref', st.ref.replace('-', '\u2014')));
      line.appendChild(el('span', 'nm', st.name));
      body.appendChild(line);
      body.appendChild(el('span', 'sub', st.ready ? st.sub : `verrouillé · ${st.needs}`));
      bar.appendChild(body);

      bar.appendChild(el('span', 'dots'));
      bar.appendChild(el('span', 'go', st.ready ? 'Ouvrir' : (progress[st.id] === 'done' ? 'Fait' : 'Voir')));

      bar.onclick = () => handlers.onStagePick(st.id);
      sect.appendChild(bar);
    });

    host.appendChild(sect);
  });

  const ready = STAGES.filter((s) => s.ready).length;
  $('#stat-n').textContent = String(ready).padStart(2, '0');
  $('#stat-foot').textContent = `sur ${STAGES.length} étages · ${settingsNode()}`;
}

/* Le pied de la carte de compte dit sur quel nœud on travaille ; la
   valeur est posée par l'application, qui seule connaît les réglages. */
let _node = 'hors ligne';
function setNode(label) { _node = label; }
function settingsNode() { return _node; }

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
  $, $$, el, setHandlers, toast, setStatus, setNode,
  renderConsole, renderRack, renderSheet, flashField,
  appendMessage, appendThinking, removeThinking, clearChat,
  renderInputWidget, renderPreviews,
};
