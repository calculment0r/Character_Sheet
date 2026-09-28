'use strict';

import { SHEET_FIELDS } from './schema.js';
import { talk, voiceConfig, micBlocker, Micro } from './parler.js';
import { fetchHealth, command, machinesList, verdict, when, stepsText, h3Up } from './machines.js';

/* ============================================================
   Le studio.

     #/                     le casting : les personnages en affiches
     #/p/<slug>             un personnage — sa naissance tant qu'il n'est
                            pas décrit, puis SA FICHE : tout sur une page
     #/p/<slug>/<surface>   une étape en plein cadre, ouverte depuis la
                            fiche : identite · visage · garde-robe · voix
                            · planche ; Échap ramène à la fiche
     #/p/<slug>/scene       lui parler

   La fiche (Cal, 28/09 : « une super page avec tout pour voir très bien
   notre personnage et le finetuner ») : le visage en grand et ses looks,
   le plein pied de la tenue active et ses poses, qui il est, ses
   expressions ; puis la 3D, la voix, la taille ; ses tenues, ses
   détails, où il en est. Plus d'onglets.

   On ne tranche ici que le goût : le visage, la tenue, la voix. La
   technique (A-pose, vues, 3D, rig) tourne en arrière-plan ; elle ne
   se montre que par son état, et mène aux coulisses. Aucun nom de
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
  queue: [],                 // la file entière, pour le rang d'un rendu qui attend
  look: null,                // le look montré en grand ; null = le visage de base
  bodyView: {},              // par tenue : la pose montrée ('' = le plein pied)
  motionPeek: null,          // l'expression dont on demande le mouvement
  traitsOpen: false,
  moreOpen: false,           // « toute sa fiche », déplié
  regions: {},               // la fiche : le dernier HTML de chaque région
  castFilter: null,          // le casting : 'mine' | 'all' ; null = selon qui regarde
  // les machines, lues sur le pont : { health, open, busy, fast (jusqu'à quand relever vite) }
  machines: { health: null, tried: false, open: false, busy: false, fast: 0 },
};

// Le pont (tools/bridge.py) : derrière le tunnel, sur la même adresse
// (/bridge/*) ; à la maison, sur le port 8770 de la même machine.
const BRIDGE = location.port === '8765' ? `${location.protocol}//${location.hostname}:8770` : '';

// Les étapes en plein cadre, ouvertes depuis la fiche.
const SURFACES = {
  identite: 'qui il est', visage: 'son visage', 'garde-robe': 'sa garde-robe', voix: 'sa voix', planche: 'sa planche',
};
const TAB_IDS = new Set(Object.keys(SURFACES));

// Ce que fait l'arrière-plan, dit simplement pour la puce de l'atelier.
const BACKGROUND = {
  apose: 'pose', apose_ok: 'pose', views: 'vues', prep: 'vues', check: 'vues',
  mesh: '3D', rig: 'rig', rig_ok: 'rig', sheet: 'planche',
};
// Ce que fait un travail, pour la pastille d'en-tête et les annonces.
const HUMAN = {
  face: 'ses visages', fullbody: 'sa tenue', voice_design: 'ses voix', line: 'une réplique',
  presentation: 'sa planche', apose: 'sa pose', views: 'ses vues', prep: 'ses vues', check: 'ses vues',
  mesh: 'sa 3D', rig: 'son squelette', sheet: 'une planche', look: 'un look', expression_add: 'une expression',
};
const DONE_TEXT = {
  face: 'ses visages sont arrivés', fullbody: 'ses pleins pieds sont arrivés', voice_design: 'ses voix sont prêtes',
  line: 'la réplique est dite', presentation: 'sa planche est composée', look: 'le look est prêt',
  expression_add: 'la nouvelle expression est prête',
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
    // La fiche montre elle-même les candidats, avec l'orange du choix.
    if (next === 'face_lock') out.push({ c, href, text: 'attend que tu choisisses son visage' });
    else if (next === 'fullbody_ok') out.push({ c, href, text: 'attend que tu choisisses sa tenue' });
    else if (next === 'face' && !c.thumb) out.push({ c, href, text: 'attend que tu dises qui il est' });
  }
  return out;
}

/* Les miens, ou tous : chacun voit d'abord ses personnages ; Cal, tous. */
const ownerName = (o) => String(o || 'cal').split('@')[0];
const mineOf = (c) => !state.list?.me?.id || (c.owner || 'cal') === state.list.me.id;

function castFilter() {
  if (state.castFilter) return state.castFilter;
  try {
    const kept = localStorage.getItem('cf.cast.filter');
    if (kept === 'mine' || kept === 'all') return kept;
  } catch (_) { /* stockage fermé : le défaut suffit */ }
  return state.list?.me?.admin === false ? 'mine' : 'all';
}

function setCastFilter(v) {
  state.castFilter = v;
  try { localStorage.setItem('cf.cast.filter', v); } catch (_) { /* rien à garder */ }
}

function castSeg(all) {
  if (!state.list?.me || !all.length) return '';
  const f = castFilter();
  const mine = all.filter(mineOf).length;
  return `<div class="seg cast-filter" role="group" aria-label="les personnages montrés">
    <button class="tb${f === 'mine' ? ' on' : ''}" type="button" data-cast-filter="mine" aria-pressed="${f === 'mine'}">Les miens · ${mine}</button>
    <button class="tb${f === 'all' ? ' on' : ''}" type="button" data-cast-filter="all" aria-pressed="${f === 'all'}">Tous · ${all.length}</button>
  </div>`;
}

