'use strict';

import { SHEET_FIELDS } from './schema.js';

/* ============================================================
   Le studio.
   L'accueil montre les personnages en cartes ; #/p/<slug> ouvre
   un personnage, un bloc par étage, dans l'ordre de la chaîne.
   Tout ce qui calcule part dans la file du serveur (un travail
   à la fois sur DGX2) ; la page relève la file et se redessine
   quand un travail finit. Les choix (verrouiller, valider) se
   jouent tout de suite.
   ============================================================ */

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
const pad = (n) => String(n).padStart(2, '0');
const base = (rel) => String(rel || '').split('/').pop();

const state = {
  route: { view: 'home' },
  list: null,
  detail: null,          // { character, summary, busy, backends, view_methods }
  costume: undefined,    // costume affiché ; null = onglet « + Costume »
  jobs: [],
  seen: new Set(),       // travaux finis déjà pris en compte
  drafts: {},            // valeurs saisies, par champ
  refs: {},              // images déposées, par formulaire : [{ id, url, name }]
  openLogs: new Set(),
  fullLogs: {},
  pending: false,        // un rendu attend que le champ actif perde le focus
  stage: null,           // étape affichée ; null = celle où en est le personnage
  renaming: false,
  editField: null,       // champ de la fiche en cours de correction
  sig: '',
  system: null,
};

// Les choix ne prennent jamais l'orange : il y en a un par candidat.
const PICKS = new Set(['face_lock', 'fullbody_ok', 'apose_ok', 'sheet_ok', 'rig_ok']);
const ORTHO = ['front', 'left', 'back', 'right'];
const VIEWS = [...ORTHO, 'threequarter'];
const VIEW_LABEL = { front: 'face', left: 'profil gauche', back: 'dos', right: 'profil droit', threequarter: '3/4' };
const METHOD_LABEL = {
  'qwen21-pose': 'Qwen-Image 2.1 · squelette par vue',
  orbit: 'H3 · orbite redécoupée',
  per_view: 'H3 · une génération par vue',
  'qwen21-orbit': 'Qwen-Image 2.1 · LoRA orbite',
  'qwen-2511': 'Qwen-Image-Edit 2511 · LoRA angles',
  'qwen-2509': 'Qwen-Image-Edit 2509 · LoRA angles',
};
const NEXT_TEXT = {
  face: 'suite : variantes du visage', face_lock: 'suite : verrouiller un visage',
  costume_add: 'suite : un costume', fullbody: 'suite : plein pied', fullbody_ok: 'suite : valider un plein pied',
  apose: 'suite : A-pose', apose_ok: 'suite : valider une A-pose', sheet: 'suite : planche',
  sheet_ok: 'suite : valider une planche', views: 'suite : vues orthogonales',
  prep: 'suite : préparer les vues', check: "suite : contrôle d'alignement", mesh: 'suite : mesh 3D',
  rig: 'suite : rig', rig_ok: 'suite : regarder le rig',
};
const JOB_STATE = { queued: 'en file', running: 'en cours', done: 'fini', error: 'échec', cancelled: 'annulé' };

/* ── réseau ─────────────────────────────────────────────── */

async function api(path, { method = 'GET', body, raw } = {}) {
  const init = { method, headers: {} };
  if (raw) Object.assign(init, raw);
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers['content-type'] = 'application/json';
  }
  const res = await fetch(path, init);
  let json = null;
  try { json = await res.json(); } catch (_) { /* corps vide */ }
  if (!res.ok) throw new Error(json?.error?.message || `${res.status} ${res.statusText}`);
  return json;
}

function fileUrl(slug, rel, version) {
  if (!rel) return '';
  const path = `/files/${encodeURIComponent(slug)}/${rel.split('/').map(encodeURIComponent).join('/')}`;
  return version ? `${path}?v=${encodeURIComponent(version)}` : path;
}

const slug = () => state.route.slug;
const actionUrl = (action) => `/api/characters/${encodeURIComponent(slug())}/actions/${action}`;

/* ── petits morceaux ────────────────────────────────────── */

let toastTimer = null;
function toast(msg, ms = 2800) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), ms);
}

function when(at) {
  if (!at) return '—';
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? at : d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' });
}

function size(v) {
  if (v == null) return '—';
  const parts = (Array.isArray(v) ? v : [v]).map((x) => Number(x).toFixed(2).replace('.', ','));
  return `${parts.join(' × ')} m`;
}

