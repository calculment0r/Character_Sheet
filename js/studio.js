'use strict';

import { SHEET_FIELDS } from './schema.js';
import { talk, voiceConfig, micBlocker, Micro } from './parler.js';

/* ============================================================
   Le studio.

     #/                     le casting : les personnages en affiches
     #/p/<slug>             un personnage — sa naissance tant qu'il n'est
                            pas décrit, puis le volet où il attend un choix
     #/p/<slug>/<volet>     identite · visage · garde-robe · voix · planche
     #/p/<slug>/scene       lui parler

   On ne tranche ici que le goût : le visage, la tenue, la voix. La
   technique (A-pose, vues, 3D, rig) tourne en arrière-plan ; elle ne
   se montre que par une puce qui mène aux coulisses. Aucun nom de
   modèle, aucune graine, aucune méthode dans le parcours.

   Tout ce qui calcule part dans la file du serveur, un travail à la
   fois ; la page relève la file et se redessine quand le personnage
   change. Un seul bouton orange par écran : la question du moment.
   ============================================================ */

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
const base = (rel) => String(rel || '').split('/').pop();
const enc = encodeURIComponent;

const state = {
  route: { view: 'home' },   // home | perso { slug, tab }
  list: null,
  attention: null,           // GET /api/attention, null si le studio ne la sert pas
  detail: null,              // { character, summary, busy, backends }
  jobs: [],
  seen: new Set(),           // travaux finis déjà annoncés
  drafts: {},                // valeurs saisies, par champ
  refs: {},                  // images déposées, par formulaire : [{ id, url, name }]
  pick: {},                  // le candidat choisi, par décision : face, fb:<tenue>, voice
  costume: undefined,        // tenue affichée ; null = nouvelle tenue
  fbAgain: false,            // revoir les pleins pieds d'une tenue déjà choisie
  pending: false,            // un rendu attend que le champ actif perde le focus
  renaming: false,
  editField: null,
  sig: '',
  voice: { config: null },   // GET /api/voice/config
  missing: {},               // actions que le serveur ne connaît pas encore
  scene: { mode: 'talk', busy: false, mic: null, micState: '' },
  system: null,
};

const TABS = [
  ['identite', 'Identité'], ['visage', 'Visage'], ['garde-robe', 'Garde-robe'], ['voix', 'Voix'], ['planche', 'Planche'],
];
const TAB_IDS = new Set(TABS.map(([id]) => id));

// Ce que fait l'arrière-plan, dit simplement pour la puce de l'atelier.
const BACKGROUND = {
  apose: 'pose', apose_ok: 'pose', views: 'vues', prep: 'vues', check: 'vues',
  mesh: '3D', rig: 'rig', rig_ok: 'rig', sheet: 'planche',
};
// Ce que fait un travail, pour la pastille d'en-tête et les annonces.
const HUMAN = {
  face: 'ses visages', fullbody: 'sa tenue', voice_design: 'ses voix', line: 'une réplique',
  presentation: 'sa planche', apose: 'sa pose', views: 'ses vues', prep: 'ses vues', check: 'ses vues',
  mesh: 'sa 3D', rig: 'son squelette', sheet: 'une planche',
};
const DONE_TEXT = {
  face: 'ses visages sont arrivés', fullbody: 'ses pleins pieds sont arrivés', voice_design: 'ses voix sont prêtes',
  line: 'la réplique est dite', presentation: 'sa planche est composée',
};
const VOICE_ACTIONS = new Set(['voice_design', 'voice_lock', 'voice_unlock', 'line', 'line_keep']);

const DIRECTIONS = [
  ['older', 'plus âgé'], ['younger', 'plus jeune'], ['harder', 'plus dur'], ['softer', 'plus doux'],
  ['hair', 'autre coiffure'], ['smile', 'plus ouvert'],
];
const TRAITS = ['audacieux', 'discret', 'loyal', 'impulsif', 'méfiant', 'chaleureux', 'ironique', 'calme', 'têtu',
  'curieux', 'protecteur', 'rêveur', 'rancunier', 'drôle', 'solitaire', 'généreux'];
const FIELD_FR = {
  alias: 'surnom', gender: 'genre', age: 'âge', height: 'taille', body_type: 'silhouette', ethnicity: 'origine',
  face_description: 'visage', role: 'rôle', archetype: 'archétype', personality_traits: 'traits',
  core_theme: 'ce qui le travaille', emotional_range: 'émotions', behavior_notes: 'comportement',
  speech_style: 'façon de parler',
};
const SUGGEST = ['Qui es-tu ?', 'Qu\'est-ce qui te met en colère ?', 'Raconte-moi ta journée.'];
// La planche en attente : autant de cases que la recette en prévoit.
const PLANCHE_ROWS = [
  ['expressions', 'Expressions', 6, 'sq'], ['poses', 'Poses naturelles', 5, 'tall'], ['details', 'Détails', 4, 'sq'],
];

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
  if (!res.ok) {
    const err = new Error(json?.error?.message || `${res.status} ${res.statusText}`);
    err.status = res.status;
    throw err;
  }
  return json;
}

// Une action que ce serveur ne connaît pas encore : on le dit, sans erreur.
const unknown = (e) => e.status === 404 || (e.status === 409 && /action inconnue/.test(e.message));

function fileUrl(slug, rel, version) {
  if (!rel) return '';
  const path = `/files/${enc(slug)}/${String(rel).split('/').map(enc).join('/')}`;
  return version ? `${path}?v=${enc(version)}` : path;
}

// Un chemin rendu par le serveur : déjà une URL, ou relatif au personnage.
const anyUrl = (slug, v) => (!v ? '' : /^(\/|https?:|data:|blob:)/.test(v) ? v : fileUrl(slug, v));

const slug = () => state.route.slug;
const actionUrl = (action, s = slug()) => `/api/characters/${enc(s)}/actions/${action}`;

/* ── petits morceaux ────────────────────────────────────── */

let toastTimer = null;
function toast(msg, ms = 3200) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), ms);
}

function day(at) {
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long' });
}