function renderHome() {
  const all = state.list?.characters || [];
  const list = castFilter() === 'mine' ? all.filter(mineOf) : all;
  const waits = waitsOnCasting(list);
  // « Ce qui attend » ne se montre que s'il y a quelque chose : un panneau vide ne dit rien.
  return `
  <section class="cast-top${waits.length ? '' : ' solo'}">
    <div class="cast-hero">
      <span class="kicker">casting</span>
      <h1 class="cast-title">Les personnages</h1>
      <p class="prose">Un nom pour commencer, une phrase pour dire qui il est. Son visage, sa voix et sa tenue viennent
        ensuite, une question à la fois ; le reste se fait tout seul, pendant que tu travailles.</p>
      ${castSeg(all)}
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
      ${mineOf(c) ? '' : `<span class="lbl" title="${esc(c.owner)}">de ${esc(ownerName(c.owner))}</span>`}
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

// La tenue que montre la fiche : celle qu'on a choisie, sinon la
// première habillée, sinon la première.
function activeCostume(c) {
  const keys = Object.keys(c.costumes);
  if (state.costume && keys.includes(state.costume)) return state.costume;
  return keys.find((k) => c.costumes[k].fullbody.validated) || keys[0] || null;
}

// Sans onglet : la fiche, une étape en plein cadre, la Scène, ou la
// naissance tant qu'il n'est ni décrit ni en train de naître.
function currentTab(d) {
  const t = state.route.tab;
  if (t === 'scene' || TAB_IDS.has(t)) return t;
  const f = d.character.face;
  if (!f.locked && !f.candidates.length && !f.brief && !liveJob('face')) return 'naissance';
  return 'fiche';
}

function renderPerso() {
  const d = state.detail;
  if (!d) return '<p class="prose loading">chargement…</p>';
  const tab = currentTab(d);
  if (tab === 'naissance') return renderNaissance(d);
  if (tab === 'scene') return renderScene(d);
  const body = { identite: tabIdentite, visage: tabVisage, 'garde-robe': tabGarde, voix: tabVoix, planche: tabPlanche }[tab](d);
  return `${surfaceHead(d, tab)}${attentionCards(d)}<div class="vol-body">${body}</div>`;
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

/* Une étape en plein cadre : la fiche reste à un clic (ou Échap), le
   visage et le nom rappellent de qui on parle. */
function surfaceHead(d, tab) {
  const c = d.character;
  const s = d.summary;
  const face = s.thumb ? `<img class="p-face" src="${esc(s.thumb)}" alt="" data-zoom="${esc(s.thumb)}" data-cap="${esc(c.name)}">`
    : `<span class="p-face ph">${esc(initials(c.name))}</span>`;
  const [chip, chipCls] = atelier(d);
  const what = tab === 'identite' && she(c) ? 'qui elle est' : SURFACES[tab];
  return `<div class="p-head">
    <a class="tb ghost sm back" href="${tabHref('')}" title="revenir à sa fiche — Échap">◂ Fiche · Échap</a>
    <div class="p-id">${face}<div class="p-who"><span class="kicker">${esc(what)}</span>${nameBlock(c, 'p-name')}</div></div>
    <span class="sp"></span>
    <a class="atelier ${chipCls}" href="./coulisses.html#/${enc(c.slug)}" title="ce que fait l'atelier : les coulisses"><i></i>${esc(chip)}</a>
    ${c.face.locked ? `<a class="tb" href="${tabHref('scene')}">Parler ▸</a>`
      : '<span class="tb disabled-link" title="il lui faut d\'abord un visage">Parler</span>'}
  </div>`;
}

/* ══ LA FICHE ═══════════════════════════════════════════════
   Une page, tout sous les yeux (maquette du 28/09, vue « La fiche »).
   Elle se dessine par régions : une région ne se redessine que si
   son HTML a changé — la 3D, dans son cadre, ne se recharge pas à
   chaque relevé de la file. Un personnage pas encore fait montre les
   mêmes blocs, en places ouvertes qui disent quoi faire.
   ══════════════════════════════════════════════════════════ */

const she = (c) => /^(f\b|fem|woman|fille|girl)/i.test(String(c.identity?.gender || '').trim());
const cap1 = (t) => { const s = String(t || '').trim(); return s ? s[0].toUpperCase() + s.slice(1) : ''; };
const low1 = (t) => { const s = String(t || '').trim(); return s ? s[0].toLowerCase() + s.slice(1) : ''; };
const sentence = (t) => { const s = String(t || '').trim(); return !s || /[.!?…»]$/.test(s) ? s : `${s}.`; };
const HEX = /^#[0-9a-f]{3,8}$/i;

function ageText(v) {
  const s = String(v || '').trim();
  if (/^\d{1,3}$/.test(s)) return `${s} ans`;
  return /\d/.test(s) || s.length >= 6 ? s : '';     // « la cinquantaine », pas un reste de lecture
}

function heightText(v) {
  const s = String(v || '').trim().replace(',', '.');
  const m = /(\d+(?:\.\d+)?)/.exec(s);
  if (!m) return '';
  let n = Number(m[1]);
  if (/cm/i.test(s) || n > 3) n /= 100;
  return n > 0.3 && n < 3 ? `${n.toFixed(2).replace('.', ',')} m` : '';
}

// Un trait du visage tiré de sa description : « yeux bleus vifs », « short black hair ».
function feature(desc, re) {
  const parts = String(desc || '').split(/[,.;]/).map((x) => x.trim().replace(/^(and|et|with|avec)\s+/i, '')).filter(Boolean);
  let p = parts.find((x) => re.test(x));
  if (!p) return '';
  if (p.length > 30) p = p.split(/\s+(?:et|and|with|avec|comme)\s+/i)[0];
  if (p.length > 34) p = p.split(/\s+/).slice(0, 4).join(' ');
  return p.toLowerCase();
}

function metaLine(c) {
  const s = c.identity || {};
  return [ageText(s.age), heightText(s.height), feature(s.face_description, /\b(yeux|eyes?)\b/i),
    feature(s.face_description, /\b(cheveux|hair|chauve|bald)\b/i), c.style === 'stylized' ? 'stylisé' : 'photo'].filter(Boolean);
}

// La plaque : qui il est, en prose — jamais une liste de champs.
function plaqueText(c) {
  const s = c.identity || {};
  if (String(s.plaque || c.plaque || '').trim()) return String(s.plaque || c.plaque).trim();
  const elle = she(c);
  const known = ['personality_traits', 'core_theme', 'behavior_notes', 'emotional_range', 'speech_style', 'role', 'archetype']
    .some((k) => String(s[k] || '').trim()) || c.face.brief;
  if (!known) return '';
  let first = [c.name, ageText(s.age), heightText(s.height)].filter(Boolean).join(', ');
  const role = String(s.role || s.archetype || '').trim();
  if (role) first += ` — ${low1(role)}`;
  const out = [sentence(first)];
  if (c.face.brief) out.push(sentence(cap1(c.face.brief)));
  if (s.personality_traits) out.push(sentence(cap1(s.personality_traits)));
  if (s.core_theme) out.push(sentence(`Ce qui ${elle ? 'la' : 'le'} travaille : ${low1(s.core_theme)}`));
  if (s.behavior_notes) out.push(sentence(cap1(s.behavior_notes)));
  if (s.emotional_range) out.push(sentence(`Ses émotions : ${low1(s.emotional_range)}`));
  if (s.speech_style) out.push(sentence(`Sa façon de parler : ${low1(s.speech_style)}`));
  return out.join(' ');
}

const traitList = (c) => String(c.identity?.personality_traits || '').split(',').map((t) => t.trim().toLowerCase())
  .filter((t) => t && t.length < 28);

// Les expressions d'une tenue : `panels.expressions` (liste), ou une table par id.
function exprList(pres) {
  const raw = pres?.panels?.expressions ?? pres?.expressions ?? [];
  const list = Array.isArray(raw) ? raw : Object.entries(raw).map(([id, e]) => ({ id, ...e }));
  const gone = new Set((pres?.removed_expressions || []).map((r) => r.id));
  return list.filter((e) => e && !gone.has(e.id));
}

function panelList(pres, group) {
  const raw = pres?.panels?.[group] ?? pres?.[group] ?? [];
  return (Array.isArray(raw) ? raw : Object.entries(raw).map(([id, e]) => ({ id, ...e }))).filter((e) => e?.file);
}

const looksOf = (c) => (Array.isArray(c.face.looks) ? c.face.looks.filter((l) => l && l.id) : []);

/* Le rang d'un rendu dans la file entière : Cal passe avant l'autopilote,
   puis l'ordre d'arrivée — celui de la file du serveur. */
function rank(job) {
  if (!job) return 'en calcul';
  if (job.status === 'running') return `en cours · ${Math.round((job.progress || 0) * 100)} %`;
  const all = state.queue.length ? state.queue : state.jobs;
  const q = all.filter((j) => j.status === 'queued')
    .sort((a, b) => (Number(!!a.auto) - Number(!!b.auto)) || String(a.created).localeCompare(String(b.created)));
  const i = q.findIndex((j) => j.id === job.id);
  return i >= 0 ? `en file · rang ${i + 1}` : 'en file';
}

// La technique d'une tenue : l'autopilote, ou un étage lancé à la main.
const TECH = new Set(['autopilot', 'apose', 'views', 'prep', 'check', 'mesh', 'rig']);
function techJob(key) {
  return state.jobs.find((j) => live(j) && TECH.has(j.action) && (!j.params?.costume || j.params.costume === key));
}

function techStep(cos, job) {
  const s = String(job && job.action !== 'autopilot' ? job.action : cos?.autopilot?.step || '');
  if (s.startsWith('apose')) return 'apose';
  if (/^(views|prep|check)/.test(s)) return 'views';
  if (s.startsWith('mesh')) return 'mesh';
  if (s.startsWith('rig')) return 'rig';
  return '';
}
const STEP_FR = { apose: 'A-pose', views: 'vues', mesh: 'mesh', rig: 'rig' };

function model3d(cos) {
  if (!cos) return null;
  const rigs = cos.rigs || [];
  const rig = [...rigs].reverse().find((r) => r.verdict === 'accepted' && r.glb);
  if (rig) return { glb: rig.glb, what: `rig v${rig.version} · accepté` };
  const m = [...(cos.meshes || [])].reverse().find((x) => x.glb);
  if (m) return { glb: m.glb, what: `mesh v${m.version} · ${rigs.length ? 'rig à regarder' : 'sans rig'}` };
  return null;
}

// Ce qui attend Cal pour une tenue, par sorte (rig_review, apose_failed…).
function waitsFor(d, key, re) {
  return attentionOf(d).some((a) => (!a.costume || a.costume === key) && re.test(String(a.kind || '')));
}

/* La prochaine décision de goût : l'unique bouton orange de la fiche. */
function nextDecision(d) {
  const c = d.character;
  const f = c.face;
  if (!f.locked) {
    if (!f.candidates.length) return liveJob('face') ? { text: 'ses visages arrivent' } : { html: link('Ses visages ▸', tabHref('visage'), { go: true }) };
    const n = state.pick.face;
    return {
      html: btn('Choisir ce visage ▸', {
        act: 'face_lock', params: n ? { candidate: String(n) } : undefined, go: true, disabled: !n,
        why: n ? '' : 'clique d\'abord le visage qui est lui',
        confirm: 'Garder ce visage ? Il fera autorité sur tout le reste — la tenue, la planche, la 3D — et ne changera plus.',
      }),
    };
  }
  const keys = Object.keys(c.costumes);
  if (!keys.length) return { html: btn('L\'habiller ▸', { go: true, attrs: 'data-goto="costume_new.brief"' }) };
  const act = activeCostume(c);
  const k = !c.costumes[act].fullbody.validated ? act : keys.find((x) => !c.costumes[x].fullbody.validated);
  if (k) {
    const cos = c.costumes[k];
    if (k !== act) return { html: btn(`${cos.name} ▸`, { go: true, attrs: `data-costume-pick="${esc(k)}"` }) };
    if (cos.fullbody.candidates.length) {
      const n = state.pick[`fb:${k}`];
      return {
        html: btn('Choisir ce plein pied ▸', {
          act: 'fullbody_ok', costume: k, params: n ? { candidate: String(n) } : undefined, go: true, disabled: !n,
          why: n ? '' : 'clique d\'abord le plein pied qui lui va',
        }),
      };
    }
    if (liveJob('fullbody', k)) return { text: `${she(c) ? 'elle' : 'il'} s'habille` };
    return { html: btn('L\'habiller ▸', { act: 'fullbody', costume: k, params: { variants: 3 }, go: true }) };
  }
  if (voicePossible(c) && !voiceOf(c)?.locked) return { html: link('Choisir sa voix ▸', tabHref('voix'), { go: true }) };
  const pres = c.costumes[act].presentation;
  if (!pres?.sheet && !exprList(pres).length && !state.missing.presentation && !liveJob('presentation', act)) {
    return {
      html: btn('Composer sa planche ▸', {
        act: 'presentation', costume: act, go: true,
        confirm: 'Composer sa planche ? Six expressions, cinq poses naturelles et quatre détails, tirés de son visage et de ce plein pied.',
      }),
    };
  }
  return { html: link('Lui parler ▸', tabHref('scene'), { go: true }), talk: true };
}

