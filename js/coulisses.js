'use strict';

/* ============================================================
   Les coulisses.
   La page de Cal, pas celle du designer : pour un personnage,
   tout ce que la chaîne a produit, dans l'ordre des étages —
   chaque candidat avec son moteur, sa graine et le JSON posé à
   côté du fichier, les squelettes, les azimuts demandés et
   mesurés et les graines relancées, les versions de mesh et
   leurs canaux, les rigs, les travaux et leurs journaux, l'état
   de la machine, et le manifeste brut.
   Lecture seule : aucune action ne part d'ici.
   #/<slug>[/<costume>] ouvre un personnage.
   ============================================================ */

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
const base = (rel) => String(rel || '').split('/').pop();
const dirOf = (rel) => String(rel || '').split('/').slice(0, -1).join('/');

const ORTHO = ['front', 'left', 'back', 'right'];
const VIEWS = [...ORTHO, 'threequarter'];
const VIEW_LABEL = { front: 'face', left: 'profil gauche', back: 'dos', right: 'profil droit', threequarter: '3/4' };
const TOLERANCE = 5;
const JOB_STATE = { queued: 'en file', running: 'en cours', done: 'fini', error: 'échec', cancelled: 'annulé' };
const VERDICT = { unseen: 'à regarder', accepted: 'accepté', rejected: 'refusé' };
const MAP_ORDER = ['albedo', 'basecolor', 'normal', 'roughness', 'metallic', 'orm', 'occlusion'];

const state = {
  slug: null,
  costume: null,
  list: [],
  detail: null,          // { character, summary, backends, … }
  sig: '',               // le manifeste tel qu'affiché, pour ne redessiner qu'au changement
  tree: new Map(),       // chemin → { size, mtime }
  truncated: false,
  jobs: [],
  jobsAll: false,        // tous les personnages, ou celui-ci
  system: null,
  open: new Set(),       // blocs dépliés, par clé : ils le restent d'un rendu à l'autre
  texts: {},             // fichiers texte déjà lus, par URL
  logs: {},              // journaux complets des travaux finis, par id
  inline: {},            // objets à montrer en JSON, par clé
};

/* ── réseau ─────────────────────────────────────────────── */

async function api(path) {
  const res = await fetch(path, { headers: { accept: 'application/json' }, cache: 'no-store' });
  let json = null;
  try { json = await res.json(); } catch (_) { /* corps vide */ }
  if (!res.ok) throw new Error(json?.error?.message || `${res.status} ${res.statusText}`);
  return json;
}

function fileUrl(rel) {
  if (!rel) return '';
  const path = `/files/${encodeURIComponent(state.slug)}/${String(rel).split('/').map(encodeURIComponent).join('/')}`;
  // La date du fichier fait la version : une vue relancée change d'URL.
  const f = state.tree.get(rel);
  return f ? `${path}?v=${encodeURIComponent(f.mtime)}` : path;
}

const has = (rel) => !state.tree.size || state.tree.has(rel);
const under = (prefix) => [...state.tree.keys()].filter((p) => p.startsWith(prefix));
const isImg = (rel) => /\.(png|jpe?g|webp)$/i.test(rel);

/* ── mise en forme ──────────────────────────────────────── */

let toastTimer = null;
function toast(msg, ms = 3200) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), ms);
}

function when(at) {
  if (!at) return '—';
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? String(at)
    : d.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function dur(a, b) {
  if (!a) return '';
  const s = Math.max(0, Math.round(((b ? new Date(b) : new Date()) - new Date(a)) / 1000));
  if (Number.isNaN(s)) return '';
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${Math.floor(s / 3600)} h ${Math.floor(s / 60) % 60} min`;
}

function bytes(n) {
  if (n == null) return '—';
  if (n < 1024) return `${n} o`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1).replace('.', ',')} ko`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1).replace('.', ',')} Mo`;
  return `${(n / 1024 ** 3).toFixed(2).replace('.', ',')} Go`;
}

const num = (n) => (n == null ? '—' : Number(n).toLocaleString('fr-FR'));
const deg = (v) => (v == null || Number.isNaN(Number(v)) ? '—' : `${Number(v).toFixed(1).replace('.', ',')}°`);
const signed = (v) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1).replace('.', ',')}°`);
const angErr = (m, t) => ((((m - t + 180) % 360) + 360) % 360) - 180;

function size3(v) {
  if (!Array.isArray(v)) return '—';
  return `${v.map((x) => Number(x).toFixed(2).replace('.', ',')).join(' × ')} m`;
}