function initials(name) {
  return String(name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();
}

function draft(key, fallback = '') {
  return key in state.drafts ? state.drafts[key] : fallback;
}

function textarea(key, fallback, placeholder, rows = 3, cls = '') {
  return `<textarea class="fld ${cls}" rows="${rows}" data-draft="${esc(key)}" placeholder="${esc(placeholder)}">${
    esc(draft(key, fallback))}</textarea>`;
}

function input(key, fallback, placeholder, cls = '') {
  return `<input class="fld ${cls}" data-draft="${esc(key)}" value="${esc(draft(key, fallback))}"
    placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false">`;
}

/* Un bouton. `go` le rend orange — une fois par écran au plus : le
   second demandé reste fantôme. Grisé, il dit pourquoi à côté. */
let goUsed = false;
function btn(label, { act, params, form, costume, confirm, go = false, sm = false, block = false, disabled = false,
  why = '', attrs = '', type = 'button' } = {}) {
  const orange = go && !goUsed;
  if (orange) goUsed = true;
  const a = [
    act ? `data-act="${act}"` : '',
    params ? `data-params="${esc(JSON.stringify(params))}"` : '',
    form ? `data-form="${esc(form)}"` : '',
    costume ? `data-costume="${esc(costume)}"` : '',
    confirm ? `data-confirm="${esc(confirm)}"` : '',
    disabled ? 'disabled' : '',
    why && disabled ? `title="${esc(why)}"` : '',
    attrs,
  ].filter(Boolean).join(' ');
  const cls = `tb ${orange ? 'go' : 'ghost'}${sm ? ' sm' : ''}${block ? ' block' : ''}`;
  const b = `<button type="${type}" class="${cls}" ${a}>${esc(label)}</button>`;
  return why && disabled ? `<span class="why">${esc(why)}</span>${b}` : b;
}

function link(label, href, { go = false, sm = false, cls = '' } = {}) {
  const orange = go && !goUsed;
  if (orange) goUsed = true;
  return `<a class="tb ${orange ? 'go' : 'ghost'}${sm ? ' sm' : ''} ${cls}" href="${esc(href)}">${esc(label)}</a>`;
}

function refsZone(form, what) {
  const list = state.refs[form] || [];
  return `<div class="refs">${list.map((r, i) =>
    `<span class="thumb" style="background-image:url('${r.url}')" title="${esc(r.name)}">` +
    `<button data-drop-ref="${esc(form)}:${i}" title="retirer">×</button></span>`).join('')}
    <label class="add" data-refs-zone="${esc(form)}">+ ${esc(what)}
      <input type="file" accept="image/*" multiple data-refs="${esc(form)}"></label></div>`;
}

const live = (j) => j.status === 'queued' || j.status === 'running';

function liveJob(action, costume) {
  return state.jobs.find((j) => live(j) && j.action === action && (!costume || (j.params?.costume || null) === costume));
}

function lastJob(action, costume) {
  return state.jobs.find((j) => !live(j) && j.action === action && (!costume || (j.params?.costume || null) === costume));
}

const tabHref = (tab, s = slug()) => `#/p/${enc(s)}${tab ? `/${tab}` : ''}`;

/* Le lot du moment : les candidats du travail en cours (ou du dernier),
   les précédents dans la bande d'historique. */
function lot(cands, action, costume) {
  const job = liveJob(action, costume);
  const done = lastJob(action, costume);
  let current = [];
  if (job) current = job.started ? cands.filter((x) => x.at >= job.started) : [];
  else if (done?.started && done.status === 'done') {
    current = cands.filter((x) => x.at >= done.started && (!done.ended || x.at <= done.ended));
  }
  if (!job && !current.length) current = cands.slice(-4);
  const older = cands.filter((x) => !current.includes(x)).reverse();
  const want = Number(job?.params?.variants || job?.params?.n) || 4;
  return { current, older, job, expected: job ? Math.max(want, current.length) : current.length };
}

/* Une case qui attend son image : la planche qui se remplit. */
function slot(job, i, arrived, expected, cls = '') {
  const running = job?.status === 'running';
  const first = running && i === arrived;
  const pr = running ? Math.max(0, Math.min(1, (job.progress || 0) * expected - i)) : 0;
  return `<div class="prop slot ${cls}${first ? ' on' : ''}"><span class="slot-in">
    <span class="lbl">${first ? 'en cours' : running ? 'ensuite' : 'en file'}</span>
    ${first ? `<span class="slot-bar"><i style="width:${Math.round(pr * 100)}%"></i></span>` : ''}</span></div>`;
}

function prop({ kind, n, src, cap, sel, cls = '' }) {
  return `<div class="prop ${cls}${sel ? ' sel' : ''}" data-pick="${esc(kind)}" data-val="${n}" tabindex="0"
      role="button" aria-pressed="${sel}" title="choisir le n° ${n}">
    <img src="${esc(src)}" alt="proposition n° ${n}" loading="lazy">
    <span class="prop-n">n° ${n}</span>
    ${sel ? '<span class="prop-mark">choisi</span>' : ''}
    <button class="prop-zoom" data-zoom="${esc(src)}" data-cap="${esc(cap)}" title="agrandir" aria-label="agrandir">+</button>
  </div>`;
}

function strip(kind, items, pickVal, srcOf, label = 'propositions précédentes') {
  if (!items.length) return '';
  return `<div class="strip-wrap"><span class="lbl">${esc(label)} · ${items.length}</span><div class="strip">${
    items.map((x) => `<button class="strip-it${String(x.n) === String(pickVal) ? ' sel' : ''}" data-pick="${esc(kind)}"
      data-val="${x.n}" title="n° ${x.n}"><img src="${esc(srcOf(x))}" alt="n° ${x.n}" loading="lazy"></button>`).join('')
  }</div></div>`;
}

function failure(job, retry = '') {
  if (!job || job.status !== 'error') return '';
  const msg = String(job.error || '').replace(/^refusé : /, '');
  return `<div class="notice"><p><b>Le dernier rendu n'a pas abouti.</b> ${esc(msg.slice(0, 180))}</p>
    ${retry}<a class="lbl" href="./coulisses.html#/${enc(job.slug)}">détails dans les coulisses</a></div>`;
}

/* ── le casting ─────────────────────────────────────────── */

function waitsOnCasting(list) {
  const out = [];
  const global = Array.isArray(state.attention) ? state.attention : null;
  for (const c of list) {
    const href = `#/p/${enc(c.slug)}`;
    const items = global ? global.filter((a) => a.slug === c.slug) : [];
    items.forEach((a) => out.push({ c, href, amb: true, text: a.title || 'une question de l\'atelier' }));
    if (!global && c.attention) {
      out.push({ c, href, amb: true, text: `${c.attention} question${c.attention > 1 ? 's' : ''} de l'atelier` });
    }
    const next = c.next?.action;
    if (next === 'face_lock') out.push({ c, href: `${href}/visage`, text: 'attend que tu choisisses son visage' });
    else if (next === 'fullbody_ok') out.push({ c, href: `${href}/garde-robe`, text: 'attend que tu choisisses sa tenue' });
    else if (next === 'face' && !c.thumb) out.push({ c, href, text: 'attend que tu dises qui il est' });
  }
  return out;
}

function renderHome() {
  const list = state.list?.characters || [];
  const waits = waitsOnCasting(list);
  // « Ce qui attend » ne se montre que s'il y a quelque chose : un panneau vide ne dit rien.
  return `
  <section class="cast-top${waits.length ? '' : ' solo'}">
    <div class="cast-hero">
      <span class="kicker">casting</span>
      <h1 class="cast-title">Les personnages</h1>
      <p class="prose">Un nom pour commencer, une phrase pour dire qui il est. Son visage, sa voix et sa tenue viennent
        ensuite, une question à la fois ; le reste se fait tout seul, pendant que tu travailles.</p>
    </div>
    ${waits.length ? `<aside class="waits-panel">
      <div class="panel-head"><h2>Ce qui attend</h2><span class="lbl">${waits.length}</span></div>
      <ul class="waits-list">${waits.slice(0, 6).map((w) => `<li><a class="wait-it${w.amb ? ' amb' : ''}"
        href="${w.href}">${w.c.thumb ? `<img src="${esc(w.c.thumb)}" alt="">` : `<span class="ph">${esc(initials(w.c.name))}</span>`}
        <span><b>${esc(w.c.name)}</b> ${esc(w.text)}</span></a></li>`).join('')}</ul>
    </aside>` : ''}
  </section>
  <section class="posters">
    <form class="poster new" data-form="create">
      <span class="kicker">nouveau personnage</span>
      <span class="new-q">Comment s'appelle-t-il ?</span>
      <input class="fld" name="name" placeholder="un nom, même provisoire" autocomplete="off" spellcheck="false">
      ${btn('Créer ▸', { go: true, type: 'submit' })}
      <span class="hint">Le nom se change ensuite d'un clic.</span>
    </form>
    ${list.map(poster).join('')}
  </section>`;
}

function castState(c) {
  const next = c.next?.action;
  if (!c.thumb) return ['à naître', ''];
  if (next === 'face_lock') return ['visage à choisir', 'amb'];
  if (next === 'costume_add' || next === 'fullbody') return ['à habiller', ''];
  if (next === 'fullbody_ok') return ['tenue à choisir', 'amb'];
  if (c.attention) return ['une question', 'amb'];
  if (c.poster) return ['habillé', 'ok'];
  return c.locked ? ['visage choisi', 'ok'] : ['', ''];
}

function poster(c) {
  const [badge, bcls] = castState(c);
  const img = c.thumb
    ? `<img class="p1" src="${esc(c.thumb)}" alt="" loading="lazy">${c.poster ? `<img class="p2" src="${esc(c.poster)}" alt="" loading="lazy">` : ''}`
    : `<span class="ph">${esc(initials(c.name))}</span>`;
  const line = c.role || c.archetype || (c.thumb ? '' : 'à décrire');
  return `<article class="poster">
    <a class="poster-img" href="#/p/${enc(c.slug)}">${img}<span class="poster-veil"></span>
      ${badge ? `<span class="poster-badge ${bcls}">${esc(badge)}</span>` : ''}
      <span class="poster-name"><span class="nm">${esc(c.name)}</span>${line ? `<span class="line">${esc(line)}</span>` : ''}</span>
    </a>
    <div class="poster-acts">
      ${c.voice ? `<button class="play" data-play="${esc(c.voice)}" title="écouter sa voix"><i></i><span>sa voix</span></button>`
        : '<span class="lbl">pas encore de voix</span>'}
      <span class="sp"></span>
      ${c.locked ? `<a class="tb ghost sm" href="#/p/${enc(c.slug)}/scene">Parler</a>` : ''}
    </div>
  </article>`;
}

/* ── un personnage ──────────────────────────────────────── */

const voiceOf = (c) => (c.voice && typeof c.voice === 'object' ? c.voice : null);

// La voix est-elle possible ? Le manifeste en a une, ou le studio sert sa configuration.
function voicePossible(c) {
  if (voiceOf(c)) return true;
  if (state.missing.voice_design) return false;
  return !!state.voice.config && !state.voice.config.missing;
}

function validatedCostume(c) {
  const keys = Object.keys(c.costumes);
  if (state.costume && c.costumes[state.costume]?.fullbody.validated) return state.costume;
  return keys.find((k) => c.costumes[k].fullbody.validated) || null;
}

function currentTab(d) {
  const t = state.route.tab;
  if (t === 'scene' || TAB_IDS.has(t)) return t;
  const c = d.character;
  const f = c.face;
  if (!f.locked && !f.candidates.length && !f.brief && !liveJob('face')) return 'naissance';
  if (!f.locked) return 'visage';
  const v = voiceOf(c);
  if (voicePossible(c) && !v?.locked) return 'voix';
  if (!validatedCostume(c)) return 'garde-robe';
  return 'planche';
}

function renderPerso() {
  const d = state.detail;
  if (!d) return '<p class="prose loading">chargement…</p>';
  const tab = currentTab(d);
  // Le volet choisi d'office s'écrit dans l'adresse : un choix fait ici
  // (« C'est lui ») ne fait pas sauter la page vers un autre volet.
  if (!state.route.tab && TAB_IDS.has(tab)) {
    state.route.tab = tab;
    history.replaceState(null, '', tabHref(tab));
  }
  if (tab === 'naissance') return renderNaissance(d);
  if (tab === 'scene') return renderScene(d);
  const body = { identite: tabIdentite, visage: tabVisage, 'garde-robe': tabGarde, voix: tabVoix, planche: tabPlanche }[tab](d);
  return `${persoHead(d)}${vols(d, tab)}${attentionCards(d)}<div class="vol-body">${body}</div>`;
}

function nameBlock(c, cls) {
  return state.renaming
    ? `<form data-form="rename" class="rename"><input class="fld" name="name" value="${esc(c.name)}" autocomplete="off"
        spellcheck="false" aria-label="nom"><button class="tb ghost sm" type="submit">OK</button></form>`
    : `<h1 class="${cls}" data-rename title="renommer">${esc(c.name)}</h1>`;
}

function atelier(d) {
  const c = d.character;
  const run = state.jobs.find((j) => live(j) && BACKGROUND[j.action]);
  const att = attentionOf(d).length;
  if (att) return [`atelier · ${att} en attente`, 'amb'];
  if (run) return [`atelier · ${BACKGROUND[run.action]} en cours`, 'run'];
  const cos = Object.values(c.costumes).filter((x) => x.fullbody.validated);
  if (!cos.length) return ['atelier · au repos', ''];
  if (cos.some((x) => (x.rigs || []).some((r) => r.verdict === 'accepted'))) return ['atelier · riggé', 'ok'];
  if (cos.some((x) => (x.rigs || []).length)) return ['atelier · rig fait', 'ok'];
  if (cos.some((x) => (x.meshes || []).length)) return ['atelier · 3D prête', 'ok'];
  if (cos.some((x) => x.views?.check?.ok)) return ['atelier · vues prêtes', 'ok'];
  if (cos.some((x) => x.apose?.validated)) return ['atelier · pose prête', 'ok'];
  return ['atelier · en attente', ''];
}

function persoHead(d) {
  const c = d.character;
  const s = d.summary;
  const face = s.thumb ? `<img class="p-face" src="${esc(s.thumb)}" alt="" data-zoom="${esc(s.thumb)}" data-cap="${esc(c.name)}">`
    : `<span class="p-face ph">${esc(initials(c.name))}</span>`;
  const [chip, chipCls] = atelier(d);
  return `<div class="p-head">
    <a class="tb ghost sm back" href="#/" title="le casting">◂ Casting</a>
    <div class="p-id">${face}<div class="p-who">${nameBlock(c, 'p-name')}
      <span class="p-line">${esc([s.role, s.archetype].filter(Boolean).join(' · ') || 'un clic sur le nom pour le changer')}</span>
    </div></div>
    <span class="sp"></span>
    <a class="atelier ${chipCls}" href="./coulisses.html#/${enc(c.slug)}" title="ce que fait l'atelier : les coulisses"><i></i>${esc(chip)}</a>
    ${c.face.locked ? `<a class="tb" href="${tabHref('scene')}">Parler ▸</a>`
      : '<span class="tb disabled-link" title="il lui faut d\'abord un visage">Parler</span>'}
  </div>`;
}

function tabDot(d, id) {
  const c = d.character;
  const f = c.face;
  const v = voiceOf(c);
  const cos = Object.values(c.costumes);
  if (id === 'identite') return d.summary.identity.filled >= 8 ? 'done' : '';
  if (id === 'visage') return f.locked ? 'done' : liveJob('face') ? 'run' : f.candidates.length ? 'wait' : '';
  if (id === 'garde-robe') {
    if (liveJob('fullbody')) return 'run';
    if (cos.some((x) => x.fullbody.validated)) return 'done';
    return cos.some((x) => x.fullbody.candidates.length) ? 'wait' : '';
  }
  if (id === 'voix') {
    if (liveJob('voice_design')) return 'run';
    return v?.locked ? 'done' : v?.candidates?.length ? 'wait' : '';
  }
  if (liveJob('presentation')) return 'run';
  return cos.some((x) => x.presentation?.sheet) ? 'done' : '';
}

function vols(d, tab) {
  return `<nav class="vols" aria-label="volets">${TABS.map(([id, label]) => {
    const dot = tabDot(d, id);
    return `<a class="vol${id === tab ? ' on' : ''}" href="${tabHref(id)}"${id === tab ? ' aria-current="page"' : ''}>
      <span>${esc(label)}</span>${dot ? `<i class="dot ${dot}"></i>` : ''}</a>`;
  }).join('')}</nav>`;
}

/* ── ce qui attend : les cartes de l'atelier ────────────── */

function attentionOf(d) {
  const own = d.character.attention || d.attention;
  if (Array.isArray(own)) return own;
  return Array.isArray(state.attention) ? state.attention.filter((a) => a.slug === d.character.slug) : [];
}

function optionOf(o, i) {
  return typeof o === 'string' ? { id: o, label: o } : { id: o.id ?? String(i), label: o.label || o.id || '…', ...o };
}

function attentionCards(d) {
  const list = attentionOf(d);
  if (!list.length) return '';
  return `<section class="waits">${list.map((a) => `<article class="att">
    <span class="att-k">ce qui attend${a.kind ? ` · ${esc(a.kind)}` : ''}</span>
    <h3>${esc(a.title || 'Une question de l\'atelier')}</h3>
    ${a.text ? `<p>${esc(a.text)}</p>` : ''}
    <div class="att-row">${(a.options || []).map((o, i) => {
      const opt = optionOf(o, i);
      return `<button class="tb ghost sm${opt.recommended ? ' rec' : ''}" data-attention="${esc(a.id)}"
        data-option="${esc(JSON.stringify(opt))}">${esc(opt.label)}${opt.recommended ? '<i>recommandé</i>' : ''}</button>`;
    }).join('')}<span class="sp"></span>
      <a class="lbl" href="./coulisses.html#/${enc(d.character.slug)}">voir dans les coulisses</a></div>
  </article>`).join('')}</section>`;
}

/* ── naissance : un nom, puis qui il est ────────────────── */

function renderNaissance(d) {
  const c = d.character;
  return `<div class="birth-top"><a class="tb ghost sm" href="#/">◂ Casting</a></div>
  <section class="birth">
    <span class="kicker">naissance</span>
    ${nameBlock(c, 'birth-name')}
    <label class="birth-q" for="birth-brief">Qui est-il ?</label>
    <textarea id="birth-brief" class="fld birth-fld" rows="3" data-draft="face.brief"
      placeholder="Une phrase suffit : « un vieux marin breton, taiseux, barbe blanche, les mains abîmées ».">${
      esc(draft('face.brief', ''))}</textarea>
    <div class="birth-row">
      <div class="birth-photo">${refsZone('face', 'une photo')}<span class="hint">une photo de départ, si tu en as une</span></div>
      <span class="sp"></span>
      ${btn('Le faire naître ▸', { act: 'face', form: 'face', params: { variants: 4 }, go: true })}
    </div>
    <p class="hint">Sa fiche s'écrit d'abord, en quelques secondes. Puis quatre visages arrivent : tu choisis celui qui est lui.</p>
  </section>`;
}

/* ── volet Identité ─────────────────────────────────────── */

function ficheRows(d, section) {
  const sheet = d.character.identity || {};
  return SHEET_FIELDS.filter((f) => f.section === section && f.key !== 'character_name').map((f) => {
    const v = sheet[f.key] || '';
    const label = FIELD_FR[f.key] || f.label;
    const cell = state.editField === f.key
      ? `<form data-form="field" data-key="${f.key}" class="sheet-edit"><textarea class="fld" name="v" rows="${
        Math.min(6, Math.max(1, Math.ceil(v.length / 46)))}" aria-label="${esc(label)}">${esc(v)}</textarea></form>`
      : `<div class="sheet-val ${v ? 'filled' : 'empty'}" data-edit="${f.key}" title="corriger" tabindex="0">${esc(v)}</div>`;
    return `<div class="sheet-row"><div class="sheet-key">${esc(label)}</div>${cell}</div>`;
  }).join('');
}

function traitsChips(d) {
  const have = String(d.character.identity?.personality_traits || '').split(',').map((t) => t.trim().toLowerCase())
    .filter(Boolean);
  const all = [...new Set([...TRAITS, ...have.filter((t) => t.length < 22)])];
  return `<div class="chips-row">${all.map((t) => `<button class="chip${have.includes(t) ? ' chip-selected' : ''}"
    data-trait="${esc(t)}">${esc(t)}</button>`).join('')}</div>`;
}

function tabIdentite(d) {
  const c = d.character;
  const brief = c.face.brief;
  return `<div class="duo">
    <section class="panel">
      <div class="panel-head"><h2>Qui il est</h2><span class="lbl">un clic pour corriger</span></div>
      <div class="sheet-section fiche">${ficheRows(d, 'CORE')}</div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Son caractère</h2><span class="lbl">gardé tout seul</span></div>
      ${traitsChips(d)}
      <div class="sheet-section fiche">${ficheRows(d, 'PSYCHE')}</div>
    </section>
  </div>
  ${brief ? `<section class="panel quote"><span class="lbl">sa première phrase</span><blockquote>${esc(brief)}</blockquote></section>` : ''}
  <div class="row-end">
    <details class="adv"><summary>réglages avancés</summary><div class="adv-body">
      <span class="lbl">rendu</span>
      <span class="seg">
        <button class="tb sm${c.style === 'photoreal' ? ' on' : ''}" data-style-set="photoreal">Photo</button>
        <button class="tb sm${c.style === 'stylized' ? ' on' : ''}" data-style-set="stylized">Stylisé</button>
      </span></div></details>
    <span class="sp"></span>
    ${link('Approfondir avec l\'assistant ▸', `./console.html?slug=${enc(c.slug)}`)}
  </div>`;
}

/* ── volet Visage ───────────────────────────────────────── */

function knownPanel(d, job) {
  const sheet = d.character.identity || {};
  const keys = ['role', 'age', 'archetype', 'personality_traits', 'face_description', 'speech_style'];
  const rows = keys.filter((k) => String(sheet[k] || '').trim());
  const body = rows.length
    ? `<div class="sheet-section">${rows.map((k) => `<div class="sheet-row"><div class="sheet-key">${esc(FIELD_FR[k] || k)}</div>
        <div class="sheet-val filled">${esc(sheet[k])}</div></div>`).join('')}</div>`
    : job ? '<div class="writing"><div class="thinking"><span></span><span></span><span></span></div><span class="hint">sa fiche s\'écrit…</span></div>'
      : '<p class="hint">Rien encore.</p>';
  return `<aside class="panel known"><div class="panel-head"><h2>Ce qu'on sait de lui</h2></div>${body}
    <a class="lbl more" href="${tabHref('identite')}">toute sa fiche ▸</a></aside>`;
}

function tabVisage(d) {
  const c = d.character;
  const f = c.face;
  if (f.locked) return visageLocked(d);
  const cands = f.candidates.map((x, i) => ({ ...x, n: i + 1 }));
  const { current, older, job, expected } = lot(cands, 'face');
  const pickN = state.pick.face;
  const picked = cands.find((x) => String(x.n) === String(pickN));
  const src = (x) => fileUrl(c.slug, x.file, x.at);
  const grid = `<div class="props">${current.map((x) => prop({
    kind: 'face', n: x.n, src: src(x), cap: `n° ${x.n}`, sel: picked === x,
  })).join('')}${Array.from({ length: expected - current.length }, (_, i) =>
    slot(job, current.length + i, current.length, expected)).join('')}</div>`;
  const around = picked ? `<div class="around"><span class="lbl">autour du n° ${picked.n}</span>
      <div class="chips-row">${DIRECTIONS.map(([k, label]) => `<button class="chip" data-act="face"
        data-params="${esc(JSON.stringify({ around: picked.n, variants: 3, direction: k }))}"${job ? ' disabled' : ''}>${
        esc(label)}</button>`).join('')}
        ${btn('Trois comme lui', { act: 'face', params: { around: picked.n, variants: 3 }, sm: true, disabled: !!job })}</div></div>`
    : `<p class="hint around">${cands.length ? 'Clique le visage qui est lui. Pour chercher autour d\'un visage, choisis-le d\'abord.' : ''}</p>`;
  const redo = `<details class="redo"${cands.length || job ? '' : ' open'}><summary>le redécrire</summary><div class="redo-body">
      ${textarea('face.brief', f.brief || '', 'qui il est, en une phrase ou deux', 2)}
      <div class="form-row">${refsZone('face', 'une photo')}<span class="sp"></span>
        ${btn('Quatre autres ▸', { act: 'face', form: 'face', params: { variants: 4 }, disabled: !!job })}</div>
    </div></details>`;
  return `<div class="work">
    <div class="work-main">
      ${failure(job ? null : lastJob('face'))}
      ${grid}
      ${around}
      ${strip('face', older, pickN, src)}
      ${redo}
      ${job ? waitingCard(d, 'visage') : ''}
    </div>
    ${knownPanel(d, job)}
  </div>
  ${decide({
    thumb: picked && src(picked),
    text: picked ? `le n° ${picked.n}, c'est lui ?` : cands.length ? 'Clique le visage qui est lui.'
      : job ? 'Ses visages arrivent.' : 'Décris-le pour voir ses visages.',
    button: btn('C\'est lui ▸', {
      act: 'face_lock', params: picked ? { candidate: String(picked.n) } : undefined, go: true, disabled: !picked,
      confirm: 'Garder ce visage ? Il fera autorité sur tout le reste — la tenue, la planche, la 3D — et ne changera plus.',
    }),
  })}`;
}

function decide({ thumb, text, button }) {
  return `<div class="decide">${thumb ? `<img class="decide-thumb" src="${esc(thumb)}" alt="">` : '<span class="decide-thumb ph"></span>'}
    <span class="decide-txt">${esc(text)}</span><span class="sp"></span>${button}</div>`;
}

function nextStepLink(d) {
  const c = d.character;
  if (voicePossible(c) && !voiceOf(c)?.locked) return link('Lui trouver une voix ▸', tabHref('voix'), { go: true });
  if (!validatedCostume(c)) return link('L\'habiller ▸', tabHref('garde-robe'), { go: true });
  return link('Voir sa planche ▸', tabHref('planche'), { go: true });
}

function visageLocked(d) {
  const c = d.character;
  const f = c.face;
  const src = fileUrl(c.slug, f.locked, f.locked_at);
  return `<div class="hero-pick">
    <img class="hero-img-sq" src="${esc(src)}" alt="son visage" data-zoom="${esc(src)}" data-cap="${esc(c.name)}">
    <div class="hero-txt">
      <span class="kicker">son visage</span>
      <h2 class="big">C'est lui.</h2>
      <p class="prose">Choisi le ${esc(day(f.locked_at))}. Ce visage fait autorité sur tout le reste — sa tenue, sa planche,
        sa 3D — et ne change plus. Pour un autre visage, un autre personnage.</p>
      <div class="row-start">${nextStepLink(d)}</div>
    </div>
  </div>`;
}

/* ── en attendant : ce qu'on peut faire pendant un rendu ── */

// Les rendus H3 prennent la mémoire du modèle de texte : l'assistant attend.
function heavy(run) {
  const p = run.params || {};
  return run.action === 'sheet' || (['face', 'fullbody'].includes(run.action) && p.engine === 'h3')
    || (run.action === 'face' && (p.refs || []).length && !p.engine);
}

function waitingCard(d, here) {
  const run = state.jobs.find((j) => j.status === 'running') || state.jobs.find((j) => j.status === 'queued');
  if (!run) return '';
  const c = d.character;
  const sheet = c.identity || {};
  let body;
  if (!sheet.personality_traits && here !== 'identite') {
    body = `<p>Pendant que ça calcule : trois traits de caractère. Un clic suffit, c'est gardé.</p>${traitsChips(d)}`;
  } else if (!Object.keys(c.costumes).length && here !== 'garde-robe') {
    body = `<p>Pendant que ça calcule : ce qu'il porte, de la tête aux pieds. Gardé dès que tu quittes le champ.</p>
      ${textarea('costume_new.brief', '', 'en français : « caban bleu marine usé, pull de laine écrue, bottes de pont »', 2)}`;
  } else if (voicePossible(c) && !voiceOf(c) && here !== 'voix') {
    body = `<p>Pendant que ça calcule : sa voix. Décris-la en quelques mots, quatre propositions suivront.</p>
      <div class="row-start"><a class="tb ghost sm" href="${tabHref('voix')}">Décrire sa voix ▸</a></div>`;
  } else {
    body = `<p>Pendant que ça calcule : relis sa fiche, corrige d'un clic ce qui ne va pas.</p>
      <div class="row-start"><a class="tb ghost sm" href="${tabHref('identite')}">Sa fiche ▸</a></div>`;
  }
  body += heavy(run) ? '<p class="hint">Ce rendu occupe la mémoire : l\'assistant revient juste après.</p>'
    : `<a class="lbl more" href="./console.html?slug=${enc(c.slug)}">ou en parler avec l'assistant ▸</a>`;
  return `<section class="panel waiting"><div class="panel-head"><h2>En attendant</h2>
    <span class="lbl">${esc(run.status === 'queued' ? 'en file' : `${HUMAN[run.action] || run.label} · ${Math.round((run.progress || 0) * 100)} %`)}</span></div>
    ${body}</section>`;
}

