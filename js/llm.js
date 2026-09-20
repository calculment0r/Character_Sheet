'use strict';

import { cfg } from './config.js';

/* ============================================================
   Client de modèle unifié.

   Le reste de l'application ne parle qu'un seul dialecte, celui
   d'Anthropic : une liste de messages dont le contenu est fait de
   blocs { text | image | tool_use | tool_result }. C'est le
   dialecte d'origine du générateur, on ne le touche pas.

   Ici on le traduit vers ce que comprend la cible :
     - DGX local      → /v1/chat/completions, dialecte OpenAI
     - Anthropic      → /v1/messages, dialecte natif

   La réponse est toujours renormalisée en
     { text, toolCalls: [{ id, name, input }], stopReason, via }
   ============================================================ */

const ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages';
const MAX_TOKENS = 4096;

class LLMError extends Error {
  constructor(message, { via, status, retryable = false } = {}) {
    super(message);
    this.name = 'LLMError';
    this.via = via;
    this.status = status;
    this.retryable = retryable;
  }
}

/* ── traduction vers le dialecte OpenAI ─────────────────── */

function blocksToOpenAIContent(content) {
  if (typeof content === 'string') return content;
  const parts = [];
  for (const b of content) {
    if (b.type === 'text') {
      parts.push({ type: 'text', text: b.text });
    } else if (b.type === 'image') {
      const { media_type, data } = b.source || {};
      parts.push({ type: 'image_url', image_url: { url: `data:${media_type};base64,${data}` } });
    }
  }
  if (parts.length === 1 && parts[0].type === 'text') return parts[0].text;
  return parts;
}

function toOpenAIMessages(system, messages) {
  const out = [{ role: 'system', content: system }];

  for (const msg of messages) {
    const content = msg.content;

    if (typeof content === 'string') {
      out.push({ role: msg.role, content });
      continue;
    }

    // Un tour d'outils revient côté utilisateur chez Anthropic ;
    // chez OpenAI chaque résultat est un message de rôle « tool ».
    const toolResults = content.filter((b) => b.type === 'tool_result');
    if (toolResults.length) {
      for (const tr of toolResults) {
        out.push({
          role: 'tool',
          tool_call_id: tr.tool_use_id,
          content: typeof tr.content === 'string' ? tr.content : JSON.stringify(tr.content),
        });
      }
      const rest = content.filter((b) => b.type !== 'tool_result');
      if (rest.length) out.push({ role: msg.role, content: blocksToOpenAIContent(rest) });
      continue;
    }

    const toolUses = content.filter((b) => b.type === 'tool_use');
    if (toolUses.length) {
      const text = content.filter((b) => b.type === 'text').map((b) => b.text).join('\n');
      out.push({
        role: 'assistant',
        content: text || null,
        tool_calls: toolUses.map((tu) => ({
          id: tu.id,
          type: 'function',
          function: { name: tu.name, arguments: JSON.stringify(tu.input ?? {}) },
        })),
      });
      continue;
    }

    out.push({ role: msg.role, content: blocksToOpenAIContent(content) });
  }

  return out;
}

function toOpenAITools(tools) {
  return tools.map((t) => ({
    type: 'function',
    function: { name: t.name, description: t.description, parameters: t.input_schema },
  }));
}

function fromOpenAI(json) {
  const choice = (json.choices || [])[0] || {};
  const msg = choice.message || {};
  const toolCalls = (msg.tool_calls || []).map((tc, i) => {
    let input = {};
    try {
      input = tc.function?.arguments ? JSON.parse(tc.function.arguments) : {};
    } catch (_) {
      // Un modèle local rend parfois des arguments tronqués ou mal
      // échappés. On ne casse pas le tour pour autant : on remonte
      // un objet vide, l'appelant le traitera comme un appel raté.
      input = {};
    }
    return { id: tc.id || `call_${i}`, name: tc.function?.name || '', input };
  });
  return {
    text: typeof msg.content === 'string' ? msg.content : '',
    toolCalls,
    stopReason: choice.finish_reason || 'stop',
  };
}

/* ── traduction depuis le dialecte Anthropic ────────────── */

function fromAnthropic(json) {
  const content = json.content || [];
  return {
    text: content.filter((b) => b.type === 'text').map((b) => b.text).join('\n'),
    toolCalls: content
      .filter((b) => b.type === 'tool_use')
      .map((b) => ({ id: b.id, name: b.name, input: b.input || {} })),
    stopReason: json.stop_reason || 'end_turn',
  };
}

