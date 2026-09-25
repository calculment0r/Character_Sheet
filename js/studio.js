'use strict';

import { SHEET_FIELDS, SECTIONS } from './schema.js';

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
  sig: '',
  system: null,
};

// Les choix ne prennent jamais l'orange : il y en a un par candidat.
const PICKS = new Set(['face_lock', 'fullbody_ok', 'sheet_ok', 'rig_ok']);
const ORTHO = ['front', 'left', 'back', 'right'];
const VIEWS = [...ORTHO, 'threequarter'];
const VIEW_LABEL = { front: 'face', left: 'profil gauche', back: 'dos', right: 'profil droit', threequarter: '3/4' };
const METHOD_LABEL = {
  orbit: 'H3 · orbite redécoupée',
  per_view: 'H3 · une génération par vue',
  'qwen21-orbit': 'Qwen-Image 2.1 · LoRA orbite',
  'qwen-2511': 'Qwen-Image-Edit 2511 · LoRA angles',
  'qwen-2509': 'Qwen-Image-Edit 2509 · LoRA angles',
};
const NEXT_TEXT = {
  face: 'suite : variantes du visage', face_lock: 'suite : verrouiller un visage',
  costume_add: 'suite : un costume', fullbody: 'suite : plein pied', fullbody_ok: 'suite : valider un plein pied',
  sheet: 'suite : planche', sheet_ok: 'suite : valider une planche', views: 'suite : vues orthogonales',
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
function act(action, label, { costume, form, params, confirm: ask, disabled = false, block = false } = {}) {
  const next = state.detail?.summary.next;
  const go = next && next.action === action && !PICKS.has(action) && (!next.costume || next.costume === costume);
  const running = activeJob(action, costume);
  const attrs = [
    `data-act="${action}"`,
    costume ? `data-costume="${esc(costume)}"` : '',
    form ? `data-form="${esc(form)}"` : '',
    params ? `data-params="${esc(JSON.stringify(params))}"` : '',
    ask ? `data-confirm="${esc(ask)}"` : '',
    disabled || running || state.detail?.busy && PICKS.has(action) ? 'disabled' : '',
  ].filter(Boolean).join(' ');
  const cls = `tb ${go ? 'go' : 'ghost'}${block ? ' block' : ''}${PICKS.has(action) ? ' sm' : ''}`;
  return `<button class="${cls}" ${attrs}>${esc(running ? `${running.status === 'queued' ? 'en file' : 'en cours'}…` : label)}</button>`;
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
  const locked = list.filter((c) => c.locked).length;
  return `
  <div class="console-top">
    <div class="hero">
      <span class="ref">00_studio</span>
      <h2 class="studio-title">Les personnages</h2>
      <p>Un personnage par carte. Un clic l'ouvre : la fiche d'identité par la conversation, puis le visage,
        les costumes, le plein pied, la planche, les vues, la 3D et le rig — chaque étage à la main, dans
        l'ordre. Tout calcule sur DGX2, un travail à la fois.</p>
    </div>
    <div class="statcard">
      <span class="ref">personnages</span>
      <span class="n">${pad(list.length)}</span>
      <span class="foot">${locked} visage${locked > 1 ? 's' : ''} verrouillé${locked > 1 ? 's' : ''}</span>
      <span class="dots"></span>
    </div>
  </div>
  <section class="sect">
    <div class="sect-head"><h2>Personnages</h2><span class="k">ST-00</span>
      <span class="cnt">${list.length} au studio</span></div>
    <div class="cards">${newCard()}${list.map(card).join('')}</div>
  </section>`;
}

function newCard() {
  return `<div class="card new">
    <div class="who">
      <span class="ref">nouveau</span>
      <span class="nm">Un personnage</span>
      <p class="hint">La conversation remplit la fiche d'identité avec le modèle de texte de DGX2, puis crée
        le personnage.</p>
    </div>
    <a class="tb go block" href="./console.html?new=1">Nouveau personnage ▸</a>
    <form data-form="quick">
      <input class="fld" name="name" placeholder="ou juste un nom" autocomplete="off">
      <button class="tb ghost" type="submit">Créer</button>
    </form>
  </div>`;
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

/* ── un personnage ──────────────────────────────────────── */

function renderPerso() {
  const d = state.detail;
  if (!d) return '<p class="prose">chargement…</p>';
  const c = d.character;
  const s = d.summary;
  const fake = Object.entries(d.backends).filter(([k, v]) => v === 'stub' && ['h3', 'trellis'].includes(k))
    .map(([k]) => k);
  const img = s.thumb ? `<img class="face" src="${esc(s.thumb)}" alt="" data-zoom="${esc(s.thumb)}" data-cap="${
    esc(c.name)}">` : '<div class="face"></div>';
  return `
  <div class="perso-head">
    ${img}
    <div class="who">
      <span class="ref">${esc(c.slug)} · ${esc(c.style)}${fake.length ? ` · ${esc(fake.join(', '))} factice` : ''}</span>
      <h1>${esc(c.name)}</h1>
      <span class="role">${esc([s.role, s.archetype].filter(Boolean).join(' · '))}</span>
      ${ticks(s.stages)}
    </div>
    <div class="acts">
      <a class="tb ghost sm" href="#/">◂ Studio</a>
      <a class="tb ghost sm" href="./console.html?slug=${encodeURIComponent(c.slug)}">Conversation</a>
    </div>
  </div>
  <div class="perso-grid">
    <div class="perso-main">
      ${boxFace(d)}
      ${boxCostumes(d)}
      ${state.costume ? costumeBoxes(d, state.costume) : ''}
    </div>
    <aside class="perso-side">
      <div class="c-head"><h2>Travaux</h2><span class="sec">file</span><span class="cnt" id="jobs-count"></span></div>
      <div class="jobs" id="jobs">${renderJobs()}</div>
      ${boxIdentity(d)}
    </aside>
  </div>`;
}

function boxFace(d) {
  const c = d.character;
  const f = c.face;
  const st = f.locked ? ['done', 'verrouillé'] : f.candidates.length ? ['partial', `${f.candidates.length} candidat(s)`]
    : ['todo', 'à faire'];
  let body;
  if (f.locked) {
    const src = fileUrl(c.slug, f.locked, f.locked_at);
    body = `<div class="hero-img">
      <img src="${esc(src)}" alt="" data-zoom="${esc(src)}" data-cap="visage verrouillé">
      <div class="stage-body">
        <dl class="kv">
          <dt>candidat</dt><dd>${esc(base(f.locked_from))}</dd>
          <dt>graine</dt><dd>${esc(f.locked_seed ?? '—')}</dd>
          <dt>moteur</dt><dd>${esc(f.locked_backend || '—')}</dd>
          <dt>verrouillé</dt><dd>${esc(when(f.locked_at))}</dd>
        </dl>
        <p class="hint">Le visage fait autorité sur toute la suite et ne change plus. Pour un autre visage, un
          autre personnage.</p>
      </div></div>`;
  } else {
    body = `<p>Le portrait neutre, de face. Génère des variantes, puis verrouille celle qui fait le personnage —
      une seule fois.</p>
      <label class="field"><span class="lbl">Précisions pour le visage</span>
        ${textarea('face.prompt', f.prompt, 'ce que la fiche ne dit pas : traits, peau, cheveux… (facultatif)')}</label>
      <div class="field"><span class="lbl">Photo source · facultatif</span>${refsZone('face', 'photo')}
        ${f.refs.length ? `<p class="hint">déjà en référence : ${esc(f.refs.map(base).join(', '))}</p>` : ''}</div>
      <div class="form-row">
        ${select('face.variants', 'Variantes', [1, 2, 3, 4, 6, 8].map((n) => [n, String(n)]), 4)}
        ${input('face.seed', 'Graine', { placeholder: 'au hasard', cls: 'num' })}
        <span class="sp"></span>
        ${act('face', 'Générer ▸', { form: 'face' })}
      </div>`;
    if (f.candidates.length) {
      body += `<div class="box-sub">Candidats</div><div class="cands">${f.candidates.map((x, i) => {
        const [mark, markCls] = stubMark(x);
        return cand({
          src: fileUrl(c.slug, x.file, x.at), label: `n° ${i + 1}`, cap: `n° ${i + 1} · graine ${x.seed}`, mark, markCls,
          button: act('face_lock', 'Verrouiller', {
            params: { candidate: String(i + 1) },
            confirm: `Verrouiller le visage n° ${i + 1} ? Il fera autorité sur toute la suite ; on ne pourra plus le changer.`,
          }),
        });
      }).reverse().join('')}</div>`;
    }
  }
  return box({ sec: 'ST-02', title: 'Visage', st: st[0], stLabel: st[1], body, next: isNext('face', 'face_lock') });
}

function boxCostumes(d) {
  const c = d.character;
  const keys = Object.keys(c.costumes);
  const tabs = keys.map((k) => `<button class="tb sm ${k === state.costume ? 'on' : 'ghost'}" data-costume-tab="${
    esc(k)}">${esc(c.costumes[k].name)}</button>`).join('') +
    `<button class="tb sm ${state.costume === null ? 'on' : 'ghost'}" data-costume-tab="">+ Costume</button>`;
  let body = `<div class="tabs">${tabs}</div>`;
  if (state.costume === null) {
    body += `<p>Un costume : sa description, et des images de vêtements s'il y en a. Le plein pied part du visage
      verrouillé et de ces références.</p>
      <div class="form-row">${input('costume_new.name', 'Nom du costume', { placeholder: 'travail, gala…', grow: true })}</div>
      <label class="field"><span class="lbl">Description</span>
        ${textarea('costume_new.prompt', '', 'coupe, matières, couleurs, usure, accessoires portés…')}</label>
      <div class="field"><span class="lbl">Vêtements · images</span>${refsZone('costume_new', 'vêtement')}</div>
      <div class="form-row"><span class="sp"></span>${act('costume_add', 'Créer le costume ▸', { form: 'costume_new' })}</div>`;
  } else {
    const key = state.costume;
    const cos = c.costumes[key];
    const form = `cos.${key}`;
    body += `<label class="field"><span class="lbl">Description</span>
        ${textarea(`${form}.prompt`, cos.prompt, 'coupe, matières, couleurs, usure, accessoires portés…')}</label>
      <div class="field"><span class="lbl">Vêtements · images</span>
        <div class="refs">${cos.refs.map((r) => {
          const src = fileUrl(c.slug, r);
          return `<span class="thumb" style="background-image:url('${esc(src)}')" title="${esc(base(r))}" data-zoom="${
            esc(src)}" data-cap="${esc(base(r))}"><button data-act="costume_edit" data-costume="${esc(key)}"
            data-params="${esc(JSON.stringify({ drop_refs: [r] }))}" title="retirer"
            data-confirm="${esc(`Retirer ${base(r)} des références du costume ?`)}">×</button></span>`;
        }).join('')}</div>
        ${refsZone(form, 'vêtement')}</div>
      <div class="form-row"><span class="hint">Un plein pied déjà validé ne change pas : régénère-le après une
        modification.</span><span class="sp"></span>${act('costume_edit', 'Enregistrer', { form, costume: key })}</div>`;
  }
  const st = keys.length ? ['done', `${keys.length} costume(s)`] : ['todo', 'à faire'];
  return box({ sec: 'ST-03', title: 'Costumes', st: st[0], stLabel: st[1], body, next: isNext('costume_add') });
}

function costumeBoxes(d, key) {
  const c = d.character;
  const cos = c.costumes[key];
  if (!cos) return '';
  return [boxFullbody(c, key, cos), boxSheet(c, key, cos), boxViews(c, key, cos, d), boxMesh(c, key, cos),
    boxRig(c, key, cos)].join('');
}

function waitBox(sec, title, text) {
  return box({ sec, title, st: 'todo', stLabel: 'en attente', body: `<p>${esc(text)}</p>`, wait: true });
}

function boxFullbody(c, key, cos) {
  const fb = cos.fullbody;
  if (!c.face.locked) return waitBox('ST-04', 'Plein pied', 'Le plein pied attend le visage verrouillé.');
  const form = `fb.${key}`;
  let body = `<p>Le personnage en pied, habillé du costume, de face : le visage verrouillé et les vêtements en
    références. Valide celui qui tient — la planche partira de lui.</p>
    <div class="form-row">
      ${select(`${form}.variants`, 'Variantes', [1, 2, 3, 4, 6].map((n) => [n, String(n)]), 2)}
      ${input(`${form}.seed`, 'Graine', { placeholder: 'au hasard', cls: 'num' })}
      <span class="sp"></span>${act('fullbody', 'Générer ▸', { form, costume: key })}
    </div>`;
  if (fb.candidates.length) {
    body += `<div class="box-sub">Candidats</div><div class="cands tall">${fb.candidates.map((x, i) => {
      const chosen = x.file === fb.validated_from;
      const [mark, markCls] = chosen ? ['validé', 'ok'] : stubMark(x);
      return cand({
        src: fileUrl(c.slug, x.file, x.at), label: `n° ${i + 1}`, cap: `n° ${i + 1} · graine ${x.seed}`, mark, markCls,
        sel: chosen,
        button: chosen ? '' : act('fullbody_ok', 'Valider', {
          costume: key, params: { candidate: String(i + 1) },
          confirm: fb.validated ? 'Valider ce plein pied ? La planche validée sur l\'ancien ne vaudra plus.' : undefined,
        }),
      });
    }).reverse().join('')}</div>`;
  }
  const st = fb.validated ? ['done', 'validé'] : fb.candidates.length ? ['partial', `${fb.candidates.length} candidat(s)`]
    : ['todo', 'à faire'];
  return box({ sec: 'ST-04', title: `Plein pied · ${cos.name}`, st: st[0], stLabel: st[1], body,
    next: isNext('fullbody', 'fullbody_ok') });
}

function boxSheet(c, key, cos) {
  if (!cos.fullbody.validated) return waitBox('ST-05', 'Planche', 'La planche attend un plein pied validé.');
  const form = `sheet.${key}`;
  let body = `<p>La planche de personnage en une génération : plein pied de face, de profil, de dos, 3/4, gros plans.
    Elle valide le design avant les vues. Le disque sur le visage des plein pieds se juge sur pièces : A/B en rend
    deux, même graine, avec et sans.</p>
    <div class="form-row">
      ${check(`${form}.ab`, 'A/B avec et sans disque', true)}
      ${check(`${form}.mask_face`, 'disque sur le visage', false)}
      ${input(`${form}.seed`, 'Graine', { placeholder: 'au hasard', cls: 'num' })}
      <span class="sp"></span>${act('sheet', 'Générer ▸', { form, costume: key })}
    </div>`;
  if (cos.sheets.length) {
    body += `<div class="box-sub">Planches</div><div class="cands wide">${cos.sheets.map((s) => {
      const chosen = s.id === cos.sheet;
      const [mark, markCls] = chosen ? ['validée', 'ok'] : stubMark(s);
      return cand({
        src: fileUrl(c.slug, s.file, s.at), label: `${s.id} · ${s.mask_face ? 'disque' : 'sans disque'}`,
        cap: `${s.id} · ${s.mask_face ? 'disque' : 'sans disque'} · graine ${s.seed}`,
        mark, markCls, sel: chosen,
        button: chosen ? '' : act('sheet_ok', 'Valider', {
          costume: key, params: { id: s.id },
          confirm: cos.views.raw && Object.keys(cos.views.raw).length
            ? 'Valider cette planche ? Les vues faites sur l\'ancienne ne vaudront plus.' : undefined,
        }),
      });
    }).reverse().join('')}</div>`;
  }
  const st = cos.sheet ? ['done', `${cos.sheet} validée`] : cos.sheets.length ? ['partial', `${cos.sheets.length} planche(s)`]
    : ['todo', 'à faire'];
  return box({ sec: 'ST-05', title: 'Planche', st: st[0], stLabel: st[1], body, next: isNext('sheet', 'sheet_ok') });
}

function boxViews(c, key, cos, d) {
  if (!cos.sheet) return waitBox('ST-06', 'Vues orthogonales', 'Les vues attendent une planche validée.');
  const v = cos.views;
  const form = `views.${key}`;
  const methods = (d.view_methods || Object.keys(METHOD_LABEL)).map((m) => [m, METHOD_LABEL[m] || m]);
  let body = `<p>Face, profils, dos et 3/4, plein cadre, pour la 3D. L'orbite H3 tient les angles ; une génération par
    vue revient vers la face. Les LoRA d'angle Qwen tournent le plein pied validé.</p>
    <div class="form-row">
      ${select(`${form}.method`, 'Méthode', methods, v.method || 'orbit')}
      ${input(`${form}.seed`, 'Graine', { placeholder: 'au hasard', cls: 'num' })}
      <span class="sp"></span>${act('views', 'Générer ▸', { form, costume: key })}
    </div>`;
  const raw = VIEWS.filter((n) => v.raw[n]);
  if (raw.length) {
    body += `<div class="box-sub">Vues brutes · ${esc(METHOD_LABEL[v.method] || v.method || '')}</div>
      <div class="cands tall">${raw.map((n) => {
        const e = v.raw[n];
        const est = e.azimuth_estimated != null ? ` · relevé ${Number(e.azimuth_estimated).toFixed(0)}°` : '';
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
  return box({ sec: 'ST-06', title: 'Vues orthogonales', st: st[0], stLabel: st[1], body,
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
  if (!Object.keys(v.prepared).length) return waitBox('ST-07', 'Mesh 3D', 'Le mesh attend les vues préparées.');
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
  return box({ sec: 'ST-07', title: 'Mesh 3D', st: st[0], stLabel: st[1], body, next: isNext('mesh') });
}

function boxRig(c, key, cos) {
  if (!cos.meshes.length) return waitBox('ST-08', 'Rig SOMA', 'Le rig attend un mesh.');
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
  return box({ sec: 'ST-08', title: 'Rig SOMA', st: st[0], stLabel: st[1], body, next: isNext('rig', 'rig_ok') });
}

function boxIdentity(d) {
  const c = d.character;
  const sheet = c.identity || {};
  const filled = d.summary.identity.filled;
  const sections = SECTIONS.map((name) => {
    const rows = SHEET_FIELDS.filter((f) => f.section === name && sheet[f.key]).map((f) =>
      `<div class="sheet-row"><div class="sheet-key">${esc(f.label)}</div><div class="sheet-val filled">${
        esc(sheet[f.key])}</div></div>`).join('');
    return rows ? `<div class="sheet-section"><div class="sheet-section-title">${name}</div>${rows}</div>` : '';
  }).join('');
  const notes = (c.notes || []).length ? `<div class="sheet-section"><div class="sheet-section-title">notes (${
    c.notes.length})</div>${c.notes.map((n, i) => `<div class="sheet-row"><div class="sheet-key">#${i + 1}</div>
    <div class="sheet-val filled">${esc(n)}</div></div>`).join('')}</div>` : '';
  const body = `${sections || notes ? sections + notes : '<p>Fiche vide : la conversation la remplit.</p>'}
    <label class="field"><span class="lbl">Style</span><select class="fld" data-style>
      <option value="photoreal"${c.style === 'photoreal' ? ' selected' : ''}>photoréaliste</option>
      <option value="stylized"${c.style === 'stylized' ? ' selected' : ''}>stylisé</option></select></label>
    <a class="tb ghost block" href="./console.html?slug=${encodeURIComponent(c.slug)}">Continuer la conversation ▸</a>`;
  const st = filled >= d.summary.identity.total ? 'done' : filled ? 'partial' : 'todo';
  return box({ sec: 'ST-01', title: 'Identité', st, stLabel: `${filled}/${d.summary.identity.total}`, body });
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
    const out = await api(actionUrl(action), { method: 'POST', body: params });
    if (out.job) {
      toast(`${out.job.label} : en file`);
      state.jobs.unshift(out.job);
    } else {
      toast('fait');
      if (action === 'costume_add') state.costume = out.result.costume;
    }
    forget(btn.dataset.form);
    await loadDetail();
    render(true);
  } catch (e) {
    toast(e.message, 7000);
    btn.disabled = false;
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
  render(true);
}

async function quickCreate(form) {
  const name = form.elements.name.value.trim();
  if (!name) return;
  try {
    const out = await api('/api/characters', { method: 'POST', body: { name } });
    location.hash = `#/p/${encodeURIComponent(out.slug)}`;
  } catch (e) {
    toast(e.message, 6000);
  }
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
    if (e.target.dataset?.refs) { uploadFiles(e.target.dataset.refs, [...e.target.files]); e.target.value = ''; }
    if (e.target.matches('[data-style]')) setStyle(e.target.value);
  });
  app.addEventListener('submit', (e) => {
    if (e.target.dataset.form === 'quick') { e.preventDefault(); quickCreate(e.target); }
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
  app.addEventListener('focusout', () => {
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
