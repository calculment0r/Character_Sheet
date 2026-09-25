# Passation — pour une session sur la machine

Ce document est écrit pour une session Claude Code lancée **sur le DGX**.
Il dit où en est le travail, ce qui a été vérifié et contre quoi, et ce
qui reste à faire, dans l'ordre.

Branche : `claude/epic-wright-y2kbrc`
Mode d'emploi : [`LOCAL.md`](./LOCAL.md)
Cadrage complet : [`BRIEF_CHARACTER_FACTORY.md`](./BRIEF_CHARACTER_FACTORY.md)
État technique : [`ARCHITECTURE.md`](./ARCHITECTURE.md)

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