function initials(name) {
  return String(name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();
}

function ticks(stages) {
  return `<div class="ticks">${stages.map((s) =>
    `<i class="${s.state}" title="${esc(`${s.ref} ${s.label}`)}"></i>`).join('')}</div>`;
}

function draft(key, fallback = '') {
  return key in state.drafts ? state.drafts[key] : fallback;
}

function textarea(key, fallback, placeholder, rows = 3) {
  return `<textarea class="fld" rows="${rows}" data-draft="${esc(key)}" placeholder="${esc(placeholder)}">${
    esc(draft(key, fallback))}</textarea>`;
}

function input(key, label, { fallback = '', placeholder = '', cls = '', grow = false } = {}) {
  return `<label class="field${grow ? ' grow' : ''}"><span class="lbl">${esc(label)}</span>
    <input class="fld ${cls}" data-draft="${esc(key)}" value="${esc(draft(key, fallback))}"
           placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false"></label>`;
}

function select(key, label, options, fallback) {
  const cur = String(draft(key, fallback));
  return `<label class="field"><span class="lbl">${esc(label)}</span><select class="fld" data-draft="${esc(key)}">${
    options.map(([v, text]) => `<option value="${esc(v)}"${String(v) === cur ? ' selected' : ''}>${esc(text)}</option>`)
      .join('')}</select></label>`;
}

function check(key, label, fallback = false) {
  return `<label class="check"><input type="checkbox" data-draft="${esc(key)}"${
    draft(key, fallback) ? ' checked' : ''}>${esc(label)}</label>`;
}

function refsZone(form, what) {
  const list = state.refs[form] || [];
  return `<div class="refs">${list.map((r, i) =>
    `<span class="thumb" style="background-image:url('${r.url}')" title="${esc(r.name)}">` +
    `<button data-drop-ref="${esc(form)}:${i}" title="retirer">×</button></span>`).join('')}
    <label class="add" data-refs-zone="${esc(form)}">+ ${esc(what)}
      <input type="file" accept="image/*" multiple data-refs="${esc(form)}"></label></div>`;
}

function activeJob(action, costume) {
  return state.jobs.find((j) => (j.status === 'queued' || j.status === 'running') && j.action === action &&
    (!costume || (j.params.costume || null) === costume));
}

/* Le bouton d'une action. Il prend l'orange s'il porte l'étape
   suivante de la chaîne — un seul par écran. */
// Un bouton qui porte l'étape suivante sous un autre nom.
const GO_AS = { costume_go: ['costume_add', 'fullbody'] };

function act(action, label, { costume, form, params, confirm: ask, disabled = false, block = false, small = false } = {}) {
  const next = state.detail?.summary.next;
  const goes = GO_AS[action] || [action];
  const go = !small && next && goes.includes(next.action) && !PICKS.has(action)
    && (!next.costume || next.costume === costume || action === 'costume_go');
  const running = activeJob(action, costume);
  const attrs = [
    `data-act="${action}"`,
    costume ? `data-costume="${esc(costume)}"` : '',
    form ? `data-form="${esc(form)}"` : '',
    params ? `data-params="${esc(JSON.stringify(params))}"` : '',
    ask ? `data-confirm="${esc(ask)}"` : '',
    disabled || (running && !small) ? 'disabled' : '',
  ].filter(Boolean).join(' ');
  const cls = `tb ${go ? 'go' : 'ghost'}${block ? ' block' : ''}${PICKS.has(action) || small ? ' sm' : ''}`;
  const busy = running && !small ? `${running.status === 'queued' ? 'en file' : 'en cours'}…` : label;
  return `<button class="${cls}" ${attrs}>${esc(busy)}</button>`;
}

function box({ sec, title, st, stLabel, body, next = false, wait = false }) {
  return `<section class="box${next ? ' next' : ''}${wait ? ' wait' : ''}">
    <div class="box-head"><span class="sec">${sec}</span><h2>${esc(title)}</h2>
      <span class="state ${st}">${esc(stLabel)}</span></div>
    <div class="box-body">${body}</div></section>`;
}

function isNext(...actions) {
  const next = state.detail?.summary.next;
  return !!next && actions.includes(next.action) && (!next.costume || next.costume === state.costume);
}

function cand({ src, label, cap, mark = '', markCls = '', button = '', sel = false, off = false }) {
  return `<div class="cand${sel ? ' sel' : ''}${off ? ' off' : ''}">
    <img src="${esc(src)}" alt="" loading="lazy" data-zoom="${esc(src)}" data-cap="${esc(cap || label)}">
    ${mark ? `<span class="mark ${markCls}">${esc(mark)}</span>` : ''}
    <div class="cap"><span class="lbl">${esc(label)}</span>${button}</div></div>`;
}

const stubMark = (e) => (e && e.backend === 'stub' ? ['factice', 'stub'] : ['', '']);

/* ── accueil ────────────────────────────────────────────── */

function renderHome() {
  const list = state.list?.characters || [];
  return `
  <div class="console-top">
    <div class="hero">
      <span class="ref">00_studio</span>
      <h2 class="studio-title">Les personnages</h2>
      <p>Un nom suffit pour commencer. Ensuite, on décrit ce qu'on veut voir — le visage, puis la tenue — et
        la fiche se remplit toute seule à partir de là. Tout calcule sur DGX2, un rendu à la fois ; pendant ce
        temps, on continue de travailler.</p>
      <form data-form="create" class="create">
        <input class="fld" name="name" placeholder="Nom du personnage" autocomplete="off" spellcheck="false">
        <button class="tb go" type="submit">Créer ▸</button>
      </form>
    </div>
    <div class="statcard">
      <span class="ref">personnages</span>
      <span class="n">${pad(list.length)}</span>
      <span class="foot">${list.filter((c) => c.locked).length} visage(s) verrouillé(s)</span>
      <span class="dots"></span>
    </div>
  </div>
  ${list.length ? `<section class="sect">
    <div class="sect-head"><h2>Personnages</h2><span class="cnt">${list.length} au studio</span></div>
    <div class="cards">${list.map(card).join('')}</div>
  </section>` : ''}`;
}

function card(c) {
  const img = c.thumb ? `<img src="${esc(c.thumb)}" alt="" loading="lazy">` : `<span class="ph">${esc(initials(c.name))}</span>`;
  const badge = c.locked ? '<span class="badge lock">visage verrouillé</span>' : c.thumb ? '<span class="badge">candidat</span>' : '';
  return `<a class="card" href="#/p/${encodeURIComponent(c.slug)}">
    <div class="card-img">${img}${badge}</div>
    <div class="card-body">
      <span class="ref">${esc(c.slug)} · ${esc(c.style)}</span>
      <span class="nm">${esc(c.name)}</span>
      <span class="role">${esc(c.role || c.archetype || '')}</span>
      ${ticks(c.stages)}
      <span class="sub">${esc(c.next ? NEXT_TEXT[c.next.action] || c.next.action : 'chaîne complète')}</span>
    </div></a>`;
}

/* ── un personnage : l'atelier ──────────────────────────── */

const STAGES = [
  { id: 'face', ref: '01', label: 'Visage' },
  { id: 'costume', ref: '02', label: 'Costume' },
  { id: 'pose', ref: '03', label: 'A-pose' },
  { id: 'sheet', ref: '04', label: 'Planche' },
  { id: 'views', ref: '05', label: 'Vues' },
  { id: 'mesh', ref: '06', label: '3D' },
  { id: 'rig', ref: '07', label: 'Rig' },
];
const STAGE_OF = {
  face: 'face', face_lock: 'face', costume_add: 'costume', fullbody: 'costume', fullbody_ok: 'costume',
  apose: 'pose', apose_ok: 'pose', sheet: 'sheet', sheet_ok: 'sheet', views: 'views', prep: 'views', check: 'views', mesh: 'mesh', rig: 'rig',
  rig_ok: 'rig',
};
const ENGINE_LABEL = {
  zimage: 'Z-Image Turbo · rapide, 8 s',
  flux2: 'FLUX.2 dev · qualité, 75 s',
  qwen21: 'Qwen-Image 2.1 · 40 s',
  h3: 'H3 · pour normaliser une photo',
};
const TRAITS = ['audacieux', 'discret', 'loyal', 'impulsif', 'méfiant', 'chaleureux', 'ironique', 'calme', 'têtu',
  'curieux', 'protecteur', 'rêveur', 'rancunier', 'drôle', 'solitaire', 'généreux'];
// Les rendus H3 (~100 Go) : pendant eux, la mémoire manque au modèle de texte. Qwen-Image 2.1 tient à côté.
function usesH3(run) {
  const p = run.params || {};
  return run.action === 'sheet' || (['face', 'fullbody'].includes(run.action) && p.engine === 'h3')
    || (run.action === 'views' && ['per_view', 'orbit'].includes(p.method));
}

function costumeOf(c) {
  return (state.costume && c.costumes[state.costume]) || Object.values(c.costumes)[0] || null;
}

function stageStates(c) {
  const cos = costumeOf(c);
  const v = cos?.views;
  return {
    face: c.face.locked ? 'done' : c.face.candidates.length ? 'partial' : 'open',
    costume: cos?.fullbody.validated ? 'done' : cos ? 'partial' : 'open',
    pose: !cos?.fullbody.validated ? 'locked' : cos.apose.validated ? 'done'
      : cos.apose.candidates.length ? 'partial' : 'open',
    sheet: !cos?.apose.validated ? 'locked' : cos.sheet ? 'done' : qwenSheets(cos).length ? 'partial' : 'open',
    views: !cos?.apose.validated ? 'locked' : v.check?.ok ? 'done' : Object.keys(v.raw).length ? 'partial' : 'open',
    mesh: !v || !Object.keys(v.prepared).length ? 'locked' : cos.meshes.length ? 'done' : 'open',
    rig: !cos?.meshes.length ? 'locked' : cos.rigs.some((r) => r.verdict === 'accepted') ? 'done'
      : cos.rigs.length ? 'partial' : 'open',
  };
}

function currentStage(d, st) {
  if (state.stage && st[state.stage] !== 'locked') return state.stage;
  const next = d.summary.next;
  return next ? STAGE_OF[next.action] || 'face' : 'rig';
}

function frise(st, cur) {
  const label = { done: 'fait', partial: 'en cours', open: 'à faire', locked: 'attend' };
  return `<nav class="frise">${STAGES.map((s) => `<button class="step ${st[s.id]}${s.id === cur ? ' cur' : ''}"
    data-stage="${s.id}"${st[s.id] === 'locked' ? ' disabled' : ''}>
    <span class="n">${s.ref}</span><span class="nm">${s.label}</span><span class="st">${label[st[s.id]]}</span></button>`)
    .join('')}</nav>`;
}

function renderPerso() {
  const d = state.detail;
  if (!d) return '<p class="prose">chargement…</p>';
  const c = d.character;
  const s = d.summary;
  const st = stageStates(c);
  const cur = currentStage(d, st);
  const img = s.thumb ? `<img class="face" src="${esc(s.thumb)}" alt="" data-zoom="${esc(s.thumb)}" data-cap="${
    esc(c.name)}">` : `<div class="face ph">${esc(initials(c.name))}</div>`;
  const name = state.renaming
    ? `<form data-form="rename" class="rename"><input class="fld" name="name" value="${esc(c.name)}"
        autocomplete="off" spellcheck="false"><button class="tb ghost sm" type="submit">OK</button></form>`
    : `<h1 class="name" data-rename title="renommer">${esc(c.name)}</h1>`;
  return `
  <div class="perso-head">
    ${img}
    <div class="who">
      <span class="ref">${esc(c.slug)}</span>
      ${name}
      <span class="role">${esc([s.role, s.archetype].filter(Boolean).join(' · '))}</span>
    </div>
    <div class="acts">
      <span class="seg">
        <button class="tb sm${c.style === 'photoreal' ? ' on' : ''}" data-style-set="photoreal">Photo</button>
        <button class="tb sm${c.style === 'stylized' ? ' on' : ''}" data-style-set="stylized">Stylisé</button>
      </span>
      <a class="tb ghost sm" href="#/">◂ Studio</a>
    </div>
  </div>
  ${frise(st, cur)}
  <div class="perso-grid">
    <div class="perso-main">${stageView(cur, d)}</div>
    <aside class="perso-side">
      <div id="waiting">${waitingCard(d)}</div>
      <div class="c-head"><h2>Rendus</h2><span class="cnt" id="jobs-count"></span></div>
      <div class="jobs" id="jobs">${renderJobs()}</div>
      ${fiche(d)}
    </aside>
  </div>`;
}

function stageView(stage, d) {
  const c = d.character;
  if (stage === 'face') return stageFace(d);
  if (stage === 'costume') return stageCostume(d);
  const cos = costumeOf(c);
  if (!cos) return stageCostume(d);
  const key = Object.keys(c.costumes).find((k) => c.costumes[k] === cos);
  const picker = Object.keys(c.costumes).length > 1 ? `<div class="tabs">${Object.entries(c.costumes).map(([k, x]) =>
    `<button class="tb sm ${k === key ? 'on' : 'ghost'}" data-costume-tab="${esc(k)}">${esc(x.name)}</button>`).join('')}</div>` : '';
  const view = { pose: boxPose, sheet: boxSheet, views: boxViews, mesh: boxMesh, rig: boxRig }[stage];
  return picker + view(c, key, cos, d);
}

function goNext(label, stage) {
  return `<div class="form-row next-row"><span class="sp"></span>
    <button class="tb go" data-goto="${stage}">${esc(label)} ▸</button></div>`;
}

function stageFace(d) {
  const c = d.character;
  const f = c.face;
  if (f.locked) {
    const src = fileUrl(c.slug, f.locked, f.locked_at);
    const from = f.candidates.find((x) => x.file === f.locked_from);
    const hasCostume = Object.keys(c.costumes).length > 0;
    return box({
      sec: '01', title: 'Visage', st: 'done', stLabel: 'verrouillé', body: `<div class="hero-img">
        <img src="${esc(src)}" alt="" data-zoom="${esc(src)}" data-cap="visage verrouillé">
        <div class="stage-body">
          <p>Le visage fait autorité sur toute la suite et ne change plus. Pour un autre visage, un autre
            personnage.</p>
          ${from?.desc ? `<p class="hint">${esc(from.desc)}</p>` : ''}
          <dl class="kv"><dt>modèle</dt><dd>${esc(ENGINE_LABEL[from?.engine || 'h3'] || from?.engine || '—')}</dd>
            <dt>graine</dt><dd>${esc(f.locked_seed ?? '—')}</dd><dt>verrouillé</dt><dd>${esc(when(f.locked_at))}</dd></dl>
        </div></div>${goNext(hasCostume ? 'Au costume' : 'Habiller le personnage', 'costume')}`,
    });
  }
  const engines = Object.keys(d.face_engines || ENGINE_LABEL).map((k) => [k, ENGINE_LABEL[k] || k]);
  const photo = (state.refs.face || []).length || f.refs.length;
  let body = `
    <label class="field"><span class="lbl">Décris son visage</span>
      ${textarea('face.brief', f.brief || '', 'en français, comme ça vient : « 20 ans, peau noire, coupe courte dégradée, fine moustache, regard doux ». Le studio en tire quatre propositions différentes, et remplit la fiche.', 4)}</label>
    <div class="form-row">
      <div class="field"><span class="lbl">Photo · facultatif</span>${refsZone('face', 'photo')}</div>
      ${select('face.engine', 'Modèle', engines, f.engine && !photo ? f.engine : photo ? 'h3' : 'zimage')}
      ${select('face.variants', 'Propositions', [[2, '2'], [4, '4'], [6, '6']], 4)}
      <span class="sp"></span>
      ${act('face', 'Générer ▸', { form: 'face' })}
    </div>`;
  if (f.prompt_en) {
    body += `<details class="read"><summary>ce que le modèle en a tiré</summary><p class="hint">${esc(f.prompt_en)}</p></details>`;
  }
  if (f.candidates.length) {
    body += `<div class="box-sub">Propositions · verrouille celle qui fait le personnage</div>
      <div class="cands big">${f.candidates.map((x, i) => {
        const [mark, markCls] = x.backend === 'stub' ? ['factice', 'stub'] : [ENGINE_LABEL[x.engine]?.split(' · ')[0] || '', ''];
        return cand({
          src: fileUrl(c.slug, x.file, x.at), label: `n° ${i + 1}`, mark, markCls,
          cap: `n° ${i + 1} · ${x.desc || ''}`,
          button: act('face', 'Autour', { params: { around: i + 1, variants: 3 }, small: true }) +
            act('face_lock', 'Verrouiller', {
              params: { candidate: String(i + 1) },
              confirm: `Verrouiller le visage n° ${i + 1} ? Il fera autorité sur toute la suite ; on ne pourra plus le changer.`,
            }),
        });
      }).reverse().join('')}</div>`;
  }
  const st = f.candidates.length ? ['partial', `${f.candidates.length} proposition(s)`] : ['open', 'à faire'];
  return box({ sec: '01', title: 'Visage', st: st[0], stLabel: st[1], body });
}

function stageCostume(d) {
  const c = d.character;
  const keys = Object.keys(c.costumes);
  const locked = !!c.face.locked;
  if (state.costume === undefined || (state.costume !== null && !keys.includes(state.costume))) {
    state.costume = keys[0] || null;
  }
  const tabs = keys.length ? `<div class="tabs">${keys.map((k) => `<button class="tb sm ${k === state.costume ? 'on' : 'ghost'}"
    data-costume-tab="${esc(k)}">${esc(c.costumes[k].name)}</button>`).join('')}
    <button class="tb sm ${state.costume === null ? 'on' : 'ghost'}" data-costume-tab="">+ Tenue</button></div>` : '';
  // Sans visage verrouillé, pas de plein pied : on le dit en tête, avec le
  // chemin, au lieu de laisser chercher un bouton qui n'existe pas.
  const faceFirst = locked ? '' : `<div class="gate">
      <p>Le plein pied part du <b>visage verrouillé</b>, et ce personnage n'en a pas encore. La tenue peut déjà
        s'écrire : elle est gardée dès que tu quittes le champ.</p>
      <button class="tb go" data-goto="face">Choisir le visage ▸</button></div>`;
  const renderBtn = (key) => (locked
    ? act(key ? 'fullbody' : 'costume_go', 'Générer le plein pied ▸',
      { form: key ? `cos.${key}` : 'costume_new', costume: key || undefined })
    : '<button class="tb ghost" disabled title="il faut d\'abord un visage verrouillé">Générer le plein pied ▸</button>');
  let body;
  if (state.costume === null) {
    body = `${faceFirst}${tabs}
      <label class="field"><span class="lbl">Décris sa tenue · gardée dès que tu quittes le champ</span>
        ${textarea('costume_new.brief', '', 'en français : « hoodie bleu Adidas capuche baissée, baggy blanc usé aux genoux, baskets blanches, casquette noire à l\'envers »', 4)}</label>
      <div class="form-row">
        <div class="field"><span class="lbl">Vêtements · images</span>${refsZone('costume_new', 'vêtement')}</div>
        ${input('costume_new.name', 'Nom de la tenue', { placeholder: `tenue ${keys.length + 1}` })}
        ${select('costume_new.variants', 'Propositions', [[1, '1'], [2, '2'], [3, '3']], 2)}
        <span class="sp"></span>${renderBtn(null)}
      </div>`;
    return box({ sec: '02', title: 'Costume', st: 'open', stLabel: 'à faire', body });
  }
  const key = state.costume;
  const cos = c.costumes[key];
  const form = `cos.${key}`;
  const fb = cos.fullbody;
  body = `${faceFirst}${tabs}
    <label class="field"><span class="lbl">Décris sa tenue · gardée dès que tu quittes le champ</span>
      ${textarea(`${form}.brief`, cos.brief || cos.prompt || '', 'en français, comme ça vient', 4)}</label>
    <div class="form-row">
      <div class="field"><span class="lbl">Vêtements · images</span>
        <div class="refs">${cos.refs.map((r) => {
          const src = fileUrl(c.slug, r);
          return `<span class="thumb" style="background-image:url('${esc(src)}')" title="${esc(base(r))}" data-zoom="${
            esc(src)}" data-cap="${esc(base(r))}"><button data-act="costume_edit" data-costume="${esc(key)}"
            data-params="${esc(JSON.stringify({ drop_refs: [r] }))}" title="retirer"
            data-confirm="${esc(`Retirer ${base(r)} des références de la tenue ?`)}">×</button></span>`;
        }).join('')}</div>${refsZone(form, 'vêtement')}</div>
      ${select(`${form}.variants`, 'Propositions', [[1, '1'], [2, '2'], [3, '3']], 2)}
      <span class="sp"></span>${renderBtn(key)}
    </div>`;
  if (cos.brief_read && cos.prompt) {
    body += `<details class="read"><summary>ce que le modèle en a tiré</summary><p class="hint">${esc(cos.prompt)}</p></details>`;
  }
  if (fb.candidates.length) {
    body += `<div class="box-sub">Pleins pieds · valide celui qui tient</div><div class="cands tall">${fb.candidates.map((x, i) => {
      const chosen = x.file === fb.validated_from;
      const [mark, markCls] = chosen ? ['validé', 'ok'] : stubMark(x);
      return cand({
        src: fileUrl(c.slug, x.file, x.at), label: `n° ${i + 1}`, cap: `n° ${i + 1} · graine ${x.seed}`, mark, markCls,
        sel: chosen,
        button: chosen ? '' : act('fullbody_ok', 'Valider', {
          costume: key, params: { candidate: String(i + 1) },
          confirm: fb.validated ? 'Valider ce plein pied ? L\'A-pose validée sur l\'ancien ne vaudra plus.' : undefined,
        }),
      });
    }).reverse().join('')}</div>`;
  }
  if (fb.validated) body += goNext('À l\'A-pose', 'pose');
  const st = fb.validated ? ['done', 'plein pied validé'] : fb.candidates.length ? ['partial', `${fb.candidates.length} plein(s) pied(s)`]
    : ['partial', 'tenue écrite'];
  return box({ sec: '02', title: `Costume · ${cos.name}`, st: st[0], stLabel: st[1], body });
}

