'use strict';

/* ============================================================
   Les machines — l'état des deux DGX, lu sur le pont
   (tools/bridge.py, GET /bridge/health).

   Un seul rendu pour trois pages : la page d'état publiée sur
   GitHub (« Les machines »), le panneau « Machines » de l'en-tête
   du studio, et la page du pont (/bridge/), quand le studio
   lui-même dort. Les classes sont celles de rack.css : .mach-*.

   La santé ne dit rien de secret : allumées ou non, ce qui
   tourne, la mémoire, l'heure. Le démarrage, lui, ne se fait que
   derrière la porte (Cloudflare Access) ou depuis la maison.
   ============================================================ */

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

// Ce que chaque service fait, dit simplement (au survol).
const SERVICES = {
  studio: ['studio', 'la page et la file des rendus'],
  relay: ['relais', 'DGX2 renvoie le studio et le pont de DGX1 par le câble direct'],
  comfyui: ['comfyui', 'les images, les vues et la 3D'],
  h3: ['h3', 'le turnaround de présentation'],
  ollama: ['ollama', 'le modèle de texte'],
};

/* La santé, ou une erreur si le pont ne répond pas (machine éteinte,
   tunnel fermé) dans le temps donné. */
export async function fetchHealth(url, ms = 6000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try {
    const res = await fetch(url, { cache: 'no-store', signal: ctl.signal });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const h = await res.json();
    if (!Array.isArray(h?.machines)) throw new Error('réponse illisible');
    return h;
  } finally {
    clearTimeout(timer);
  }
}

/* Démarrer, arrêter : POST protégé, en JSON (jamais un formulaire
   simple, que le pont refuse). */
export async function command(base, verb, body) {
  let res;
  try {
    res = await fetch(`${base}/bridge/${verb}`, {
      method: 'POST', cache: 'no-store', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch (_) {
    // Le pont muet, ou la porte (Cloudflare Access) qui renvoie vers sa page de connexion.
    throw new Error('le pont ne répond pas, ou la porte a expiré : recharge la page');
  }
  let json = null;
  try { json = await res.json(); } catch (_) { /* pas du JSON : ce n'est pas le pont qui a répondu */ }
  if (!res.ok || !json) throw new Error(json?.error?.message || `${res.status} ${res.statusText}`);
  return json;
}

const machine = (h, id) => (h?.machines || []).find((m) => m.id === id) || null;

export function serviceUp(h, machineId, service) {
  const m = machine(h, machineId);
  return !!(m?.up && (m.services || []).find((s) => s.id === service)?.up);
}

// H3 tourne-t-il quelque part ? (pour « Arrêter H3 »)
export const h3Up = (h) => (h?.machines || []).some((m) => serviceUp(h, m.id, 'h3'));

/* Le verdict en un mot, et la phrase qui va avec. */
export function verdict(h) {
  if (!h) {
    return { cls: 'off', word: 'éteintes', text: 'Les machines ne répondent pas : éteintes, ou le tunnel est fermé.' };
  }
  if (h.ready) return { cls: 'on', word: 'prêt', text: 'Tout ce qu\'il faut pour travailler tourne.' };
  const miss = (h.missing || []).map((k) => {
    const [mid, sid] = k.split(':');
    return `${SERVICES[sid]?.[0] || sid} de ${machine(h, mid)?.label || mid}`;
  });
  return {
    cls: 'amb', word: 'à démarrer',
    text: miss.length ? `Manque : ${miss.join(', ')}.` : 'Il manque de quoi travailler.',
  };
}

/* Quand le relevé a été fait, en heure locale. */
export function when(h) {
  const d = new Date(h?.at);
  return Number.isNaN(d.getTime()) ? '' : `relevé à ${d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`;
}

const go = (n) => Number(n).toLocaleString('fr-FR', { maximumFractionDigits: 0 });

function machineHtml(m, missing) {
  const mem = m.up && m.memory ? `${go(m.memory.free_gb)} Go libres sur ${go(m.memory.total_gb)}` : '';
  const services = (m.services || []).map((s) => {
    const [label, what] = SERVICES[s.id] || [s.id, ''];
    const wanted = missing.includes(`${m.id}:${s.id}`);
    const cls = s.busy ? 'work' : s.up ? 'on' : wanted ? 'amb' : 'off';
    const note = s.busy ? 'au travail' : s.up ? '' : 'arrêté';
    return `<li class="${cls}" title="${esc(what)}"><i></i>${esc(label)}${note ? `<em>${esc(note)}</em>` : ''}</li>`;
  }).join('');
  return `<div class="mach-m ${m.up ? 'on' : 'off'}">
    <div class="mach-mh"><i></i><b>${esc(m.label)}</b><span>${m.up ? 'allumée' : 'éteinte'}</span><span class="sp"></span>${
      mem ? `<span>${esc(mem)}</span>` : ''}</div>
    ${m.up ? `<ul class="mach-s">${services}</ul>` : ''}
  </div>`;
}

/* Les machines en rangées : une par DGX, ses services en puces. */
export function machinesList(h) {
  if (!h) return '';
  return (h.machines || []).map((m) => machineHtml(m, h.missing || [])).join('');
}

/* Ce qu'une réponse de démarrage a fait, en une ligne. */
export function stepsText(out) {
  const steps = out?.steps || [];
  const failed = steps.filter((s) => /^(échec|refusé|éteinte)/.test(s.result));
  if (failed.length) return `${failed[0].label || failed[0].machine} · ${failed[0].what} : ${failed[0].result}`;
  const started = steps.filter((s) => /^(démarré|arrêté|lancé)/.test(s.result));
  if (!started.length) return 'rien à faire : tout tournait déjà';
  return `${started.map((s) => `${s.what} (${s.label || s.machine})`).join(', ')} : ${started[0].result}`;
}
