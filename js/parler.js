'use strict';

import { converse } from './llm.js';

/* ============================================================
   Parler au personnage — la Scène.

   Le studio répond lui-même quand il sait le faire :
     POST /api/characters/<slug>/chat {message, history} → {reply, audio}
   Sans cette route (404), repli sur le relais du modèle de texte
   (/v1, par llm.js, seul à parler le dialecte OpenAI) avec une
   consigne tirée de la fiche : il répond à la première personne,
   court, par écrit — pas de voix dans ce cas.

   La voix en direct passe par le service voix, dont l'adresse vient
   de GET /api/voice/config → {ws_url, https_url?, available, reason?}.
   Le micro du navigateur exige HTTPS (ou localhost).
   ============================================================ */

const ABSENT = new Set([404, 405, 501]);
let chatRoute = null;          // null : pas encore essayé ; false : absente

const LABELS = {
  alias: 'surnom', gender: 'genre', age: 'âge', height: 'taille', body_type: 'silhouette',
  ethnicity: 'origine', face_description: 'visage', role: 'rôle', archetype: 'archétype',
  personality_traits: 'caractère', core_theme: 'ce qui le travaille', emotional_range: 'émotions',
  behavior_notes: 'comportement', speech_style: 'façon de parler', default_outfit_description: 'tenue',
  props: 'objets',
};

/** La consigne du repli : qui il est, et comment répondre. */
function persona(name, sheet = {}) {
  const facts = Object.entries(LABELS)
    .filter(([k]) => String(sheet[k] || '').trim())
    .map(([k, label]) => `- ${label} : ${String(sheet[k]).trim()}`)
    .join('\n');
  return `Tu es ${name}. Tu n'es pas un assistant : tu es ce personnage, et tu parles à la première personne, en français.

Ce que tu sais de toi :
${facts || '- presque rien encore : invente avec retenue, sans te contredire'}

Réponds comme à l'oral, en une à trois phrases, avec ta façon de parler. Pas de gestes entre astérisques, pas de liste, pas de titre. Ne dis jamais que tu es un modèle, une IA ou un programme ; une question hors de ton monde reçoit la réponse que ferait le personnage.`;
}

// Un modèle qui réfléchit à voix haute rend parfois <think>…</think>.
const clean = (text) => String(text || '').replace(/<think>[\s\S]*?<\/think>/gi, '').trim();

async function reason(res) {
  try {
    const j = await res.json();
    return j?.error?.message || `${res.status} ${res.statusText}`;
  } catch (_) {
    return `${res.status} ${res.statusText}`;
  }
}

/**
 * Un tour de conversation.
 * @param {{slug: string, name: string, sheet: object, history: Array<{role: string, content: string}>,
 *          message: string}} turn
 * @returns {Promise<{reply: string, audio: string|null, via: 'studio'|'texte'}>}
 */
async function talk({ slug, name, sheet, history, message }) {
  if (chatRoute !== false) {
    let res = null;
    try {
      res = await fetch(`/api/characters/${encodeURIComponent(slug)}/chat`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ message, history }),
      });
    } catch (_) {
      throw new Error('le studio ne répond pas');
    }
    if (res.ok) {
      chatRoute = true;
      const out = await res.json();
      return { reply: clean(out.reply), audio: out.audio || null, via: 'studio' };
    }
    if (!ABSENT.has(res.status)) throw new Error(await reason(res));
    chatRoute = false;
  }
  const out = await converse({
    system: persona(name, sheet),
    messages: [...history, { role: 'user', content: message }],
    tools: [],
  });
  return { reply: clean(out.text) || '…', audio: null, via: 'texte' };
}

/** L'état du service voix ; `missing` quand le studio ne le connaît pas. */
async function voiceConfig() {
  try {
    const res = await fetch('/api/voice/config');
    if (!res.ok) return { available: false, missing: ABSENT.has(res.status), reason: await reason(res) };
    return { missing: false, ...(await res.json()) };
  } catch (e) {
    return { available: false, missing: true, reason: e.message };
  }
}

/** Le micro est-il possible ici ? Rend la raison sinon. */
function micBlocker(config) {
  if (!config || config.missing) return 'le service voix n\'est pas encore branché';
  if (!config.available) return config.reason || 'le service voix est arrêté';
  if (!config.ws_url) return 'le service voix ne donne pas d\'adresse';
  if (!window.isSecureContext) return 'le micro du navigateur exige une page en HTTPS';
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return 'ce navigateur n\'enregistre pas le micro';
  return null;
}

/* Le micro, appuyer pour parler. Le son part en morceaux webm/opus sur
   le WebSocket du service voix ; un {"type":"end"} clôt la prise. En
   retour : du JSON {type: "transcript"|"reply", text, audio?}, ou un
   son brut à jouer. */
class Micro {
  constructor(url, { onTranscript, onReply, onAudio, onState } = {}) {
    Object.assign(this, { url, onTranscript, onReply, onAudio, onState });
    this.ws = null;
    this.rec = null;
    this.stream = null;
  }

  async open() {
    if (this.ws && this.ws.readyState <= 1) return;
    this.ws = new WebSocket(this.url);
    this.ws.binaryType = 'blob';
    this.ws.onmessage = (e) => {
      if (typeof e.data !== 'string') { this.onAudio?.(URL.createObjectURL(e.data)); return; }
      let msg = null;
      try { msg = JSON.parse(e.data); } catch (_) { return; }
      if (msg.type === 'transcript') this.onTranscript?.(msg.text || '');
      else if (msg.type === 'reply') this.onReply?.(msg.text || '', msg.audio || null);
    };
    this.ws.onclose = () => this.onState?.('closed');
    await new Promise((ok, ko) => {
      this.ws.onopen = ok;
      this.ws.onerror = () => ko(new Error('le service voix refuse la connexion'));
    });
  }

  async start() {
    await this.open();
    this.stream ||= await navigator.mediaDevices.getUserMedia({ audio: true });
    this.rec = new MediaRecorder(this.stream, { mimeType: 'audio/webm;codecs=opus' });
    this.rec.ondataavailable = (e) => { if (e.data.size && this.ws?.readyState === 1) this.ws.send(e.data); };
    this.rec.onstop = () => { if (this.ws?.readyState === 1) this.ws.send(JSON.stringify({ type: 'end' })); };
    this.rec.start(250);
    this.onState?.('listening');
  }

  stop() {
    if (this.rec && this.rec.state !== 'inactive') this.rec.stop();
    this.onState?.('waiting');
  }

  close() {
    this.stop();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.ws?.close();
    this.ws = null;
  }
}

export { talk, persona, voiceConfig, micBlocker, Micro };
