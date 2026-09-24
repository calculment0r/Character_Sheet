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
index.html              la page : console et étage Identité
theme.html              le catalogue du thème
tools/chain_check.py    la chaîne de bout en bout, moteurs factices
tools/mock_comfy.py     un faux ComfyUI
tools/e2e.mjs           la page de bout en bout, dans un navigateur
api/ · start.sh · check.sh · docs/CLOUDFLARE.md
                        la version serveur, gardée pour plus tard
```