/* ── volet Garde-robe ───────────────────────────────────── */

function tabGarde(d) {
  const c = d.character;
  const keys = Object.keys(c.costumes);
  if (state.costume === undefined || (state.costume !== null && !keys.includes(state.costume))) {
    state.costume = keys.find((k) => !c.costumes[k].fullbody.validated) || keys[0] || null;
  }
  const locked = !!c.face.locked;
  const gate = locked ? '' : `<div class="gate"><p>La tenue se porte sur <b>son visage</b>, et il n'est pas encore choisi.
      Tu peux déjà écrire ce qu'il porte : c'est gardé dès que tu quittes le champ.</p>
      ${link('Choisir son visage ▸', tabHref('visage'), { go: true })}</div>`;
  const tenues = keys.length ? `<div class="seg tenues">${keys.map((k) => `<button class="tb sm${k === state.costume ? ' on' : ''}"
      data-costume-tab="${esc(k)}">${esc(c.costumes[k].name)}</button>`).join('')}
      <button class="tb sm${state.costume === null ? ' on' : ''}" data-costume-tab="">+ Tenue</button></div>` : '';
  if (state.costume === null) {
    const body = `<section class="panel">
      <div class="panel-head"><h2>${keys.length ? 'Une autre tenue' : 'Ce qu\'il porte'}</h2><span class="lbl">gardé dès que tu quittes le champ</span></div>
      ${textarea('costume_new.brief', '', 'en français : « hoodie bleu capuche baissée, baggy blanc usé aux genoux, baskets blanches »', 3)}
      <div class="form-row">${refsZone('costume_new', 'vêtement')}
        <label class="field"><span class="lbl">nom de la tenue</span>${input('costume_new.name', '', `tenue ${keys.length + 1}`)}</label>
        <span class="sp"></span>
        ${btn('L\'habiller ▸', { act: 'costume_go', form: 'costume_new', go: locked, disabled: !locked,
          why: locked ? '' : 'il faut d\'abord son visage' })}</div>
    </section>`;
    return `${gate}${tenues}${body}`;
  }
  const key = state.costume;
  const cos = c.costumes[key];
  const fb = cos.fullbody;
  const form = `cos.${key}`;
  if (fb.validated && !state.fbAgain) return `${tenues}${gardeValidated(d, key, cos)}`;
  const cands = fb.candidates.map((x, i) => ({ ...x, n: i + 1 }));
  const { current, older, job, expected } = lot(cands, 'fullbody', key);
  const pickN = state.pick[`fb:${key}`];
  const picked = cands.find((x) => String(x.n) === String(pickN));
  const src = (x) => fileUrl(c.slug, x.file, x.at);
  const brief = `<section class="panel">
      <div class="panel-head"><h2>Ce qu'il porte</h2><span class="lbl">gardé dès que tu quittes le champ</span></div>
      ${textarea(`${form}.brief`, cos.brief || '', 'en français, comme ça vient', 3)}
      <div class="form-row">
        <div class="refs">${cos.refs.map((r) => {
          const u = fileUrl(c.slug, r);
          return `<span class="thumb" style="background-image:url('${esc(u)}')" title="${esc(base(r))}" data-zoom="${esc(u)}"
            data-cap="${esc(base(r))}"><button data-act="costume_edit" data-costume="${esc(key)}"
            data-params="${esc(JSON.stringify({ drop_refs: [r] }))}" title="retirer"
            data-confirm="${esc('Retirer cette image de la tenue ?')}">×</button></span>`;
        }).join('')}</div>${refsZone(form, 'vêtement')}
        <span class="sp"></span>
        ${btn(cands.length ? 'Trois autres ▸' : 'L\'habiller ▸', {
          act: 'fullbody', form, costume: key, params: { variants: 3 }, go: locked && !cands.length && !job,
          disabled: !locked || !!job, why: !locked ? 'il faut d\'abord son visage' : job ? 'un rendu est en cours' : '' })}
      </div>
    </section>`;
  const grid = cands.length || job ? `<div class="props tall">${current.map((x) => prop({
    kind: `fb:${key}`, n: x.n, src: src(x), cap: `${cos.name} · n° ${x.n}`, sel: picked === x, cls: 'tall',
  })).join('')}${Array.from({ length: expected - current.length }, (_, i) =>
    slot(job, current.length + i, current.length, expected, 'tall')).join('')}</div>` : '';
  const again = fb.validated ? `<p class="hint">Un autre plein pied : l'atelier refera sa pose, ses vues et sa 3D à partir de lui.
    <button class="linkish" data-fb-again="0">garder l'actuel</button></p>` : '';
  const decision = cands.length ? decide({
    thumb: picked && src(picked),
    text: picked ? `le n° ${picked.n}, cette tenue ?` : 'Clique le plein pied qui lui va.',
    button: btn('Cette tenue ▸', {
      act: 'fullbody_ok', costume: key, params: picked ? { candidate: String(picked.n) } : undefined, go: true,
      disabled: !picked,
      confirm: fb.validated ? 'Garder ce plein pied ? L\'atelier refera sa pose, ses vues et sa 3D à partir de lui.' : undefined,
    }),
  }) : '';
  return `${gate}${tenues}<div class="work one">
    <div class="work-main">${brief}${failure(job ? null : lastJob('fullbody', key))}${again}${grid}
      ${strip(`fb:${key}`, older, pickN, src)}${job ? waitingCard(d, 'garde-robe') : ''}</div>
  </div>${decision}`;
}

