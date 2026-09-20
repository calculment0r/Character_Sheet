'use strict';

/* ============================================================
   Réglages du poste.
   Rien de secret n'est écrit dans le dépôt : l'URL du DGX, le
   jeton et la clé de repli vivent dans le localStorage du
   navigateur, saisis par l'opérateur. Le front ne connaît que
   des adresses ; il ne signe rien lui-même.
   ============================================================ */

const STORE_KEYS = {
  dgxUrl:   'factory_dgx_url',
  dgxModel: 'factory_dgx_model',
  dgxToken: 'factory_dgx_token',
  anthropicKey: 'anthropic_api_key',     // repris du générateur d'origine
  anthropicModel: 'claude_model',        // idem
  engine:   'factory_engine',
};

const DEFAULTS = {
  dgxUrl: '',
  dgxModel: 'local-model',
  dgxToken: '',
  anthropicKey: '',
  anthropicModel: 'claude-sonnet-4-6',
  engine: 'auto',                        // auto | dgx | anthropic
};

const ANTHROPIC_MODELS = [
  'claude-sonnet-4-6',
  'claude-opus-4-7',
  'claude-haiku-4-5',
];

const cfg = {
  read(key) {
    const v = localStorage.getItem(STORE_KEYS[key]);
    return v === null ? DEFAULTS[key] : v;
  },
  write(key, value) {
    if (value === '' || value === null || value === undefined) {
      localStorage.removeItem(STORE_KEYS[key]);
    } else {
      localStorage.setItem(STORE_KEYS[key], String(value));
    }
  },
  all() {
    return Object.fromEntries(Object.keys(STORE_KEYS).map((k) => [k, cfg.read(k)]));
  },

  /* Normalise une base d'URL : pas de barre oblique finale, et on
     tolère que l'opérateur colle « …/v1 » ou « …/v1/ ». */
  dgxBase() {
    let u = cfg.read('dgxUrl').trim();
    if (!u) return '';
    u = u.replace(/\/+$/, '');
    u = u.replace(/\/v1$/, '');
    return u;
  },

  /* Quel moteur pour ce tour ? 'auto' préfère le DGX dès qu'une
     URL est posée, et ne retombe sur Anthropic que si le DGX
     répond mal — c'est llm.js qui arbitre à l'exécution. */
  plan() {
    const engine = cfg.read('engine');
    const hasDgx = !!cfg.dgxBase();
    const hasKey = !!cfg.read('anthropicKey').trim();
    if (engine === 'dgx') return hasDgx ? ['dgx'] : [];
    if (engine === 'anthropic') return hasKey ? ['anthropic'] : [];
    const chain = [];
    if (hasDgx) chain.push('dgx');
    if (hasKey) chain.push('anthropic');
    return chain;
  },

  ready() { return cfg.plan().length > 0; },
};

export { cfg, ANTHROPIC_MODELS, DEFAULTS };