function waitBox(sec, title, text) {
  return box({ sec, title, st: 'todo', stLabel: 'en attente', body: `<p>${esc(text)}</p>`, wait: true });
}

function boxPose(c, key, cos) {
  if (!cos.fullbody.validated) return waitBox('03', 'A-pose', 'L\'A-pose attend un plein pied validé.');
  const ap = cos.apose;
  const form = `pose.${key}`;
  const skel = ap.skeleton ? `<div class="field"><span class="lbl">Squelette</span><div class="refs">
    <span class="thumb" style="background-image:url('${esc(fileUrl(c.slug, ap.skeleton))}')" title="squelette"
      data-zoom="${esc(fileUrl(c.slug, ap.skeleton))}" data-cap="squelette A-pose"></span></div></div>` : '';
  let body = `<p>Le plein pied validé, remis en A-pose : bras à 45°, jambes légèrement ouvertes, la pose que veulent le
    mesh et le rig. La pose est imposée par un squelette relevé sur le plein pied, pas par le prompt ; la tenue et le
    visage restent ceux du plein pied.</p>
    <div class="form-row">
      ${skel}
      ${select(`${form}.variants`, 'Propositions', [[1, '1'], [2, '2'], [3, '3']], 2)}
      ${input(`${form}.seed`, 'Graine', { placeholder: 'au hasard', cls: 'num' })}
      <span class="sp"></span>${act('apose', 'Générer ▸', { form, costume: key })}
    </div>`;
  if (ap.candidates.length) {
    body += `<div class="box-sub">A-poses · valide celle qui tient</div><div class="cands tall">${ap.candidates.map((x, i) => {
      const chosen = x.file === ap.validated_from;
      const [mark, markCls] = chosen ? ['validée', 'ok'] : stubMark(x);
      return cand({
        src: fileUrl(c.slug, x.file, x.at), label: `n° ${i + 1}`, cap: `n° ${i + 1} · graine ${x.seed}`, mark, markCls,
        sel: chosen,
        button: chosen ? '' : act('apose_ok', 'Valider', {
          costume: key, params: { candidate: String(i + 1) },
          confirm: Object.keys(cos.views.raw).length
            ? 'Valider cette A-pose ? Les vues faites sur l\'ancienne ne vaudront plus.' : undefined,
        }),
      });
    }).reverse().join('')}</div>`;
  }
  if (ap.validated) body += goNext('À la planche', 'sheet');
  const st = ap.validated ? ['done', 'validée'] : ap.candidates.length ? ['partial', `${ap.candidates.length} proposition(s)`]
    : ['todo', 'à faire'];
  return box({ sec: '03', title: 'A-pose', st: st[0], stLabel: st[1], body, next: isNext('apose', 'apose_ok') });
}