function initials(name) {
  return String(name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();
}

function ticks(stages) {
  return `<div class="ticks">${(stages || []).map((s) =>
    `<i class="${s.state}" title="${esc(`${s.ref} ${s.label}`)}"></i>`).join('')}</div>`;
}

function kv(pairs) {
  const rows = pairs.filter(([, v]) => v !== undefined && v !== null && v !== '');
  if (!rows.length) return '';
  return `<dl class="kv">${rows.map(([k, v, cls]) =>
    `<dt>${esc(k)}</dt><dd${cls ? ` class="${cls}"` : ''}>${typeof v === 'object' ? esc(JSON.stringify(v)) : esc(v)}</dd>`).join('')}</dl>`;
}

/* Un bloc repliable. Son contenu vient d'un fichier (`src`), d'un
   objet de la page (`inline`) ou du journal d'un travail (`job`) ; il
   n'est lu qu'à l'ouverture, et le reste d'un rendu à l'autre. */
function fold(key, label, { src, inline, job, tall } = {}) {
  if (inline !== undefined) state.inline[key] = inline;
  const open = state.open.has(key);
  let body = '';
  if (open) {
    if (src && src in state.texts) body = state.texts[src];
    else if (inline !== undefined) body = pretty(inline);
    else if (job && state.logs[job]) body = state.logs[job];
  }
  const attrs = [`data-key="${esc(key)}"`, src ? `data-src="${esc(src)}"` : '', inline !== undefined ? 'data-inline' : '',
    job ? `data-job="${esc(job)}"` : ''].join(' ');
  return `<details class="read" ${attrs}${open ? ' open' : ''}><summary>${esc(label)}</summary>` +
    `<pre class="cz-pre${tall ? ' tall' : ''}">${esc(body)}</pre></details>`;
}

function pretty(v) {
  if (typeof v === 'string') {
    try { return JSON.stringify(JSON.parse(v), null, 2); } catch (_) { return v; }
  }
  return JSON.stringify(v, null, 2);
}

async function fill(d) {
  const pre = $('pre', d);
  const key = d.dataset.key;
  try {
    if (d.dataset.src) {
      const src = d.dataset.src;
      if (!(src in state.texts)) {
        const res = await fetch(src);
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        state.texts[src] = pretty(await res.text());
      }
      pre.textContent = state.texts[src];
    } else if (d.hasAttribute('data-inline')) {
      pre.textContent = pretty(state.inline[key]);
    } else if (d.dataset.job) {
      const id = d.dataset.job;
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      const text = [(job.log || []).join('\n'), job.error ? `\n${job.error}` : ''].join('').trim() || '(journal vide)';
      if (!['queued', 'running'].includes(job.status)) state.logs[id] = text;
      pre.textContent = text;
    }
  } catch (e) {
    pre.textContent = `illisible : ${e.message}`;
  }
}

/* Une image : un clic l'agrandit. Un fichier que le dossier n'a plus le dit. */
function img(rel, cap) {
  if (!rel) return '';
  if (!has(rel)) return `<div class="cz-meta"><span class="no">fichier absent : ${esc(rel)}</span></div>`;
  const url = fileUrl(rel);
  return `<img loading="lazy" decoding="async" src="${esc(url)}" alt="${esc(cap || base(rel))}" data-zoom="${
    esc(url)}" data-cap="${esc(cap || rel)}">`;
}

/* Un candidat : l'image, ce qu'on sait de lui, et le JSON posé à côté du fichier. */
function cand(rel, { mark, markCls = '', sel = false, lines = [], sidecar = true, key, more = '' } = {}) {
  const side = rel ? rel.replace(/\.(png|jpe?g|webp)$/i, '.json') : '';
  const extra = sidecar && side && side !== rel && state.tree.has(side)
    ? fold(`side:${key || side}`, `json · ${base(side)}`, { src: fileUrl(side) }) : '';
  return `<div class="cand${sel ? ' sel' : ''}">${img(rel, lines[0] ? `${base(rel)}` : rel)}${
    mark ? `<span class="mark ${markCls}">${esc(mark)}</span>` : ''}
    <div class="cz-meta">${lines.filter(Boolean).map((l) => `<div>${l}</div>`).join('')}</div>${more}${extra}</div>`;
}

const line = (label, value, cls = '') => (value === undefined || value === null || value === ''
  ? '' : `<span class="dim">${esc(label)}</span> <span class="${cls}">${esc(value)}</span>`);
const prose = (text) => (text ? `<span class="prose">${esc(text)}</span>` : '');

function box(id, sec, title, stateLabel, body, cls = '') {
  return `<section class="box ${cls}" id="${esc(id)}"><div class="box-head"><span class="sec">${esc(sec)}</span><h2>${
    esc(title)}</h2><span class="state">${esc(stateLabel || '')}</span></div><div class="box-body">${body}</div></section>`;
}

const empty = (text) => `<p>${esc(text)}</p>`;
const sub = (text) => `<div class="box-sub">${esc(text)}</div>`;

function filesTable(paths, strip = '') {
  if (!paths.length) return '';
  return `<div class="table-wrap"><table class="table"><thead><tr><th>fichier</th><th class="num">taille</th>
    <th>modifié</th></tr></thead><tbody>${paths.map((p) => {
    const f = state.tree.get(p) || {};
    return `<tr><td class="path"><a href="${esc(fileUrl(p))}" target="_blank" rel="noopener">${
      esc(strip && p.startsWith(strip) ? p.slice(strip.length) : p)}</a></td><td class="num">${esc(bytes(f.size))}</td>
      <td>${esc(when(f.mtime))}</td></tr>`;
  }).join('')}</tbody></table></div>`;
}

function imagesOf(paths, cls = 'sm', label = base) {
  const imgs = paths.filter(isImg);
  if (!imgs.length) return '';
  return `<div class="cands cz-grid ${cls}">${imgs.map((p) => cand(p, { lines: [line('', label(p))], sidecar: false }))
    .join('')}</div>`;
}

/* ── les personnages ────────────────────────────────────── */

function renderSide() {
  const items = state.list.map((c) => {
    const face = c.thumb ? `<img loading="lazy" src="${esc(c.thumb)}" alt="">` : `<span class="ph">${esc(initials(c.name))}</span>`;
    return `<a class="cz-who${c.slug === state.slug ? ' on' : ''}" href="#/${encodeURIComponent(c.slug)}">${face}
      <span class="txt"><span class="nm">${esc(c.name)}</span><span class="ref">${esc(c.slug)}</span>${ticks(c.stages)}</span></a>`;
  }).join('');
  const pick = `<label class="field cz-pick"><span class="lbl">Personnage</span><select class="fld" id="pick">${
    state.list.map((c) => `<option value="${esc(c.slug)}"${c.slug === state.slug ? ' selected' : ''}>${esc(c.name)} · ${
      esc(c.slug)}</option>`).join('')}</select></label>`;
  $('#side').innerHTML = `<span class="lbl">${state.list.length} personnage(s)</span>${pick}<div class="cz-list">${items}</div>`;
}

/* ── un personnage ──────────────────────────────────────── */

function renderMain() {
  const d = state.detail;
  if (!d) {
    $('#main').innerHTML = state.list.length ? '<p class="hint">chargement…</p>'
      : '<div class="gate"><p>Aucun personnage dans le dossier des projets.</p></div>';
    return;
  }
  const c = d.character;
  const keys = Object.keys(c.costumes || {});
  if (!keys.includes(state.costume)) state.costume = keys[0] || null;
  const cos = state.costume ? c.costumes[state.costume] : null;
  const k = state.costume;

  const blocks = [
    ['cz-identite', 'Identité'], ['cz-visage', 'Visage'],
    ...(cos ? [['cz-costume', 'Costume'], ['cz-pleinpied', 'Plein pied'], ['cz-apose', 'A-pose'],
      ['cz-planches', 'Planches'], ['cz-vues', 'Vues'], ['cz-mesh', 'Mesh'], ['cz-rig', 'Rig']] : []),
    ['cz-fichiers', 'Fichiers'], ['cz-travaux', 'Travaux'], ['cz-systeme', 'Système'], ['cz-manifeste', 'Manifeste'],
  ];
  const jump = `<nav class="cz-jump" aria-label="sommaire">${blocks.map(([id, t]) =>
    `<a class="tb ghost sm" href="#${id}" data-jump="${id}">${esc(t)}</a>`).join('')}</nav>`;
  const tabs = keys.length
    ? `<div class="tabs">${keys.map((key) => `<a class="tb sm ${key === k ? 'on' : 'ghost'}" href="#/${
      encodeURIComponent(state.slug)}/${encodeURIComponent(key)}">${esc(c.costumes[key].name || key)}</a>`).join('')}</div>` : '';

  $('#main').innerHTML = [
    head(d), jump, identity(c), face(c),
    cos ? `${sub(`costume · ${cos.name || k}`)}${tabs}` : box('cz-costume', 'ST-03', 'Costumes', 'aucun', empty('Aucun costume.')),
    ...(cos ? [costume(c, k, cos), fullbody(k, cos), aposeBox(k, cos), sheets(k, cos), views(k, cos), meshes(k, cos),
      rigs(k, cos)] : []),
    files(), `<div id="cz-jobs">${jobsBox()}</div>`, `<div id="cz-sys">${systemBox()}</div>`,
    box('cz-manifeste', 'RAW', 'Manifeste', 'project.json',
      `<p>Le manifeste tel que le studio le lit, sans rien d'arrangé.</p>${
        fold('manifest', 'project.json', { inline: c, tall: true })}`),
  ].join('');
  hydrate($('#main'));
}

