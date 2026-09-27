'use strict';

/* ============================================================
   Le contrat entre l'application et le modèle.
   Champs de la fiche, outils exposés, préambule système, et la
   liste des étages de la chaîne telle que le brief la découpe.
   ============================================================ */

const SHEET_FIELDS = [
  { section: 'CORE', key: 'character_name', label: 'name' },
  { section: 'CORE', key: 'alias', label: 'alias' },
  { section: 'CORE', key: 'gender', label: 'gender' },
  { section: 'CORE', key: 'age', label: 'age' },
  { section: 'CORE', key: 'height', label: 'height' },
  { section: 'CORE', key: 'body_type', label: 'body type' },
  { section: 'CORE', key: 'ethnicity', label: 'ethnicity' },
  { section: 'CORE', key: 'face_description', label: 'face & hair' },
  { section: 'CORE', key: 'role', label: 'role' },
  { section: 'CORE', key: 'archetype', label: 'archetype' },
  { section: 'PSYCHE', key: 'personality_traits', label: 'personality' },
  { section: 'PSYCHE', key: 'core_theme', label: 'core theme' },
  { section: 'PSYCHE', key: 'emotional_range', label: 'emotional range' },
  { section: 'PSYCHE', key: 'behavior_notes', label: 'behavior' },
  { section: 'PSYCHE', key: 'speech_style', label: 'speech / accent' },
  { section: 'OUTFIT', key: 'default_outfit_description', label: 'outfit (overall)' },
  { section: 'OUTFIT', key: 'top_description', label: 'top' },
  { section: 'OUTFIT', key: 'bottom_description', label: 'bottom' },
  { section: 'OUTFIT', key: 'shoes_description', label: 'shoes' },
  { section: 'OUTFIT', key: 'accessories', label: 'accessories' },
  { section: 'EXTRA', key: 'color_palette', label: 'color palette' },
  { section: 'EXTRA', key: 'props', label: 'props' },
];

const SECTIONS = ['CORE', 'PSYCHE', 'OUTFIT', 'EXTRA'];
const SCALAR_KEYS = SHEET_FIELDS.map((f) => f.key);

const SYSTEM_PREAMBLE = `Tu es un assistant qui construit une "character sheet" pour générer ensuite un prompt d'image (style cinematic concept art turnaround). Tu suis rigoureusement la MÉTHODE ci-dessous, qui est ta seule référence normative.

TOOLS DISPONIBLES (rappel technique):
- update_character_sheet(fields) : range une ou plusieurs infos dans les champs de la sheet.
- add_note(text) : ajoute une annotation visuelle courte au panel notes.
- request_input(field, question, input_type, options?, ...) : affiche un widget interactif (chips, multi_chips, slider, color_palette, text) pour éviter à l'utilisateur d'avoir à taper. Après un appel à request_input, N'APPELLE PLUS AUCUN AUTRE TOOL dans la même réponse — attends la réponse.

CHAMPS EXACTS À UTILISER (case-sensitive) :
- CORE : character_name, alias, gender, age, height, body_type, ethnicity, face_description, role, archetype
- PSYCHE : personality_traits, core_theme, emotional_range, behavior_notes, speech_style
- OUTFIT : default_outfit_description, top_description, bottom_description, shoes_description, accessories
- EXTRA : color_palette, props
- notes (via add_note, plusieurs possibles)

=== MÉTHODE (à suivre) ===

`;

const SHEET_PROPERTIES = Object.fromEntries(SCALAR_KEYS.map((k) => [k, { type: 'string' }]));

const TOOLS = [
  {
    name: 'update_character_sheet',
    description:
      "Met à jour un ou plusieurs champs de la character sheet. Utilise les noms de champs exacts. Appelle ce tool dès que tu apprends une info.",
    input_schema: {
      type: 'object',
      properties: SHEET_PROPERTIES,
      additionalProperties: false,
    },
  },
  {
    name: 'add_note',
    description: "Ajoute une petite annotation visuelle au panel notes (ex: 'manches retroussées', 'cheveux en bataille').",
    input_schema: {
      type: 'object',
      properties: { text: { type: 'string', description: 'Le texte de la note' } },
      required: ['text'],
      additionalProperties: false,
    },
  },
  {
    name: 'request_input',
    description:
      "Affiche un widget interactif (chips à cliquer, slider, color picker) pour que l'utilisateur réponde sans taper. Préfère TOUJOURS ce tool aux questions ouvertes quand le champ a un set fini de réponses raisonnables. Après l'appel, ATTENDS la réponse — n'appelle pas d'autre tool dans la même réponse.",
    input_schema: {
      type: 'object',
      properties: {
        field: {
          type: 'string',
          description: 'Nom du champ ciblé (ex: gender, age, role, color_palette).',
          enum: SCALAR_KEYS,
        },
        question: { type: 'string', description: "Question affichée au-dessus du widget. Concise." },
        input_type: {
          type: 'string',
          enum: ['chips', 'multi_chips', 'slider', 'color_palette', 'text'],
          description: "chips=choix unique, multi_chips=plusieurs choix, slider=numérique, color_palette=picker de couleurs, text=zone de texte courte.",
        },
        options: {
          type: 'array',
          items: { type: 'string' },
          description: 'Options pour chips/multi_chips (4-8 max).',
        },
        allow_custom: {
          type: 'boolean',
          description: "Si true, ajoute une option 'autre…' qui ouvre un champ texte. Recommandé pour chips quand la liste n'est pas exhaustive.",
        },
        slider_min: { type: 'number' },
        slider_max: { type: 'number' },
        slider_default: { type: 'number' },
        slider_unit: { type: 'string', description: "Suffixe (ex: 'ans', 'cm')." },
        placeholder: { type: 'string', description: "Pour input_type=text, placeholder du champ." },
      },
      required: ['field', 'question', 'input_type'],
      additionalProperties: false,
    },
  },
];

