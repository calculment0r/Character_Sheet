# REPRISE — Character Factory, session locale

Brief de reprise pour une session Claude Code lancée **sur la machine de
Cal (le DGX)**. Il se suffit à lui-même : il dit où est le code, ce que
Cal a décidé, ce qui existe, ce qui est vérifié, ce qu'il faut
installer et dans quel ordre avancer.

---

## Pour Cal — comment s'en servir

1. Sur la machine, dans un dossier vide :
   ```sh
   mkdir -p ~/character-factory && cd ~/character-factory
   # y déposer ce fichier, REPRISE.md
   claude
   ```
   (Si Claude Code n'est pas installé : `npm install -g @anthropic-ai/claude-code`,
   Node 18 ou plus.)
2. Premier message à la session : **« Lis REPRISE.md et fais la reprise. »**
3. Elle clone le dépôt, installe, vérifie, puis avance étage par étage.
   Elle demandera la permission avant les commandes : accepter.

Le code n'est sur aucun ordinateur en particulier : tout est sur GitHub.

---

## 0. En deux mots

Character Factory : **prompt ou photo → visage verrouillé → costumes →
planche → vues orthogonales → mesh PBR → rig SOMA → animation.**

La chaîne est écrite, en Python, pilotée par `./usine <commande>`, et
**tourne de bout en bout** — mais sur des moteurs factices, sauf H3 qui
passe par ComfyUI. Ton travail : la brancher sur les vrais modèles de
la machine, dans l'ordre, en vérifiant chaque étage sur de vraies
images.

---

## 1. Récupérer le code

```sh
git clone -b claude/epic-wright-y2kbrc https://github.com/calculment0r/Character_Sheet.git
cd Character_Sheet
git log --oneline -8
```

- Dépôt public : le clone marche sans identifiants. **Pousser** demande
  une authentification GitHub sur la machine (`gh auth login`, ou un
  jeton HTTPS). Sans elle, committer en local et le dire à Cal.