function gardeValidated(d, key, cos) {
  const c = d.character;
  const src = fileUrl(c.slug, cos.fullbody.validated, cos.fullbody.validated_at);
  const [chip] = atelier(d);
  return `<div class="hero-pick tall">
    <img class="hero-img-tall" src="${esc(src)}" alt="${esc(cos.name)}" data-zoom="${esc(src)}" data-cap="${esc(cos.name)}">
    <div class="hero-txt">
      <span class="kicker">sa tenue · ${esc(cos.name)}</span>
      <h2 class="big">Il est habillé.</h2>
      <p class="prose">L'atelier prend la suite tout seul : sa pose, ses vues, sa 3D, son squelette. Rien à faire ici ;
        si quelque chose demande ton œil, ça arrivera dans « Ce qui attend ».</p>
      <p class="lbl">${esc(chip)}</p>
      ${cos.brief ? `<blockquote class="small">${esc(cos.brief)}</blockquote>` : ''}
      <div class="row-start">${link('Voir sa planche ▸', tabHref('planche'), { go: true })}
        <button class="tb ghost" data-fb-again="1">Changer de plein pied</button></div>
    </div>
  </div>`;
}

/* ── volet Voix ─────────────────────────────────────────── */

function voiceSoon(d) {
  const sheet = d.character.identity || {};
  const reason = state.voice.config && !state.voice.config.missing ? state.voice.config.reason : '';
  return `<section class="soon">
    <span class="kicker">sa voix</span>
    <h2 class="big">Sa voix arrive bientôt.</h2>
    <p class="prose">Le studio n'a pas encore de service de voix${reason ? ` (${esc(reason)})` : ''}. Dès qu'il sera branché,
      tu la décriras ici en quelques mots et tu en écouteras quatre, qui liront une de ses répliques.</p>
    ${sheet.speech_style ? `<p class="prose">Ce qu'on sait déjà de sa façon de parler : <q>${esc(sheet.speech_style)}</q></p>` : ''}
    <div class="row-start">${d.character.face.locked ? link('Lui parler par écrit ▸', tabHref('scene'))
      : link('Choisir son visage ▸', tabHref('visage'))}</div>
  </section>`;
}

function player(url, { n, text, cls = '' } = {}) {
  return `<span class="player ${cls}"><button class="play" data-play="${esc(url)}" title="écouter" aria-label="écouter"><i></i></button>
    <span class="wave" data-wave="${esc(url)}"><i></i></span>${n ? `<span class="lbl">${esc(n)}</span>` : ''}</span>
    ${text ? `<span class="vc-text">« ${esc(text)} »</span>` : ''}`;
}

function tabVoix(d) {
  const c = d.character;
  const v = voiceOf(c);
  if (!v && !voicePossible(c)) return voiceSoon(d);
  if (v?.locked) return voiceLocked(d, v);
  const sheet = c.identity || {};
  const cands = (v?.candidates || []).map((x, i) => ({ ...x, n: i + 1 }));
  const { current, older, job, expected } = lot(cands, 'voice_design');
  const pickN = state.pick.voice;
  const picked = cands.find((x) => String(x.n) === String(pickN));
  const url = (x) => fileUrl(c.slug, x.file, x.at);
  const brief = `<section class="panel">
    <div class="panel-head"><h2>Sa voix, en mots</h2></div>
    ${textarea('voice.description', v?.description || sheet.speech_style || '',
      '« grave et lente, un peu cassée, accent du Finistère, jamais pressée »', 2)}
    <label class="field"><span class="lbl">ce qu'il dit pour l'essai</span>${input('voice.text', cands.at(-1)?.text || '',
      'laisse vide : il se présente')}</label>
    <div class="form-row"><span class="hint">Quatre voix liront la même phrase ; tu gardes la sienne.</span><span class="sp"></span>
      ${btn(cands.length ? 'Quatre autres ▸' : 'Quatre voix ▸', { act: 'voice_design', form: 'voice', params: { n: 4 },
        go: !cands.length && !job, disabled: !!job, why: job ? 'des voix sont en cours' : '' })}</div>
  </section>`;
  const cards = cands.length || job ? `<div class="voices">${current.map((x) => `<div class="vc${picked === x ? ' sel' : ''}"
      data-pick="voice" data-val="${x.n}" tabindex="0" role="button" aria-pressed="${picked === x}">
      <span class="vc-n">voix n° ${x.n}${picked === x ? ' · choisie' : ''}</span>${player(url(x), { text: x.text })}</div>`).join('')}
    ${Array.from({ length: expected - current.length }, (_, i) => slot(job, current.length + i, current.length, expected, 'vc'))
      .join('')}</div>` : '';
  const olderList = older.length ? `<div class="strip-wrap"><span class="lbl">voix précédentes · ${older.length}</span>
    <div class="voices small">${older.map((x) => `<div class="vc${picked === x ? ' sel' : ''}" data-pick="voice" data-val="${x.n}"
      tabindex="0" role="button"><span class="vc-n">n° ${x.n}</span>${player(url(x))}</div>`).join('')}</div></div>` : '';
  return `<div class="work one"><div class="work-main">${brief}${failure(job ? null : lastJob('voice_design'))}${cards}
    ${olderList}${job ? waitingCard(d, 'voix') : ''}</div></div>
  ${cands.length ? decide({
    text: picked ? `la voix n° ${picked.n}, c'est la sienne ?` : 'Écoute, puis clique la voix qui est la sienne.',
    button: btn('Cette voix ▸', { act: 'voice_lock', params: picked ? { candidate: String(picked.n) } : undefined,
      go: true, disabled: !picked }),
  }) : ''}`;
}

function lockedVoiceFile(v) {
  if (!v?.locked) return null;
  if (/^\d+$/.test(String(v.locked))) return v.candidates?.[Number(v.locked) - 1]?.file || null;
  return v.locked;
}

function voiceLocked(d, v) {
  const c = d.character;
  const url = fileUrl(c.slug, lockedVoiceFile(v), v.locked_at);
  const lines = (v.lines || []).slice().reverse();
  const job = liveJob('line');
  return `<div class="duo wide-left">
    <section class="panel voice-hero">
      <span class="kicker">sa voix</span>
      <h2 class="big">C'est la sienne.</h2>
      ${player(url, { text: v.locked_text, cls: 'lg' })}
      ${v.description ? `<p class="hint">${esc(v.description)}</p>` : ''}
      <div class="row-start">${link('Lui parler ▸', tabHref('scene'), { go: true })}
        ${btn('Changer de voix', { act: 'voice_unlock', confirm: 'Rouvrir le choix de sa voix ? Les répliques gardées restent.' })}</div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>Faire dire</h2><span class="lbl">trois prises par réplique</span></div>
      ${textarea('line.text', '', 'ce qu\'il dit', 2)}
      <label class="field"><span class="lbl">direction de jeu</span>${input('line.direction', '', '« à voix basse, épuisé »')}</label>
      <div class="form-row"><span class="sp"></span>${btn('Faire dire ▸', { act: 'line', form: 'line', params: { takes: 3 },
        disabled: !!job, why: job ? 'une réplique est en cours' : '' })}</div>
    </section>
  </div>
  <section class="panel">
    <div class="panel-head"><h2>Ses répliques</h2><span class="lbl">${lines.length || 'aucune'}</span></div>
    ${job ? `<div class="line-it pending"><span class="lbl">en cours · ${Math.round((job.progress || 0) * 100)} %</span>
      <p>« ${esc(job.params?.text || '')} »</p></div>` : ''}
    ${lines.map((l) => lineItem(c, l)).join('') || (job ? '' : '<p class="hint">Rien encore. Fais-lui dire une première phrase, ou parle-lui dans la Scène.</p>')}
  </section>`;
}

function lineItem(c, l) {
  const takes = l.takes || [];
  return `<div class="line-it"><p>« ${esc(l.text)} »${l.direction ? ` <span class="lbl">${esc(l.direction)}</span>` : ''}</p>
    <div class="takes">${takes.map((t, i) => {
      // `kept` : le numéro de la prise (1, 2…), ou son fichier.
      const kept = t.kept === true || String(l.kept) === String(i + 1) || (!!t.file && l.kept === t.file);
      return `<span class="take${kept ? ' kept' : ''}">${player(fileUrl(c.slug, t.file, t.at), { n: `prise ${i + 1}` })}
        ${kept ? '<span class="lbl ok">gardée</span>' : btn('Garder', { act: 'line_keep', params: { line: l.id, take: String(i + 1) }, sm: true })}</span>`;
    }).join('')}</div></div>`;
}

/* ── volet Planche ──────────────────────────────────────── */

const panelFile = (p) => (typeof p === 'string' ? p : p?.file);

function tabPlanche(d) {
  const c = d.character;
  const key = validatedCostume(c);
  if (!key) {
    return `<section class="soon"><span class="kicker">sa planche</span><h2 class="big">Pas encore de planche.</h2>
      <p class="prose">La planche de présentation se compose une fois sa tenue choisie : poses naturelles, expressions,
        détails, palette et taille, sur une seule image à montrer.</p>
      <div class="row-start">${link(c.face.locked ? 'L\'habiller ▸' : 'Choisir son visage ▸',
        tabHref(c.face.locked ? 'garde-robe' : 'visage'), { go: true })}</div></section>`;
  }
  const cos = c.costumes[key];
  const pres = cos.presentation || null;
  const job = liveJob('presentation', key);
  const panels = pres?.panels || {};
  const src = (f) => fileUrl(c.slug, f, pres?.at);
  const tenues = Object.keys(c.costumes).filter((k) => c.costumes[k].fullbody.validated);
  const tabs = tenues.length > 1 ? `<div class="seg tenues">${tenues.map((k) => `<button class="tb sm${k === key ? ' on' : ''}"
    data-costume-tab="${esc(k)}">${esc(c.costumes[k].name)}</button>`).join('')}</div>` : '';
  const soon = state.missing.presentation;
  const compose = soon ? '<p class="hint">La planche de présentation arrive bientôt : l\'atelier ne sait pas encore la composer.</p>'
    : btn(pres ? 'Recomposer' : 'Composer sa planche ▸', {
      act: 'presentation', costume: key, go: !pres && !job, disabled: !!job, why: job ? 'elle se compose' : '',
      confirm: pres ? 'Recomposer sa planche ? L\'ancienne reste dans les coulisses.' : undefined,
    });
  const rows = (pres || job) ? PLANCHE_ROWS.map(([k, label, want, shape]) => {
    const files = (panels[k] || []).map(panelFile).filter(Boolean);
    const n = job ? Math.max(want, files.length) : files.length;
    if (!n) return '';
    return `<div class="pl-row"><span class="lbl">${esc(label)}</span><div class="pl-grid ${shape}">${
      files.map((f, i) => `<button class="pl-cell ${shape}" data-zoom="${esc(src(f))}" data-cap="${esc(`${label} · ${i + 1}`)}">
        <img src="${esc(src(f))}" alt="" loading="lazy"></button>`).join('')}${
      Array.from({ length: n - files.length }, (_, i) => slot(job, files.length + i, files.length, n, `pl-cell ${shape}`)).join('')
    }</div></div>`;
  }).join('') : '';
  const sheet = pres?.sheet ? `<figure class="pl-sheet"><img src="${esc(src(pres.sheet))}" alt="sa planche" data-zoom="${
    esc(src(pres.sheet))}" data-cap="${esc(`${c.name} · ${cos.name}`)}"></figure>` : '';
  // Rien de composé : ce qu'on a déjà de lui, en attendant.
  const already = !pres && !job ? fallbackPlanche(c, key, cos) : '';
  return `${tabs}<section class="panel pl">
      <div class="panel-head"><h2>Sa planche · ${esc(cos.name)}</h2><span class="sp"></span>
        ${pres?.sheet ? `<a class="tb ghost sm" href="${esc(src(pres.sheet))}" download="${esc(`${c.slug}-planche.png`)}">Exporter</a>` : ''}
        ${compose}</div>
      ${job ? '<p class="hint">Elle se remplit case par case : expressions, poses, détails.</p>' : ''}
      ${sheet}${sheet && rows && !job ? `<details class="redo"><summary>les cases une à une</summary>
        <div class="redo-body">${rows}</div></details>` : rows}${already}
    </section>`;
}