// Les crans de goût, lus sur le manifeste : fait · en calcul · attend Cal · à venir.
function crans(d) {
  const c = d.character;
  const f = c.face;
  const all = Object.values(c.costumes);
  const key = activeCostume(c);
  const cos = key ? c.costumes[key] : null;
  const v = voiceOf(c);
  const tech = cos && techJob(key);
  const d3 = !cos ? '' : (cos.rigs || []).some((r) => r.verdict === 'accepted') ? 'ok'
    : tech || cos.autopilot?.state === 'running' ? 'run' : attentionOf(d).some((a) => !a.costume || a.costume === key) ? 'wait' : '';
  return [
    ['visage', f.locked ? 'ok' : liveJob('face') ? 'run' : f.candidates.length ? 'wait' : ''],
    ['tenue', all.some((x) => x.fullbody.validated) ? 'ok' : liveJob('fullbody') ? 'run'
      : all.some((x) => x.fullbody.candidates.length) ? 'wait' : ''],
    ['expressions', casesJob(key) || liveJob('expression_add', key) ? 'run'
      : exprList(cos?.presentation).some((e) => e.file) ? 'ok' : ''],
    ['voix', v?.locked ? 'ok' : liveJob('voice_design') ? 'run' : v?.candidates?.length ? 'wait' : ''],
    ['3D', d3],
  ];
}
const CRAN_TITLE = { ok: 'fait', run: 'en calcul', wait: 'attend ton œil', '': 'à venir' };

function ficheHead(d) {
  const c = d.character;
  const next = nextDecision(d);
  return `<header class="fi-head">
    <a class="fi-back" href="#/" title="le casting">◂ Casting</a>
    <div class="fi-who">${nameBlock(c, 'fi-name')}
      <div class="fi-meta">${metaLine(c).map((x) => `<span>${esc(x)}</span>`).join('')}${d.can_edit === false
        ? `<span class="fi-owner" title="${esc(d.owner)}">à ${esc(ownerName(d.owner))} · regarder seulement</span>` : ''}</div></div>
    <div class="fi-crans" role="list" aria-label="où ${she(c) ? 'elle' : 'il'} en est">${crans(d).map(([k, st]) =>
      `<div class="fi-cran ${st}" role="listitem" title="${esc(`${k} : ${CRAN_TITLE[st]}`)}"><i></i><span>${esc(k)}</span></div>`).join('')}</div>
    <div class="fi-acts">${next.html || `<span class="fi-next">${esc(next.text || '')}</span>`}
      ${c.face.locked && !next.talk ? `<a class="tb ghost" href="${tabHref('scene')}">Parler</a>` : ''}
      <a class="tb ghost" href="./coulisses.html#/${enc(c.slug)}" title="tout ce que la chaîne fabrique, pour vérifier">Coulisses</a>
    </div>
  </header>`;
}

// Ce qui attend : un bandeau discret sous l'en-tête, jamais en haut de page.
function ficheWaits(d) {
  const list = attentionOf(d);
  if (!list.length) return '';
  return `<section class="fi-waits" aria-label="ce qui attend">${list.map((a) => `<article class="fi-wait">
    <i></i><b>${esc(a.title || 'une question de l\'atelier')}</b>${a.text ? `<span>${esc(a.text)}</span>` : ''}
    <span class="fi-wait-acts">${(a.options || []).map((o, i) => {
      const opt = optionOf(o, i);
      return `<button class="tb ghost sm${opt.recommended ? ' rec' : ''}" data-attention="${esc(a.id)}"
        data-option="${esc(JSON.stringify(opt))}">${esc(opt.label)}${opt.recommended ? '<i>recommandé</i>' : ''}</button>`;
    }).join('')}<a class="lbl" href="./coulisses.html#/${enc(d.character.slug)}">coulisses ▸</a></span>
  </article>`).join('')}</section>`;
}

/* ── le visage en grand, ses looks ── */

function lookJob(l) {
  return state.jobs.find((j) => live(j) && j.action === 'look' && (j.params?.id === l.id || j.params?.prompt === l.prompt));
}

function ficheFace(d) {
  const c = d.character;
  const f = c.face;
  if (!f.locked) return faceAudition(d);
  const looks = looksOf(c);
  const cur = looks.find((l) => l.id === state.look && l.status === 'ready' && l.file);
  const src = cur ? fileUrl(c.slug, cur.file, cur.at) : fileUrl(c.slug, f.locked, f.locked_at);
  const ready = looks.filter((l) => l.status === 'ready' && l.file).length;
  const chips = looks.map((l) => {
    const nm = l.name || l.prompt || 'un look';
    const x = `<button class="fi-x" data-act="look_remove" data-params="${esc(JSON.stringify({ id: l.id }))}"
      data-confirm="${esc(`Retirer le look « ${nm} » ? Le visage de base ne bouge pas.`)}" title="le retirer" aria-label="retirer ${esc(nm)}">✕</button>`;
    if (l.status === 'pending') return `<span class="fi-chip run"><i class="dot"></i>${esc(nm)} · ${esc(rank(lookJob(l)))}</span>`;
    if (l.status === 'error' || !l.file) return `<span class="fi-chipx"><span class="fi-chip err">${esc(nm)} · raté</span>${x}</span>`;
    return `<span class="fi-chipx"><button class="fi-chip" data-look="${esc(l.id)}" aria-pressed="${cur === l}">${
      cur === l ? '<i class="dot"></i>' : ''}${esc(nm)}</button>${x}</span>`;
  });
  // Un look demandé que le manifeste ne porte pas encore : son travail suffit.
  state.jobs.filter((j) => live(j) && j.action === 'look' && !looks.some((l) => lookJob(l) === j)).forEach((j) => {
    chips.push(`<span class="fi-chip run"><i class="dot"></i>${esc(j.params?.name || j.params?.prompt || 'un look')} · ${esc(rank(j))}</span>`);
  });
  const ask = state.missing.look
    ? '<p class="fi-soon"><b>Bientôt</b> · un deuxième visage — maquillage, cicatrice, barbe — sur le même visage : l\'atelier ne sait pas encore le retoucher.</p>'
    : `<form class="fi-ask" data-form="look" autocomplete="off">
        <input name="prompt" data-draft="look.prompt" value="${esc(draft('look.prompt'))}" spellcheck="false"
          placeholder="ce qui change : « une cicatrice sur la pommette gauche »" aria-label="un look : ce qui change sur son visage">
        <button class="tb ghost sm" type="submit">Essayer</button></form>`;
  return `<figure class="fi-frame fi-face">
      <img src="${esc(src)}" alt="${esc(c.name)}, ${cur ? 'look' : 'visage'}" data-zoom="${esc(src)}" data-cap="${esc(cur ? `${c.name} · ${cur.name || cur.prompt}` : c.name)}">
      <figcaption class="fi-cap"><span class="lbl"><b>${esc(cur ? `look · ${cur.name || cur.prompt}` : 'visage')}</b>${
        cur ? '' : ` · choisi le ${esc(day(f.locked_at))}`}</span><span class="lbl">${ready ? `${ready} look${ready > 1 ? 's' : ''}` : ''}</span></figcaption>
    </figure>
    <div class="fi-under">
      <div class="fi-chips" aria-label="ses looks"><span class="lbl">Looks</span>
        <button class="fi-chip" data-look="" aria-pressed="${!cur}">${cur ? '' : '<i class="dot"></i>'}naturel</button>${chips.join('')}</div>
      ${ask}
    </div>`;
}

/* L'audition dans le cadre : un visage pas encore choisi montre ses
   candidats ; un clic le prend en grand, l'orange de l'en-tête le garde. */