- **Travailler sur la branche `claude/epic-wright-y2kbrc`**, pas sur
  `main` (qui ne contient que deux fichiers d'origine). Une autre
  branche, `claude/interactive-character-generator-5IwJo`, porte l'ancien
  générateur servi par GitHub Pages : ne pas y toucher.
- Si `docs/research/` existe après un `git pull`, ce sont des rapports
  de recherche sur les API des modèles : les lire avant d'écrire un
  adaptateur.

À lire dans le dépôt, dans cet ordre : `CLAUDE.md`, `docs/LOCAL.md`
(mode d'emploi), `docs/ARCHITECTURE.md`, puis le cadrage complet
`docs/BRIEF_CHARACTER_FACTORY.md`.

---

## 2. Cal et ses décisions

Cal parle français, veut **avancer**, n'aime pas les détours. Réponses
courtes et concrètes, en français, **jamais d'émoticône**. Il a tranché :

- **Tout en local, pas d'API pour l'instant.** Pas de serveur, pas de
  file, pas de tunnel. `api/`, `start.sh`, `check.sh` et
  `docs/CLOUDFLARE.md` restent dans le dépôt pour plus tard : **ne pas
  les étendre**.
- **H3 en local, 768 px de petit côté** (pas l'API 2K). **H3 est déjà
  installé et tourne dans ComfyUI** sur la machine.
- **Outil interne** : les fontes commerciales du dépôt restent, la
  question de licence est close.
- **Thème** : sombre « NL », sans bascule.

Défauts posés pour les arbitrages encore ouverts du §14 du brief (Cal
peut les changer) :

- vues orthogonales : **une génération par vue** ; l'orbite redécoupée
  existe (`./usine vues --orbite`) pour le banc que le brief demande ;
- moteur 3D par défaut : **TRELLIS 2** (MIT). Hunyuan3D 2.1 reste au
  choix, mais sa licence exclut l'UE, le Royaume-Uni et la Corée : le
  dire à Cal avant de s'en servir en production ;
- disque sur le visage : **à trancher sur pièces**, `./usine planche --ab` ;
- conversion MHR → SOMA : **ouverte**, à trancher en branchant SAM 3D Body.

---

## 3. Ce qui existe

```
usine                  la commande : ./usine <étage> …   (./usine -h)
factory/               la chaîne, en Python (numpy + Pillow)
  project.py           un dossier par personnage, manifeste project.json, règles dures
  chain.py             visage → costumes → planche → vues → prep → contrôle → mesh
  cli.py, cli_motion.py  les commandes ; rig → prises → timeline → bake
  h3.py, comfy.py      H3 par ComfyUI (défaut), ruse des 5 frames, frame la plus nette
  gabarit.py           ./usine gabarit : adopte un workflow ComfyUI exporté (API)
  prompts.py           prompts Ref2VA en six sections (même méthode que js/promptbuilder.js)
  imaging.py           détourage, recentrage, même échelle, marges égales, frames vidéo (ffmpeg)
  mesh.py              interface MeshEngine : TRELLIS 2 / Hunyuan3D 2.1, canaux PBR à part
  skeleton.py          squelette SOMA 77, T-pose reconstruite, delta de bind, poses de contrôle
  rig.py               rig : bind A-pose, refus T-pose, écriture glTF skinnée
  motion.py, mix.py, bake.py   prises, mixage par masques (slerp), NPZ + GLB animé
  gltf.py              lecture/écriture GLB (numpy seul)
  stubs.py, stubs3d.py moteurs factices : vrais PNG / GLB / NPZ, étiquetés FACTICE
  doctor.py            ./usine doctor : GPU, torch, ComfyUI et ses nœuds, paquets
  viewer.py            ./usine voir (viewer) et ./usine page (étage Identité), serveur statique local
data/soma77.json       les 77 articulations SOMA, hiérarchie, pose neutre (relevées dans Kimodo)
data/hand_poses.json   poses de main : détendue, ouverte, poing, pince, pointer
viewer.html            viewer 3D local (three.js) : éclairages, canaux PBR, A/B, clips, poses
index.html, js/, assets/   la page : console + étage Identité (conversation qui remplit la fiche)
theme.html             le catalogue du thème
tools/chain_check.py   la chaîne de bout en bout sur factices + trajet ComfyUI factice
tools/mock_comfy.py    un faux ComfyUI ; tools/fixtures/h3_export_api.json, un export d'exemple
tools/e2e.mjs, tools/mock_llm.py   test de la page dans un navigateur
```

Un personnage vit dans `projects/<perso>/` (ignoré par git) :
`face/`, `costumes/<c>/{fullbody,sheets,views/raw,views/prepared,mesh/vNNN,rig/vNNN}`,
`takes/tNNN/motion.npz`, `bake/<timeline>/anim.{npz,glb}`, et `project.json`.

### Vérifié, dans un conteneur cloud sans GPU ni accès à la machine

- `python3 tools/chain_check.py` : **29/29**. La chaîne entière sur les
  moteurs factices, les refus du brief, le skinning glTF rejoué en numpy
  (mains au-dessus de la tête bras levés, hanches plus basses accroupi,
  racine continue entre deux prises), et le trajet H3 → ComfyUI contre
  le faux ComfyUI (références 0/1/2/3 selon l'étage, nœuds REF vides
  retirés, 768 px de petit côté, frame la plus nette gardée).
- Le viewer dans Chromium, sur les fichiers de la chaîne : rig à 77 os,
  5/5 poses de contrôle jouées, timeline cuite lue, aucune erreur.
- La page (étage Identité) : 12/12 contre un faux modèle de texte.

### Jamais testé

Un vrai ComfyUI avec H3, un vrai GPU, et **aucun** modèle de l'aval
(TRELLIS 2, Hunyuan3D, delight, UniRig, Kimodo, SAM 3D Body). Chaque
réglage par défaut est une hypothèse à confirmer sur la machine.

---

## 4. Installer

1. **La chaîne** : dans l'environnement Python de ton choix,
   `pip install -r requirements.txt` (numpy, Pillow). `./usine` prend
   `.venv/bin/python` s'il existe, sinon `python3` ; `FACTORY_PYTHON`
   en impose un autre.
2. **Vérifier avant tout** : `python3 tools/chain_check.py` doit rendre
   29/29 sur la machine aussi.
3. **Inventaire** : `./usine doctor`. Ne touche à rien ; dit ce que la
   machine sert déjà (GPU, torch/CUDA, ComfyUI et ses nœuds par
   capacité, paquets des moteurs). `--ecrire` range les moteurs trouvés
   dans `factory.local.json`. Les noms de paquets qu'il cherche
   (`trellis2`, `hy3dshape`, `hy3dpaint`, `kimodo`, `sam_3d_body`…) sont
   **supposés** : les corriger dans `factory/doctor.py` d'après ce qui
   est vraiment installé.
4. **Les modèles de l'aval** : regarder d'abord s'ils sont déjà là (dans
   ComfyUI comme nœuds, ou en paquets). Pour installer :
   - TRELLIS 2 — https://github.com/microsoft/TRELLIS.2 (MIT, ~16 Go VRAM)
   - Hunyuan3D 2.1 — https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1 (~29 Go, licence territoriale)
   - delight et multi-vues 2mv — https://github.com/Tencent-Hunyuan/Hunyuan3D-2 (HF `tencent/Hunyuan3D-2`, sous-dossier `hunyuan3d-delight-v2-0`)
   - UniRig — https://github.com/VAST-AI-Research/UniRig (MIT)
   - Kimodo — https://github.com/nv-tlabs/kimodo, modèle `Kimodo-SOMA-RP-v1` (pas la variante SMPL-X, licence recherche)
   - SAM 3D Body — https://github.com/facebookresearch/sam-3d-body (poids sous conditions sur Hugging Face)

   **Leurs dépendances se contredisent** (versions de torch, extensions
   CUDA compilées). Un environnement par modèle. Deux façons de les
   brancher, au choix selon ce qui est déjà en place :
   - **par ComfyUI**, s'il a déjà les nœuds (il garde les modèles
     résidents) : même mécanique que H3, un gabarit au format API avec
     nœuds `REF n` et `OUT` ;
   - **par sous-processus** : l'adaptateur lance le Python de
     l'environnement du modèle (variable `FACTORY_<CAPACITÉ>_PYTHON` à
     créer) sur un petit script d'entrée, avec des fichiers en entrée et
     en sortie. Plus lent (le modèle se recharge à chaque appel), mais
     sans conflit.