function fallbackPlanche(c, key, cos) {
  const f = c.face;
  const items = [];
  if (cos.fullbody.validated) {
    items.push(['tall', fileUrl(c.slug, cos.fullbody.validated, cos.fullbody.validated_at), `${cos.name} · plein pied`]);
  }
  if (f.locked) items.push(['sq', fileUrl(c.slug, f.locked, f.locked_at), 'son visage']);
  (cos.sheets || []).filter((s) => s.engine === 'qwen21').slice(-2)
    .forEach((s) => items.push(['wide', fileUrl(c.slug, s.file, s.at), 'planche de référence']));
  return `<p class="hint">Pas encore composée. Ce qu'on a déjà de lui :</p><div class="pl-have">${items.map(([shape, u, cap]) =>
    `<button class="pl-cell ${shape}" data-zoom="${esc(u)}" data-cap="${esc(cap)}"><img src="${esc(u)}" alt="${esc(cap)}"
      loading="lazy"><span class="lbl">${esc(cap)}</span></button>`).join('')}</div>`;
}

/* ── la Scène : lui parler ──────────────────────────────── */

function sceneKey(s = slug()) { return `cf.scene.${s}`; }

function sceneLog(s = slug()) {
  state.scene.logs ||= {};
  if (!state.scene.logs[s]) {
    let saved = [];
    try { saved = JSON.parse(sessionStorage.getItem(sceneKey(s)) || '[]'); } catch (_) { /* navigation privée */ }
    state.scene.logs[s] = Array.isArray(saved) ? saved : [];
  }
  return state.scene.logs[s];
}

