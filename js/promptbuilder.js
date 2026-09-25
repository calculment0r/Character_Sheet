'use strict';

import { SCALAR_KEYS } from './schema.js';

/* ============================================================
   Deux fabricants de prompt.

   1. renderTemplate — le gabarit texte historique du dépôt, celui
      que l'opérateur copie vers un modèle d'image quelconque.
   2. buildRef2VA  — le format en six sections que MiniMax H3
      attend, avec ses règles dures : aucun prompt négatif, un rôle
      nommé par référence, un bloc « prendre tel quel ».
   ============================================================ */

/* ── gabarit historique ─────────────────────────────────── */

function renderTemplate(template, sheet, notes) {
  let out = template;

  out = out.replace(/\{\{#if\s+(\w+)\}\}([\s\S]*?)\{\{\/if\}\}/g, (_, key, body) => (sheet[key] ? body : ''));

  out = out.replace(/\{\{#each\s+notes\}\}([\s\S]*?)\{\{\/each\}\}/g, (_, body) => {
    if (!notes.length) return '';
    return notes.map((n) => body.replace(/\{\{this\}\}/g, n)).join('');
  });

  out = out.replace(/\{\{(\w+)\}\}/g, (_, key) => sheet[key] || `(à compléter: ${key})`);

  return out.trim();
}

/* ── format Ref2VA ──────────────────────────────────────── */

/* La pose de référence, écrite en prose et identique sur toutes
   les vues. A-pose, jamais T-pose : cf. §8 du brief. */
const POSE_APOSE =
  "The subject stands in a relaxed A-pose: arms held about forty-five degrees away from the torso, " +
  "palms turned inward toward the thighs, fingers separated and individually visible, legs slightly apart " +
  "at hip width, weight evenly distributed on both feet, gaze level with the camera horizon. " +
  "There is no twist in the pelvis and no twist in the shoulders, and no perspective emphasis on any limb.";

/* Le bloc qui interdit au modèle d'améliorer ce qu'il documente. */
const TAKE_AS_IS =
  "Every garment piece, strap, buckle, seam, fastening, colour and finish keeps exactly what it has in the " +
  "reference images. Nothing is redrawn, simplified, tidied, embellished or improved. This plate documents an " +
  "existing design; it does not create one.";

const NO_TEXT = "The image carries no text, no caption, no logo and no watermark.";

const AZIMUTHS = {
  front: { deg: 0,   label: 'FULLBODY FRONT',  prose: 'seen from directly in front, camera azimuth zero degrees' },
  left:  { deg: 90,  label: 'FULLBODY LEFT',   prose: 'seen in pure left profile, camera azimuth ninety degrees' },
  back:  { deg: 180, label: 'FULLBODY BACK',   prose: 'seen from directly behind, camera azimuth one hundred and eighty degrees' },
  right: { deg: 270, label: 'FULLBODY RIGHT',  prose: 'seen in pure right profile, camera azimuth two hundred and seventy degrees' },
  three: { deg: 45,  label: 'FULLBODY 3/4',    prose: 'seen at a three-quarter angle, camera azimuth forty-five degrees' },
};

const CLOSEUPS = {
  neutral: 'a close-up of the face at eye level, neutral expression, mouth closed',
  profile: 'a close-up of the face in pure profile at eye level, neutral expression',
  three:   'a close-up of the face at a three-quarter angle, neutral expression',
  smile:   'a close-up of the face at eye level, a restrained smile',
  sad:     'a close-up of the face at eye level, a sad expression',
  nervous: 'a close-up of the face at a three-quarter angle, a nervous expression',
};

/** Décrit le personnage en prose à partir de la fiche. */
function describeSubject(sheet) {
  const bits = [];
  const push = (key, fmt) => { if (sheet[key]) bits.push(fmt(sheet[key])); };

  push('age', (v) => `${v} old`);
  push('gender', (v) => v);
  push('ethnicity', (v) => v);
  push('body_type', (v) => `${v} build`);
  push('height', (v) => `standing ${v}`);

  const who = bits.length ? bits.join(', ') : 'a person';
  const name = sheet.character_name || 'the character';
  const role = sheet.role ? `, working as ${sheet.role}` : '';
  const arch = sheet.archetype ? `, archetype: ${sheet.archetype}` : '';
  return `${name} is ${who}${role}${arch}.`;
}

function describeOutfit(sheet) {
  const parts = [];
  if (sheet.default_outfit_description) parts.push(sheet.default_outfit_description);
  if (sheet.top_description) parts.push(`Top: ${sheet.top_description}.`);
  if (sheet.bottom_description) parts.push(`Bottom: ${sheet.bottom_description}.`);
  if (sheet.shoes_description) parts.push(`Shoes: ${sheet.shoes_description}.`);
  if (sheet.accessories) parts.push(`Accessories: ${sheet.accessories}.`);
  return parts.join(' ');
}

/**
 * Construit un prompt Ref2VA en six sections.
 *
 * @param {object} o
 * @param {object} o.sheet        la fiche d'identité
 * @param {string[]} o.notes      les annotations
 * @param {Array<{role:string, what:string}>} o.refs
 *        Les références dans l'ordre où elles sont envoyées. Chacune
 *        DOIT porter un rôle nommé : sans ça le modèle pioche dans
 *        toutes les images et le résultat devient imprévisible.
 * @param {'plate'|'view'|'closeup'} o.target
 * @param {string} [o.azimuth]    clé de AZIMUTHS, pour target='view'
 * @param {string} [o.expression] clé de CLOSEUPS, pour target='closeup'
 * @param {boolean} [o.maskFace]  disque neutre sur le visage des plein pieds
 */
function buildRef2VA({ sheet, notes = [], refs = [], target = 'plate', azimuth = 'front', expression = 'neutral', maskFace = false }) {
  const subject = describeSubject(sheet);
  const outfit = describeOutfit(sheet);

  // 1. Rôles des références, nommés un par un.
  const subject_definitions = [
    refs.length
      ? refs.map((r, i) => `Image ${i + 1} defines ${r.role}${r.what ? ` — ${r.what}` : ''}.`).join(' ')
      : 'No reference image is supplied; the subject is defined by the written description alone.',
    subject,
    outfit,
  ].filter(Boolean).join(' ');

  // 2. Ce que la frame montre.
  let summary;
  if (target === 'view') {
    const az = AZIMUTHS[azimuth] || AZIMUTHS.front;
    summary =
      `A single full-body orthographic reference frame of the subject, ${az.prose}. ` +
      `The subject fills the frame with an even margin on every side, standing on a plain neutral seamless background.`;
  } else if (target === 'closeup') {
    summary = `A single reference frame: ${CLOSEUPS[expression] || CLOSEUPS.neutral}, on a plain neutral seamless background.`;
  } else {
    summary =
      'A single character model sheet laid out as a clean studio grid. The top row holds five full-body views at ' +
      'identical scale and identical margins — front at zero degrees, left profile at ninety, back at one hundred ' +
      'and eighty, right profile at two hundred and seventy, and a three-quarter view at forty-five. The bottom row ' +
      'holds face close-ups: neutral front, pure profile, and three-quarter.';
  }

  // 3. Ce qui doit rester rigoureusement constant.
  const retention = [
    'The identity of the face is defined exclusively by the reference image named as the face reference.',
    maskFace
      ? 'In the full-body panels the head is covered by a plain neutral disc; the head there serves only silhouette and proportion and is not an identity reference.'
      : 'In the full-body panels the head serves only silhouette and proportion and is not an identity reference.',
    TAKE_AS_IS,
    'Lighting is a single even neutral studio setup, identical across every panel, with no coloured rim and no dramatic falloff.',
    'The scale of the subject is constant from panel to panel, and the margin around the silhouette is constant too.',
  ].join(' ');

  // 4. Le détail : pose, matières, palette, annotations.
  const detailed = [
    POSE_APOSE,
    sheet.color_palette ? `The colour palette of the character is: ${sheet.color_palette}.` : '',
    sheet.props ? `Props present: ${sheet.props}.` : '',
    notes.length ? `Design notes to respect: ${notes.join('; ')}.` : '',
    'The background is a plain uniform neutral grey seamless, with no set, no props on the floor and no cast shadow beyond a soft contact shadow.',
    NO_TEXT,
  ].filter(Boolean).join(' ');

  return {
    subject_definitions,
    summary,
    retention_analysis: retention,
    detailed_description: detailed,
    overall_soundscape: 'Silent. No ambience, no foley, no room tone.',
    non_diegetic_music: 'None.',
  };
}

/** Met le format six sections à plat, pour l'afficher ou le copier. */
function ref2vaToText(p) {
  return [
    ['subject_definitions',   p.subject_definitions],
    ['summary',               p.summary],
    ['retention_analysis',    p.retention_analysis],
    ['detailed_description',  p.detailed_description],
    ['overall_soundscape',    p.overall_soundscape],
    ['non_diegetic_music',    p.non_diegetic_music],
  ].map(([k, v]) => `${k}:\n${v}`).join('\n\n');
}

/** Combien de champs sont remplis — sert la jauge de la fiche. */
function filledCount(sheet) {
  return SCALAR_KEYS.filter((k) => sheet[k]).length;
}

export { renderTemplate, buildRef2VA, ref2vaToText, filledCount, AZIMUTHS, CLOSEUPS, POSE_APOSE };