---

## 5. Le plan, dans l'ordre

Après chaque étape : `python3 tools/chain_check.py` reste vert, un
commit clair sur la branche, push si possible, et un mot court à Cal.

### 5.1 H3 par ComfyUI — la priorité

1. Faire exporter par Cal (ou retrouver dans `ComfyUI/user/default/workflows/`)
   le workflow H3 **Ref2VA** qui donne de bonnes images, **au format API**
   (Workflow → Export (API)). Un export « Save » ordinaire ne va pas.
2. `./usine gabarit export.json` → `workflows/h3_ref2va.json`. Relire
   les changements affichés : nœuds `REF 1…n`, `{{prompt}}`, `{{seed}}`,
   `{{width}}`, `{{height}}`, `{{frames}}`, `OUT`.
3. `./usine page` → étage Identité → « Exporter l'identité .json »,
   puis `./usine nouveau --identite <fichier>`, puis `./usine visage <perso>`.
   La page parle au modèle de texte local : son URL se pose dans l'écran
   MOTEUR. Si le navigateur refuse l'appel (CORS), relancer le serveur
   d'inférence en autorisant `http://127.0.0.1:8765` (vLLM :
   `--allowed-origins`). La fiche peut aussi s'écrire à la main en JSON.
4. **C'est là que ça cassera.** Points à vérifier sur le vrai nœud H3 :
   - le nom des champs (prompt, graine, taille, nombre de frames) ;
   - le minimum de frames (le brief dit 5, « le minimum du nœud ») ; si
     le nœud impose 4n+1 ou autre, ajuster `FRAMES_STILL` dans `factory/h3.py` ;
   - le format de prompt attendu : la chaîne envoie les six sections
     en texte, `nom_de_section:\ncontenu` séparées par une ligne vide
     (`prompts.to_text`) ; `prompts.to_json` existe si H3 veut du JSON ;
   - la façon dont les images de référence sont numérotées dans le prompt
     (« Image 1 », « Image 2 »…) ;
   - aucun son : les deux sections sonores sont neutres.
5. Monter jusqu'aux vues contrôlées : `pleinpied`, `planche --ab`,
   `vues`, `prep`, `controle`. Regarder les planches contact
   (`face/contact.png`, `costumes/<c>/views/contact_*.png`).
6. Le workflow d'orbite, s'il existe : `./usine gabarit … --nom h3_orbit.json`.

### 5.2 Mesh 3D — TRELLIS 2, puis Hunyuan3D 2.1

`factory/mesh.py` appelle, pour le moteur `python` :

```python
mesh_trellis.generate(views=dict[str, Path], single_view=Path | None,
                      dest=Path, seed=int, texture=bool, report=callable)
mesh_hunyuan.generate(...)   # mêmes arguments
```

