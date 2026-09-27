'use strict';

/* ============================================================
   Page de test de l'étage Voix (voix.html). Minimale exprès :
   l'écran définitif viendra avec le studio. Elle ne parle qu'à
   son origine — le studio, ou le service vocal qui relaie le
   studio (HTTPS : le micro n'est prêté qu'en contexte sûr) —
   plus la WebSocket de la conversation (docs/VOIX.md).
   ============================================================ */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let slug = '';
let character = null;
let voiceCfg = null;

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error?.message || `${res.status}`);
  return data;
}

function pill(text, ok) {
  $('pill-text').textContent = text;
  $('pill').dataset.state = ok ? 'ok' : 'warn';
}

async function act(action, params, label) {
  const out = await api(`/api/characters/${encodeURIComponent(slug)}/actions/${action}`, params);
  if (!out.job) { await load(); return out; }
  const tag = label ? $(label) : null;
  for (;;) {
    const job = await api(`/api/jobs/${out.job.id}`);
    if (tag) tag.textContent = `${job.status} · ${Math.round(job.progress * 100)} % · ${job.message}`;
    if (!['queued', 'running'].includes(job.status)) {
      await load();
      if (job.status !== 'done') throw new Error(job.error || job.status);
      return job;
    }
    await new Promise((r) => setTimeout(r, 800));
  }
}

const fileUrl = (rel) => `/files/${encodeURIComponent(slug)}/${rel}?v=${Date.now()}`;

/* ── le personnage ───────────────────────────────────────── */

async function listCharacters() {
  const { characters } = await api('/api/characters');
  const sel = $('perso');
  sel.innerHTML = characters.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join('');
  const want = new URLSearchParams(location.search).get('slug') || localStorage.getItem('voix.slug');
  if (want && characters.some((c) => c.slug === want)) sel.value = want;
  slug = sel.value;
}

async function load() {
  if (!slug) return;
  const { character: c } = await api(`/api/characters/${encodeURIComponent(slug)}`);
  character = c;
  const v = c.voice || {};
  const sheet = c.identity || {};
  $('perso-info').textContent = [sheet.age && `${sheet.age} ans`, sheet.gender, sheet.ethnicity, sheet.speech_style]
    .filter(Boolean).join(' · ') + (v.locked ? ' — voix verrouillée' : ' — voix libre');
  if (document.activeElement !== $('desc')) $('desc').value = v.description || '';
  $('cands').innerHTML = v.locked
    ? `<div class="item on"><span class="meta">voix verrouillée</span><audio controls src="${fileUrl(v.locked)}"></audio>
        <span class="note">${esc(v.description_fr || '')}</span><button class="tb ghost sm" data-unlock>Libérer</button></div>`
      + candList(v, false)
    : candList(v, true);
  $('lines').innerHTML = (v.lines || []).slice().reverse().map((ln) => `
    <div class="item"><div style="flex:1 1 100%"><b>« ${esc(ln.text)} »</b>
      <div class="note">${esc(ln.play_state)}</div></div>
      ${ln.takes.map((t, i) => `<span class="item ${ln.kept === t.file ? 'on' : ''}">
        <audio controls src="${fileUrl(t.file)}"></audio>
        <span class="meta">prise ${i + 1} · sim ${t.similarity ?? '–'}</span>
        <button class="tb ghost sm" data-keep="${ln.id}:${i + 1}">Garder</button></span>`).join('')}
    </div>`).join('');
}

function candList(v, lockable) {
  return (v.candidates || []).slice().reverse().map((cd, j) => {
    const n = v.candidates.length - j;
    return `<div class="item"><span class="meta">n° ${n} · graine ${cd.seed} · ${cd.duration_s ?? '?'} s · ${cd.gen_s ?? '?'} s de calcul</span>
      <audio controls preload="none" src="${fileUrl(cd.file)}"></audio>
      ${lockable ? `<button class="tb ghost sm" data-lock="${n}">Verrouiller</button>` : ''}</div>`;
  }).join('');
}

/* ── la conversation ─────────────────────────────────────── */

let ws = null;
let ctx = null;
let micStream = null;
let micNode = null;
let playing = [];
let playHead = 0;
let pendingAudio = null;
let assistantLine = null;