function head(d) {
  const c = d.character;
  const s = d.summary || {};
  const next = s.next ? `suite : ${s.next.action}${s.next.costume ? ` · ${s.next.costume}` : ''}` : 'chaîne au bout';
  const face = c.face?.locked ? `<img class="face" src="${esc(fileUrl(c.face.locked))}" alt="" data-zoom="${
    esc(fileUrl(c.face.locked))}" data-cap="visage verrouillé">` : `<span class="face ph">${esc(initials(c.name))}</span>`;
  return `<div class="cz-head"><div class="perso-head">${face}
    <div class="who"><span class="ref">${esc(c.slug)} · ${esc(c.style)} · créé ${esc(when(c.created_at))}</span>
      <h1>${esc(c.name)}</h1>
      <span class="role">${esc(s.role || '')}${d.busy ? ' · un travail tourne sur ce personnage' : ''}</span>
      ${ticks(s.stages)}<span class="lbl">${esc(next)}${state.truncated ? ' · liste de fichiers tronquée' : ''}</span></div>
    <div class="acts"><a class="tb ghost sm" href="./studio.html#/p/${encodeURIComponent(c.slug)}">Studio</a>
      <button class="tb ghost sm" data-refresh>Relire</button></div></div></div>`;
}

function identity(c) {
  const sheet = c.identity || {};
  const filled = Object.entries(sheet).filter(([, v]) => String(v ?? '').trim());
  const chat = c.identity_chat || [];
  const body = `${filled.length ? kv(filled.map(([key, v]) => [key, v, 'prose'])) : empty('Fiche vide.')}
    ${(c.notes || []).length ? `${sub('notes')}${kv(c.notes.map((n, i) => [`n° ${i + 1}`, n, 'prose']))}` : ''}
    ${c.mhr_identity ? `${sub('identité MHR')}${kv([['source', c.mhr_identity.source], ['figée', when(c.mhr_identity.locked_at)]])}` : ''}
    ${chat.length ? fold('chat', `conversation · ${chat.length} message(s)`, { inline: chat, tall: true }) : ''}
    ${fold('sheet', 'fiche · json', { inline: sheet })}`;
  return box('cz-identite', 'ST-01', 'Identité', `${filled.length} champ(s)`, body);
}

function face(c) {
  const f = c.face || {};
  const cands = f.candidates || [];
  const lockedFrom = f.locked_from;
  const body = `${kv([['brief', f.brief, 'prose'], ['précisions', f.prompt, 'prose'], ['prompt lu (en)', f.prompt_en, 'prose'],
    ['moteur', f.engine], ['verrouillé', f.locked ? `${f.locked} ← ${lockedFrom || '?'} · graine ${f.locked_seed ?? '—'} · ${
      f.locked_backend || '—'} · ${when(f.locked_at)}` : 'non']])}
    ${(f.variations || []).length ? fold('face:variations', `variations lues par le modèle · ${f.variations.length}`,
      { inline: f.variations }) : ''}
    ${(f.refs || []).length ? `${sub('photos source')}${imagesOf(f.refs)}` : ''}
    ${sub(`candidats · ${cands.length}`)}
    ${cands.length ? `<div class="cands">${cands.map((x, i) => cand(x.file, {
      sel: x.file === lockedFrom, mark: x.file === lockedFrom ? 'verrouillé' : x.backend === 'stub' ? 'factice' : '',
      markCls: x.file === lockedFrom ? 'ok' : 'stub', key: `face:${i}`,
      lines: [`<b>n° ${i + 1}</b> · ${esc(base(x.file))}`, line('moteur', `${x.engine || '—'} · ${x.backend || '—'}`),
        line('graine', x.seed), line('le', when(x.at)), prose(x.desc)],
    })).join('')}</div>` : empty('Aucun candidat.')}
    ${state.tree.has('face/contact.png') ? `${sub('planche contact')}${imagesOf(['face/contact.png'], '')}` : ''}`;
  return box('cz-visage', 'ST-02', 'Visage', f.locked ? 'verrouillé' : `${cands.length} candidat(s)`, body);
}

