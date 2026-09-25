# Character Factory — où en est la chaîne

Document d'état. Le mode d'emploi est dans [`LOCAL.md`](./LOCAL.md), la
reprise du travail dans [`HANDOFF.md`](./HANDOFF.md), le cadrage complet
dans [`BRIEF_CHARACTER_FACTORY.md`](./BRIEF_CHARACTER_FACTORY.md).

## Le découpage

Tout tourne sur la machine, DGX2 — décision de Cal : pas d'API
distante pour l'instant. Le studio est un serveur local, sur le réseau
de la maison, pas une API.

```
./usine studio                         ← le studio : studio.html, cartes et étages à la main
    │     studio.py                    serveur (stdlib), file à un ouvrier, relais du modèle de texte
    │     memory.py                    décharger Ollama, vider ComfyUI, attendre de la mémoire
./usine <commande>                     ← une commande par étage
    │
    ├── factory/                       la chaîne, en Python
    │     project.py                   un dossier par personnage, un manifeste, les règles
    │     chain.py                     visage → costumes → planche → vues → prep → contrôle → mesh
    │     cli_motion.py                rig → prises → timeline → cuisson
    │     h3.py · comfy.py             H3 par ComfyUI, qui le garde résident
    │     mesh.py                      TRELLIS 2 et Hunyuan3D 2.1, une interface
    │     rig.py · skeleton.py         SOMA 77, bind en A-pose, poses de contrôle
    │     motion.py · mix.py · bake.py Kimodo, SAM 3D Body, mixage, NPZ + glTF
    │     gltf.py                      lecture et écriture GLB, numpy seul
    │
    ├── ComfyUI (déjà sur la machine)  H3 résident ; workflows/ au format API
    ├── projects/<perso>/              tout ce que la chaîne produit
    └── viewer.html                    le viewer 3D, une page locale
```

La console (`console.html`) reste l'outil de l'étage Identité : une
conversation avec le modèle de texte local qui remplit la fiche. Servie
par le studio, elle crée ou met à jour le personnage elle-même ; ouverte
seule, elle exporte un JSON que `./usine nouveau --identite` reprend.

Le studio appelle les fonctions de `chain.py` telles quelles : il n'a
pas de règle à lui. Un travail GPU passe par la file ; les `print` de
l'étage vont à son journal, ses `report` à sa progression.

`api/` — FastAPI, file RQ, MinIO, tunnel — reste dans le dépôt pour le
jour où la page pilotera la chaîne à distance. Rien de la chaîne locale
n'en dépend.

## Ce qui tourne

**Les huit étages**, du portrait neutre à la timeline cuite, avec les
refus que le brief impose : visage verrouillé une fois, pas d'étage
sans le précédent, contrôle ±5° avant le mesh, pas de bind en T-pose.

**H3, à 768 px, par ComfyUI** — l'arbitrage du §14 est tranché : local.
La ruse des cinq frames du §5.2 est appliquée partout : on demande le
minimum, on garde la plus nette. Les prompts sont au format Ref2VA en
six sections, sans négatif, chaque référence avec un rôle nommé. Le
workflow est celui qui marche déjà dans ComfyUI, adopté par
`./usine gabarit`.

**La passe de préparation des vues** (§6.2, §6.3) : détourage par écart
à la couleur du fond, ou rembg ; recentrage ; même échelle, réglée sur la
hauteur de la silhouette, le seul invariant d'un angle à l'autre ; même
marge ; pieds sur la même ligne. Le rapport chiffré sort dans
`prep.json`.

**Le rig SOMA** dans la convention de Kimodo, relevée dans son code :
rotations locales relatives à la pose neutre, repères alignés sur le
monde. La T-pose du personnage est reconstruite à partir de ses
articulations en A-pose et des directions d'os SOMA ; le delta de bind
en sort. Cinq poses de contrôle sont écrites dans le GLB.

**Le mixage** par masque d'articulations — corps, main gauche, main
droite, visage — en slerp, avec lissage par couche, racine continue
d'une prise à l'autre et mise à l'échelle des hanches du personnage. Une
bibliothèque de mains couvre les plans sans source de doigts.

**Le viewer** du §9 : éclairages, canaux PBR, grille et silhouette, A/B
en côte à côte ou en volet, clips et poses de contrôle, timeline à la
molette.

## Ce qui attend la machine

| Capacité | Moteur réel | État |
|---|---|---|
| H3 | ComfyUI, DGX1 :8189 | **tourne** : visage, plein pied, planche, orbite sur de vraies images |
| Détourage | BiRefNet par ComfyUI, DGX1 | **tourne** : vues et frames d'orbite |
| Mesh 3D | TRELLIS 2 par ComfyUI, DGX2 | gabarits multi-vues et image unique validés à blanc ; jamais lancé |
| Mesure d'azimut | SAM 3D Body par ComfyUI | câblée (`controle --mesurer`), jamais lancée ; signe à confirmer |
| Delight | hunyuan3d-delight-v2-0 | branchement prévu dans `prep` |
| Rig | UniRig par ssh, DGX2, reconnu sur la topologie → SOMA 77 | installé, câblé, jamais lancé |
| Animation | Kimodo par ssh, DGX2 ; SAM 3D Body | Kimodo installé, bloqué sur Llama-3 (licence Meta) ; vidéo → SOMA à trancher |

Tant qu'une capacité n'a pas son moteur, elle tourne en factice et le
dit : étiquette FACTICE sur les images, « (factice) » dans `./usine etat`.

## Les arbitrages du §14

- **H3** : local, 768 px. Tranché par Cal.
- **Vues orthogonales** : une génération par vue par défaut, pour un
  contrôle d'angle exact ; l'orbite redécoupée est disponible
  (`--orbite`) pour le banc que le brief demande. Premier banc sur la machine (24/09) : par vue, H3 revient vers la face (profil
  demandé à 90°, rendu vers 55°) ; l'orbite donne de vrais profils et un vrai dos,
  frames choisies sur la silhouette. Défaut à basculer sur l'orbite : à Cal de trancher.
- **Moteur 3D par défaut** : TRELLIS 2, MIT. Hunyuan3D 2.1 reste au
  choix, avec l'avertissement de licence territoriale à chaque appel.
- **Disque sur le visage** : `./usine planche --ab` sort les deux
  planches de même graine ; la décision se prend sur pièces.
- **Conversion MHR → SOMA** : ouverte, elle se tranche avec le
  branchement de SAM 3D Body.
- **Thème** : sombre NL, tel quel. **Fontes** : outil interne, elles
  restent.
