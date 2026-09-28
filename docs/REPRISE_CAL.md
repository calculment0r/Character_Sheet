# Reprise — à lire en premier (état au 28/09/2026, fin d'après-midi)

La session du 28/09 s'arrête parce qu'elle est pleine, pas sur un échec :
Cal a dit « ça marche assez bien tout cela, bravo ». La suite est **la
page d'un personnage**, fait ou en train de se faire (voir « Première
chose à faire »). Lire ce fichier, `CLAUDE.md`, la mémoire
(`C:\Users\calcu\.claude\projects\C--Character-Factory\memory\`), puis
`docs/BRIEF_CAL_2026-09-28.md` (tout ce que Cal a dit le 28/09, mot pour mot).

## Règles de travail (Cal, répétées)

- **ssh / curl / scp seulement** vers `dgx1` et `dgx2`, Edge sans affichage
  depuis Bash pour voir une page (`msedge --headless=new --screenshot=… URL`
  suffit, sans playwright). Jamais le navigateur intégré, ni Chrome, ni un
  outil qui demande une autorisation à Cal. Le dire à chaque sous-agent.
- **On travaille sur DGX1** depuis le 28/09 ; **DGX2 est son miroir exact**
  (code, modèles, nœuds, environnements) pour pouvoir lancer sur les deux.
  Câble direct 200 Gb : DGX1 169.254.110.6 ↔ DGX2 169.254.42.193 (rsync entre
  eux, jamais par le PC). `factory.local.json` diffère par machine : ne pas
  le recopier ; le studio le garde en cache → le redémarrer après l'avoir
  modifié.
- **Le lien pour tester à chaque réponse** : http://192.168.10.247:8765/
  (Tailscale http://100.108.108.65:8765/), vers le personnage (`#/p/<slug>`) ;
  la file des rendus : `coulisses.html#/file`. Le studio tourne sur DGX1,
  mais le Wi-Fi de DGX1 est en 2,4 GHz (28/09 : 135 ms, 5 % de pertes,
  des pages en plusieurs secondes) : on passe par DGX2 (5 GHz, 9 ms), où
  `tools/relay.py` renvoie vers DGX1 par le câble direct. Le relais ne
  survit pas à un redémarrage : `cd ~/Character_Factory && setsid nohup
  python3 tools/relay.py 8765 169.254.110.6:8765 > ~/relay_studio.log 2>&1
  < /dev/null &` sur DGX2.
- **Chercher avant de faire** (sessions passées, mémoire, artifacts, gabarits
  ComfyUI, Hugging Face, Civitai…). Cal déteste qu'on réinvente.
- Réponses en français, courtes, factuelles ; montrer des images ; dire ce
  qui est moche. Télécharger un fichier : demander d'abord (nom, source,
  taille) — Cal a dit oui le 28/09 pour ceux listés dans `docs/ETUDES.md` §8.
- Une **autre session de Cal installe OmniChar (`.char`)** sur les deux DGX :
  ne pas le faire ici ; ne pas redémarrer le ComfyUI :8188 pendant qu'il
  calcule pour elle.

## Ce qui a été fait le 28/09 (branche `claude/epic-wright-y2kbrc`, poussée, tirée sur les deux DGX)

- **Krea 2 remplace Qwen-Image 2.1** pour tout ce qui se voit : visage,
  plein pied (photo en pied + visage verrouillé reporté sur la tête),
  expressions et poses de la planche (tirées du plein pied validé, tenue
  gardée). `factory/krea2.py`. Photos : ordonnanceur beta, LoRA UltraReal à
  0,7, lumière décrite, second passage à 0,25 pour le plein pied. Retouches :
  Identity Edit **v1.2** par les nœuds `comfyui-krea2edit` v1.2.5. Qwen 2.1
  ne fait plus que l'A-pose et les vues. Tout le banc : `docs/ETUDES.md` §8.
- **Import d'une image** (Midjourney, plein pied ou planche, illustré ou
  3D) : `factory/importer.py`, action `import` du studio. Lecture par le
  modèle de texte (style, identité, tenue, taille), extraction du personnage
  seul de face, visage, puis l'autopilote jusqu'au rig. Un personnage
  illustré garde son style (pas de LoRA photo, prompts d'A-pose et de vues
  sans « photograph »).
- **Bras trop longs corrigés** (Cal : « quasiment tous des bras beaucoup
  trop longs ») : le squelette de pose était cadré sur le nez et les
  chevilles ; le modèle rétrécissait le corps en gardant les mains sur les
  poignets. Il est maintenant cadré sur la silhouette réelle
  (`tools/remote/apose_skeleton.py`). Mesuré : bras à ±5 % du plein pied,
  contre +14 à +29 % avant. Touche aussi David (essai-atelier) : à refaire.
