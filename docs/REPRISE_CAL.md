# Reprise — à lire en premier (état au 27/09/2026, soir)

Cal a arrêté la journée, mécontent. Il va **briefer à nouveau** la
prochaine session : on ne construit rien avant son brief. Commencer par
lire ce document, relire `CLAUDE.md`, puis lui demander son brief et le
noter.

## Ce que Cal a dit en fin de journée (mot pour mot ou presque)

- « Les images créées pour David : il y a des mains à l'envers, les
  portraits en grand ont une peau plastique etc. »
- « L'UX est nulle aussi… on a envie d'**une seule page avec les trucs de
  notre character**. »
- « La création du character est aussi super mal pensée, je vais devoir te
  briefer car tu comprends mal et fais des trucs nuls. »
- Sur la 3D : « c'est vraiment super moche et il a encore 4 bras… les gens
  y arrivent super facilement et ont des modèles assez fidèles. »
- Sur la mise en page : « plein de trucs pas lisibles car ça se
  superpose » (il avait joint une capture, jamais reçue).

## Règles de travail (Cal, répétées)

- **Uniquement ssh / curl / scp** vers `dgx1` et `dgx2`, et Edge headless
  (playwright-core) depuis Bash. Jamais le navigateur intégré ni Chrome ni
  un outil qui lui demande une autorisation.
- **Le lien pour tester à chaque réponse** : http://192.168.10.247:8765/
  (Tailscale http://100.108.108.65:8765/), vers le personnage concerné
  (`#/p/<slug>`).
- **Chercher avant de faire** : sessions passées, mémoire, artifacts de
  Cal, gabarits ComfyUI de DGX2, Hugging Face, Civitai, Reddit. Cal
  déteste qu'on réinvente ou qu'on bricole moins bien que « les gens ».
- Réponses en français, courtes, factuelles ; montrer des images ; dire
  honnêtement ce qui est moche.
- Mémoire : `C:\Users\calcu\.claude\projects\C--Character-Factory\memory\`
  (MEMORY.md et ses fiches).

## Où en est le code (branche `claude/epic-wright-y2kbrc`, poussée)

Fusionné et vérifié (`python tools/chain_check.py` 61/61) :
- plein pied en pose naturelle (Qwen-Image 2.1 turbo) ; A-pose par
  squelette DWPose ; vues guidées par squelette et **mesurées par SAM 3D
  Body** avec relance ; planche de référence Qwen (face/dos/gros plan) ;
- mesh TRELLIS.2 : décimation auto à 50 000 faces ; par défaut **face +
  dos seulement** (les 4 vues faisaient des bras en trop) et couleur
  reprise des vues (`factory/texproject.py`) — le résultat n'a **pas** été
  montré à Cal ; le mesh reste jugé moche ; un banc des moteurs (TRELLIS.2
  bf16 image seule, Hunyuan3D 2.1 + Paint, Hunyuan3D-2mv, Pixal3D) a été
  lancé puis **arrêté avant résultat** (branche éventuelle
  `claude/cf-mesh2`, travail sur DGX1 `~/cf_mesh`) ;
- rig UniRig réel sur DGX2 (SOMA 77, bind A-pose, 5 poses de contrôle
  vérifiées) — rig v1 d'essai-atelier fait, non accepté ;
- pilote automatique des étages techniques (`factory/autopilot.py`,
  validation par mesure, « Ce qui attend ») ;
- planche de présentation (`factory/presentation.py` : 6 expressions,
  5 poses naturelles, détails SeedVR2, palette, composition 3840×2160) —
  **Cal la juge mauvaise** (mains à l'envers, peau plastique) ;
- nouvelle interface « Casting · volets Identité/Visage/Garde-robe/Voix/
  Planche · Scène » (`js/studio.js`, `js/parler.js`) — **Cal la juge
  nulle** : il veut une seule page par personnage ; des chevauchements de
  texte restent (noms longs coupés sur les affiches, pastille d'en-tête,
  en-tête des coulisses, Identité en 390 px) ;
- **Coulisses** (`coulisses.html`) : panneau de debug de Cal, tout ce que
  l'utilisateur ne voit pas — Cal l'a demandé, à garder.

Non fusionné, arrêté en cours :
- **voix** (branche `claude/cf-voice` si poussée, worktree
  `.claude/worktrees/agent-a8aa2de0e85823230`) : Qwen3-TTS VoiceDesign /
  Base, chat STT (Kyutai) → LLM → TTS, service `factory.voice_server`
  testé sur DGX1 (env `~/cf-voice-env`). Contrat d'API prévu : actions
  voice_design / voice_lock / voice_unlock / line / line_keep, POST
  /api/characters/<slug>/chat, GET /api/voice/config. Études voix de Cal :
  artifacts 9168zq2W4ZCy835ZCAJr5Y, 653eYQsS8aZUygEKLc7icB,
  FW7QtAgfvvunhPdUpcTb35, ThXjhqEQLq7cnEdgXPP9sP, 9F5JpD2vSnKm4hcoKQ9Lw9 ;
- correctifs de mise en page de l'interface (worktree
  `.claude/worktrees/agent-a1759431a6efd5a8e`).

Études du jour : `docs/ETUDES.md` (pose, vues, planche, mesh, autopilote),
et la synthèse UX/planche/voix :
`C:\Users\calcu\AppData\Local\Temp\claude\C--Character-Factory\289efc68-c625-4b85-aaff-f0fe2c65cae4\scratchpad\etudes_ux_27-09.md`
(à recopier dans le dépôt si elle sert).

## État des machines

- DGX2 : **studio arrêté** ; ComfyUI :8188 et :8189 vidés (modèles
  déchargés) ; Ollama vide. Relancer le studio :
  `cd ~/Character_Factory && git pull && PYTHONUNBUFFERED=1 setsid nohup ./usine studio > studio.log 2>&1 < /dev/null &`
  (pour l'arrêter : `pkill -f "[m] factory studio"`).
  `factory.local.json` de DGX2 a UniRig branché (`"unirig": "python"`).
- DGX1 : serveurs voix et Kyutai de la session voix arrêtés, modèles
  Ollama et ComfyUI déchargés ; le serveur de diarisation « reelbench »
  (port 8448) n'est pas à nous, laissé tel quel.
- Personnage de travail : `essai-atelier` (« David »), visage, tenue
  « veryday », plein pied, A-pose, vues mesurées, meshes v1–v3 (v2/v3 ont
  des bras en trop), rig v1, expressions de présentation partielles.

## Première chose à faire à la reprise

1. Lire ce fichier, `CLAUDE.md`, la mémoire.
2. Demander à Cal son brief (création du personnage, page unique, ce qu'il
   attend de la planche, de la 3D, de la voix) ; le noter tel quel dans
   `docs/BRIEF_CAL_<date>.md`.
3. Proposer un plan court sur cette base, avec les études à mener
   (chercher d'abord ce que font les meilleurs), avant de coder.