// Les planches Qwen-Image 2.1 ; les anciennes planches H3 restent dans le manifeste, hors de l'atelier.
function qwenSheets(cos) {
  return cos.sheets.filter((s) => s.engine === 'qwen21');
}

function boxSheet(c, key, cos) {
  if (!cos.apose.validated) return waitBox('04', 'Planche', 'La planche attend une A-pose validée.');
  const form = `sheet.${key}`;
  const sheets = qwenSheets(cos);
  let body = `<p>La planche de référence : face et dos en pied dans l'A-pose, et un gros plan tête et épaules. Le visage
    verrouillé donne l'identité, l'A-pose la tenue, une mise en page faite des squelettes la composition. Elle sert au
    turnaround de présentation ; les vues n'en dépendent pas.</p>
    <div class="form-row">
      ${select(`${form}.variants`, 'Propositions', [[1, '1'], [2, '2'], [3, '3']], 2)}
      ${input(`${form}.seed`, 'Graine', { placeholder: 'au hasard', cls: 'num' })}
      <span class="sp"></span>${act('sheet', 'Générer ▸', { form, costume: key })}
    </div>`;
  if (sheets.length) {
    body += `<div class="box-sub">Planches · valide celle qui tient</div><div class="cands wide">${sheets.map((s) => {
      const chosen = s.id === cos.sheet;
      const [mark, markCls] = chosen ? ['validée', 'ok'] : stubMark(s);
      return cand({
        src: fileUrl(c.slug, s.file, s.at), label: s.id, cap: `${s.id} · graine ${s.seed}`, mark, markCls, sel: chosen,
        button: chosen ? '' : act('sheet_ok', 'Valider', { costume: key, params: { id: s.id } }),
      });
    }).reverse().join('')}</div>`;
  }
  if (cos.sheet) body += goNext('Aux vues', 'views');
  const st = cos.sheet ? ['done', `${cos.sheet} validée`] : sheets.length ? ['partial', `${sheets.length} planche(s)`]
    : ['todo', 'à faire'];
  return box({ sec: '04', title: 'Planche', st: st[0], stLabel: st[1], body, next: isNext('sheet', 'sheet_ok') });
}