function costume(c, k, cos) {
  const body = `${kv([['clé', k], ['nom', cos.name], ['créé', when(cos.created_at)], ['brief', cos.brief, 'prose'],
    ['brief lu', cos.brief_read === cos.brief ? 'oui, à jour' : cos.brief_read ? 'une version antérieure' : 'non'],
    ['prompt lu par le modèle', cos.prompt, 'prose']])}
    ${cos.outfit ? fold(`cos:${k}:outfit`, 'fiche de la tenue · json', { inline: cos.outfit }) : ''}
    ${(cos.refs || []).length ? `${sub(`images de vêtements · ${cos.refs.length}`)}${imagesOf(cos.refs)}` : ''}`;
  return box('cz-costume', 'ST-03', 'Costume', cos.name || k, body);
}

function fullbody(k, cos) {
  const fb = cos.fullbody || { candidates: [] };
  const from = fb.validated_from;
  const cands = fb.candidates || [];
  const body = `${kv([['validé', fb.validated ? `${fb.validated} ← ${from || '?'} · ${when(fb.validated_at)}` : 'non']])}
    ${cands.length ? `<div class="cands tall">${cands.map((x, i) => cand(x.file, {
      sel: x.file === from, mark: x.file === from ? 'validé' : x.backend === 'stub' ? 'factice' : '',
      markCls: x.file === from ? 'ok' : 'stub', key: `fb:${k}:${i}`,
      lines: [`<b>n° ${i + 1}</b> · ${esc(base(x.file))}`, line('moteur', `${x.engine || '—'} · ${x.backend || '—'}`),
        line('graine', x.seed), line('le', when(x.at))],
    })).join('')}</div>` : empty('Aucun candidat.')}
    ${state.tree.has(`costumes/${k}/fullbody/contact.png`) ? `${sub('planche contact')}${
      imagesOf([`costumes/${k}/fullbody/contact.png`], '')}` : ''}`;
  return box('cz-pleinpied', 'ST-04', 'Plein pied', fb.validated ? 'validé' : `${cands.length} candidat(s)`, body);
}

function aposeBox(k, cos) {
  const ap = cos.apose || { candidates: [] };
  const from = ap.validated_from;
  const cands = ap.candidates || [];
  const skels = under(`costumes/${k}/apose/`).filter((p) => /\/skeleton[^/]*\.png$/.test(p));
  const body = `${kv([['validée', ap.validated ? `${ap.validated} ← ${from || '?'} · ${when(ap.validated_at)}` : 'non'],
    ['squelette', ap.skeleton]])}
    ${skels.length ? `${sub('squelette DWPose, bras à 45°')}${imagesOf(skels)}` : ''}
    ${sub(`candidats · ${cands.length}`)}
    ${cands.length ? `<div class="cands tall">${cands.map((x, i) => cand(x.file, {
      sel: x.file === from, mark: x.file === from ? 'validée' : x.backend === 'stub' ? 'factice' : '',
      markCls: x.file === from ? 'ok' : 'stub', key: `ap:${k}:${i}`,
      lines: [`<b>n° ${i + 1}</b> · ${esc(base(x.file))}`, line('moteur', `${x.engine || '—'} · ${x.backend || '—'}`),
        line('graine', x.seed), line('le', when(x.at))],
    })).join('')}</div>` : empty('Aucun candidat.')}`;
  return box('cz-apose', 'ST-05', 'A-pose', ap.validated ? 'validée' : `${cands.length} candidat(s)`, body);
}

function sheets(k, cos) {
  const list = cos.sheets || [];
  const body = list.length ? list.slice().reverse().map((s) => {
    const dir = dirOf(s.file);
    const inDir = under(`${dir}/`).filter((p) => p.split('/').length === dir.split('/').length + 1);
    const imgs = inDir.filter((p) => isImg(p) && p !== s.file);
    // Un tirage de plusieurs planches Qwen n'écrit son prompt que dans le
    // dossier de la première : on remonte jusqu'à elle.
    let promptTxt = null;
    for (let i = list.indexOf(s); i >= 0 && !promptTxt; i -= 1) {
      const txt = `${dirOf(list[i].file)}/prompt.txt`;
      if (state.tree.has(txt)) promptTxt = txt;
      if (i !== list.indexOf(s) && (list[i].engine || 'h3') !== (s.engine || 'h3')) break;
    }
    return `<div class="cz-ver${cos.sheet === s.id ? ' sel' : ''}">
      <div class="cz-ver-head"><span class="id">${esc(s.id)}</span><span class="lbl">${esc(s.engine || 'h3')} · ${
      esc(s.backend || '—')} · graine ${esc(s.seed ?? '—')}${s.mask_face ? ' · disque sur le visage' : ''}</span>
        <span class="sp"></span><span class="lbl">${cos.sheet === s.id ? 'retenue · ' : ''}${esc(when(s.at))}</span></div>
      <div class="cz-grid cands">${cand(s.file, { lines: [line('planche', base(s.file))], sidecar: false })}</div>
      ${imgs.length ? `${sub('entrées')}${imagesOf(imgs)}` : ''}
      <div class="cz-folds">${promptTxt ? fold(`sheet:${k}:${s.id}:prompt`, `prompt · ${promptTxt.split('/').slice(-2).join('/')}`,
      { src: fileUrl(promptTxt) }) : ''}
      ${fold(`sheet:${k}:${s.id}:json`, 'entrée du manifeste', { inline: s })}</div></div>`;
  }).join('') : empty('Aucune planche.');
  return box('cz-planches', 'ST-05b', 'Planches', list.length ? `${list.length} · retenue ${cos.sheet || '—'}` : 'aucune', body);
}