function audition({ kind, cands, current, older, job, expected, pickN, src, cls, label }) {
  const picked = cands.find((x) => String(x.n) === String(pickN));
  const all = [...current, ...older];
  if (picked) {
    return `<figure class="fi-frame ${cls}">
        <img src="${esc(src(picked))}" alt="${esc(`${label} n° ${picked.n}`)}" data-zoom="${esc(src(picked))}" data-cap="${esc(`n° ${picked.n}`)}">
        <figcaption class="fi-cap"><span class="lbl"><b>n° ${picked.n} · choisi</b></span><span class="lbl">pas encore gardé</span></figcaption>
      </figure>
      <div class="fi-strip" aria-label="les autres">${all.map((x) => `<button class="fi-strip-it${x === picked ? ' on' : ''}"
        data-pick="${esc(kind)}" data-val="${x.n}" title="${x === picked ? 'revoir tous les candidats' : `n° ${x.n}`}">
        <img src="${esc(src(x))}" alt="n° ${x.n}" loading="lazy"></button>`).join('')}</div>`;
  }
  const n = current.length + Math.max(0, expected - current.length);
  const cells = current.map((x) => `<button class="fi-cand" data-pick="${esc(kind)}" data-val="${x.n}" title="choisir le n° ${x.n}">
      <img src="${esc(src(x))}" alt="${esc(`${label} n° ${x.n}`)}" loading="lazy"><span class="fi-n">n° ${x.n}</span></button>`)
    .concat(Array.from({ length: n - current.length }, (_, i) => {
      const first = job?.status === 'running' && i === 0;
      return `<div class="fi-cand slot${first ? ' on' : ''}"><span class="lbl">${first ? rank(job) : 'ensuite'}</span></div>`;
    }));
  return `<div class="fi-frame ${cls} fi-audition n${Math.min(n, 4)}">${cells.join('')}</div>
    ${older.length ? `<div class="fi-strip" aria-label="les précédents">${older.map((x) => `<button class="fi-strip-it"
      data-pick="${esc(kind)}" data-val="${x.n}" title="n° ${x.n}"><img src="${esc(src(x))}" alt="n° ${x.n}" loading="lazy"></button>`).join('')}</div>` : ''}`;
}

function faceAudition(d) {
  const c = d.character;
  const cands = c.face.candidates.map((x, i) => ({ ...x, n: i + 1 }));
  const l = lot(cands, 'face');
  const pickN = state.pick.face;
  const body = cands.length || l.job
    ? audition({ kind: 'face', cands, ...l, pickN, src: (x) => fileUrl(c.slug, x.file, x.at), cls: 'fi-face', label: 'visage' })
    : `<div class="fi-frame fi-face fi-open"><div><div class="fi-plus">+</div><p>Ses visages viennent de sa première phrase.</p></div></div>`;
  return `${body}
    <div class="fi-under"><div class="fi-chips"><span class="lbl">${
      cands.length ? `Audition · ${cands.length} visage${cands.length > 1 ? 's' : ''} · un clic : choisir` : 'Audition'}</span>
      <a class="tb ghost sm" href="${tabHref('visage')}" title="plus âgé, plus jeune, autre coiffure… et le redécrire">Plein cadre ⤢</a></div>
      ${failure(l.job ? null : lastJob('face'))}</div>`;
}

/* ── le plein pied de la tenue active, ses poses ── */