function saveSceneLog(s = slug()) {
  try { sessionStorage.setItem(sceneKey(s), JSON.stringify(sceneLog(s).slice(-60))); } catch (_) { /* rien */ }
}

function bubble(m, name) {
  if (m.role === 'note') return `<div class="bub note${m.err ? ' err' : ''}">${esc(m.text)}</div>`;
  const me = m.role === 'user';
  return `<div class="bub ${me ? 'me' : 'them'}"><span class="who">${me ? 'toi' : esc(name)}</span>
    <div class="txt">${esc(m.text)}</div>${m.audio ? player(m.audio) : ''}</div>`;
}

function sceneLogHtml(d) {
  const c = d.character;
  const log = sceneLog();
  if (state.scene.mode === 'say') {
    const v = voiceOf(c);
    const lines = (v?.lines || []).slice().reverse();
    const job = liveJob('line');
    const pend = job ? `<div class="line-it pending"><span class="lbl">il la dit · ${Math.round((job.progress || 0) * 100)} %</span>
      <p>« ${esc(job.params?.text || '')} »</p></div>` : '';
    return pend + (lines.map((l) => lineItem(c, l)).join('')
      || (job ? '' : `<p class="scene-empty">Écris une phrase et une direction de jeu : il la dit, en trois prises. Tu gardes la meilleure.</p>`));
  }
  if (!log.length) {
    return `<div class="scene-empty"><p>${esc(c.name)} t'écoute.</p><div class="chips-row">${SUGGEST.map((s) =>
      `<button class="chip" data-suggest="${esc(s)}">${esc(s)}</button>`).join('')}</div></div>`;
  }
  return log.map((m) => bubble(m, c.name)).join('') + (state.scene.busy
    ? `<div class="bub them"><span class="who">${esc(c.name)}</span><div class="thinking"><span></span><span></span><span></span></div></div>` : '');
}

function renderScene(d) {
  const c = d.character;
  const s = d.summary;
  const v = voiceOf(c);
  const cos = validatedCostume(c);
  const face = c.face.locked ? fileUrl(c.slug, c.face.locked, c.face.locked_at) : s.thumb;
  const body = cos ? fileUrl(c.slug, c.costumes[cos].fullbody.validated, c.costumes[cos].fullbody.validated_at) : '';
  const blocker = micBlocker(state.voice.config);
  const voiceLine = v?.locked ? 'sa voix est choisie' : voicePossible(c) ? 'pas encore de voix : il répond par écrit'
    : 'il répond par écrit';
  const say = state.scene.mode === 'say';
  const canSay = !!v?.locked;
  const mic = !say && !blocker ? `<button type="button" class="tb ghost mic${state.scene.micState === 'listening' ? ' on' : ''}"
      data-mic title="appuie et parle">${state.scene.micState === 'listening' ? 'Je t\'écoute…' : 'Micro'}</button>` : '';
  const compose = say
    ? `${textarea('scene.line', '', 'ce qu\'il dit', 2)}
       <div class="compose-row">${input('scene.direction', '', 'direction de jeu : « à voix basse, épuisé »')}
         ${btn('Faire dire ▸', { type: 'submit', go: true, disabled: !canSay || !!liveJob('line'),
           why: !canSay ? 'il lui faut d\'abord une voix' : liveJob('line') ? 'il en dit déjà une' : '' })}</div>
       ${canSay ? '' : `<a class="lbl more" href="${tabHref('voix')}">lui trouver une voix ▸</a>`}`
    : `${textarea('scene.msg', '', `Dis-lui quelque chose…`, 2)}
       <div class="compose-row">${mic}${!say && blocker && voicePossible(c) ? `<span class="hint">micro : ${esc(blocker)}</span>` : ''}
         <span class="sp"></span>${btn('Dire ▸', { type: 'submit', go: true, disabled: state.scene.busy })}</div>`;
  return `<div class="scene">
    <div class="scene-bar">
      <a class="tb ghost sm" href="${tabHref('')}">◂ ${esc(c.name)}</a>
      <span class="sp"></span>
      <span class="seg"><button class="tb sm${say ? '' : ' on'}" data-scene-mode="talk">Conversation</button>
        <button class="tb sm${say ? ' on' : ''}" data-scene-mode="say">Faire dire</button></span>
    </div>
    <div class="scene-grid">
      <figure class="scene-portrait">
        ${face ? `<img class="sp-face" src="${esc(face)}" alt="${esc(c.name)}">` : `<span class="ph">${esc(initials(c.name))}</span>`}
        ${body ? `<img class="sp-body" src="${esc(body)}" alt="" data-zoom="${esc(body)}" data-cap="${esc(c.name)}">` : ''}
        <figcaption><span class="nm">${esc(c.name)}</span><span class="lbl">${esc(voiceLine)}</span></figcaption>
      </figure>
      <section class="scene-talk">
        <div class="scene-log" id="scene-log" aria-live="polite">${sceneLogHtml(d)}</div>
        <form class="scene-compose" data-form="scene">${compose}</form>
      </section>
    </div>
  </div>`;
}

function paintScene() {
  const node = $('#scene-log');
  if (!node || !state.detail) return;
  node.innerHTML = sceneLogHtml(state.detail);
  node.scrollTop = node.scrollHeight;
  paintPlayers();
}