function views(k, cos) {
  const v = cos.views || { raw: {}, prepared: {} };
  const raw = v.raw || {};
  const names = [...VIEWS.filter((n) => n in raw), ...Object.keys(raw).filter((n) => !VIEWS.includes(n))];
  const rows = names.map((n) => {
    const e = raw[n];
    const limit = ORTHO.includes(n) ? TOLERANCE : 2 * TOLERANCE;
    const measured = e.azimuth_measured ?? e.azimuth_estimated;
    const err = measured != null ? angErr(measured, e.azimuth) : null;
    const m = e.azimuth_measure || {};
    const tries = (m.tries || []).map((t) => {
      const te = angErr(t.azimuth, e.azimuth);
      const cls = [Math.abs(te) <= limit ? 'ok' : 'no', t.seed === e.seed ? 'kept' : ''].join(' ');
      return `<span class="${cls}" title="${t.seed === e.seed ? 'gardée' : 'écartée'}">${esc(t.seed)} → ${esc(deg(t.azimuth))} (${
        esc(signed(te))})</span>`;
    }).join('');
    return `<tr><td>${esc(VIEW_LABEL[n] || n)}</td><td class="num">${esc(deg(e.azimuth))}</td>
      <td class="num">${esc(deg(measured))}</td>
      <td class="num ${err == null ? '' : Math.abs(err) <= limit ? 'ok' : 'no'}">${esc(signed(err))}</td>
      <td class="num">±${limit}°</td><td>${esc(e.seed ?? '—')}</td>
      <td>${tries ? `<div class="cz-tries">${tries}</div>` : '—'}</td>
      <td class="wrap">${esc(e.azimuth_source || '')}${m.engine ? ` · ${esc(m.engine)}, lacet brut ${esc(deg(m.yaw_raw))}` : ''}${
      e.precision_deg != null ? ` · précision ${esc(deg(e.precision_deg))}` : ''}${e.frame != null ? ` · frame ${esc(e.frame)}` : ''}</td>
      <td>${esc(e.backend || '—')}</td><td>${esc(when(e.at))}</td></tr>`;
  }).join('');
  const table = rows ? `<div class="table-wrap"><table class="table"><thead><tr><th>vue</th><th class="num">demandé</th>
    <th class="num">mesuré</th><th class="num">écart</th><th class="num">limite</th><th>graine</th><th>essais (graine → mesuré)</th>
    <th>source</th><th>moteur</th><th>le</th></tr></thead><tbody>${rows}</tbody></table></div>` : '';

  const rawImgs = names.length ? `<div class="cands tall">${names.map((n) => {
    const e = raw[n];
    return cand(e.file, { key: `view:${k}:${n}`, lines: [`<b>${esc(VIEW_LABEL[n] || n)}</b> · ${esc(base(e.file))}`,
      line('demandé', deg(e.azimuth)), line('mesuré', e.azimuth_measured != null ? deg(e.azimuth_measured) : ''),
      line('graine', e.seed)],
      // Le prompt est aussi dans le JSON à côté de la vue, quand il y en a un.
      more: e.prompt && !state.tree.has(e.file.replace(/\.png$/, '.json'))
        ? fold(`view:${k}:${n}:prompt`, 'prompt', { inline: e.prompt }) : '' });
  }).join('')}</div>` : '';

  const skels = under(`costumes/${k}/views/raw/`).filter((p) => /\/skeleton[^/]*\.png$/.test(p));
  const skelLabel = (p) => {
    const m = /skeleton_(\d+)\.png$/.exec(p);
    return m ? `squelette ${Number(m[1])}°` : 'squelette 0°';
  };

  const prepared = v.prepared || {};
  const prepNames = [...VIEWS.filter((n) => n in prepared), ...Object.keys(prepared).filter((n) => !VIEWS.includes(n))];
  const prepImgs = prepNames.length ? `<div class="cands tall">${prepNames.map((n) => {
    const e = prepared[n];
    const mt = e.metrics || {};
    return cand(e.file, { sidecar: false, lines: [`<b>${esc(VIEW_LABEL[n] || n)}</b>`,
      ...Object.entries(mt).map(([key, val]) => line(key, typeof val === 'object' ? JSON.stringify(val) : val))] });
  }).join('')}</div>` : '';
  const prepJson = `costumes/${k}/views/prepared/prep.json`;

  const chk = v.check;
  const chkTable = chk ? `<div class="table-wrap"><table class="table"><thead><tr><th>vue</th><th class="num">cible</th>
    <th class="num">valeur</th><th class="num">écart</th><th>source</th><th>verdict</th></tr></thead><tbody>${
    ORTHO.map((n) => {
      const a = (chk.angles || {})[n];
      const errText = (chk.errors || {})[n];
      if (!a) return `<tr><td>${esc(VIEW_LABEL[n])}</td><td colspan="4">—</td><td class="no">${esc(errText || 'absente')}</td></tr>`;
      const ok = Math.abs(a.error) <= (chk.tolerance ?? TOLERANCE);
      return `<tr><td>${esc(VIEW_LABEL[n])}</td><td class="num">${esc(deg(a.target))}</td><td class="num">${esc(deg(a.value))}</td>
        <td class="num ${ok ? 'ok' : 'no'}">${esc(signed(a.error))}</td><td class="wrap">${esc(a.source)}${
        a.precision_deg != null ? ` · ±${esc(a.precision_deg)}°` : ''}</td>
        <td class="${ok ? 'ok' : 'no'}">${ok ? 'passe' : esc(errText || 'refusé')}</td></tr>`;
    }).join('')}</tbody></table></div>` : '';

  const contacts = [`costumes/${k}/views/contact_raw.png`, `costumes/${k}/views/contact_prepared.png`]
    .filter((p) => state.tree.has(p));
  const bench = under(`costumes/${k}/views/banc/`).filter((p) => p.endsWith('/contact.png'));
  const status = chk ? (chk.ok ? 'contrôle passé' : 'contrôle refusé') : names.length ? `${names.length} vue(s)` : 'aucune';

  const body = names.length ? `${kv([['méthode', v.method], ['delight', v.delighted ? 'oui' : 'non']])}
    ${sub('vues brutes · azimuts demandés et mesurés')}${table}${rawImgs}
    ${skels.length ? `${sub('squelettes par azimut')}${imagesOf(skels, 'sm', skelLabel)}` : ''}
    ${sub(`vues préparées · ${prepNames.length}`)}
    ${v.prep ? kv(Object.entries(v.prep).map(([key, val]) => [key, key === 'at' ? when(val) : val])) : ''}
    ${prepImgs || empty('Pas encore préparées.')}
    ${state.tree.has(prepJson) ? fold(`prep:${k}`, 'rapport de préparation · prep.json', { src: fileUrl(prepJson) }) : ''}
    ${sub(`contrôle d'alignement · ±${chk?.tolerance ?? TOLERANCE}°`)}
    ${chk ? `${kv([['verdict', chk.ok ? 'passe' : 'refusé', chk.ok ? 'ok' : 'no'], ['mesuré', chk.measured ? 'oui' : 'non, angles demandés'],
      ['écart de hauteur avant prep', chk.height_spread_before], ['le', when(chk.at)]])}${chkTable}` : empty('Pas encore contrôlées.')}
    ${contacts.length ? `${sub('planches contact')}${imagesOf(contacts, '')}` : ''}
    ${bench.length ? `${sub('banc des méthodes')}${imagesOf(bench, '', (p) => p.split('/').slice(-2, -1)[0])}` : ''}
    ${fold(`views:${k}:json`, 'entrée du manifeste', { inline: v })}` : empty('Aucune vue.');
  return box('cz-vues', 'ST-06', 'Vues', status, body);
}

function meshes(k, cos) {
  const list = cos.meshes || [];
  const body = list.length ? list.slice().reverse().map((m) => {
    const st = m.stats || {};
    const maps = Object.entries(m.maps || {}).sort(([a], [b]) => {
      const ia = MAP_ORDER.indexOf(a); const ib = MAP_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
    });
    const dirFiles = m.dir ? under(`${m.dir}/`) : [];
    const glb = state.tree.get(m.glb);
    const prev = list.find((x) => x.version === m.parent_version);
    const view = `./viewer.html?src=${encodeURIComponent(fileUrl(m.glb))}`;
    const compare = prev ? `${view}&b=${encodeURIComponent(fileUrl(prev.glb))}&mode=side` : '';
    return `<div class="cz-ver">
      <div class="cz-ver-head"><span class="id">v${esc(m.version)}</span><span class="lbl">${esc(m.engine)} · ${
      esc(m.backend)}${m.single_view ? ' · une vue' : ' · multi-vues'}</span><span class="sp"></span>
        <a class="tb ghost sm" href="${esc(view)}" target="_blank" rel="noopener">Viewer</a>${compare
      ? `<a class="tb ghost sm" href="${esc(compare)}" target="_blank" rel="noopener">Comparer à v${esc(prev.version)}</a>` : ''}</div>
      ${kv([['sommets', num(st.vertices)], ['triangles', num(st.triangles)], ['taille', size3(st.size_m)],
      ['matériaux · textures', st.materials != null ? `${st.materials} · ${st.textures}` : ''],
      ['glb', `${m.glb} · ${bytes(glb?.size)}`], ['parent', m.parent_version ? `v${m.parent_version}` : ''], ['le', when(m.at)]])}
      ${maps.length ? `${sub('canaux PBR')}<div class="cands cz-grid sm">${maps.map(([name, rel]) =>
      cand(rel, { sidecar: false, lines: [`<b>${esc(name)}</b>`, line('', bytes(state.tree.get(rel)?.size))] })).join('')}</div>` : ''}
      <div class="cz-folds">${state.tree.has(`${m.dir}/mesh.json`)
      ? fold(`mesh:${k}:${m.version}:json`, 'mesh.json', { src: fileUrl(`${m.dir}/mesh.json`) }) : ''}
      ${dirFiles.length ? fold(`mesh:${k}:${m.version}:files`, `fichiers · ${dirFiles.length}`, {
      inline: dirFiles.map((p) => `${p.slice(m.dir.length + 1)}  ${bytes(state.tree.get(p).size)}  ${when(state.tree.get(p).mtime)}`).join('\n'),
    }) : ''}
      ${fold(`mesh:${k}:${m.version}:entry`, 'entrée du manifeste', { inline: m })}</div></div>`;
  }).join('') : empty('Aucun mesh.');
  return box('cz-mesh', 'ST-07', 'Mesh 3D', list.length ? `${list.length} version(s)` : 'aucun', body);
}

function rigs(k, cos) {
  const list = cos.rigs || [];
  const body = list.length ? list.slice().reverse().map((r) => {
    const meta = r.meta || {};
    const poses = meta.control_poses || [];
    const mesh = (cos.meshes || []).find((m) => m.version === r.mesh);
    const view = `./viewer.html?src=${encodeURIComponent(fileUrl(r.glb))}`;
    const vsMesh = mesh ? `${view}&b=${encodeURIComponent(fileUrl(mesh.glb))}&mode=side` : '';
    const cls = r.verdict === 'accepted' ? 'ok' : r.verdict === 'rejected' ? 'no' : '';
    return `<div class="cz-ver${r.verdict === 'accepted' ? ' sel' : ''}">
      <div class="cz-ver-head"><span class="id">v${esc(r.version)}</span><span class="lbl">mesh v${esc(r.mesh)} · ${
      esc(r.backend)}</span><span class="sp"></span>
        <a class="tb ghost sm" href="${esc(view)}" target="_blank" rel="noopener">Poses de contrôle</a>${vsMesh
      ? `<a class="tb ghost sm" href="${esc(vsMesh)}" target="_blank" rel="noopener">Face au mesh</a>` : ''}</div>
      ${kv([['verdict', `${VERDICT[r.verdict] || r.verdict}${r.verdict_at ? ` · ${when(r.verdict_at)}` : ''}`, cls],
      ['articulations', meta.joints], ['bras / verticale', meta.arm_angle_deg != null ? `${meta.arm_angle_deg}°` : ''],
      ['hanches', meta.hip_height_m != null ? `${meta.hip_height_m} m` : ''],
      ['poses de contrôle', Array.isArray(poses) ? poses.join(' · ') : Object.keys(poses).join(' · ')],
      ['glb', `${r.glb} · ${bytes(state.tree.get(r.glb)?.size)}`], ['squelette', r.skeleton], ['delta de bind', r.bind_delta],
      ['le', when(r.at)]])}
      ${imagesOf(r.dir ? under(`${r.dir}/`) : [])}
      <div class="cz-folds">${[r.skeleton, r.bind_delta, ...(r.dir ? under(`${r.dir}/`) : [])]
      .filter((p, i, all) => p && /\.(json|txt)$/i.test(p) && state.tree.has(p) && all.indexOf(p) === i)
      .map((p) => fold(`rig:${k}:${r.version}:${base(p)}`, base(p), { src: fileUrl(p) })).join('')}
      ${fold(`rig:${k}:${r.version}:json`, 'entrée du manifeste', { inline: r })}</div></div>`;
  }).join('') : empty('Aucun rig.');
  const last = list.at(-1);
  return box('cz-rig', 'ST-08', 'Rig SOMA', last ? `v${last.version} · ${VERDICT[last.verdict] || last.verdict}` : 'aucun', body);
}

function files() {
  const groups = new Map();
  for (const p of state.tree.keys()) {
    const d = dirOf(p) || '.';
    if (!groups.has(d)) groups.set(d, []);
    groups.get(d).push(p);
  }
  const dirs = [...groups.keys()].sort();
  let total = 0;
  for (const f of state.tree.values()) total += f.size;
  const body = dirs.length ? `<p>Tout le dossier du personnage, dossiers cachés compris (essais de SAM 3D Body, détourages).
    Un clic ouvre le fichier.</p>${dirs.map((d) => {
    const list = groups.get(d);
    const sum = list.reduce((a, p) => a + state.tree.get(p).size, 0);
    const key = `dir:${d}`;
    const open = state.open.has(key);
    return `<details class="read cz-dir" data-key="${esc(key)}"${open ? ' open' : ''}><summary>${esc(d === '.' ? 'racine' : d)}
      <span class="n">${list.length} · ${esc(bytes(sum))}</span></summary>${open ? filesTable(list, d === '.' ? '' : `${d}/`) : ''}</details>`;
  }).join('')}` : empty('Liste des fichiers indisponible.');
  return box('cz-fichiers', 'FS', 'Fichiers', `${state.tree.size} · ${bytes(total)}${state.truncated ? ' · tronqué' : ''}`, body);
}

/* ── travaux ────────────────────────────────────────────── */

function jobsBox() {
  const jobs = state.jobs;
  const seg = `<div class="seg"><button class="tb${state.jobsAll ? '' : ' on'}" data-jobs="one">Ce personnage</button>
    <button class="tb${state.jobsAll ? ' on' : ''}" data-jobs="all">Tous</button></div>`;
  const list = jobs.length ? `<div class="jobs">${jobs.map((j) => {
    const p = { ...(j.params || {}) };
    return `<div class="job ${esc(j.status)}"><div class="top"><span class="nm">${esc(j.label)} · ${esc(j.action)}${
      state.jobsAll ? ` · ${esc(j.slug)}` : ''}</span><span class="st ${esc(j.status)}">${esc(JOB_STATE[j.status] || j.status)}</span></div>
      ${j.status === 'running' ? `<div class="bar"><i style="width:${Math.round((j.progress || 0) * 100)}%"></i></div>` : ''}
      ${j.status === 'done' && j.message === 'fini' ? '' : `<div class="msg">${esc(j.error || j.message || '')}</div>`}
      <div class="when">${esc(j.id)} · créé ${esc(when(j.created))}${j.started ? ` · parti ${esc(when(j.started))}` : ''}${
      j.ended ? ` · fini ${esc(when(j.ended))}` : ''}${j.started ? ` · ${esc(dur(j.started, j.ended))}` : ''}${
      p.costume ? ` · costume ${esc(p.costume)}` : ''}</div>
      ${j.status === 'running' && (j.log || []).length ? `<pre class="cz-pre">${esc(j.log.join('\n'))}</pre>` : ''}
      <div class="cz-folds">${fold(`job:${j.id}:params`, 'paramètres', { inline: p })}
      ${j.result != null ? fold(`job:${j.id}:result`, 'résultat', { inline: j.result }) : ''}
      ${fold(`job:${j.id}:log`, 'journal complet', { job: j.id, tall: true })}</div></div>`;
  }).join('')}</div>` : '<div class="jobs-empty">Aucun travail depuis le démarrage du studio.</div>';
  const running = jobs.filter((j) => j.status === 'running').length;
  const queued = jobs.filter((j) => j.status === 'queued').length;
  return box('cz-travaux', 'JOB', 'Travaux', `${running} en cours · ${queued} en file`,
    `<div class="form-row">${seg}<span class="hint">La file vit dans la mémoire du studio : elle repart à zéro quand il redémarre.</span></div>${list}`);
}

function systemBox() {
  const s = state.system;
  if (!s) return box('cz-systeme', 'SYS', 'Système', 'muet', empty('Le studio ne répond pas.'));
  const comfy = Object.entries(s.comfy || {}).map(([url, c]) =>
    [`ComfyUI ${url}`, `${c.busy === null ? 'muet' : c.busy ? 'occupé' : 'libre'}${c.family ? ` · ${c.family}` : ''}`]);
  const body = `${kv([['mémoire libre', s.memory?.available_gb != null ? `${s.memory.available_gb} Go (seuil ${s.memory.min_free_gb} Go)` : '—'],
    ['modèle de texte', `${s.llm?.model || '—'} · ${s.llm?.url || ''}`],
    ['chargés', (s.llm?.loaded || []).length ? JSON.stringify(s.llm.loaded) : 'aucun'], ...comfy,
    ['en cours', s.running ? `${s.running.label} · ${s.running.slug} · ${Math.round((s.running.progress || 0) * 100)} % · ${s.running.message}` : 'rien'],
    ['en file', s.queued]])}
    ${sub('moteurs')}${kv(Object.entries(s.backends || {}))}
    ${fold('system', '/api/system · json', { inline: s })}`;
  const low = s.memory?.available_gb != null && s.memory.available_gb < s.memory.min_free_gb;
  return box('cz-systeme', 'SYS', 'Système', s.running ? 'occupé' : low ? 'mémoire basse' : 'au repos', body);
}

/* ── chargement ─────────────────────────────────────────── */

function parseRoute() {
  const h = decodeURIComponent(location.hash.replace(/^#\/?/, ''));
  if (h.startsWith('cz-')) return null;                     // un saut dans la page, pas une route
  const parts = h.replace(/^p\//, '').split('/').filter(Boolean);
  return { slug: parts[0] || null, costume: parts[1] || null };
}

async function loadList() {
  const out = await api('/api/characters');
  state.list = out.characters || [];
}

async function loadCharacter(force = false) {
  if (!state.slug) return false;
  const d = await api(`/api/characters/${encodeURIComponent(state.slug)}`);
  const sig = JSON.stringify(d.character);
  if (!force && sig === state.sig) {
    state.detail = d;
    return false;
  }
  try {
    const t = await api(`/api/characters/${encodeURIComponent(state.slug)}/tree`);
    state.tree = new Map((t.files || []).map((f) => [f.path, { size: f.size, mtime: f.mtime }]));
    state.truncated = !!t.truncated;
  } catch (_) {
    state.tree = new Map();   // un studio d'avant les coulisses : pas de liste, les images restent
    state.truncated = false;
  }
  state.detail = d;
  state.sig = sig;
  return true;
}

async function loadJobs() {
  const q = state.jobsAll || !state.slug ? '' : `?slug=${encodeURIComponent(state.slug)}`;
  const out = await api(`/api/jobs${q}`);
  state.jobs = out.jobs || [];
}

async function onRoute() {
  const r = parseRoute();
  if (!r) return;
  if (!r.slug) {
    if (state.list[0]) location.replace(`#/${encodeURIComponent(state.list[0].slug)}`);
    else renderMain();
    return;
  }
  const changed = r.slug !== state.slug;
  state.slug = r.slug;
  state.costume = r.costume;
  if (changed) {
    state.detail = null;
    state.sig = '';
    state.tree = new Map();
    state.texts = {};
    renderMain();
  }
  renderSide();
  try {
    await Promise.all([loadCharacter(changed), loadJobs()]);
    renderMain();
    document.title = `CHARACTER FACTORY · COULISSES · ${state.detail.character.name}`;
  } catch (e) {
    $('#main').innerHTML = `<div class="gate"><p>${esc(e.message)}</p></div>`;
  }
}