function ficheBody(d) {
  const c = d.character;
  const keys = Object.keys(c.costumes);
  const key = activeCostume(c);
  if (!key) {
    if (!c.face.locked) {
      return `<div class="fi-frame fi-body fi-open"><div><div class="fi-plus">+</div>
        <p>Sa tenue, en pied et dans une pose naturelle, viendra une fois son visage choisi.</p></div></div>`;
    }
    return `<div class="fi-frame fi-body fi-open fi-newcos"><form data-form="costume_new">
      <div class="fi-plus">+</div>
      <p>Ce qu'${she(c) ? 'elle' : 'il'} porte, de la tête aux pieds. Trois pleins pieds suivront, dans une pose naturelle.</p>
      ${textarea('costume_new.brief', '', 'en français : « caban bleu marine usé, pull de laine écrue, bottes de pont »', 4)}
      ${refsZone('costume_new', 'une photo de vêtement')}
      ${btn('L\'habiller ▸', { act: 'costume_go', form: 'costume_new' })}
    </form></div>`;
  }
  const cos = c.costumes[key];
  const fb = cos.fullbody;
  const i = keys.indexOf(key);
  const pager = keys.length > 1 ? `<div class="fi-pager">
      <button class="tb" data-costume-step="-1" aria-label="tenue précédente" title="tenue précédente">◂</button>
      <button class="tb" data-costume-step="1" aria-label="tenue suivante" title="tenue suivante">▸</button></div>` : '';
  const bar = `<div class="fi-bodybar"><span class="lbl">${esc(cos.name)}${keys.length > 1 ? ` · ${i + 1}/${keys.length}` : ''}</span>${pager}</div>`;
  if (!fb.validated) {
    const cands = fb.candidates.map((x, n) => ({ ...x, n: n + 1 }));
    const l = lot(cands, 'fullbody', key);
    if (cands.length || l.job) {
      return `<div class="fi-bodywrap">${audition({ kind: `fb:${key}`, cands, ...l, pickN: state.pick[`fb:${key}`],
        src: (x) => fileUrl(c.slug, x.file, x.at), cls: 'fi-body', label: cos.name })}${bar}</div>
        <div class="fi-under"><div class="fi-chips"><span class="lbl">${cands.length ? 'un clic : choisir'
          : `${she(c) ? 'elle' : 'il'} s'habille`}</span>
          <a class="tb ghost sm" href="${tabHref('garde-robe')}" title="sa description, ses images, trois autres">Plein cadre ⤢</a></div>
          ${failure(l.job ? null : lastJob('fullbody', key))}</div>`;
    }
    return `<div class="fi-bodywrap"><div class="fi-frame fi-body fi-open"><div><div class="fi-plus">+</div>
        <p>${cos.brief ? `« ${esc(cos.brief)} »` : 'Cette tenue attend son plein pied.'}</p>
        <p class="lbl">même visage · pose naturelle</p>
        <a class="tb ghost sm" href="${tabHref('garde-robe')}">La décrire ⤢</a></div></div>${bar}</div>
      <div class="fi-under">${failure(lastJob('fullbody', key))}</div>`;
  }
  const poses = panelList(cos.presentation, 'poses');
  const view = state.bodyView[key] || '';
  const pose = poses.find((p) => p.id === view);
  const src = pose ? fileUrl(c.slug, pose.file, pose.at) : fileUrl(c.slug, fb.validated, fb.validated_at);
  const chips = poses.length ? `<div class="fi-chips" aria-label="ses poses">
      <button class="fi-chip on-veil" data-body-view="" aria-pressed="${!pose}">plein pied</button>${poses.map((p) =>
      `<button class="fi-chip on-veil" data-body-view="${esc(p.id)}" aria-pressed="${pose === p}">${esc(p.label || p.id)}</button>`).join('')}</div>`
    : `<span class="lbl">${fb.validated_by === 'import' ? 'tiré de son image' : `choisi le ${esc(day(fb.validated_at))}`} · ses poses viennent avec la planche</span>`;
  return `<div class="fi-bodywrap"><figure class="fi-frame fi-body">
      <img src="${esc(src)}" alt="${esc(`${c.name} en pied, ${cos.name}${pose ? `, ${pose.label || pose.id}` : ''}`)}"
        data-zoom="${esc(src)}" data-cap="${esc(`${cos.name}${pose ? ` · ${pose.label || pose.id}` : ''}`)}">
      <figcaption class="fi-cap wrap">${chips}</figcaption>
    </figure>${bar}</div>`;
}

/* ── qui il est ── */

function ficheWho(d) {
  const c = d.character;
  const elle = she(c);
  const prose = plaqueText(c);
  const have = traitList(c);
  const s = c.identity || {};
  const thin = prose && !['core_theme', 'behavior_notes', 'emotional_range', 'speech_style'].some((k) => String(s[k] || '').trim());
  const chips = state.traitsOpen
    ? [...new Set([...have, ...TRAITS])].map((t) => `<button class="fi-chip" data-trait="${esc(t)}" aria-pressed="${have.includes(t)}">${esc(t)}</button>`)
    : have.map((t) => `<span class="fi-chip on">${esc(t)}</span>`);
  return `<section class="fi-block fi-plaque">
    <header><span class="lbl">${elle ? 'Qui elle est' : 'Qui il est'}</span><span class="lbl">écrit avec l'IA · <b>tu corriges</b></span></header>
    ${prose ? `<p>${esc(prose)}</p>` : `<p class="fi-empty">Son caractère reste à écrire. Dis à l'IA qui ${elle ? 'elle' : 'il'} est :
      ses traits, ce qui ${elle ? 'la' : 'le'} travaille, ses émotions, sa façon de parler.</p>`}
    ${thin ? `<p class="fi-note">Le reste — ce qui ${elle ? 'la' : 'le'} travaille, ses émotions, sa façon de parler — s'écrit avec l'IA.</p>` : ''}
    <div class="fi-chips">${chips.join('')}
      <button class="fi-chip open" data-traits-open>${state.traitsOpen ? 'fini' : '+ un trait'}</button></div>
    <div class="fi-row-acts">
      <a class="tb ghost" href="./console.html?slug=${enc(c.slug)}" title="la console Identité : l'IA le définit avec toi">Affiner avec l'IA</a>
      ${c.face.locked ? `<a class="tb ghost" href="${tabHref('scene')}">Lui parler</a>` : ''}
    </div>
    <details class="fi-more"${state.moreOpen ? ' open' : ''}><summary>toute sa fiche · un clic pour corriger</summary>
      <div class="sheet-section fiche">${ficheRows(d, 'CORE')}${ficheRows(d, 'PSYCHE')}</div>
      <div class="adv-body"><span class="lbl">rendu</span><span class="seg">
        <button class="tb sm${c.style === 'photoreal' ? ' on' : ''}" data-style-set="photoreal">Photo</button>
        <button class="tb sm${c.style === 'stylized' ? ' on' : ''}" data-style-set="stylized">Stylisé</button></span></div>
    </details>
  </section>`;
}

/* ── ses expressions ── */

function ficheExpr(d) {
  const c = d.character;
  const key = activeCostume(c);
  const cos = key ? c.costumes[key] : null;
  const pres = cos?.presentation;
  const ready = !!(c.face.locked && cos?.fullbody.validated);
  const list = exprList(pres).filter((e) => e.file);
  const removed = pres?.removed_expressions || [];
  const presJob = casesJob(key);
  // Une expression en calcul n'a pas d'entrée tant qu'elle n'est pas faite : son travail suffit.
  const jobs = state.jobs.filter((j) => live(j) && j.action === 'expression_add' && (j.params?.costume || null) === key);
  const tiles = [];
  for (const e of list) {
    const nm = e.label || e.name || e.id;
    const src = fileUrl(c.slug, e.file, e.at);
    const peek = state.motionPeek === e.id;
    const clip = e.clip ? fileUrl(c.slug, e.clip, e.at) : '';
    tiles.push(`<figure class="fi-ex${peek ? ' peek' : ''}"${e.custom && e.prompt ? ` title="${esc(`« ${e.prompt} »`)}"` : ''}><div class="fi-frame">
      ${peek && clip ? `<video src="${esc(clip)}" autoplay loop muted playsinline></video>`
        : `<img src="${esc(src)}" alt="${esc(nm)}" loading="lazy" data-zoom="${esc(src)}" data-cap="${esc(`${c.name} · ${nm}`)}">`}
      ${peek && !clip ? '<span class="fi-peek">en mouvement : à venir</span>' : ''}
      <div class="fi-tools">
        <button class="tb sm" data-motion="${esc(e.id)}" title="${clip ? 'la voir bouger' : 'en mouvement : à venir'}"
          aria-label="la voir bouger">▶</button>
        <button class="tb sm" data-act="expression_remove" data-params="${esc(JSON.stringify({ costume: key, id: e.id }))}"
          title="l'écarter" aria-label="écarter ${esc(nm)}">✕</button>
      </div></div><figcaption>${e.custom ? '<i class="fi-own" title="une expression à toi"></i>' : ''}${esc(nm)}</figcaption></figure>`);
  }
  jobs.slice().reverse().forEach((j) => tiles.push(exprRun(j.params?.prompt || 'une expression', j)));
  if (presJob && !list.length) {
    for (let i = 0; i < 6; i++) tiles.push(`<figure class="fi-ex run"><div class="fi-frame">${i ? '' : '<div class="fi-bar"></div>'}
      <div><div class="lbl fi-cy">${i ? 'ensuite' : esc(rank(presJob))}</div></div></div><figcaption>planche</figcaption></figure>`);
  }
  if (!ready) {
    tiles.push(`<figure class="fi-ex open wide"><div class="fi-frame"><p>Ses expressions viennent de son visage, une fois ${
      c.face.locked ? 'sa tenue choisie' : 'son visage choisi'}.</p></div></figure>`);
  } else {
    if (!list.length && !presJob) {
      tiles.push(`<figure class="fi-ex open wide"><div class="fi-frame"><p>Six expressions de base viennent avec sa planche :
        neutre, joie, colère, tristesse, surprise, malice.</p></div></figure>`);
    }
    tiles.push(state.missing.expression_add
      ? '<figure class="fi-ex open"><div class="fi-frame"><p><b>Bientôt</b> · une expression à toi, en une phrase.</p></div><figcaption>une expression</figcaption></figure>'
      : `<figure class="fi-ex open"><form class="fi-frame" data-form="expr" data-costume="${esc(key)}" autocomplete="off">
          <div class="fi-plus">+</div><input name="prompt" data-draft="expr.prompt" value="${esc(draft('expr.prompt'))}" spellcheck="false"
            placeholder="en mots · Entrée" aria-label="une nouvelle expression, en mots, puis Entrée"></form>
          <figcaption>une expression</figcaption></figure>`);
  }
  const last = removed[removed.length - 1];
  const tray = last ? `<div class="fi-tray"><span class="lbl">« ${esc(last.label || last.name || last.id)} » écartée${
      removed.length > 1 ? ` · ${removed.length} à la corbeille` : ''}</span>
      <button class="tb ghost sm" data-act="expression_restore" data-params="${esc(JSON.stringify({ costume: key, id: last.id }))}">Annuler</button>
      ${removed.slice(0, -1).reverse().map((r) => `<button class="fi-chip" data-act="expression_restore"
        data-params="${esc(JSON.stringify({ costume: key, id: r.id }))}" title="la remettre">${esc(r.label || r.name || r.id)} ↺</button>`).join('')}
    </div>` : '';
  // La planche composée ne suit pas d'elle-même une expression ajoutée ou écartée.
  const stale = sheetStale(pres) && !liveJob('presentation', key) ? `<div class="fi-tray"><span class="lbl">sa planche date d'avant ces changements</span>
      ${recompose(key)}</div>` : '';
  const n = list.length;
  return `<section class="fi-block">
    <header><span class="lbl">Expressions · <b>${n}</b></span><span class="lbl">${n ? 'survole : ▶ la voir bouger · ✕ l\'écarter' : ''}</span></header>
    <div class="fi-expr">${tiles.join('')}</div>${tray}${stale}
  </section>`;
}

// Un travail de planche qui refait des cases — pas la seule recomposition.
function casesJob(key) {
  const j = liveJob('presentation', key);
  return j && String(j.params?.redo || '') !== 'sheet' ? j : null;
}

function sheetStale(pres) {
  if (!pres?.sheet) return false;
  const at = String(pres.at || '');
  return exprList(pres).some((e) => e.custom && String(e.at || '') > at)
    || (pres.removed_expressions || []).some((r) => String(r.removed_at || '') > at);
}

const recompose = (key) => btn('Recomposer la planche', {
  act: 'presentation', costume: key, params: { redo: ['sheet'] }, sm: true,
  confirm: 'Recomposer sa planche avec ses expressions d\'aujourd\'hui ? Aucune case n\'est refaite : seule la planche se recompose.',
});

function exprRun(text, job) {
  return `<figure class="fi-ex run"><div class="fi-frame"><div class="fi-bar"></div>
    <div><div class="lbl fi-cy">en calcul</div><q>${esc(text)}</q></div></div><figcaption>${esc(rank(job))}</figcaption></figure>`;
}

/* ── la bande du dessous : la 3D, la voix, la taille ── */

function fiche3d(d) {
  const c = d.character;
  const key = activeCostume(c);
  const cos = key ? c.costumes[key] : null;
  const m = model3d(cos);
  const job = cos && techJob(key);
  const running = !!(job || cos?.autopilot?.state === 'running');
  const step = STEP_FR[techStep(cos, job)] || '';
  const status = running ? `en calcul${step ? ` · ${step}` : ''} · ${rank(job)}` : m ? m.what : '';
  const head = `<div class="fi-tile-head"><span class="lbl">Modèle 3D</span><span class="lbl${running ? ' fi-cy' : ''}">${esc(status)}</span></div>`;
  let view;
  if (m) {
    const src = fileUrl(c.slug, m.glb);
    view = `<div class="fi-frame fi-3d"><iframe src="./viewer.html?embed=1&amp;slug=${enc(c.slug)}&amp;src=${enc(src)}"
      title="${esc(`le modèle 3D de ${c.name}`)}" loading="lazy"></iframe></div>`;
  } else if (running) {
    const still = cos.apose?.validated || cos.fullbody.validated;
    view = `<div class="fi-frame fi-3d fi-calc">${still ? `<img src="${esc(fileUrl(c.slug, still))}" alt="">` : ''}
      <div class="fi-bar"></div><span class="fi-state">en calcul${step ? ` · ${esc(step)}` : ''}</span></div>`;
  } else {
    const still = cos?.apose?.validated || cos?.fullbody.validated;
    view = `<div class="fi-frame fi-3d fi-open${still ? ' fi-calc' : ''}">${still ? `<img src="${esc(fileUrl(c.slug, still))}" alt="">` : ''}
      <div><p>${cos?.fullbody.validated
      ? 'L\'atelier est au repos pour cette tenue : A-pose, vues, mesh et rig reprennent depuis les coulisses.'
      : 'Il se fait seul après le plein pied : A-pose, vues, mesh, rig.'}</p></div></div>`;
  }
  const foot = `<div class="fi-tile-foot"><p class="fi-note">${m ? 'Tourne-le à la souris. Se refait seul quand le plein pied change.'
    : 'Se fait seul après le plein pied, validé par la mesure ; deux échecs t\'appellent.'}</p>
    ${m ? `<a class="tb ghost sm" href="./viewer.html?slug=${enc(c.slug)}&amp;src=${enc(fileUrl(c.slug, m.glb))}">Ouvrir le viewer ⤢</a>`
      : `<a class="tb ghost sm" href="./coulisses.html#/${enc(c.slug)}">Coulisses ▸</a>`}</div>`;
  return { head, view, foot };
}

function ficheVoice(d) {
  const c = d.character;
  const v = voiceOf(c);
  const s = c.identity || {};
  if (v?.locked) {
    const url = fileUrl(c.slug, lockedVoiceFile(v), v.locked_at);
    return `<div class="fi-tile"><div class="fi-tile-head"><span class="lbl">Sa voix</span><span class="lbl ok">choisie</span></div>
      ${player(url, { text: v.locked_text, cls: 'lg' })}${v.description ? `<p class="fi-note">${esc(v.description)}</p>` : ''}
      <div class="fi-row-acts"><a class="tb ghost sm" href="${tabHref('voix')}">Faire dire · changer ⤢</a></div></div>`;
  }
  const job = liveJob('voice_design');
  const n = v?.candidates?.length || 0;
  const what = job ? `en calcul · ${rank(job)}` : n ? 'à choisir' : voicePossible(c) ? 'à trouver' : 'bientôt';
  const body = n ? `<p>${n} voix à écouter, sur la même réplique. Tu gardes la sienne ; elle sert aussi quand tu lui parles.</p>`
    : voicePossible(c) ? '<p>Décris-la en quelques mots : quatre voix liront la même réplique, tu gardes la sienne.</p>'
      : `<p>Le studio n'a pas encore de service de voix. Dès qu'il est branché : quatre voix sur la même réplique.</p>${
        s.speech_style ? `<p class="fi-note">Ce qu'on sait déjà : « ${esc(s.speech_style)} »</p>` : ''}`;
  return `<div class="fi-tile wait"><div class="fi-tile-head"><span class="lbl">Sa voix</span><span class="lbl">${esc(what)}</span></div>
    <div class="fi-frame fi-open amb"><div>${body}</div></div>
    ${voicePossible(c) ? `<div class="fi-row-acts"><a class="tb ghost sm" href="${tabHref('voix')}">${n ? 'Écouter ⤢' : 'La décrire ⤢'}</a></div>` : ''}</div>`;
}

function ficheSize(d) {
  const c = d.character;
  const s = c.identity || {};
  const key = activeCostume(c);
  const cos = key ? c.costumes[key] : null;
  const h = heightText(s.height);
  const pal = (cos?.presentation?.palette || []).filter((x) => HEX.test(String(x)));
  // Les teintes de la palette sont des données du personnage, pas du thème.
  const swatches = (list, what) => `<div class="fi-swatches" aria-label="${esc(what)}">${
    list.map((x) => `<i style="background:${x}" title="${x}"></i>`).join('')}</div>`;
  const written = String(s.color_palette || '').match(/#(?:[0-9a-f]{6}|[0-9a-f]{3})\b/gi) || [];
  const palette = pal.length ? `${swatches(pal, `palette de ${cos.name}`)}<span class="lbl">palette lue sur ${esc(cos.name)}</span>`
    : written.length ? `${swatches(written, 'palette écrite')}<span class="lbl">palette écrite · lue sur la planche ensuite</span>`
      : s.color_palette ? `<p class="fi-note">« ${esc(s.color_palette)} »</p><span class="lbl">palette écrite · lue sur la planche ensuite</span>`
        : '<span class="lbl">la palette se lit sur la planche</span>';
  return `<div class="fi-tile"><div class="fi-tile-head"><span class="lbl">Taille et palette</span></div>
    <button class="fi-big" data-edit-open="height" title="corriger sa taille">${h ? esc(h) : '—'}</button>
    <p class="fi-note">${h ? 'Le mesh est mis à cette échelle. La changer ne refait que le mesh et le rig.'
      : 'Sa taille n\'est pas écrite : un clic pour la donner.'}</p>
    ${palette}</div>`;
}

/* ── ses tenues, ses détails, où il en est ── */

function ficheTenues(d) {
  const c = d.character;
  const keys = Object.keys(c.costumes);
  if (!keys.length) return '';      // la première se décrit dans le cadre du plein pied
  const act = activeCostume(c);
  const cards = keys.map((k) => {
    const cos = c.costumes[k];
    const fb = cos.fullbody;
    const img = fb.validated ? fileUrl(c.slug, fb.validated, fb.validated_at)
      : fb.candidates.length ? fileUrl(c.slug, fb.candidates.at(-1).file, fb.candidates.at(-1).at) : '';
    const job = liveJob('fullbody', k);
    const tech = techJob(k);
    let st = ['à habiller', ''];
    if (fb.validated) {
      st = (cos.rigs || []).some((r) => r.verdict === 'accepted') ? ['3D faite', 'ok']
        : tech || cos.autopilot?.state === 'running' ? ['3D en calcul', 'run'] : ['habillé', 'ok'];
    } else if (job) st = [rank(job), 'run'];
    else if (fb.candidates.length) st = ['à choisir', 'amb'];
    return `<button class="fi-card${k === act ? ' on' : ''}" data-costume-pick="${esc(k)}" aria-pressed="${k === act}">
      <div class="fi-frame">${img ? `<img src="${esc(img)}" alt="" loading="lazy"${fb.validated ? '' : ' class="dim"'}>` : ''}
        <span class="fi-state ${st[1]}">${esc(st[0])}</span></div>
      <span class="lbl"><b>${esc(cos.name)}</b></span>${cos.brief ? `<p>« ${esc(cos.brief)} »</p>` : ''}</button>`;
  });
  const add = c.face.locked ? `<form class="fi-card wide open" data-form="costume_new">
      <div class="fi-frame"><div>
        <div class="fi-plus">+</div>
        ${textarea('costume_new.brief', '', 'une autre tenue, en mots : « robe noire droite, veste de cuir courte, bottines »', 3)}
        ${refsZone('costume_new', 'une photo de vêtement')}
        <p class="lbl">même visage · même corps · pose naturelle</p>
        ${btn('L\'habiller ▸', { act: 'costume_go', form: 'costume_new', sm: true })}
      </div></div><span class="lbl">une tenue de plus</span></form>` : '';
  return `<section class="fi-band">
    <header><h2>Ses tenues</h2><span class="lbl">chaque tenue a son plein pied, ses poses et sa 3D · un clic : la montrer</span>
      <span class="fi-hacts"><a class="tb ghost sm" href="${tabHref('garde-robe')}">Garde-robe ⤢</a></span></header>
    <div class="fi-row">${cards.join('')}${add}</div>
  </section>`;
}

function ficheDetails(d) {
  const c = d.character;
  const key = activeCostume(c);
  const cos = key ? c.costumes[key] : null;
  const pres = cos?.presentation;
  const det = panelList(pres, 'details');
  if (!det.length && !pres?.sheet) return '';
  const src = (f) => fileUrl(c.slug, f, pres.at);
  return `<section class="fi-band">
    <header><h2>Détails</h2><span class="lbl">tirés du plein pied validé · ${esc(cos.name)}</span>
      <span class="fi-hacts">${sheetStale(pres) && !liveJob('presentation', key) ? recompose(key) : ''}<a class="tb ghost sm" href="${tabHref('planche')}">Sa planche ⤢</a></span></header>
    <div class="fi-row">${det.map((x) => `<figure class="fi-card sq"><div class="fi-frame"><img src="${esc(src(x.file))}"
        alt="${esc(x.label || x.id)}" loading="lazy" data-zoom="${esc(src(x.file))}" data-cap="${esc(x.label || x.id)}"></div>
        <span class="lbl">${esc(x.label || x.id)}</span></figure>`).join('')}
      ${pres.sheet ? `<figure class="fi-card sheet"><div class="fi-frame"><img src="${esc(src(pres.sheet))}" alt="sa planche" loading="lazy"
        data-zoom="${esc(src(pres.sheet))}" data-cap="${esc(`${c.name} · ${cos.name} · planche`)}"></div>
        <span class="lbl">sa planche · <a href="${esc(src(pres.sheet))}" download="${esc(`${c.slug}-planche.png`)}">exporter</a></span></figure>` : ''}
    </div>
  </section>`;
}

// Où il en est : l'état de chaque étage, lu sur le manifeste et la file.
function ficheStatus(d) {
  const c = d.character;
  const f = c.face;
  const key = activeCostume(c);
  const cos = key ? c.costumes[key] : null;
  const fb = cos?.fullbody;
  const pres = cos?.presentation;
  const v = voiceOf(c);
  const tech = cos && techJob(key);
  const step = cos ? (tech || cos.autopilot?.state === 'running' ? techStep(cos, tech) : '') : '';
  const run = (id) => (step === id ? [`en calcul · ${rank(tech)}`, 'run'] : null);
  const presJob = liveJob('presentation', key);
  const cases = casesJob(key);
  const looks = looksOf(c).filter((l) => l.status === 'ready').length;
  const nEx = exprList(pres).filter((e) => e.file).length;
  const nPo = panelList(pres, 'poses').length;
  const views = cos?.views || {};
  const rigs = cos?.rigs || [];
  const cells = [
    [`Visage${looks ? ` · ${looks} look${looks > 1 ? 's' : ''}` : ''}`, f.locked ? ['à jour', 'ok'] : liveJob('face') ? [rank(liveJob('face')), 'run']
      : f.candidates.length ? ['attend ton choix', 'wait'] : ['à faire', 'todo']],
    [`Plein pied${cos ? ` · ${cos.name}` : ''}`, !cos ? [f.locked ? 'à décrire' : 'après le visage', 'todo']
      : fb.validated ? [fb.validated_by === 'import' ? 'à jour · importé' : 'à jour', 'ok'] : liveJob('fullbody', key) ? [rank(liveJob('fullbody', key)), 'run']
        : fb.candidates.length ? ['attend ton choix', 'wait'] : ['à faire', 'todo']],
    [`Expressions${nEx ? ` · ${nEx}` : ''}`, cases || liveJob('expression_add', key) ? [rank(cases || liveJob('expression_add', key)), 'run']
      : nEx ? ['à jour', 'ok'] : ['avec la planche', 'todo']],
    [`Poses${nPo ? ` · ${nPo}` : ''}`, cases ? [rank(cases), 'run'] : nPo ? ['à jour', 'ok'] : ['avec la planche', 'todo']],
    ['Voix', v?.locked ? ['à jour', 'ok'] : liveJob('voice_design') ? [rank(liveJob('voice_design')), 'run']
      : v?.candidates?.length ? ['attend ton choix', 'wait'] : voicePossible(c) ? ['à trouver', 'wait'] : ['bientôt', 'todo']],
    ['A-pose', run('apose') || (cos?.apose?.validated ? [cos.apose.validated_by === 'auto' ? 'mesurée · auto' : 'choisie', 'ok']
      : cos && waitsFor(d, key, /^apose/) ? ['attend ton œil', 'wait'] : [fb?.validated ? 'à faire' : 'après le plein pied', 'todo'])],
    ['Vues · contrôle ±5°', run('views') || (views.check?.ok ? ['passé · auto', 'ok']
      : views.check || (cos && waitsFor(d, key, /^(views|check)/)) ? ['attend ton œil', 'wait']
        : [(views.raw || []).length ? 'à contrôler' : 'après l\'A-pose', 'todo'])],
    ['Mesh', run('mesh') || ((cos?.meshes || []).length ? [`à jour · v${cos.meshes.at(-1).version}`, 'ok']
      : cos && waitsFor(d, key, /^mesh/) ? ['attend ton œil', 'wait'] : ['après les vues', 'todo'])],
    ['Rig', run('rig') || (rigs.some((r) => r.verdict === 'accepted') ? [`accepté · v${[...rigs].reverse().find((r) => r.verdict === 'accepted').version}`, 'ok']
      : rigs.length ? ['à regarder', 'wait'] : ['après le mesh', 'todo'])],
    ['Planche', presJob ? [rank(presJob), 'run'] : sheetStale(pres) ? ['à recomposer', 'wait'] : pres?.sheet ? ['à jour', 'ok']
      : fb?.validated ? ['à composer', 'wait'] : ['après le plein pied', 'todo']],
  ];
  return `<section class="fi-band">
    <header><h2>Où ${she(c) ? 'elle' : 'il'} en est</h2><span class="lbl">à jour · en calcul · attend ton œil · à faire</span>
      <span class="fi-hacts"><a class="tb ghost sm" href="./coulisses.html#/${enc(c.slug)}">Tout dans les coulisses ▸</a></span></header>
    <div class="fi-status">${cells.map(([k, [t, cls]]) => `<div><span class="lbl">${esc(k)}</span><span class="v ${cls}">${esc(t)}</span></div>`).join('')}</div>
  </section>`;
}

/* Les régions de la fiche, dans un squelette fixe : les grilles tiennent
   la mise en page, chaque région n'est qu'un contenu (display: contents). */
const FICHE_SKELETON = `<div class="fi">
  <div class="fi-r" data-region="head"></div>
  <div class="fi-r" data-region="waits"></div>
  <div class="fi-main">
    <div class="fi-col fi-r-face" data-region="face"></div>
    <div class="fi-col fi-r-body" data-region="body"></div>
    <div class="fi-side"><div class="fi-r" data-region="who"></div><div class="fi-r" data-region="expr"></div></div>
  </div>
  <section class="fi-band fi-tiles">
    <div class="fi-tile fi-t3d"><div class="fi-r" data-region="d3head"></div><div class="fi-r" data-region="d3view"></div>
      <div class="fi-r" data-region="d3foot"></div></div>
    <div class="fi-stack"><div class="fi-r" data-region="voice"></div><div class="fi-r" data-region="size"></div></div>
  </section>
  <div class="fi-r" data-region="tenues"></div>
  <div class="fi-r" data-region="details"></div>
  <div class="fi-r" data-region="status"></div>
</div>`;

function ficheRegions(d) {
  const d3 = fiche3d(d);
  return [
    ['head', ficheHead(d)], ['waits', ficheWaits(d)], ['face', ficheFace(d)], ['body', ficheBody(d)],
    ['who', ficheWho(d)], ['expr', ficheExpr(d)], ['d3head', d3.head], ['d3view', d3.view], ['d3foot', d3.foot],
    ['voice', ficheVoice(d)], ['size', ficheSize(d)], ['tenues', ficheTenues(d)], ['details', ficheDetails(d)],
    ['status', ficheStatus(d)],
  ];
}

function paintFiche(app, d) {
  const regions = ficheRegions(d);
  const page = `fiche:${d.character.slug}`;
  if (app.dataset.page !== page || !app.querySelector('.fi')) {
    app.dataset.page = page;
    app.innerHTML = FICHE_SKELETON;
    state.regions = {};
  }
  for (const [k, html] of regions) {
    if (state.regions[k] === html) continue;
    const node = app.querySelector(`[data-region="${k}"]`);
    if (node) node.innerHTML = html;
    state.regions[k] = html;
  }
}

/* ── ce qui attend : les cartes de l'atelier ────────────── */

function attentionOf(d) {
  if (Array.isArray(d.attention)) return d.attention;
  if (Array.isArray(d.character.attention)) return d.character.attention.filter((a) => !a.resolved);
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
      <h2 class="big">C'est ${she(c) ? 'elle' : 'lui'}.</h2>
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
  const app = $('#app');
  const d = state.detail;
  const fiche = state.route.view === 'perso' && !!d && currentTab(d) === 'fiche';
  const logBefore = $('#scene-log');
  const keep = logBefore ? logBefore.scrollHeight - logBefore.scrollTop : null;
  if (fiche) paintFiche(app, d);
  else {
    app.dataset.page = '';
    app.innerHTML = state.route.view === 'perso' ? renderPerso() : renderHome();
  }
  const logAfter = $('#scene-log');
  if (logAfter) logAfter.scrollTop = keep == null ? logAfter.scrollHeight : logAfter.scrollHeight - keep;
  $('#coulisses-link').href = state.route.view === 'perso' ? `./coulisses.html#/${enc(slug())}` : './coulisses.html';
  document.body.classList.toggle('in-scene', state.route.view === 'perso' && state.route.tab === 'scene');
  document.body.classList.toggle('in-fiche', fiche);
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
  const wasFiche = state.route.view === 'perso' && !state.route.tab;
  let backTo = null;
  state.route = route;
  state.renaming = false;
  state.editField = null;
  if (changed) {
    state.detail = null;
    state.jobs = [];
    state.queue = [];
    state.costume = undefined;
    state.pick = {};
    state.fbAgain = false;
    state.look = null;
    state.bodyView = {};
    state.motionPeek = null;
    state.traitsOpen = false;
    state.sig = '';
    state.seen.clear();
    state.scene.mic?.close();
    state.scene.mic = null;
    window.scrollTo(0, 0);
  } else if (tabChanged) {
    // De la fiche à une étape en plein cadre : on part du haut ; au retour,
    // la fiche reprend où on l'avait laissée.
    if (wasFiche) state.ficheY = window.scrollY;
    backTo = route.tab ? 0 : state.ficheY || 0;
  }
  render(true);
  if (backTo != null) window.scrollTo(0, backTo);
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
  look_remove: 'look retiré', expression_remove: 'expression écartée : « Annuler » la remet', expression_restore: 'expression remise',
};

// Rend vrai si l'atelier a pris l'action.
async function doAction(el) {
  const action = el.dataset.act;
  if (el.dataset.confirm && !(await ask(el.dataset.confirm))) return false;
  const params = collect(el);
  if (action === 'face') {
    if (!String(params.brief ?? 'x').trim()) delete params.brief;
    if (!('around' in params) && !params.brief && !(params.refs || []).length && !state.detail.character.face.brief) {
      toast('dis en une phrase qui il est, ou dépose une photo', 5000);
      $('[data-draft="face.brief"]')?.focus();
      return false;
    }
  }
  el.disabled = true;
  try {
    if (action === 'costume_go') {
      // Une nouvelle tenue : elle se garde (une seule fois, même si le champ
      // vient d'être quitté), puis son plein pied part dans la file.
      const key = await createCostume();
      if (!key) { el.disabled = false; return false; }
      const run = await api(actionUrl('fullbody'), { method: 'POST', body: { costume: key, variants: 3 } });
      state.jobs.unshift(run.job);
      schedulePoll(600);
      toast('il s\'habille : trois pleins pieds arrivent');
      await loadDetail();
      render(true);
      return true;
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
      if (action === 'look_remove' && state.look === params.id) state.look = null;
    }
    forget(el.dataset.form);
    await loadDetail();
    render(true);
    return true;
  } catch (e) {
    if (unknown(e)) {
      state.missing[action] = true;
      if (VOICE_ACTIONS.has(action)) state.missing.voice_design = true;
      toast('pas encore branché dans l\'atelier : ça arrive bientôt', 5000);
      render(true);
      return false;
    }
    toast(e.message, 7000);
    el.disabled = false;
    return false;
  }
}

/* Un look, une expression : une phrase, Entrée, et le rendu part en file.
   La phrase ne se perd que si l'atelier l'a prise. */
async function submitPhrase(form, action, key, extra = {}) {
  const field = form.elements.prompt;
  const prompt = String(field?.value || '').trim();
  if (!prompt) { toast(action === 'look' ? 'écris ce qui change sur son visage' : 'écris l\'expression, en mots'); return; }
  const fake = document.createElement('button');
  fake.dataset.act = action;
  fake.dataset.params = JSON.stringify({ ...extra, prompt });
  delete state.drafts[key];
  field.value = '';
  field.blur();
  if (!(await doAction(fake)) && !state.missing[action]) state.drafts[key] = prompt;
  render(true);
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
    // la fiche : un look, une pose, une tenue, le mouvement d'une expression
    const look = t.closest('[data-look]');
    if (look) { state.look = look.dataset.look || null; render(true); return; }
    const bodyView = t.closest('[data-body-view]');
    if (bodyView) {
      state.bodyView = { ...state.bodyView, [activeCostume(state.detail.character)]: bodyView.dataset.bodyView };
      render(true);
      return;
    }
    const cosPick = t.closest('[data-costume-pick]');
    if (cosPick) { state.costume = cosPick.dataset.costumePick; state.fbAgain = false; render(true); return; }
    const cosStep = t.closest('[data-costume-step]');
    if (cosStep) {
      const keys = Object.keys(state.detail.character.costumes);
      const i = keys.indexOf(activeCostume(state.detail.character));
      state.costume = keys[(i + Number(cosStep.dataset.costumeStep) + keys.length) % keys.length];
      render(true);
      return;
    }
    const cf = t.closest('[data-cast-filter]');
    if (cf) { setCastFilter(cf.dataset.castFilter); render(true); return; }
    const motion = t.closest('[data-motion]');
    if (motion) { state.motionPeek = state.motionPeek === motion.dataset.motion ? null : motion.dataset.motion; render(true); return; }
    if (t.closest('[data-traits-open]')) { state.traitsOpen = !state.traitsOpen; render(true); return; }
    const goto = t.closest('[data-goto]');
    if (goto) {
      const field = $(`[data-draft="${CSS.escape(goto.dataset.goto)}"]`);
      if (field) { field.scrollIntoView({ behavior: 'smooth', block: 'center' }); field.focus({ preventScroll: true }); }
      return;
    }
    const editOpen = t.closest('[data-edit-open]');
    if (editOpen) {
      state.moreOpen = true;
      state.editField = editOpen.dataset.editOpen;
      render(true);
      const inp = $('form[data-form="field"] textarea');
      if (inp) { inp.scrollIntoView({ behavior: 'smooth', block: 'center' }); inp.focus({ preventScroll: true }); }
      return;
    }
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
    if (form === 'look') submitPhrase(e.target, 'look', 'look.prompt');
    if (form === 'expr') submitPhrase(e.target, 'expression_add', 'expr.prompt', { costume: e.target.dataset.costume });
  });
  // « toute sa fiche » garde son dépli d'un rendu à l'autre
  app.addEventListener('toggle', (e) => { if (e.target.matches?.('.fi-more')) state.moreOpen = e.target.open; }, true);
  app.addEventListener('keydown', (e) => {
    const t = e.target;
    if (e.key === 'Escape' && (state.renaming || state.editField)) {
      e.stopPropagation();     // Échap ferme le champ, pas l'étape
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

  // Les machines : le panneau s'ouvre sur l'en-tête, se ferme en cliquant ailleurs ou par Échap.
  $('#machines-btn').addEventListener('click', () => {
    state.machines.open = !state.machines.open;
    paintMachines();
    if (state.machines.open) pollMachines(true);
  });
  $('#machines-pop').addEventListener('click', (e) => {
    const b = e.target.closest('[data-mach]');
    if (b && !b.disabled) machinesCommand(b.dataset.mach);
  });
  document.addEventListener('click', (e) => {
    // le chemin du clic, pris au départ : un bouton redessiné entre-temps compte encore comme dedans
    if (state.machines.open && !e.composedPath().includes($('#machines'))) {
      state.machines.open = false;
      paintMachines();
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (state.machines.open) { state.machines.open = false; paintMachines(); $('#machines-btn').focus(); return; }
    const box = $('#lightbox');
    if (!box.hidden) { box.hidden = true; return; }
    // Échap : d'une étape en plein cadre, retour à la fiche.
    if (state.route.view === 'perso' && TAB_IDS.has(state.route.tab) && !typing() && $('#confirm').hidden
      && !state.renaming && !state.editField) location.hash = tabHref('');
  });
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
      const before = jobsSig() + state.queue.map((j) => `${j.id}:${j.status}`).join('|');
      const jobs = await loadJobs();
      // Un rendu qui attend : la file entière dit son rang.
      state.queue = jobs.some((j) => j.status === 'queued') ? (await api('/api/jobs?limit=120')).jobs : [];
      const moved = jobsSig() + state.queue.map((j) => `${j.id}:${j.status}`).join('|') !== before;
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

/* ── les machines (le pont, tools/bridge.py) ────────────── */

/* Le panneau de l'en-tête : les deux DGX, ce qui tourne, et « Tout
   démarrer » quand quelque chose manque. Jamais orange : l'orange de
   l'écran reste la question du personnage. */
function paintMachines() {
  const m = state.machines;
  const h = m.health;
  const v = verdict(h);
  const btn = $('#machines-btn');
  btn.className = `mach-btn ${h ? v.cls : m.tried ? 'err' : ''}`;
  btn.setAttribute('aria-expanded', String(m.open));
  $('#machines-text').textContent = h ? `machines · ${v.word}` : m.tried ? 'machines · pont muet' : 'machines';
  const pop = $('#machines-pop');
  pop.hidden = !m.open;
  if (!m.open) return;
  if (!h) {
    pop.innerHTML = `<div class="mach-top"><span class="lbl">les machines</span></div>
      <p class="mach-note">Le pont ne répond pas. Il tourne sur DGX1 (tools/bridge.py, port 8770) ; à la maison,
        par DGX2, il faut aussi son relais.</p>`;
    return;
  }
  const missing = (h.missing || []).length;
  pop.innerHTML = `<div class="mach-top"><span class="lbl">les machines · ${esc(v.word)}</span><span class="sp"></span>
      <span class="lbl">${esc(when(h))}</span></div>
    <div class="mach-list">${machinesList(h)}</div>
    ${h.ready && !missing ? '' : `<p class="mach-note">${esc(v.text)}</p>`}
    <div class="mach-foot">
      ${missing ? `<button class="tb ghost sm" type="button" data-mach="start"${m.busy ? ' disabled' : ''}>Tout démarrer</button>` : ''}
      ${h3Up(h) ? `<button class="tb ghost sm" type="button" data-mach="stop-h3"${m.busy ? ' disabled' : ''}
          title="libérer la mémoire : H3 ne sert qu'au turnaround de présentation">Arrêter H3</button>`
        : `<button class="tb ghost sm" type="button" data-mach="start-h3"${m.busy ? ' disabled' : ''}>Démarrer H3</button>`}
      <span class="sp"></span>${m.busy ? '<span class="lbl">en cours…</span>' : ''}
    </div>`;
}

// Un relevé ; sans `once`, il reprogramme le suivant (vite après un démarrage).
let machTimer = null;
async function pollMachines(once = false) {
  const m = state.machines;
  try {
    m.health = await fetchHealth(`${BRIDGE}/bridge/health`);
  } catch (_) {
    m.health = null;
  }
  m.tried = true;
  paintMachines();
  if (once) return;
  clearTimeout(machTimer);
  machTimer = setTimeout(pollMachines, Date.now() < m.fast ? 3000 : 15000);
}

async function machinesCommand(what) {
  const m = state.machines;
  const [verb, body] = {
    start: ['start', { what: 'all', machine: 'all' }],
    'start-h3': ['start', { what: 'h3', machine: 'all' }],
    'stop-h3': ['stop', { what: 'h3', machine: 'all' }],
  }[what] || [];
  if (!verb) return;
  m.busy = true;
  paintMachines();
  try {
    const out = await command(BRIDGE, verb, body);
    toast(`machines : ${stepsText(out)}${verb === 'start' ? ' · ComfyUI met une minute à s\'éveiller' : ''}`, 7000);
    m.fast = Date.now() + 120000;
  } catch (e) {
    toast(e.message, 7000);
  }
  m.busy = false;
  await pollMachines();
}

wire();
onRoute().then(() => { pollJobs(); pollSystem(); pollMachines(); });