- `views` : les vues **préparées** — PNG RGBA 1024×1024, fond
  transparent, même échelle, pieds sur la même ligne — clés `front`,
  `left`, `back`, `right` (et `threequarter`). En mono-vue, `single_view`
  est le 3/4 (une vue de face donne un dos plat, §7.2).
- Écrire **un GLB unique, textures PBR embarquées**, à `dest`. `mesh.py`
  en tire ensuite `albedo/metallic/roughness/normal.png` et les stats.
- Hunyuan3D multi-vues (2mv) : slots ordonnés front/left/back/right.
- Mètres, Y en haut, +Z devant ; si le moteur sort un cube normalisé,
  remettre à l'échelle d'un humain (hauteur de la silhouette).
- Créer `factory/mesh_trellis.py` / `factory/mesh_hunyuan.py` ; régler
  `FACTORY_TRELLIS=python` (ou `FACTORY_HUNYUAN3D=python`).
- Delight : `factory/delight.py` avec `run(img: PIL.Image) -> PIL.Image`,
  appelé par `prep` si `FACTORY_DELIGHT=hunyuan`. Il est appelé **avant**
  le détourage ; si le modèle attend une image détourée, déplacer
  l'appel après `imaging.matte` dans `chain.prep`.

Critère (brief §15, lot 3) : un GLB PBR inspectable dans `./usine voir`,
jugé bon par Cal ; `--a 1 --b 2` pour comparer deux versions ou moteurs.

### 5.3 Rig — UniRig vers SOMA 77

`factory/rig.py` appelle, pour le moteur `python` :

```python
rig_unirig.run(mesh_glb=Path, workdir=Path, report=callable)
    -> (Skeleton, weights, mesh)
```

- `mesh = rig._mesh_parts(mesh_glb)` (le GLB chargé, ses primitives,
  ses nœuds) ;
- `weights[(mesh_idx, prim_idx)] = (joints uint16 (V,4), weights float32 (V,4))`,
  indices dans l'ordre SOMA 77 de `data/soma77.json`, poids normalisés ;
- `Skeleton = skeleton.soma_rig_from_apose(positions)`, où `positions`
  (77, 3) sont les articulations du personnage **en A-pose**, en mètres.

Le travail : lancer UniRig (squelette puis poids), puis un **mapping de
noms UniRig → SOMA établi une fois** et versionné dans
`data/unirig_to_soma.json` (brief §10.3, ne pas le recalculer par
personnage). Os UniRig sans équivalent : verser leurs poids sur
l'ancêtre mappé le plus proche. Articulations SOMA sans équivalent
(doigts, mâchoire, yeux, bouts) : positions interpolées sur les
proportions SOMA, poids nuls. `rig.py` fait le reste : refus si les bras
sont à plus de 70° de la verticale (T-pose), bind A-pose, delta de bind,
écriture du GLB avec les cinq poses de contrôle.

Critère (lot 4) : les cinq poses dans `./usine voir <perso> --a rig:N`,
sans épaule cassée ; puis `./usine rig-ok <perso> accepte`.

Option du §10.4, pas encore faite : « base paramétrique » — le corps
SOMA lui-même, déjà riggé, au lieu d'un mesh généré.

### 5.4 Animation — Kimodo, puis SAM 3D Body

**Déjà établi dans le code de Kimodo (commit `58e7818`) — ne pas refaire :**

- les 77 articulations et leur hiérarchie sont dans
  `kimodo/skeleton/definitions.py` (`SOMASkeleton77`) : c'est la source
  de `data/soma77.json` ; attention, `LeftLeg` est la hanche et
  `LeftShin` le genou ;
- FK de Kimodo (`kimodo/skeleton/kinematics.py`) : rotation locale
  identité = pose neutre (`assets/skeletons/somaskel77/joints.p`, une
  T-pose, bras gauche vers +X), **repères alignés sur le monde**. C'est
  exactement la convention de la chaîne : une prise Kimodo se range
  **sans conversion** ;
- le modèle sort en `somaskel30` ; `SOMASkeleton30.output_to_SOMASkeleton77`
  passe en 77 avec des mains détendues (`relaxed_hands_rest_pose.npy`).

`factory/motion.py` appelle, pour le moteur `python` :

```python
motion_kimodo.generate(prompt=str, frames=int, fps=float, constraints=list[dict],
                       heading=float, seed=int, dest=Path, report=callable)
```