/* ── transports ─────────────────────────────────────────── */

async function readError(res, via) {
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    const m = body?.error?.message || body?.detail || body?.message;
    if (m) detail = typeof m === 'string' ? m : JSON.stringify(m);
  } catch (_) { /* corps illisible, on garde le code */ }
  // 5xx et 429 valent un repli ; un 4xx de requête, non : la même
  // requête échouerait pareil sur l'autre moteur.
  const retryable = res.status >= 500 || res.status === 429 || res.status === 404;
  return new LLMError(detail, { via, status: res.status, retryable });
}

async function callDgx({ system, messages, tools, signal }) {
  const base = cfg.dgxBase();
  if (!base) throw new LLMError('aucune URL DGX configurée', { via: 'dgx', retryable: true });

  const headers = { 'content-type': 'application/json' };
  const token = cfg.read('dgxToken').trim();
  if (token) headers.authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(`${base}/v1/chat/completions`, {
      method: 'POST',
      headers,
      signal,
      body: JSON.stringify({
        model: cfg.read('dgxModel'),
        max_tokens: MAX_TOKENS,
        messages: toOpenAIMessages(system, messages),
        tools: toOpenAITools(tools),
        tool_choice: 'auto',
      }),
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    // Réseau coupé, tunnel fermé, CORS refusé : c'est exactement le
    // cas où le repli Anthropic a une raison d'exister.
    throw new LLMError(`DGX injoignable (${e.message})`, { via: 'dgx', retryable: true });
  }

  if (!res.ok) throw await readError(res, 'dgx');
  return { ...fromOpenAI(await res.json()), via: 'dgx' };
}

async function callAnthropic({ system, messages, tools, signal }) {
  const key = cfg.read('anthropicKey').trim();
  if (!key) throw new LLMError('aucune clé Anthropic', { via: 'anthropic' });

  let res;
  try {
    res = await fetch(ANTHROPIC_URL, {
      method: 'POST',
      signal,
      headers: {
        'content-type': 'application/json',
        'x-api-key': key,
        'anthropic-version': '2023-06-01',
        'anthropic-dangerous-direct-browser-access': 'true',
      },
      body: JSON.stringify({
        model: cfg.read('anthropicModel'),
        max_tokens: MAX_TOKENS,
        system,
        tools,
        messages,
      }),
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new LLMError(`Anthropic injoignable (${e.message})`, { via: 'anthropic' });
  }

  if (!res.ok) throw await readError(res, 'anthropic');
  return { ...fromAnthropic(await res.json()), via: 'anthropic' };
}

const TRANSPORTS = { dgx: callDgx, anthropic: callAnthropic };

/* ── entrée publique ────────────────────────────────────── */

/**
 * Joue un tour de conversation sur le premier moteur disponible.
 * @param {{system: string, messages: Array, tools: Array, signal?: AbortSignal,
 *          onFallback?: (from: string, to: string, reason: string) => void}} req
 */
async function converse({ system, messages, tools, signal, onFallback }) {
  const chain = cfg.plan();
  if (!chain.length) throw new LLMError('aucun moteur configuré : renseigne une URL DGX ou une clé Anthropic');

  let last = null;
  for (let i = 0; i < chain.length; i++) {
    const via = chain[i];
    try {
      return await TRANSPORTS[via]({ system, messages, tools, signal });
    } catch (e) {
      if (e.name === 'AbortError') throw e;
      last = e;
      const next = chain[i + 1];
      // On ne bascule que sur une panne de transport, jamais sur un
      // refus de la requête elle-même.
      if (!next || e.retryable === false) break;
      if (onFallback) onFallback(via, next, e.message);
    }
  }
  throw last;
}

/** Vérifie que le DGX répond et renvoie les modèles qu'il sert. */
async function probeDgx({ signal } = {}) {
  const base = cfg.dgxBase();
  if (!base) return { ok: false, reason: 'aucune URL' };

  const headers = {};
  const token = cfg.read('dgxToken').trim();
  if (token) headers.authorization = `Bearer ${token}`;

  try {
    const res = await fetch(`${base}/v1/models`, { headers, signal });
    if (!res.ok) return { ok: false, reason: `${res.status} ${res.statusText}` };
    const json = await res.json();
    const models = (json.data || []).map((m) => m.id).filter(Boolean);
    return { ok: true, models };
  } catch (e) {
    return { ok: false, reason: e.message };
  }
}

export { converse, probeDgx, LLMError };