function boxViews(c, key, cos, d) {
  if (!cos.apose.validated) return waitBox('05', 'Vues orthogonales', 'Les vues attendent une A-pose validée.');
  const v = cos.views;
  const form = `views.${key}`;
  const methods = (d.view_methods || Object.keys(METHOD_LABEL)).map((m) => [m, METHOD_LABEL[m] || m]);
  let body = `<p>Face, profils, dos et 3/4, plein cadre, pour la 3D, depuis l'A-pose validée. Par défaut, chaque vue
    est guidée par le squelette A-pose tourné à son angle : même échelle, même ligne de sol.</p>
    <div class="form-row">
      ${select(`${form}.method`, 'Méthode', methods, v.method || 'qwen21-pose')}
      ${input(`${form}.seed`, 'Graine', { placeholder: 'au hasard', cls: 'num' })}
      <span class="sp"></span>${act('views', 'Générer ▸', { form, costume: key })}
    </div>`;
  const raw = VIEWS.filter((n) => v.raw[n]);
  if (raw.length) {
    body += `<div class="box-sub">Vues brutes · ${esc(METHOD_LABEL[v.method] || v.method || '')}</div>
      <div class="cands tall">${raw.map((n) => {
        const e = v.raw[n];
        const got = e.azimuth_measured ?? e.azimuth_estimated;
        const est = got != null && n !== 'front' ? ` · ${e.azimuth_measured != null ? 'mesuré' : 'relevé'} ${
          Number(got).toFixed(0)}°` : '';
        const [mark, markCls] = stubMark(e);
        return cand({ src: fileUrl(c.slug, e.file, e.at), label: `${VIEW_LABEL[n]} · ${e.azimuth}°${est}`, mark, markCls });
      }).join('')}</div>`;
    const complete = ORTHO.every((n) => v.raw[n]);
    body += `<div class="form-row"><span class="hint">${complete
      ? 'Détourage BiRefNet, recentrage, même échelle, marges égales.'
      : 'Il manque des vues : régénère.'}</span><span class="sp"></span>${
      act('prep', 'Préparer ▸', { costume: key, disabled: !complete })}</div>`;
  }
  const prepared = VIEWS.filter((n) => v.prepared[n]);
  if (prepared.length) {
    const ver = v.prep?.at;
    body += `<div class="box-sub">Vues préparées${v.prep ? ` · ${v.prep.size} px` : ''}</div>
      <div class="cands tall">${prepared.map((n) => cand({
        src: fileUrl(c.slug, v.prepared[n].file, ver), label: VIEW_LABEL[n] })).join('')}</div>
      <div class="form-row">${check(`check.${key}.measure`, "mesurer l'azimut par SAM 3D Body", false)}
        <span class="sp"></span>${act('check', 'Contrôler ±5° ▸', { form: `check.${key}`, costume: key })}</div>`;
  }
  if (v.check) body += checkTable(v.check);
  let st = ['todo', 'à faire'];
  if (v.check) st = v.check.ok ? ['done', 'contrôle passé'] : ['partial', 'contrôle en échec'];
  else if (prepared.length) st = ['partial', 'préparées'];
  else if (raw.length) st = ['partial', `${raw.length} brute(s)`];
  return box({ sec: '05', title: 'Vues orthogonales', st: st[0], stLabel: st[1], body,
    next: isNext('views', 'prep', 'check') });
}

function checkTable(chk) {
  const rows = Object.entries(chk.angles).map(([n, r]) => {
    const ok = Math.abs(r.error) <= chk.tolerance;
    return `<tr><td>${esc(VIEW_LABEL[n] || n)}</td><td>${r.target}°</td><td>${Number(r.value).toFixed(1)}°${
      r.precision_deg != null ? ` ±${r.precision_deg}°` : ''}</td><td class="${ok ? 'ok' : 'no'}">${
      r.error > 0 ? '+' : ''}${Number(r.error).toFixed(1)}°</td><td>${esc(r.source)}</td></tr>`;
  }).join('');
  const missing = Object.entries(chk.errors).filter(([n]) => !chk.angles[n])
    .map(([n, e]) => `<tr><td>${esc(VIEW_LABEL[n] || n)}</td><td colspan="3" class="no">${esc(e)}</td><td></td></tr>`).join('');
  return `<div class="box-sub">Contrôle · ${chk.ok ? 'passé' : 'en échec'} · ±${chk.tolerance}°</div>
    <div class="table-wrap"><table class="table"><thead><tr><th>vue</th><th>cible</th><th>relevé</th><th>écart</th>
    <th>source</th></tr></thead><tbody>${rows}${missing}</tbody></table></div>
    ${chk.measured ? '' : '<p class="hint">Aucun estimateur de pose n\'a regardé ces images : angles déclarés ou tirés de la silhouette.</p>'}`;
}

function boxMesh(c, key, cos) {
  const v = cos.views;
  if (!Object.keys(v.prepared).length) return waitBox('06', 'Mesh 3D', 'Le mesh attend les vues préparées.');
  const form = `mesh.${key}`;
  const multiOk = v.check?.ok;
  let body = `<p>Le mesh PBR, canaux à part. En multi-vues, les quatre vues doivent avoir passé le contrôle ; une vue
    seule part du 3/4.</p>
    <div class="form-row">
      ${select(`${form}.engine`, 'Moteur', [['trellis2', 'TRELLIS 2 · MIT'], ['hunyuan3d-2.1', 'Hunyuan3D 2.1']], 'trellis2')}
      ${check(`${form}.single_view`, 'une vue (3/4)', !multiOk)}
      ${input(`${form}.seed`, 'Graine', { placeholder: 'au hasard', cls: 'num' })}
      <span class="sp"></span>${act('mesh', 'Générer ▸', { form, costume: key })}
    </div>
    <p class="hint">Hunyuan3D 2.1 : sa licence exclut l'UE, le Royaume-Uni et la Corée du Sud.${
      multiOk ? '' : ' Contrôle non passé : le multi-vues sera refusé.'}</p>`;
  if (cos.meshes.length) {
    body += `<div class="table-wrap"><table class="table"><thead><tr><th>v</th><th>moteur</th><th>sommets</th>
      <th>taille</th><th>entrée</th><th></th></tr></thead><tbody>${cos.meshes.slice().reverse().map((m) =>
      `<tr><td>v${m.version}</td><td>${esc(m.engine)}${m.backend === 'stub' ? ' · factice' : ''}</td>
       <td>${esc(m.stats?.vertices ?? '—')}</td><td>${esc(size(m.stats?.size_m))}</td>
       <td>${m.single_view ? 'une vue' : 'multi-vues'}</td>
       <td><a class="tb ghost sm" target="_blank" rel="noopener" href="./viewer.html?src=${
         encodeURIComponent(fileUrl(c.slug, m.glb, m.at))}">Voir</a></td></tr>`).join('')}</tbody></table></div>`;
  }
  const st = cos.meshes.length ? ['done', `v${cos.meshes.at(-1).version}`] : ['todo', 'à faire'];
  return box({ sec: '06', title: 'Mesh 3D', st: st[0], stLabel: st[1], body, next: isNext('mesh') });
}