async function sendScene(text) {
  const d = state.detail;
  const s = slug();
  const log = sceneLog(s);
  const history = log.filter((m) => m.role === 'user' || m.role === 'char')
    .map((m) => ({ role: m.role === 'char' ? 'assistant' : 'user', content: m.text }));
  log.push({ role: 'user', text });
  state.scene.busy = true;
  paintScene();
  const btnSend = $('.scene-compose button[type=submit]');
  if (btnSend) btnSend.disabled = true;
  try {
    const out = await talk({ slug: s, name: d.character.name, sheet: d.character.identity || {}, history, message: text });
    const msg = { role: 'char', text: out.reply, audio: out.audio ? anyUrl(s, out.audio) : null };
    log.push(msg);
    if (out.via !== 'studio' && !state.scene.noted) {
      state.scene.noted = true;
      log.push({ role: 'note', text: 'il répond par écrit : la voix n\'est pas encore branchée à la conversation' });
    }
    if (msg.audio) play(msg.audio);
  } catch (e) {
    log.push({ role: 'note', err: true, text: `pas de réponse : ${e.message}` });
  }
  state.scene.busy = false;
  saveSceneLog(s);
  paintScene();
  if (btnSend) btnSend.disabled = false;
}

async function toggleMic() {
  if (state.scene.mic && state.scene.micState === 'listening') { state.scene.mic.stop(); return; }
  const cfgv = state.voice.config;
  const blocker = micBlocker(cfgv);
  if (blocker) { toast(blocker, 5000); return; }
  const log = sceneLog();
  state.scene.mic ||= new Micro(cfgv.ws_url, {
    onTranscript: (text) => { if (text) { log.push({ role: 'user', text }); paintScene(); } },
    onReply: (text, audio) => {
      log.push({ role: 'char', text, audio: audio ? anyUrl(slug(), audio) : null });
      saveSceneLog();
      paintScene();
      if (audio) play(anyUrl(slug(), audio));
    },
    onAudio: (url) => play(url),
    onState: (st) => { state.scene.micState = st; const b = $('[data-mic]'); if (b) b.classList.toggle('on', st === 'listening'); },
  });
  try {
    await state.scene.mic.start();
  } catch (e) {
    toast(`micro : ${e.message}`, 6000);
  }
}

/* ── lecture des voix : un seul lecteur pour toute la page ── */

const audio = new Audio();
audio.preload = 'none';

function play(url) {
  if (state.playing === url && !audio.paused) { audio.pause(); return; }
  state.playing = url;
  audio.src = url;
  audio.play().catch(() => toast('lecture impossible'));
  paintPlayers();
}

function paintPlayers() {
  $$('[data-play]').forEach((b) => b.classList.toggle('on', b.dataset.play === state.playing && !audio.paused));
  const pr = audio.duration ? audio.currentTime / audio.duration : 0;
  $$('[data-wave]').forEach((w) => {
    const i = w.firstElementChild;
    if (i) i.style.width = `${w.dataset.wave === state.playing ? Math.round(pr * 100) : 0}%`;
  });
}

['play', 'pause', 'ended', 'timeupdate'].forEach((ev) => audio.addEventListener(ev, paintPlayers));

/* ── rendu ──────────────────────────────────────────────── */

function typing() {
  const a = document.activeElement;
  return a && $('#app').contains(a) && a.matches('input:not([type=checkbox]):not([type=file]), textarea, select');
}

function render(force = false) {
  if (!force && typing()) { state.pending = true; return; }
  state.pending = false;
  goUsed = false;
  const logBefore = $('#scene-log');
  const keep = logBefore ? logBefore.scrollHeight - logBefore.scrollTop : null;
  $('#app').innerHTML = state.route.view === 'perso' ? renderPerso() : renderHome();
  const logAfter = $('#scene-log');
  if (logAfter) logAfter.scrollTop = keep == null ? logAfter.scrollHeight : logAfter.scrollHeight - keep;
  $('#coulisses-link').href = state.route.view === 'perso' ? `./coulisses.html#/${enc(slug())}` : './coulisses.html';
  document.body.classList.toggle('in-scene', state.route.view === 'perso' && state.route.tab === 'scene');
  paintPlayers();
}

/* ── chargements ────────────────────────────────────────── */

async function loadList() {
  state.list = await api('/api/characters');
}

async function loadAttention() {
  try {
    const out = await api('/api/attention');
    state.attention = Array.isArray(out) ? out : out?.attention || out?.items || [];
  } catch (_) {
    state.attention = null;    // pas de route : on s'en tient aux résumés
  }
}

async function loadDetail() {
  const d = await api(`/api/characters/${enc(slug())}`);
  const sig = JSON.stringify(d);
  const changed = sig !== state.sig;
  state.sig = sig;
  state.detail = d;
  return changed;
}

async function loadJobs() {
  const { jobs } = await api(`/api/jobs?slug=${enc(slug())}`);
  state.jobs = jobs;
  return jobs;
}

async function loadVoice() {
  state.voice.config = await voiceConfig();
}

/* ── la navigation ──────────────────────────────────────── */

function parseRoute() {
  const h = location.hash.replace(/^#\/?/, '');
  const m = /^p\/([^/]+)(?:\/([^/]+))?/.exec(h);
  return m ? { view: 'perso', slug: decodeURIComponent(m[1]), tab: m[2] || null } : { view: 'home' };
}

async function onRoute() {
  const route = parseRoute();
  const changed = route.view !== state.route.view || route.slug !== state.route.slug;
  const tabChanged = route.tab !== state.route.tab;
  state.route = route;
  state.renaming = false;
  state.editField = null;
  if (changed) {
    state.detail = null;
    state.jobs = [];
    state.costume = undefined;
    state.pick = {};
    state.fbAgain = false;
    state.sig = '';
    state.seen.clear();
    state.scene.mic?.close();
    state.scene.mic = null;
    window.scrollTo(0, 0);
  } else if (tabChanged) {
    // Changer de volet garde la page où elle est, sauf si les volets sont passés au-dessus.
    const bar = $('.vols');
    if (bar && bar.getBoundingClientRect().top < 0) window.scrollTo(0, window.scrollY + bar.getBoundingClientRect().top - 70);
  }
  render(true);
  try {
    if (route.view === 'home') {
      await Promise.all([loadList(), loadAttention()]);
      document.title = 'CASTING · CHARACTER FACTORY';
    } else {
      await Promise.all([loadDetail(), loadJobs(), state.voice.config ? null : loadVoice()]);
      state.jobs.forEach((j) => { if (!live(j)) state.seen.add(j.id); });
      document.title = `${state.detail.character.name.toUpperCase()} · CHARACTER FACTORY`;
    }
    render(true);
  } catch (e) {
    $('#app').innerHTML = `<section class="soon"><h2 class="big">Introuvable.</h2><p class="prose">${esc(e.message)}</p>
      <div class="row-start"><a class="tb ghost" href="#/">◂ Casting</a></div></section>`;
  }
}

/* ── les actions ────────────────────────────────────────── */

function collect(el) {
  const params = el.dataset.params ? JSON.parse(el.dataset.params) : {};
  if (el.dataset.costume) params.costume = el.dataset.costume;
  const form = el.dataset.form;
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
  if (['costume_new', 'line', 'scene'].includes(form)) {
    for (const k of Object.keys(state.drafts)) if (k.startsWith(`${form}.`)) delete state.drafts[k];
  }
  (state.refs[form] || []).forEach((r) => URL.revokeObjectURL(r.url));
  delete state.refs[form];
}

function ask(text) {
  const box = $('#confirm');
  $('#confirm-text').textContent = text;
  box.hidden = false;
  $('#confirm-yes').focus();
  return new Promise((resolve) => {
    const done = (v) => {
      box.hidden = true;
      box.onclick = null;
      document.removeEventListener('keydown', onKey, true);
      resolve(v);
    };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); done(false); } };
    document.addEventListener('keydown', onKey, true);
    box.onclick = (e) => {
      if (e.target.id === 'confirm-yes') done(true);
      else if (e.target.id === 'confirm-no' || e.target === box) done(false);
    };
  });
}

const DONE_NOW = {
  face_lock: 'c\'est lui', fullbody_ok: 'tenue choisie : l\'atelier prend la suite', voice_lock: 'voix choisie : tu peux lui parler',
  voice_unlock: 'le choix de sa voix est rouvert', line_keep: 'prise gardée', costume_edit: 'tenue mise à jour',
};

async function doAction(el) {
  const action = el.dataset.act;
  if (el.dataset.confirm && !(await ask(el.dataset.confirm))) return;
  const params = collect(el);
  if (action === 'face') {
    if (!String(params.brief ?? 'x').trim()) delete params.brief;
    if (!('around' in params) && !params.brief && !(params.refs || []).length && !state.detail.character.face.brief) {
      toast('dis en une phrase qui il est, ou dépose une photo', 5000);
      $('[data-draft="face.brief"]')?.focus();
      return;
    }
  }
  el.disabled = true;
  try {
    if (action === 'costume_go') {
      // Une nouvelle tenue : elle se garde (une seule fois, même si le champ
      // vient d'être quitté), puis son plein pied part dans la file.
      const key = await createCostume();
      if (!key) { el.disabled = false; return; }
      const run = await api(actionUrl('fullbody'), { method: 'POST', body: { costume: key, variants: 3 } });
      state.jobs.unshift(run.job);
      schedulePoll(600);
      toast('il s\'habille : trois pleins pieds arrivent');
      await loadDetail();
      render(true);
      return;
    }
    const out = await api(actionUrl(action), { method: 'POST', body: params });
    if (out.job) {
      state.jobs.unshift(out.job);
      schedulePoll(600);
      toast(`c'est parti : ${HUMAN[action] || out.job.label}`);
    } else {
      toast(DONE_NOW[action] || 'fait');
      if (action === 'face_lock') delete state.pick.face;
      if (action === 'fullbody_ok') { delete state.pick[`fb:${params.costume}`]; state.fbAgain = false; }
      if (action === 'voice_lock') delete state.pick.voice;
    }
    forget(el.dataset.form);
    await loadDetail();
    render(true);
  } catch (e) {
    if (unknown(e)) {
      state.missing[action] = true;
      if (VOICE_ACTIONS.has(action)) state.missing.voice_design = true;
      toast('pas encore branché dans l\'atelier : ça arrive bientôt', 5000);
      render(true);
      return;
    }
    toast(e.message, 7000);
    el.disabled = false;
  }
}

