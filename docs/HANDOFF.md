# Passation — pour une session sur la machine

Ce document est écrit pour une session Claude Code lancée **sur le DGX**.
Il dit où en est le travail, ce qui a été vérifié et contre quoi, et ce
qui reste à faire, dans l'ordre.

Branche : `claude/epic-wright-y2kbrc`
Mode d'emploi : [`LOCAL.md`](./LOCAL.md)
Cadrage complet : [`BRIEF_CHARACTER_FACTORY.md`](./BRIEF_CHARACTER_FACTORY.md)
État technique : [`ARCHITECTURE.md`](./ARCHITECTURE.md)

---

## 0. REPRENDRE ICI — le studio tourne sur DGX2 (état au 25/09/2026)

**L'atelier (25/09, après-midi — retours de Cal sur Kévin)**. Cal a
jugé le premier studio mauvais à l'usage : démarrer par le questionnaire
de 21 champs est frustrant, les images H3 du visage sont laides, le
détourage est crénelé. Ce qui a changé :

- **Créer = un nom.** L'accueil n'a qu'un champ. Le nom se corrige d'un
  clic dans l'en-tête (le dossier garde son slug).
- **Une étape à la fois** : frise Visage · Costume · Planche · Vues · 3D ·
  Rig ; l'atelier passe seul à l'étape suivante quand une étape est
  validée.
- **On décrit ce qu'on veut voir**, en français. `factory/brief.py` fait
  lire le brief par le modèle de texte (Ollama `/api/chat`, sortie JSON
  contrainte) : prompt d'image en anglais limité à ce que l'étage montre,
  **quatre propositions distinctes** pour le visage (Z-Image varie peu
  d'une graine à l'autre), et les champs de la fiche. Nouveau champ
  `face_description`. « Autour » relance autour d'une proposition.
- **Visage par un modèle d'image** (`factory/portrait.py`) : Z-Image Turbo
  par défaut (8 s, 20 Go, cohabite avec le modèle de texte), FLUX.2 dev
  (73 s, 75 Go, le plus fidèle), Qwen-Image 2.1 (40 s), H3 pour normaliser
  une photo. Banc du 25/09 sur Kévin : tous très au-dessus de H3. Le
  prompt du visage n'a plus ni rôle, ni archétype, ni notes : chez Kévin,
  « skateur » + une note « casque porté en arrière » donnaient casque et
  lunettes de ski.
- **Le temps de calcul sert** : un travail et les modifications faites
  pendant ce travail partagent le même `Project` en mémoire (`Studio.live`,
  verrou dans `Project.save`) ; plus de refus « un travail tourne ». La
  carte « En attendant » propose la tenue, des traits de caractère,
  l'assistant (sauf pendant H3, qui prend 100 Go).
- **Mémoire par famille** (`memory.FAMILY_GB`) : le modèle de texte n'est
  déchargé que si la famille ne tient pas à côté.