- **File des rendus** (`coulisses.html#/file`) : arrêter, relancer,
  autopilotes en pause/repris ; **Détruire** un personnage depuis les
  Coulisses (dossier vers `projects/.corbeille/`) ; tous les titres
  ramènent au studio ; le viewer a « ← Fiche » et « Coulisses ».
- **Orbite 360°** : LoRA pablodawson sur H3 FL2VA, essai réussi sur le
  Costaud (tour complet, retour sur l'image de départ) ; turbo 8 pas en
  6 min = 28 pas en 18 min. Script : `tools/remote/orbit360.py` (pas encore
  un étage, pas encore dans l'interface). Vidéos : `dgx1:~/cf_orbit/`.
- **Miroir DGX1** : dépôt, modèles, FaceNet, Ollama `qwen3-vl-32b-32k`,
  UniRig + Python 3.11 (deadsnakes, accord de Cal) + ses poids HF.

## En cours au moment de la coupure (DGX1, studio lancé)

Les six personnages MJ (`mj-survet`, `mj-chauve`, `mj-manteau`,
`mj-marionnette`, `mj-costaud`, `mj-garcon`) passent par l'autopilote avec le
squelette corrigé : Chauve et Costaud finis (rig accepté), Garçon et
Marionnette rig fait, Manteau et Survêt au mesh. Vérifier dans
`coulisses.html#/file`. Rendus des meshes : `tools/remote/mesh_render.py`
(python de ComfyUI).

Défauts vus, pas encore traités :
- texture des meshes : les flancs sont mal couverts (bande couleur peau le
  long du pantalon de Chauve) — la couleur ne vient que des vues face/dos ;
- l'extraction normalise les proportions exagérées (épaules en blocs de
  Chauve, bras énormes du Costaud) ;
- Chauve a son mesh à 1,75 m (fait avant que la taille soit renseignée) ;
- `krea2edit` v1.2.5 n'est chargé que sur DGX2 : redémarrer `comfyui.service`
  de DGX1 à un moment calme (sinon import et planche échouent sur DGX1) ;
- la planche de présentation : Cal la trouve « débile » (silhouette vide à
  côté du personnage en pied, blanc au-dessus des têtes, poses et
  expressions pas naturelles) — à refaire avec la page personnage.

## Première chose à faire à la reprise : la page d'un personnage

Cal, 28/09 : « je voudrais quand même qu'on réfléchisse à la page de nos
persos quand ils sont faits ou en train de se faire.. tu as tout découpé en
onglets et j'aimerais avoir un design qui est mieux ». Et plus tôt :
« on doit être plein écran ok pour chaque étape mais on doit aussi avoir une
fiche perso hyper belle et claire avec tous les éléments dont le user a
besoin sur la même page » ; « on a même pas les options pour changer ou
éditer des trucs.. ça fait des visages et on peut que valider ou pas
valider » ; « j'avais fait une étude de l'expérience utilisateur et je trouve
que tu as pas du tout bien intégré le truc ».

Son étude n'a pas été retrouvée sous ce nom. Le candidat le plus probable,
**à lui faire confirmer d'abord** : le corpus « Character Forge » de son
dépôt privé `calculment0r/X-VERSE` (`ETUDE-W05-CONCEPTION-ECRAN-FORGE`,
`ETUDE-W06-DESIGN-GLOBAL-ONGLET-WORLD`, `07_EXPERIENCE_ET_DESIGN`,
`_INGEST/ETUDE-CAL-INTERACTION-VALEUR-USAGE-V0.2.md`, maquette
`MAQUETTE-CHARACTER-FORGE.html`, artifact
https://claude.ai/code/artifact/d68a2fc3-ac4c-4a57-ae0c-9a55f871c277) : une
fiche toujours visible à gauche, des surfaces plein écran au centre,
épingler / écarter / régénérer / importer partout, jamais un formulaire.
Notes détaillées (locales, hors dépôt public) :
`C:\Character_Factory\.claude\handoff\ux_study_found.md`.

Démarche : 1) faire confirmer l'étude à Cal ; 2) la lire en entier ;
3) proposer une maquette (artifact) de la page personnage — fait / en train
de se faire — avant de coder ; 4) coder dans `studio.html` / `js/studio.js`
en gardant les Coulisses pour Cal.

## Machines

- DGX1 : studio (`cd ~/Character_Factory && PYTHONUNBUFFERED=1 setsid nohup ./usine studio > studio.log 2>&1 < /dev/null &`,
  arrêt `pkill -f "[m] factory studio"`), ComfyUI :8188 (`~/ComfyUI/venv`),
  H3 :8189, Ollama.
- DGX2 : studio arrêté, miroir à jour (même commit) ; UniRig et Python 3.11
  identiques.