function boxRig(c, key, cos) {
  if (!cos.meshes.length) return waitBox('07', 'Rig SOMA', 'Le rig attend un mesh.');
  const backend = state.detail.backends.unirig;
  let body = `<p>Squelette SOMA 77, bind en A-pose, cinq poses de contrôle à regarder dans le viewer avant
    d'accepter.${backend === 'stub' ? ' UniRig n\'est pas encore branché : le rig est factice.' : ''}</p>
    <div class="form-row"><span class="sp"></span>${act('rig', 'Rigger le dernier mesh ▸', { costume: key })}</div>`;
  if (cos.rigs.length) {
    const verdict = { unseen: 'à regarder', accepted: 'accepté', rejected: 'refusé' };
    body += `<div class="table-wrap"><table class="table"><thead><tr><th>v</th><th>mesh</th><th>moteur</th>
      <th>verdict</th><th></th></tr></thead><tbody>${cos.rigs.slice().reverse().map((r) =>
      `<tr><td>v${r.version}</td><td>v${r.mesh}</td><td>${esc(r.backend)}</td>
       <td class="${r.verdict === 'accepted' ? 'ok' : r.verdict === 'rejected' ? 'no' : ''}">${verdict[r.verdict]}</td>
       <td><a class="tb ghost sm" target="_blank" rel="noopener" href="./viewer.html?src=${
         encodeURIComponent(fileUrl(c.slug, r.glb, r.at))}">Poses</a>${r.verdict === 'unseen'
         ? act('rig_ok', 'Accepter', { costume: key, params: { verdict: 'accepte', rig: r.version } }) +
           act('rig_ok', 'Refuser', { costume: key, params: { verdict: 'refuse', rig: r.version } }) : ''}</td></tr>`)
      .join('')}</tbody></table></div>`;
  }
  const last = cos.rigs.at(-1);
  const st = !last ? ['todo', 'à faire'] : last.verdict === 'accepted' ? ['done', 'accepté']
    : ['partial', last.verdict === 'rejected' ? 'refusé' : 'à regarder'];
  return box({ sec: '07', title: 'Rig SOMA', st: st[0], stLabel: st[1], body, next: isNext('rig', 'rig_ok') });
}

/* ── en attendant : ce qu'on peut faire pendant un rendu ── */

function waitingCard(d) {
  const run = state.jobs.find((j) => j.status === 'running') || state.jobs.find((j) => j.status === 'queued');
  if (!run || !d) return '';
  const c = d.character;
  const heavy = usesH3(run);
  let body;
  if (!Object.keys(c.costumes).length) {
    body = `<p>Pendant que ça calcule : sa tenue. Elle attendra le visage verrouillé.</p>
      ${textarea('costume_new.brief', '', 'en français : ce qu\'il porte, de la tête aux pieds', 3)}
      <p class="hint">Gardée dès que tu quittes le champ.</p>`;
  } else if (!(c.identity || {}).personality_traits) {
    const picked = new Set(state.drafts['wait.traits'] || []);
    body = `<p>Pendant que ça calcule : trois traits de caractère.</p>
      <div class="chips-row">${TRAITS.map((t) => `<button class="chip${picked.has(t) ? ' chip-selected' : ''}"
        data-trait="${esc(t)}">${esc(t)}</button>`).join('')}</div>
      <div class="form-row"><span class="sp"></span><button class="tb ghost sm" data-save-traits${picked.size ? '' : ' disabled'}>
        Garder</button></div>`;
  } else {
    body = `<p>Pendant que ça calcule : relis sa fiche, corrige d'un clic ce qui ne va pas.</p>`;
  }
  body += heavy
    ? '<p class="hint">H3 occupe la mémoire : l\'assistant revient après ce rendu.</p>'
    : `<a class="tb ghost block" href="./console.html?slug=${encodeURIComponent(c.slug)}">Parler avec l'assistant ▸</a>`;
  return box({ sec: '', title: 'En attendant', st: 'partial', stLabel: run.label, body });
}

/* ── la fiche : ce que le studio sait de lui ────────────── */

function fiche(d) {
  const c = d.character;
  const sheet = c.identity || {};
  const rows = SHEET_FIELDS.filter((f) => f.section === 'CORE' || f.section === 'PSYCHE')
    .filter((f) => f.key !== 'character_name').map((f) => {
      const v = sheet[f.key] || '';
      const cell = state.editField === f.key
        ? `<form data-form="field" data-key="${f.key}" class="sheet-edit"><input class="fld" name="v" value="${esc(v)}"
            autocomplete="off"></form>`
        : `<div class="sheet-val ${v ? 'filled' : 'empty'}" data-edit="${f.key}" title="modifier">${esc(v)}</div>`;
      return `<div class="sheet-row"><div class="sheet-key">${esc(f.label)}</div>${cell}</div>`;
    }).join('');
  const notes = (c.notes || []).length ? `<div class="sheet-section"><div class="sheet-section-title">notes</div>${
    c.notes.map((n, i) => `<div class="sheet-row"><div class="sheet-key">#${i + 1}</div><div class="sheet-val filled">${
      esc(n)}</div></div>`).join('')}</div>` : '';
  const filled = d.summary.identity.filled;
  const body = `<div class="sheet-section">${rows}</div>${notes}
    <a class="tb ghost block" href="./console.html?slug=${encodeURIComponent(c.slug)}">Approfondir avec l'assistant ▸</a>`;
  return box({ sec: '', title: 'La fiche', st: filled ? 'partial' : 'todo', stLabel: `${filled}/${d.summary.identity.total}`, body });
}

/* ── la file ────────────────────────────────────────────── */

function renderJobs() {
  const jobs = state.jobs.slice(0, 14);
  if (!jobs.length) return '<div class="jobs-empty">aucun travail pour ce personnage</div>';
  return jobs.map((j) => {
    const live = j.status === 'running' || j.status === 'queued';
    const log = state.fullLogs[j.id] || j.log || [];
    const where = j.params?.costume ? ` · ${j.params.costume}` : '';
    return `<div class="job ${j.status}">
      <div class="top"><span class="nm">${esc(j.label + where)}</span><span class="st ${j.status}">${
        JOB_STATE[j.status] || j.status}</span></div>
      ${live ? `<div class="bar"><i style="width:${Math.round((j.progress || 0) * 100)}%"></i></div>` : ''}
      <div class="msg">${esc(j.error || j.message || '')}</div>
      <div class="form-row">
        <details data-job="${j.id}"${state.openLogs.has(j.id) ? ' open' : ''}><summary>journal · ${
          esc(when(j.started || j.created))}</summary><pre>${esc(log.join('\n') || '—')}</pre></details>
        ${live ? `<span class="sp"></span><button class="tb ghost sm" data-cancel="${j.id}">Annuler</button>` : ''}
      </div></div>`;
  }).join('');
}

function paintJobs() {
  const node = $('#jobs');
  if (!node) return;
  node.innerHTML = renderJobs();
  const wait = $('#waiting');
  if (wait && !wait.contains(document.activeElement)) wait.innerHTML = waitingCard(state.detail);
  const live = state.jobs.filter((j) => j.status === 'running' || j.status === 'queued').length;
  $('#jobs-count').textContent = live ? `${live} actif${live > 1 ? 's' : ''}` : '';
}

/* ── rendu ──────────────────────────────────────────────── */

function typing() {
  const a = document.activeElement;
  return a && $('#app').contains(a) && a.matches('input:not([type=checkbox]):not([type=file]), textarea, select');
}

function render(force = false) {
  if (!force && typing()) { state.pending = true; return; }
  state.pending = false;
  $('#app').innerHTML = state.route.view === 'perso' ? renderPerso() : renderHome();
  paintJobs();
}

/* ── chargements ────────────────────────────────────────── */

async function loadList() {
  state.list = await api('/api/characters');
}

async function loadDetail() {
  const d = await api(`/api/characters/${encodeURIComponent(slug())}`);
  const keys = Object.keys(d.character.costumes);
  if (state.costume === undefined || (state.costume !== null && !keys.includes(state.costume))) {
    state.costume = d.summary.next?.costume || keys[0] || null;
  }
  const sig = JSON.stringify(d);
  const changed = sig !== state.sig;
  state.sig = sig;
  state.detail = d;
  return changed;
}

async function loadJobs() {
  const { jobs } = await api(`/api/jobs?slug=${encodeURIComponent(slug())}`);
  state.jobs = jobs;
  await Promise.all([...state.openLogs].filter((id) => jobs.some((j) => j.id === id && j.status === 'running'))
    .map(fetchLog));
  return jobs;
}