async function answerAttention(el) {
  const opt = JSON.parse(el.dataset.option || '{}');
  const id = el.dataset.attention;
  el.disabled = true;
  try {
    // l'atelier ne reçoit les réponses que par l'action `attention` :
    // `do` = retry, dismiss ou choose (avec le candidat retenu)
    const body = { id, do: opt.action || 'dismiss' };
    if (opt.candidate) body.candidate = opt.candidate;
    await api(actionUrl('attention'), { method: 'POST', body });
    toast('réponse donnée à l\'atelier');
    await loadDetail();
    render(true);
  } catch (e) {
    toast(unknown(e) ? 'l\'atelier ne sait pas encore recevoir la réponse : vois dans les coulisses' : e.message, 6000);
    el.disabled = false;
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
    toast('décris d\'abord la tenue, ou dépose une image de vêtement', 5000);
    return Promise.resolve(null);
  }
  costumeSaving = (async () => {
    try {
      const out = await api(actionUrl('costume_add'), {
        method: 'POST', body: { brief, refs, name: state.drafts['costume_new.name'] || '' },
      });
      state.costume = out.result.costume;
      forget('costume_new');
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
        raw: { body: f, headers: { 'content-type': f.type, 'x-filename': enc(f.name) } },
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
  if (!name) { form.elements.name.focus(); toast('un nom, même provisoire'); return; }
  try {
    const out = await api('/api/characters', { method: 'POST', body: { name } });
    location.hash = `#/p/${enc(out.slug)}`;
  } catch (e) {
    toast(e.message, 6000);
  }
}

async function saveIdentity(body, message) {
  try {
    await api(`/api/characters/${enc(slug())}/identity`, { method: 'PUT', body });
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
  if (value !== ((state.detail.character.identity || {})[key] || '')) await saveIdentity({ fields: { [key]: value } }, 'gardé');
  render(true);
}

async function toggleTrait(t) {
  const sheet = state.detail.character.identity || {};
  const have = String(sheet.personality_traits || '').split(',').map((x) => x.trim()).filter(Boolean);
  const i = have.findIndex((x) => x.toLowerCase() === t);
  if (i >= 0) have.splice(i, 1); else have.push(t);
  await saveIdentity({ fields: { personality_traits: have.join(', ') } });
  render(true);
}

async function setStyle(value) {
  await saveIdentity({ style: value }, `rendu : ${value === 'stylized' ? 'stylisé' : 'photo'}`);
  render(true);
}

async function submitScene(form) {
  const say = state.scene.mode === 'say';
  if (say) {
    const text = String(state.drafts['scene.line'] || '').trim();
    if (!text) { toast('écris ce qu\'il doit dire'); return; }
    const fake = document.createElement('button');
    fake.dataset.act = 'line';
    fake.dataset.params = JSON.stringify({ text, direction: String(state.drafts['scene.direction'] || '').trim(), takes: 3 });
    delete state.drafts['scene.line'];
    delete state.drafts['scene.direction'];
    await doAction(fake);
    return;
  }
  const field = form.querySelector('[data-draft="scene.msg"]');
  const text = String(field?.value || '').trim();
  if (!text || state.scene.busy) return;
  field.value = '';
  delete state.drafts['scene.msg'];
  await sendScene(text);
  $('[data-draft="scene.msg"]')?.focus();
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
    const z = t.closest('[data-zoom]');
    if (z && !t.closest('[data-act], [data-drop-ref]')) {
      e.preventDefault(); e.stopPropagation(); zoom(z.dataset.zoom, z.dataset.cap); return;
    }
    const playBtn = t.closest('[data-play]');
    if (playBtn) { e.preventDefault(); e.stopPropagation(); play(playBtn.dataset.play); return; }
    const actBtn = t.closest('[data-act]');
    if (actBtn) { e.preventDefault(); e.stopPropagation(); if (!actBtn.disabled) doAction(actBtn); return; }
    const pick = t.closest('[data-pick]');
    if (pick) {
      const k = pick.dataset.pick;
      state.pick[k] = String(state.pick[k]) === pick.dataset.val ? undefined : pick.dataset.val;
      render(true);
      return;
    }
    const tab = t.closest('[data-costume-tab]');
    if (tab) { state.costume = tab.dataset.costumeTab || null; state.fbAgain = false; render(true); return; }
    const again = t.closest('[data-fb-again]');
    if (again) { state.fbAgain = again.dataset.fbAgain === '1'; render(true); return; }
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
      const inp = $('form[data-form="field"] textarea');
      if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
      return;
    }
    const trait = t.closest('[data-trait]');
    if (trait) { toggleTrait(trait.dataset.trait); return; }
    const mode = t.closest('[data-scene-mode]');
    if (mode) { state.scene.mode = mode.dataset.sceneMode; render(true); return; }
    const sug = t.closest('[data-suggest]');
    if (sug) { sendScene(sug.dataset.suggest); return; }
    if (t.closest('[data-mic]')) { toggleMic(); return; }
    const att = t.closest('[data-attention]');
    if (att) { answerAttention(att); return; }
    const drop = t.closest('[data-drop-ref]');
    if (drop) {
      e.preventDefault();
      const [form, i] = drop.dataset.dropRef.split(':');
      const [gone] = (state.refs[form] || []).splice(Number(i), 1);
      if (gone) URL.revokeObjectURL(gone.url);
      render(true);
    }
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
  });
  app.addEventListener('submit', (e) => {
    const form = e.target.dataset.form;
    if (!form) return;
    e.preventDefault();
    if (form === 'create') createCharacter(e.target);
    if (form === 'rename') rename(e.target);
    if (form === 'field') saveField(e.target);
    if (form === 'scene') submitScene(e.target);
  });
  app.addEventListener('keydown', (e) => {
    const t = e.target;
    if (e.key === 'Escape' && (state.renaming || state.editField)) {
      state.renaming = false; state.editField = null; render(true); return;
    }
    // Entrée envoie ; Maj+Entrée va à la ligne.
    if (e.key === 'Enter' && !e.shiftKey && t.matches('form[data-form="field"] textarea, [data-draft="scene.msg"]')) {
      e.preventDefault();
      t.form.requestSubmit();
      return;
    }
    if ((e.key === 'Enter' || e.key === ' ') && t.matches('[data-pick], [data-edit]')) { e.preventDefault(); t.click(); }
  });
  app.addEventListener('focusout', (e) => {
    // Un champ de la fiche se garde en quittant le champ, comme avec Entrée.
    const field = e.target.closest?.('form[data-form="field"]');
    if (field && state.editField) setTimeout(() => { if (state.editField && document.contains(field)) saveField(field); }, 120);
    setTimeout(() => { if (state.pending && !typing()) render(); }, 180);
  });

  // Déposer une image sur une zone de références.
  app.addEventListener('dragover', (e) => {
    const zone = e.target.closest?.('[data-refs-zone]');
    if (zone) { e.preventDefault(); zone.classList.add('over'); }
  });
  app.addEventListener('dragleave', (e) => { e.target.closest?.('[data-refs-zone]')?.classList.remove('over'); });
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

function announce(j) {
  if (BACKGROUND[j.action]) return;   // l'arrière-plan ne dérange pas
  if (j.status === 'done') toast(DONE_TEXT[j.action] || `${j.label} : fini`, 4500);
  else if (j.status === 'error') toast(`le rendu n'a pas abouti : ${String(j.error || '').replace(/^refusé : /, '').slice(0, 120)}`, 7000);
}

const jobsSig = () => state.jobs.map((j) => `${j.id}:${j.status}:${j.progress}`).join('|');

async function pollJobs() {
  let active = false;
  try {
    if (state.route.view === 'perso' && state.detail) {
      const before = jobsSig();
      const jobs = await loadJobs();
      const moved = jobsSig() !== before;
      active = jobs.some(live);
      const finished = jobs.filter((j) => !live(j) && !state.seen.has(j.id));
      finished.forEach((j) => { state.seen.add(j.id); announce(j); });
      const changed = await loadDetail();
      // La Scène ne se redessine pas sous les doigts : seul son fil bouge.
      if (state.route.tab === 'scene') { if ((changed || moved) && state.scene.mode === 'say') paintScene(); }
      else if (changed || moved) render();
    } else if (state.route.view === 'home') {
      const before = JSON.stringify([state.list, state.attention]);
      await Promise.all([loadList(), loadAttention()]);
      if (JSON.stringify([state.list, state.attention]) !== before) render();
    }
  } catch (_) { /* serveur momentanément muet : on réessaie */ }
  schedulePoll(active ? 1500 : 5000);
}

// Un travail vient de partir : on relève tout de suite, sans attendre le tour lent.
let pollTimer = null;
function schedulePoll(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollJobs, ms);
}

async function pollSystem() {
  const pill = $('#sys-pill');
  try {
    const s = await api('/api/system');
    state.system = s;
    const mem = s.memory?.available_gb;
    const low = mem != null && mem < s.memory.min_free_gb;
    const run = s.running;
    const who = run && state.list?.characters?.find((c) => c.slug === run.slug)?.name;
    $('#sys-text').textContent = run ? `au travail · ${HUMAN[run.action] || run.label}${who ? ` · ${who}` : ''}`
      : low ? 'mémoire basse' : 'prêt';
    pill.className = `pill ${run ? 'work' : low ? 'err' : 'on'}`;
    pill.title = s.queued ? `${s.queued} en file` : '';
  } catch (_) {
    $('#sys-text').textContent = 'studio muet';
    pill.className = 'pill err';
  }
  setTimeout(pollSystem, 5000);
}

wire();
onRoute().then(() => { pollJobs(); pollSystem(); });