/* Le nom de chaque lot, tel que le §15 du brief les ordonne. */
const LOTS = {
  'LOT 2': 'Le front',
  'LOT 3': 'Vues et 3D',
  'LOT 4': 'Le rig',
  'LOT 5': "L'animation",
};

/* ── les étages de la chaîne ────────────────────────────────
   `ready` distingue ce qui tourne dans le navigateur de ce qui
   tourne en local sur le DGX, en ligne de commande : `needs` donne
   alors la commande `./usine` de l'étage. Le rack affiche la
   différence au lieu de la masquer.
   ─────────────────────────────────────────────────────────── */

const STAGES = [
  {
    id: 'identity', lot: 'LOT 2', ref: 'ST-01', name: 'Identité',
    sub: 'fiche · 21 champs · conversation',
    ready: true,
    blurb: "La fiche fait autorité sur tout ce qui suit. Décris le personnage, dépose des références, réponds aux widgets : le modèle range chaque information dans son champ.",
  },
  {
    id: 'face', lot: 'LOT 2', ref: 'ST-02', name: 'Visage',
    sub: 'portrait neutre · verrouillage',
    ready: false, needs: './usine visage <perso>',
    blurb: "Portrait neutre — lumière égale, bouche fermée, regard caméra, fond uni. Une fois validé il devient face_locked_url et sert de référence d'identité à toutes les générations suivantes.",
  },
  {
    id: 'costume', lot: 'LOT 2', ref: 'ST-03', name: 'Costumes',
    sub: 'N costumes · plein pied de référence',
    ready: false, needs: './usine costume <perso> <nom> · ./usine pleinpied <perso>',
    blurb: "N costumes indépendants par personnage. Le visage verrouillé reste la référence d'identité ; le costume vient d'images de vêtement, de texte, ou des deux.",
  },
  {
    id: 'sheet', lot: 'LOT 3', ref: 'ST-04', name: 'Planche',
    sub: 'H3 Ref2VA · 5 frames',
    ready: false, needs: './usine planche <perso> --ab',
    blurb: "La planche sert à valider le design et la cohérence, pas à nourrir la 3D : un panneau de planche 2K fait 400 à 500 px de large, c'est insuffisant pour Hunyuan3D.",
  },
  {
    id: 'views', lot: 'LOT 3', ref: 'ST-05', name: 'Vues orthogonales',
    sub: '0° · 90° · 180° · 270° · ±5°',
    ready: false, needs: './usine vues <perso> · prep · controle',
    blurb: "Une génération plein cadre par vue. Même lumière, angles alignés à ±5°, marge de silhouette constante. Des entrées mal alignées donnent un résultat pire qu'une seule image.",
  },
  {
    id: 'mesh', lot: 'LOT 3', ref: 'ST-06', name: 'Mesh 3D',
    sub: 'TRELLIS 2 · Hunyuan3D 2.1',
    ready: false, needs: './usine mesh <perso> · ./usine voir <perso>',
    blurb: "Deux moteurs derrière une même interface. TRELLIS 2 est MIT sans restriction ; Hunyuan3D 2.1 texture mieux mais porte une exclusion territoriale UE à lire ligne à ligne.",
  },
  {
    id: 'rig', lot: 'LOT 4', ref: 'ST-07', name: 'Rig',
    sub: 'UniRig · SOMA 77 · A-pose',
    ready: false, needs: './usine rig <perso> · ./usine voir <perso>',
    blurb: "Bind pose en A-pose, delta de bind stocké, conversion appliquée au retarget. On ne modélise jamais en T-pose : le skinning calculé à 90° fait remonter les épaules.",
  },
  {
    id: 'anim', lot: 'LOT 5', ref: 'ST-08', name: 'Animation',
    sub: 'Kimodo · SAM 3D Body · timeline',
    ready: false, needs: './usine prise · timeline · bake',
    blurb: "Pistes typées body / hands_l / hands_r / face, mixage par masque de joints, crossfade en quaternions. ALT + molette change l'échelle du temps.",
  },
];

export { SHEET_FIELDS, SECTIONS, SCALAR_KEYS, SYSTEM_PREAMBLE, TOOLS, STAGES, LOTS };