async function fetchLog(id) {
  try {
    state.fullLogs[id] = (await api(`/api/jobs/${id}`)).log;
  } catch (_) { /* travail oublié par le serveur */ }
}

/* ── la navigation ──────────────────────────────────────── */

function parseRoute() {
  const h = location.hash.replace(/^#\/?/, '');
  const m = /^p\/([^/]+)/.exec(h);
  return m ? { view: 'perso', slug: decodeURIComponent(m[1]) } : { view: 'home' };
}

async function onRoute() {
  const route = parseRoute();
  const changed = route.view !== state.route.view || route.slug !== state.route.slug;
  state.route = route;
  if (changed) {
    state.detail = null;
    state.jobs = [];
    state.costume = undefined;
    state.stage = null;
    state.renaming = false;
    state.editField = null;
    state.sig = '';
    state.seen.clear();
    window.scrollTo(0, 0);
  }
  render(true);
  try {
    if (route.view === 'home') {
      await loadList();
    } else {
      await loadDetail();
      (await loadJobs()).forEach((j) => { if (!['queued', 'running'].includes(j.status)) state.seen.add(j.id); });
      document.title = `${state.detail.character.name.toUpperCase()} · STUDIO`;
    }
    if (route.view === 'home') document.title = 'CHARACTER FACTORY · STUDIO';
    render(true);
  } catch (e) {
    $('#app').innerHTML = `<div class="pan"><p class="prose">${esc(e.message)}</p>
      <p class="prose"><a class="tb ghost sm" href="#/">◂ Studio</a></p></div>`;
  }
}

/* ── les actions ────────────────────────────────────────── */

function collect(btn) {
  const params = btn.dataset.params ? JSON.parse(btn.dataset.params) : {};
  if (btn.dataset.costume) params.costume = btn.dataset.costume;
  const form = btn.dataset.form;
  if (form) {
    for (const node of $$(`[data-draft^="${CSS.escape(form)}."]`, $('#app'))) {
      params[node.dataset.draft.slice(form.length + 1)] = node.type === 'checkbox' ? node.checked : node.value;
    }
    const refs = state.refs[form] || [];
    if (refs.length) params.refs = refs.map((r) => r.id);
  }
  return params;
}

function forget(form) {
  if (!form) return;
  // Les réglages (variantes, graine, méthode) restent d'un essai à l'autre ;
  // les images déposées sont parties avec l'action.
  if (form === 'costume_new') {
    for (const k of Object.keys(state.drafts)) if (k.startsWith(`${form}.`)) delete state.drafts[k];
  }
  (state.refs[form] || []).forEach((r) => URL.revokeObjectURL(r.url));
  delete state.refs[form];
}

async function doAction(btn) {
  const action = btn.dataset.act;
  if (btn.dataset.confirm && !window.confirm(btn.dataset.confirm)) return;
  const params = collect(btn);
  btn.disabled = true;
  try {
    if (action === 'costume_go') {
      // Une nouvelle tenue : elle se garde (une seule fois, même si le champ
      // vient d'être quitté), puis son plein pied part dans la file.
      const key = await createCostume();
      if (!key) { btn.disabled = false; return; }
      const run = await api(actionUrl('fullbody'), {
        method: 'POST', body: { costume: key, variants: params.variants },
      });
      state.jobs.unshift(run.job);
      toast(`${run.job.label} : en file`);
      await loadDetail();
      render(true);
      return;
    }
    const out = await api(actionUrl(action), { method: 'POST', body: params });
    if (out.job) {
      toast(`${out.job.label} : en file`);
      state.jobs.unshift(out.job);
    } else {
      toast('fait');
      if (action === 'costume_add') state.costume = out.result.costume;
      // Un choix qui clôt une étape : l'atelier reprend la suite de la chaîne.
      if (PICKS.has(action)) state.stage = null;
    }
    forget(btn.dataset.form);
    await loadDetail();
    render(true);
  } catch (e) {
    toast(e.message, 7000);
    btn.disabled = false;
  }
}

/* La tenue se garde toute seule : son texte quand on quitte le champ, ses
   images dès qu'elles sont déposées. Pas de bouton « Enregistrer », et
   jamais de tenue vide. */
let costumeSaving = null;

function createCostume() {
  if (costumeSaving) return costumeSaving;
  const brief = String(state.drafts['costume_new.brief'] || '').trim();
  const refs = (state.refs.costume_new || []).map((r) => r.id);
  if (!brief && !refs.length) {
    toast("décris d'abord la tenue, ou dépose une image de vêtement", 5000);
    return Promise.resolve(null);
  }
  costumeSaving = (async () => {
    try {
      const out = await api(actionUrl('costume_add'), {
        method: 'POST', body: { brief, refs, name: state.drafts['costume_new.name'] || '' },
      });
      state.costume = out.result.costume;
      forget('costume_new');
      delete state.drafts['costume_new.brief'];
      delete state.drafts['costume_new.name'];
      toast('tenue gardée');
      await loadDetail();
      render();
      return state.costume;
    } catch (e) {
      toast(e.message, 6000);
      return null;
    } finally {
      costumeSaving = null;
    }
  })();
  return costumeSaving;
}

async function saveCostume(key, fields) {
  try {
    await api(actionUrl('costume_edit'), { method: 'POST', body: { costume: key, ...fields } });
    await loadDetail();
    render();
  } catch (e) {
    toast(e.message, 6000);
  }
}

async function uploadFiles(form, files) {
  for (const f of files) {
    if (!f.type.startsWith('image/')) continue;
    try {
      const out = await api('/api/uploads', {
        method: 'POST',
        raw: { body: f, headers: { 'content-type': f.type, 'x-filename': encodeURIComponent(f.name) } },
      });
      (state.refs[form] ||= []).push({ id: out.id, url: URL.createObjectURL(f), name: f.name });
    } catch (e) {
      toast(`${f.name} : ${e.message}`, 6000);
    }
  }
  const cos = /^cos\.(.+)$/.exec(form);
  if (cos && (state.refs[form] || []).length) {
    const ids = state.refs[form].map((r) => r.id);
    forget(form);
    await saveCostume(cos[1], { refs: ids });
    toast('image gardée dans la tenue');
  } else if (form === 'costume_new') {
    await createCostume();
  }
  render(true);
}

async function createCharacter(form) {
  const name = form.elements.name.value.trim();
  if (!name) { form.elements.name.focus(); return; }
  try {
    const out = await api('/api/characters', { method: 'POST', body: { name } });
    location.hash = `#/p/${encodeURIComponent(out.slug)}`;
  } catch (e) {
    toast(e.message, 6000);
  }
}

async function saveIdentity(body, message) {
  try {
    await api(`/api/characters/${encodeURIComponent(slug())}/identity`, { method: 'PUT', body });
    await loadDetail();
    if (message) toast(message);
  } catch (e) {
    toast(e.message, 6000);
  }
}

async function rename(form) {
  const name = form.elements.name.value.trim();
  state.renaming = false;
  if (name && name !== state.detail.character.name) await saveIdentity({ name }, 'renommé');
  render(true);
}

async function saveField(form) {
  const key = form.dataset.key;
  const value = form.elements.v.value.trim();
  state.editField = null;
  if (value !== ((state.detail.character.identity || {})[key] || '')) await saveIdentity({ fields: { [key]: value } });
  render(true);
}

async function setStyle(value) {
  try {
    await api(`/api/characters/${encodeURIComponent(slug())}/identity`, { method: 'PUT', body: { style: value } });
    await loadDetail();
    render(true);
    toast(`style : ${value === 'stylized' ? 'stylisé' : 'photoréaliste'}`);
  } catch (e) {
    toast(e.message, 6000);
  }
}

async function cancelJob(id) {
  try {
    const out = await api(`/api/jobs/${id}/cancel`, { method: 'POST' });
    toast(out.message, 4000);
    await loadJobs();
    paintJobs();
  } catch (e) {
    toast(e.message, 6000);
  }
}

function zoom(src, cap) {
  $('#lightbox-img').src = src;
  $('#lightbox-cap').textContent = cap || '';
  $('#lightbox').hidden = false;
}

function wire() {
  const app = $('#app');

  app.addEventListener('click', (e) => {
    const t = e.target;
    const actBtn = t.closest('[data-act]');
    if (actBtn) { e.preventDefault(); e.stopPropagation(); doAction(actBtn); return; }
    const tab = t.closest('[data-costume-tab]');
    if (tab) { state.costume = tab.dataset.costumeTab || null; render(true); return; }
    const step = t.closest('[data-stage]');
    if (step && !step.disabled) { state.stage = step.dataset.stage; render(true); window.scrollTo(0, 0); return; }
    const goto = t.closest('[data-goto]');
    if (goto) {
      state.stage = goto.dataset.goto;
      if (goto.dataset.goto === 'costume' && !Object.keys(state.detail.character.costumes).length) state.costume = null;
      render(true);
      window.scrollTo(0, 0);
      return;
    }
    if (t.closest('[data-rename]')) {
      state.renaming = true;
      render(true);
      const inp = $('form[data-form="rename"] input');
      if (inp) { inp.focus(); inp.select(); }
      return;
    }
    const styleSet = t.closest('[data-style-set]');
    if (styleSet) { setStyle(styleSet.dataset.styleSet); return; }
    const edit = t.closest('[data-edit]');
    if (edit) {
      state.editField = edit.dataset.edit;
      render(true);
      const inp = $('form[data-form="field"] input');
      if (inp) inp.focus();
      return;
    }
    const trait = t.closest('[data-trait]');
    if (trait) {
      const picked = new Set(state.drafts['wait.traits'] || []);
      picked.has(trait.dataset.trait) ? picked.delete(trait.dataset.trait) : picked.add(trait.dataset.trait);
      state.drafts['wait.traits'] = [...picked];
      $('#waiting').innerHTML = waitingCard(state.detail);
      return;
    }
    if (t.closest('[data-save-traits]')) {
      const traits = (state.drafts['wait.traits'] || []).join(', ');
      delete state.drafts['wait.traits'];
      saveIdentity({ fields: { personality_traits: traits } }, 'traits gardés').then(() => render(true));
      return;
    }
    const drop = t.closest('[data-drop-ref]');
    if (drop) {
      e.preventDefault();
      const [form, i] = drop.dataset.dropRef.split(':');
      const [gone] = (state.refs[form] || []).splice(Number(i), 1);
      if (gone) URL.revokeObjectURL(gone.url);
      render(true);
      return;
    }
    const cancel = t.closest('[data-cancel]');
    if (cancel) { cancelJob(cancel.dataset.cancel); return; }
    const z = t.closest('[data-zoom]');
    if (z) zoom(z.dataset.zoom, z.dataset.cap);
  });

  const keep = (e) => {
    const n = e.target;
    if (n.dataset?.draft) state.drafts[n.dataset.draft] = n.type === 'checkbox' ? n.checked : n.value;
  };
  app.addEventListener('input', keep);
  app.addEventListener('change', (e) => {
    keep(e);
    const draftKey = e.target.dataset?.draft || '';
    const cosBrief = /^cos\.(.+)\.brief$/.exec(draftKey);
    if (cosBrief) saveCostume(cosBrief[1], { brief: e.target.value });
    if (draftKey === 'costume_new.brief' && e.target.value.trim()) createCostume();
    if (e.target.dataset?.refs) { uploadFiles(e.target.dataset.refs, [...e.target.files]); e.target.value = ''; }
    if (e.target.matches('[data-style]')) setStyle(e.target.value);
  });
  app.addEventListener('submit', (e) => {
    const form = e.target.dataset.form;
    if (!form) return;
    e.preventDefault();
    if (form === 'create') createCharacter(e.target);
    if (form === 'rename') rename(e.target);
    if (form === 'field') saveField(e.target);
  });
  app.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (state.renaming || state.editField) { state.renaming = false; state.editField = null; render(true); }
  });
  app.addEventListener('toggle', (e) => {
    const id = e.target.dataset?.job;
    if (!id) return;
    if (e.target.open) {
      state.openLogs.add(id);
      fetchLog(id).then(() => {
        const pre = $(`details[data-job="${id}"] pre`);
        if (pre && state.fullLogs[id]) pre.textContent = state.fullLogs[id].join('\n') || '—';
      });
    } else state.openLogs.delete(id);
  }, true);
  app.addEventListener('focusout', (e) => {
    // Un champ de la fiche se garde en quittant le champ, comme avec Entrée.
    const field = e.target.closest?.('form[data-form="field"]');
    if (field && state.editField) setTimeout(() => { if (state.editField) saveField(field); }, 120);
    setTimeout(() => { if (state.pending && !typing()) render(); }, 180);
  });

  // Déposer une image sur une zone de références.
  app.addEventListener('dragover', (e) => {
    const zone = e.target.closest?.('[data-refs-zone]');
    if (zone) { e.preventDefault(); zone.classList.add('over'); }
  });
  app.addEventListener('dragleave', (e) => {
    e.target.closest?.('[data-refs-zone]')?.classList.remove('over');
  });
  app.addEventListener('drop', (e) => {
    const zone = e.target.closest?.('[data-refs-zone]');
    if (!zone) return;
    e.preventDefault();
    uploadFiles(zone.dataset.refsZone, [...e.dataTransfer.files]);
  });

  $('#lightbox').addEventListener('click', () => { $('#lightbox').hidden = true; });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('#lightbox').hidden = true; });
  window.addEventListener('hashchange', onRoute);
}