- **Détourage** : `imaging.with_mask` coupait le masque BiRefNet par des
  blocs de 5 px (marches d'escalier) et le tranchait à 50 %. Il garde
  maintenant le bord doux de BiRefNet (étiré : BiRefNet plafonne à 254),
  et retire le fond des pixels de bord. SAM 3 n'est pas installé et ne
  ferait pas mieux sur les bords : c'est de la segmentation, pas du
  matting ; si les cheveux manquent de finesse, essayer BiRefNet HR
  matting.
- Essai réel sur DGX2 : « Essai atelier », brief lu en 42 s (chargement
  compris), quatre visages Z-Image en 44 s, modèle de texte resté chargé.

**Reste à faire** : le costume par brief n'a pas encore tourné pour de
vrai (plein pied H3 à partir d'un prompt écrit par le modèle) ; l'orbite
et la suite passent encore par l'ancienne interface de bloc ; `doctor`
ne valide pas encore les gabarits de portrait ; H3 garde son rendu dur
pour le plein pied et la planche (piste : upscale/SUPIR, ou FLUX.2 Kontext
pour le plein pied).

**On ne travaille que sur DGX2** (Cal, 25/09). Le PC sert à écrire le
code et à ouvrir la page ; tout calcule sur DGX2.

**Le studio existe et tourne** : `./usine studio` sur DGX2
(`~/Character_Factory`, venv `.venv`, lancé par
`PYTHONUNBUFFERED=1 setsid nohup ./usine studio > studio.log 2>&1 < /dev/null &`),
ouvert depuis le PC sur **http://192.168.10.247:8765/** (Tailscale :
`100.108.108.65:8765`). Mode d'emploi : `LOCAL.md`, section « Le studio ».

- `factory/studio.py` : serveur stdlib, une file à un ouvrier, les
  étages de `chain.py` appelés tels quels ; les choix rapides
  (verrouiller, valider) sont refusés pendant un travail sur le même
  personnage. Relais `/v1/…` vers Ollama, le modèle imposé par le studio.
- `studio.html` + `js/studio.js` + `assets/studio.css` (montrés dans
  `theme.html`) : cartes, page personnage un bloc par étage, l'étape
  suivante en orange, file de travaux relevée toutes les 1,5 s.
- `console.html?new=1` / `?slug=<perso>` : la conversation crée le
  personnage dès qu'il a un nom, puis enregistre fiche, notes et
  conversation (sans les images) après chaque tour.
- `factory/memory.py` branché : avant un travail, déchargement d'Ollama
  et vidage du ComfyUI inutile (file vide seulement) ; avant une
  conversation sans calcul en cours, vidage de ComfyUI si la place
  manque. **H3 résident prend ~100 Go** (50 Go GPU + 53 Go de RAM pour
  son encodeur de texte) : H3 et le modèle de texte alternent.
- **Deux instances ComfyUI** : H3 sur `:8189` (H3TEST, qui a Spectrum et
  Sol-Attn), le reste sur `:8188`. `h3.py` suit maintenant
  `comfyui_url_h3` (il prenait `comfyui_url`). Le nœud d'aperçu
  `ModelPreviewOverrideKJ` est court-circuité dans les gabarits H3 (son
  entrée `tiny_vae` n'existe pas sur DGX2) et par `./usine gabarit`.
- **Modèle de texte : `qwen3-vl-32b-32k`**, dérivé de
  `qwen3-vl:32b-instruct` par `ollama create` avec `num_ctx 32768`
  (sans ce réglage, Ollama réserve 262k de contexte : 48,8 Go). Banc du
  25/09 sur un même tour de console, deux essais : seul le 32B dense
  appelle `update_character_sheet` à chaque fois (26–34 s par tour) ; le
  30B-A3B et mistral-small 3.2 récitent la fiche en texte.
- `Project.rel` écrit des chemins POSIX ; `Project.path` lit les anciens
  `\` d'un manifeste Windows (celui de Maren a été converti).

**Vérifié** : `chain_check` 43/43 sur le PC et sur DGX2, dont six
vérifications du studio par son API (création, file, refus, fichiers,
relais) ; la page entière dans Edge headless (playwright-core,
moteurs factices) : un seul orange par écran, pas d'erreur, pas de
débordement à 390 px ; `./usine doctor` sur DGX2 : tous les gabarits
valides (H3 sur `:8189`, BiRefNet, TRELLIS, SAM 3D Body, Qwen sur
`:8188`). **De vrai sur DGX2** : Maren copiée du PC ; Ilse Varga créée
par l'API, deux visages H3 en 52 s (44 s le premier, chargement
compris, 6 s le second) ; un tour de conversation après H3 : ComfyUI
vidé, modèle de texte chargé, fiche remplie par les outils, 44 s.

**À faire ensuite, dans le studio** : mener Ilse (ou un personnage de
Cal) jusqu'aux vues préparées sur DGX2 — verrouillage, costume, plein
pied, planche, orbite, prep, contrôle. Puis, sur feu vert de Cal, le
mesh TRELLIS.2 de Maren (étape suivante de sa carte).

**Limites connues** : la file et les journaux vivent en mémoire (un
redémarrage du studio les oublie, pas les personnages) ; pas de service
systemd (le studio se relance à la main) ; pas d'authentification (réseau
de la maison et Tailscale) ; l'annulation d'un travail en cours
n'interrompt que les calculs ComfyUI lancés par la chaîne.

**Toujours en attente de Cal** : licence Llama-3 (Kimodo) ; numpy de
DGX2 pour Hunyuan3D ; feu vert pour le banc des vues Qwen, TRELLIS.2 et
UniRig ; disque sur le visage.

**Façon de travailler (retours de Cal)** : chercher d'abord un modèle ou
un outil fait pour le geste (Hugging Face, modèles de workflows ComfyUI)
avant de bricoler ; télécharger directement sur les DGX sans demander ;
passer par ssh/curl, jamais par le navigateur intégré (autorisations) ;
un seul gros calcul par machine ; tenir la page d'état publiée à jour
(`CLAUDE.md`, section GitHub Pages). Sur DGX2, `pkill -f "factory studio"`
tue aussi la session ssh qui le lance : écrire `pkill -f "[f]actory studio"`.

---

## 1. Les décisions de Cal

- **Tout en local, pas d'API pour l'instant.** La chaîne est `./usine`,
  une commande par étage, un dossier par personnage. `api/` reste pour
  plus tard, sans être étendue.
- **H3 en local, 768 px** — il tourne déjà, **dans ComfyUI**.
- **Outil interne** : les fontes commerciales restent dans le dépôt.
- Thème sombre NL, tel quel.

---

## 2. Ce qui a été vérifié, et contre quoi

La session qui a écrit ce code tournait dans un conteneur cloud, sans
accès aux DGX. Vérifié dans ce conteneur :

- la chaîne entière, du visage à la timeline cuite, sur les moteurs
  factices — `tools/chain_check.py`, 29 vérifications ;
- le client ComfyUI, contre un **faux** ComfyUI (`tools/mock_comfy.py`) :
  envoi des références, remplissage du gabarit, retrait des nœuds REF
  inutiles, rapatriement des frames, choix de la plus nette ;
- `./usine gabarit` sur un export API écrit pour l'occasion ;
- le rig : le skinning glTF rejoué en numpy, et dans le viewer — les
  cinq poses de contrôle déforment le mesh comme attendu ;
- le viewer, dans Chromium, sur les fichiers de la chaîne ;
- la page, étage Identité, contre un faux modèle de texte.

**Jamais testé** : un vrai ComfyUI avec H3, un vrai GPU, et aucun des
modèles de l'aval. Chaque réglage par défaut est une hypothèse.

### 2 bis. Vérifié sur la machine, le 24/09/2026

La chaîne tourne sur le PC de Cal (Windows, Git Bash, venv du dépôt) et
parle au ComfyUI `ComfyUI-H3TEST` de DGX1 (`192.168.10.205:8189`),
réglé dans `factory.local.json`. `chain_check` : 32/32 sous Windows.

- **H3, de vrai** : le workflow Ref2VA natif qui tournait déjà sur DGX1
  (export API relevé dans les métadonnées d'une de ses images) est
  adopté par `./usine gabarit` → `workflows/h3_ref2va.json`.
- Ce que le vrai nœud impose, et que le code suit désormais : tailles
  par pas de 32 (1344, pas 1360), frames sur la grille 17k + 5 (5 pour
  une image, 124 pour l'orbite), neuf références au plus, nommées
  `<Picture 1…n>` dans le prompt.
- Les prompts suivent le guide Ref2VA que MiniMax livre avec les poids
  (`<Subject n>`, `[reference generation]`, rétention par sujet,
  `[Shot 1]`).
- Sur un personnage de test (`maren-ostrova`, photoréaliste, costume
  décrit sans image) : visage, plein pied, planche A/B et vues rendus
  par H3. La ruse des cinq frames marche. Compter 20 à 75 s par image,
  modèle résident ; le premier appel charge ~50 Go.
- **Vues : une génération par vue ne tient pas les angles.** Seule, H3
  revient vers la pose de face du plein pied : « gauche 90° » sort vers
  55°, le 3/4 presque de face. Un prompt plus explicite (nez, pieds, bras
  caché) a fait pire (35°) ; sans le plein pied en référence, tout sort de
  face. La planche, elle, a de vrais profils et un vrai dos.
- **L'orbite marche** (`./usine vues --orbite`, 124 frames, 7 min) : un
  vrai tour de 360°, identité et costume tenus. Mais la caméra ne tourne
  pas à vitesse constante (départ lent : profil gauche vers la frame 52,
  dos vers 76, profil droit vers 97, au lieu de 31, 62, 93) : le
  redécoupage « au prorata » prend de mauvaises frames. Il faut choisir
  les frames sur une mesure (largeur de silhouette ou SAM 3D Body).
- **Le détourage intégré ne tient pas les fonds H3** : dégradé, vignetage
  et grande ombre douce du sujet sur le fond. Un fond modélisé en surface
  lisse ne suffit pas non plus (essayé, retiré). Il faut BiRefNet.
- Le contrôle ±5° ne porte encore que sur l'angle demandé ou supposé :
  il laisse passer ces vues fausses.

### 2 ter. Corrigé et câblé le même jour

- **Détourage** : BiRefNet par ComfyUI (moteur `prep` = `comfyui`, par
  défaut). Vérifié sur les vraies vues : ombres et dégradé partis.
- **Orbite** : BiRefNet tourne sur les 124 frames dans le même workflow,
  les frames se choisissent sur la largeur de silhouette
  (`imaging.orbit_picks`). Vérifié sur maren-ostrova : face 0, profil
  gauche 48 (±6°), dos 78 (±4,7°), profil droit 97 (±2,4°), 3/4 à 46° ;
  `prep` puis `controle` passent, et les vues préparées sont justes à
  l'œil. Le sens de rotation (profil gauche d'abord) est celui du
  prompt, supposé, pas mesuré.
- **Poids installés** sur DGX1 et DGX2 (`~/ComfyUI/models`, partagé
  avec `ComfyUI-H3TEST`) : BiRefNet, TRELLIS.2 (image unique et
  multi-vues Pixal3D, VAE forme et texture, DINOv3), SAM 3D Body.
  Copie DGX1 → DGX2 par le câble direct (169.254.x), 330 Mo/s.
- **Câblé, jamais lancé** (Cal : « on câble uniquement pour l'instant ») :
  mesh TRELLIS.2 par ComfyUI sur DGX2 (`mesh_comfy.py`, gabarits
  `trellis2_mv.json` et `trellis2_single.json`, remise en mètres / Y en
  haut / pieds au sol) ; mesure d'azimut SAM 3D Body (`sam3d.py`,
  `./usine controle --mesurer`, signe à confirmer) ; appel des modèles
  par ssh (`remote.py`, testé à vide) ; Kimodo (`motion_kimodo.py`).
  `./usine doctor` valide tous les gabarits à blanc sur leur machine.
- **Hunyuan3D 2.1** : le nœud de DGX2 ne se charge pas (« Numba needs
  NumPy 2.4 or less. Got NumPy 2.5 ») ; le réparer demande de toucher
  l'environnement ComfyUI partagé de DGX2 — pas fait sans Cal. Le
  modèle natif de ComfyUI ne fait que la forme, sans texture.
- **Kimodo** installé sur DGX2 (`~/kimodo/.venv`, commit 58e7818, poids
  Kimodo-SOMA-RP-v1 — l'alias par défaut `kimodo-soma-rp` pointe sur la
  v1.1), script `tools/remote/kimodo_entry.py`. **Bloqué** : l'encodeur
  de texte s'appuie sur `meta-llama/Meta-Llama-3-8B-Instruct`, dépôt à
  accès restreint ; Cal doit accepter la licence Meta sur Hugging Face,
  puis on télécharge (~16 Go). MotionCorrection (anti-glissement des
  pieds) a été porté sur aarch64 (sse2neon), jamais exécuté.
- **UniRig** installé sur DGX2 (`~/UniRig/.venv`, Python 3.11, spconv,
  torch-scatter/cluster et flash-attn compilés pour sm_120/121), **sans
  Blender** : `bpy` n'existe pas pour linux-aarch64, et il ne sert qu'à
  l'import et à l'export ; `tools/remote/unirig_entry.py` fait l'import
  en numpy/trimesh et reporte les poids sur les sommets d'entrée par le
  `reskin()` d'UniRig. Jamais exécuté.
- **Écart au brief §10.3** : le modèle UniRig publié nomme ses os
  `bone_0…` sans sens, en nombre variable ; une table de noms fixe ne
  peut pas marcher. `rig_unirig.match_soma` reconnaît le squelette sur
  sa topologie et sa géométrie, par des règles fixes (bassin à trois
  branches, thorax, mi-chemin des chaînes, avant lu aux pieds), et remet
  le personnage face à +Z si le mesh regarde ailleurs. Vérifié sur des
  squelettes synthétiques anonymes (`chain_check`), pas sur UniRig.
- Licence : l'encodeur Michelangelo d'UniRig (`src/model/michelangelo/`)
  est GPL-3.0, le reste MIT.
- **Reste ouvert** : SAM 3D Body vers SOMA pour la vidéo (route MHR →
  SOMA à trancher, §11.2) ; Hunyuan3D 2.1 (numpy de DGX2).

---

## 3. À faire en premier, dans cet ordre

### a. L'inventaire

```sh
pip install -r requirements.txt
./usine doctor
```

Regarde le GPU, torch, ComfyUI et ses nœuds, les paquets des moteurs.
Ne touche à rien. `--ecrire` range ce qu'il a trouvé dans
`factory.local.json`.

### b. H3 par ComfyUI

Dans ComfyUI : ouvrir le workflow H3 Ref2VA qui donne de bonnes images,
**Workflow → Export (API)**, puis

```sh
./usine gabarit export.json
```

Relire la liste des changements qu'il affiche. Une sortie vidéo passe
(ffmpeg en tire les frames) ; un SaveImage sur les frames décodées évite
la recompression.

### c. Le premier personnage réel

```sh
./usine page                          # étage Identité, puis « Exporter l'identité .json »
./usine nouveau --identite <fichier>
./usine visage <perso> --variantes 4
```

C'est le premier contact avec H3. **S'attendre à ce que ça casse là** :
nom des champs du nœud H3, format du prompt attendu (les six sections
sont envoyées en texte, `section:\ncontenu`), nombre minimal de frames.
Tout se corrige dans le gabarit ou dans `factory/prompts.py`.

Puis la suite, étage par étage, jusqu'aux vues préparées : `LOCAL.md`.

### d. L'aval, capacité par capacité

Chaque capacité tourne en factice tant que son moteur n'est pas branché.
L'ordre du brief (§15) : TRELLIS 2 puis Hunyuan3D, delight, UniRig,
Kimodo, SAM 3D Body.

---

## 4. Ce qui reste ouvert

- **Le banc des vues** (§6.1) : une génération par vue contre une orbite
  redécoupée. Les deux méthodes existent (`./usine vues`, `--orbite`) ;
  le critère — écart angulaire mesuré, dérive d'identité — demande un
  estimateur de pose, que SAM 3D Body fournira.
- **Le contrôle d'angle mesuré** : tant qu'aucun estimateur ne regarde
  les images, le contrôle porte sur les angles demandés, et le dit.
- **MHR → SOMA** (§11.2) : à trancher avec SAM 3D Body.
- **Le disque sur le visage** : `./usine planche --ab`, à juger sur pièces.
- **La base paramétrique** du §10.4 — SOMA comme corps, sans mesh
  généré — n'est pas encore une option de `./usine rig`.

---

## 5. Carte du dépôt

```
usine                   la commande : ./usine <étage> …
factory/                la chaîne, en Python — voir ARCHITECTURE.md
workflows/              gabarits ComfyUI au format API (./usine gabarit)
data/soma77.json        squelette SOMA 77, relevé dans Kimodo
data/hand_poses.json    poses de main figées
data/methodology.md     la méthode de l'étage Identité
viewer.html             le viewer 3D, page locale
index.html              la page d'état, racine du site publié
console.html            la console : étage Identité
theme.html              le catalogue du thème
tools/chain_check.py    la chaîne de bout en bout, moteurs factices
tools/mock_comfy.py     un faux ComfyUI
tools/e2e.mjs           la page de bout en bout, dans un navigateur
api/ · start.sh · check.sh · docs/CLOUDFLARE.md
                        la version serveur, gardée pour plus tard
```