et doit écrire, avec `motion.save_take(...)`, un NPZ au format de la
chaîne : `local_quats (T, 77, 4)` en [x, y, z, w] dans l'ordre de
`data/soma77.json`, `root_positions (T, 3)` en mètres, `fps`,
`joint_names`, `source`, `engine` (+ `foot_contacts` si utile).
Charger le modèle une fois par appel ; **réutiliser le chargeur de
contraintes du CLI Kimodo** (brief §12.2), ne pas le réécrire. Pièges du
§12.3 : racine XZ à (0, 0) à la frame 0, cap initial dans
`first_heading_angle` (radians, 0 = +Z), `global_root_heading` en
[cos, sin].

Hauteur de hanches : la pose neutre SOMA met les hanches à 1,005 m
au-dessus du point le plus bas ; le brief parle de ~0,96 m debout pour
les sorties Kimodo. Vérifier sur une vraie prise et corriger
`SOMA_HIP_HEIGHT` dans `factory/motion.py` si les pieds flottent ou
s'enfoncent après `bake`.

**SAM 3D Body** : `motion_sam3d.run(video=Path, fps, dest, report) -> dict`
(`npz`, `frames`, `fps`, `backend`). Inférence par frame → paramètres
MHR → rotations SOMA 77 — **la route est à trancher** (mapping de joints
+ `convert_reference`, `PoseInversion`, ou détour SMPL-X, brief §11.2)
— puis lissage en fenêtre glissante, forme et échelle verrouillées par
piste. Règle métier : plan buste → ne contraindre que ce qui est vu,
laisser Kimodo faire les jambes. SAM 3D Body sert aussi à **mesurer
l'azimut** des vues : ranger la valeur dans
`views.raw[<vue>].azimuth_measured` et `./usine controle` l'utilisera au
lieu de l'angle demandé.

Critère (lot 5) : une vidéo d'entrée produit une animation jouée par le
personnage (`./usine bake`, puis `./usine voir <perso> --a bake:<nom>`).

### 5.5 Ensuite

- Banc des vues (§6.1) : une génération par vue contre une orbite,
  angle mesuré et dérive d'identité.
- Couche visage (§13.2) : MediaPipe → 52 ARKit → 72 MHR, table fixée une fois.
- Mains depuis la vidéo (HaMeR) ; la bibliothèque `--main` couvre le reste.

---

## 6. Règles à ne pas casser

- Le visage ne se verrouille qu'une fois ; pas d'étage sans le
  précédent validé ; ±5° au contrôle, bouclage à 360° ; pas de mesh
  multi-vues sans contrôle passé ; pas de bind en T-pose. Ces refus sont
  dans `project.py`, `chain.py`, `rig.py`, `cli_motion.py` : **ne pas
  les affaiblir sans l'accord de Cal.**
- H3 : pas de prompt négatif, chaque référence avec un rôle nommé,
  bloc « prendre tel quel », sections sonores neutres.
- Conventions : mètres, Y en haut, +Z devant, +X à gauche du
  personnage ; rotations locales relatives à la pose neutre SOMA,
  repères alignés sur le monde ; quaternions [x, y, z, w].
- Un moteur factice ne se fait jamais passer pour un vrai : étiquette
  FACTICE, `backend: stub` dans le manifeste. Un vrai moteur qui échoue
  **échoue**, il ne retombe pas en silence sur le factice.
- Front (`index.html`, `viewer.html`, `assets/`) : règles du thème de
  `CLAUDE.md` — aucune couleur en dur, filets en `box-shadow`, un seul
  bouton orange par écran.

## 7. Façon de travailler

- Lire le code existant avant d'écrire : les modules ont un contrat
  court, écrit en tête de fichier.
- Commentaires et messages en français, au ton du dépôt : ce que le
  code fait et pourquoi, sans remplissage.
- `tools/chain_check.py` après toute modification de `factory/` ;
  `tools/e2e.mjs` après toute modification de la page.
- Un commit par avancée, message clair ; push sur `claude/epic-wright-y2kbrc`.
- Tenir `docs/LOCAL.md`, `docs/HANDOFF.md` et `docs/ARCHITECTURE.md` à
  jour à mesure que les moteurs deviennent réels.
- Dire à Cal ce qui marche **sur ses images**, et ce qui n'a pas été
  vérifié. Pas de promesse sur ce qui n'a pas tourné.