/* Les blocs ouverts se remplissent : ceux qui l'étaient avant un rendu aussi. */
function hydrate(root) {
  $$('details[data-key][open]', root).forEach((d) => {
    if (!$('pre', d)?.textContent) fill(d);
  });
}

async function refresh() {
  try {
    await loadList();
    renderSide();
    state.texts = {};
    await Promise.all([loadCharacter(true), loadJobs()]);
    renderMain();
    toast('relu');
  } catch (e) {
    toast(e.message, 6000);
  }
}

/* ── relevés réguliers ──────────────────────────────────── */

async function pollJobs() {
  let active = false;
  try {
    await loadJobs();
    active = state.jobs.some((j) => ['queued', 'running'].includes(j.status));
    const box = $('#cz-jobs');
    if (box) {
      box.innerHTML = jobsBox();
      hydrate(box);
    }
    // Un travail qui finit change le manifeste : on relit le personnage.
    if (state.slug && await loadCharacter()) {
      renderMain();
    }
  } catch (_) { /* studio momentanément muet : on réessaie */ }
  setTimeout(pollJobs, active ? 2000 : 6000);
}

async function pollSystem() {
  const pill = $('#sys-pill');
  try {
    const s = await api('/api/system');
    state.system = s;
    const mem = s.memory?.available_gb;
    const low = mem != null && mem < s.memory.min_free_gb;
    const parts = [];
    if (s.running) parts.push(`${s.running.label} · ${s.running.slug}`);
    if (mem != null) parts.push(`${Math.round(mem)} go libres`);
    parts.push(`${s.queued} en file`);
    $('#sys-text').textContent = parts.join(' · ');
    pill.className = `pill ${s.running ? 'work' : low ? 'err' : 'on'}`;
  } catch (_) {
    state.system = null;
    $('#sys-text').textContent = 'studio muet';
    pill.className = 'pill err';
  }
  const box = $('#cz-sys');
  if (box) {
    box.innerHTML = systemBox();
    hydrate(box);
  }
  setTimeout(pollSystem, 5000);
}