/* ── relevés périodiques ────────────────────────────────── */

async function pollJobs() {
  let active = false;
  try {
    if (state.route.view === 'perso' && state.detail) {
      const jobs = await loadJobs();
      active = jobs.some((j) => j.status === 'running' || j.status === 'queued');
      const finished = jobs.filter((j) => !['queued', 'running'].includes(j.status) && !state.seen.has(j.id));
      finished.forEach((j) => {
        state.seen.add(j.id);
        toast(j.status === 'done' ? `${j.label} : fini` : `${j.label} : ${j.error || j.status}`, 5000);
      });
      // Pendant un travail, les candidats arrivent un à un : on relit
      // le personnage à chaque relevé et on redessine s'il a changé.
      if (await loadDetail() || finished.length) render();
      else paintJobs();
    } else if (state.route.view === 'home') {
      const before = JSON.stringify(state.list);
      await loadList();
      if (JSON.stringify(state.list) !== before) render();
    }
  } catch (_) { /* serveur momentanément muet : on réessaie */ }
  setTimeout(pollJobs, active ? 1500 : 5000);
}

async function pollSystem() {
  const pill = $('#sys-pill');
  try {
    const s = await api('/api/system');
    state.system = s;
    const mem = s.memory.available_gb;
    const low = mem != null && mem < s.memory.min_free_gb;
    const run = s.running;
    const parts = [];
    if (run) parts.push(`${run.label} · ${run.slug}`);
    if (mem != null) parts.push(`${Math.round(mem)} go libres`);
    parts.push(s.llm.loaded.length ? `texte chargé` : 'texte au repos');
    $('#sys-text').textContent = parts.join(' · ');
    pill.className = `pill ${run ? 'work' : low ? 'err' : 'on'}`;
    pill.title = [`modèle de texte : ${s.llm.model}${s.llm.loaded.length ? ' (chargé)' : ''}`,
      ...Object.entries(s.comfy).map(([u, c]) => `ComfyUI ${u} : ${c.busy === null ? 'muet' : c.busy ? 'occupé' : 'libre'}${
        c.family ? ` · ${c.family}` : ''}`),
      `en file : ${s.queued}`].join('\n');
  } catch (e) {
    $('#sys-text').textContent = 'studio muet';
    pill.className = 'pill err';
  }
  setTimeout(pollSystem, 5000);
}

wire();
onRoute().then(() => { pollJobs(); pollSystem(); });