function ensureCtx() {
  if (!ctx) ctx = new AudioContext();
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

function log(cls, text) {
  const div = document.createElement('div');
  div.className = cls;
  div.textContent = text;
  $('chat').appendChild(div);
  $('chat').scrollTop = $('chat').scrollHeight;
  return div;
}

function stopPlayback() {
  for (const s of playing) { try { s.stop(); } catch { /* déjà fini */ } }
  playing = [];
  playHead = 0;
}

function play(pcm, sr) {
  const c = ensureCtx();
  const i16 = new Int16Array(pcm);
  const buf = c.createBuffer(1, i16.length, sr);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < i16.length; i++) ch[i] = i16[i] / 32768;
  const src = c.createBufferSource();
  src.buffer = buf;
  src.connect(c.destination);
  playHead = Math.max(playHead, c.currentTime + 0.03);
  src.start(playHead);
  playHead += buf.duration;
  playing.push(src);
  src.onended = () => {
    playing = playing.filter((s) => s !== src);
    if (!playing.length && ws?.readyState === 1) ws.send(JSON.stringify({ type: 'lecture_finie' }));
  };
}

let sameOrigin = false;   // la page est-elle servie par le service vocal lui-même ?

function wsUrl() {
  if (sameOrigin) return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`;
  if (!voiceCfg) return null;
  return location.protocol === 'https:' ? voiceCfg.wss_url : voiceCfg.ws_url;
}

function connect() {
  if (ws) ws.close();
  const url = wsUrl();
  if (!url) { log('s', 'aucune adresse de conversation (voice_https_url non réglé ?)'); return; }
  ensureCtx();
  ws = new WebSocket(`${url}?slug=${encodeURIComponent(slug)}`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => { $('connect').textContent = 'Reconnecter'; $('mic').disabled = !micPossible(); };
  ws.onclose = () => { log('s', 'conversation fermée'); $('mic').disabled = true; stopMic(); };
  ws.onerror = () => log('s', `WebSocket en échec (${url}) — certificat à accepter ? ouvrir ${voiceCfg?.https_url || url.replace(/^ws/, 'http')}`);
  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) {
      if (pendingAudio) play(ev.data, pendingAudio.sr);
      pendingAudio = null;
      return;
    }
    const m = JSON.parse(ev.data);
    switch (m.type) {
      case 'pret': log('s', `${m.personnage} · voix ${m.voix ? 'verrouillée' : 'absente : texte seul'} · ${m.moteur} · ${m.llm.model}`); break;
      case 'ecoute': log('s', `oreille ouverte · cran ${m.cran ?? '?'}${m.pause_ms ? ` · ${m.pause_ms} ms de pause` : ''}`); break;
      case 'mot': $('live').textContent += m.texte; break;
      case 'tour': $('live').textContent = ''; log('u', m.texte); assistantLine = log('a', ''); break;
      case 'texte': if (assistantLine) assistantLine.textContent += m.delta; break;
      case 'audio': pendingAudio = m; break;
      case 'stop': stopPlayback(); log('s', `coupé (${m.raison})`); break;
      case 'fin': showMetrics(m.mesures); break;
      case 'erreur': log('s', `erreur : ${m.message}`); break;
      default: break;
    }
  };
}

function showMetrics(k) {
  const bits = [
    k.premier_son_apres_dernier_mot_ms != null && `dernier mot → 1er son ${k.premier_son_apres_dernier_mot_ms} ms`,
    k.premier_son_ms != null && `tour → 1er son ${k.premier_son_ms} ms`,
    k.premier_mot_ms != null && `1er mot du modèle ${k.premier_mot_ms} ms`,
    k.morceaux != null && `${k.morceaux} morceau(x)`,
    k.rtf?.length && `rtf ${k.rtf.join(' / ')}`,
  ].filter(Boolean);
  $('metrics').textContent = bits.join(' · ');
}

function micPossible() {
  return window.isSecureContext && !!navigator.mediaDevices?.getUserMedia;
}

// Le micro, ramené à 24 kHz mono en PCM 16 bits, par paquets de 80 ms
// (la trame de l'oreille Kyutai).
const WORKLET = `class Tap extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; }
}
registerProcessor('tap', Tap);`;

async function startMic() {
  const c = ensureCtx();
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  await c.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: 'text/javascript' })));
  const src = c.createMediaStreamSource(micStream);
  micNode = new AudioWorkletNode(c, 'tap');
  const ratio = c.sampleRate / 24000;
  let carry = new Float32Array(0);
  let pos = 0;
  let out = [];
  micNode.port.onmessage = (e) => {
    const inBuf = new Float32Array(carry.length + e.data.length);
    inBuf.set(carry); inBuf.set(e.data, carry.length);
    while (pos + ratio < inBuf.length) {
      const i = Math.floor(pos); const f = pos - i;
      out.push(inBuf[i] * (1 - f) + inBuf[i + 1] * f);
      pos += ratio;
    }
    const keep = Math.floor(pos);
    carry = inBuf.slice(keep);
    pos -= keep;
    if (out.length >= 1920) {
      const pcm = new Int16Array(out.length);
      for (let i = 0; i < out.length; i++) pcm[i] = Math.max(-32768, Math.min(32767, out[i] * 32768));
      out = [];
      if (ws?.readyState === 1) ws.send(pcm.buffer);
    }
  };
  src.connect(micNode);
  ws.send(JSON.stringify({ type: 'micro', on: true, cran: $('cran').value }));
  $('mic').classList.add('on');
  $('mic').textContent = 'Micro ouvert';
}

function stopMic() {
  micStream?.getTracks().forEach((t) => t.stop());
  micNode?.disconnect();
  micStream = null; micNode = null;
  if (ws?.readyState === 1) ws.send(JSON.stringify({ type: 'micro', on: false }));
  $('mic').classList.remove('on');
  $('mic').textContent = 'Micro';
}

function sendText() {
  const text = $('say').value.trim();
  if (!text) return;
  if (!ws || ws.readyState !== 1) { log('s', 'se connecter d’abord'); return; }
  ws.send(JSON.stringify({ type: 'texte', message: text }));
  $('say').value = '';
}

/* ── câblage ─────────────────────────────────────────────── */

function guard(fn) {
  return async (...a) => {
    try { await fn(...a); } catch (err) { pill(String(err.message || err), false); }
  };
}

$('perso').onchange = guard(async () => {
  slug = $('perso').value;
  localStorage.setItem('voix.slug', slug);
  if (ws) ws.close();
  $('chat').innerHTML = '';
  await load();
});
$('reload').onclick = guard(load);
$('design').onclick = guard(async () => {
  const d = $('desc').value.trim();
  await act('voice_design', d ? { description: d, n: 4 } : { n: 4 }, 'design-job');
});
$('redraft').onclick = guard(() => act('voice_design', { redraft: true, n: 4 }, 'design-job'));
$('cands').onclick = guard(async (e) => {
  const lock = e.target.closest('[data-lock]');
  if (lock) await act('voice_lock', { candidate: lock.dataset.lock });
  if (e.target.closest('[data-unlock]') && confirm('Libérer la voix verrouillée ? La référence part aux archives.')) {
    await act('voice_unlock', {});
  }
});
$('line-go').onclick = guard(() => act('line', {
  text: $('line-text').value, direction: $('line-dir').value, context: $('line-ctx').value, takes: 3,
}, 'line-job'));
$('lines').onclick = guard(async (e) => {
  const b = e.target.closest('[data-keep]');
  if (!b) return;
  const [line, take] = b.dataset.keep.split(':');
  await act('line_keep', { line, take });
});
$('connect').onclick = connect;
$('mic').onclick = guard(async () => { if (micStream) stopMic(); else await startMic(); });
$('hush').onclick = () => { stopPlayback(); ws?.readyState === 1 && ws.send(JSON.stringify({ type: 'stop' })); };
$('send').onclick = sendText;
$('say').onkeydown = (e) => { if (e.key === 'Enter') sendText(); };

guard(async () => {
  await listCharacters();
  sameOrigin = await fetch('/health').then((r) => (r.ok ? r.json() : {})).then((h) => h.service === 'voix').catch(() => false);
  voiceCfg = await api('/api/voice/config').catch(() => null);
  if (!voiceCfg) pill('pas de studio', false);
  else pill(voiceCfg.available ? `voix : ${voiceCfg.engine}` : 'voix indisponible', voiceCfg.available);
  $('mic-note').innerHTML = micPossible()
    ? 'Micro possible sur cette page. Casque conseillé : sans lui, l’oreille entend le personnage.'
    : `<span class="warn">Le navigateur ne prête pas le micro à une page en HTTP.</span> ${voiceCfg?.https_url
      ? `Ouvrir <a href="${esc(voiceCfg.https_url)}">${esc(voiceCfg.https_url)}</a> (certificat à accepter une fois), ou un tunnel ssh vers localhost.`
      : 'Passer par HTTPS ou un tunnel ssh vers localhost.'} La conversation écrite marche ici.`;
  if (voiceCfg && !voiceCfg.available && voiceCfg.reason) log('s', voiceCfg.reason);
  await load();
})();