/* ── gestes ─────────────────────────────────────────────── */

function zoom(src, cap) {
  $('#lightbox-img').src = src;
  $('#lightbox-cap').textContent = cap || '';
  $('#lightbox').hidden = false;
}

function wire() {
  document.addEventListener('click', async (e) => {
    const z = e.target.closest('[data-zoom]');
    if (z) { zoom(z.dataset.zoom, z.dataset.cap); return; }
    if (e.target.closest('[data-refresh]')) { refresh(); return; }
    const jump = e.target.closest('[data-jump]');
    if (jump) {
      e.preventDefault();
      document.getElementById(jump.dataset.jump)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    const scope = e.target.closest('[data-jobs]');
    if (scope) {
      state.jobsAll = scope.dataset.jobs === 'all';
      try { await loadJobs(); } catch (err) { toast(err.message); }
      $('#cz-jobs').innerHTML = jobsBox();
      hydrate($('#cz-jobs'));
    }
  });
  // « toggle » ne remonte pas : on l'attrape à la descente.
  document.addEventListener('toggle', (e) => {
    const d = e.target;
    if (!(d instanceof HTMLDetailsElement) || !d.dataset.key) return;
    if (d.open) state.open.add(d.dataset.key);
    else state.open.delete(d.dataset.key);
    if (!d.open) return;
    if (d.classList.contains('cz-dir')) {
      // Le tableau d'un dossier ne se construit qu'ouvert : des centaines de lignes sinon.
      if (!$('table', d)) {
        const dir = d.dataset.key.slice(4);
        const list = [...state.tree.keys()].filter((p) => (dirOf(p) || '.') === dir);
        d.insertAdjacentHTML('beforeend', filesTable(list, dir === '.' ? '' : `${dir}/`));
      }
      return;
    }
    if (!$('pre', d)?.textContent) fill(d);
  }, true);
  document.addEventListener('change', (e) => {
    if (e.target.id === 'pick') location.hash = `#/${encodeURIComponent(e.target.value)}`;
  });
  $('#lightbox').addEventListener('click', () => { $('#lightbox').hidden = true; });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('#lightbox').hidden = true; });
  window.addEventListener('hashchange', onRoute);
}

async function start() {
  wire();
  try {
    await loadList();
  } catch (e) {
    $('#main').innerHTML = `<div class="gate"><p>Le studio ne répond pas : ${esc(e.message)}</p></div>`;
  }
  renderSide();
  await onRoute();
  pollJobs();
  pollSystem();
}

start();
