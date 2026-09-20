# Passation — pour une session locale

Ce document est écrit pour une session Claude Code lancée **sur le DGX**,
ou sur une machine qui l'atteint. Il dit où en est le travail, ce qui a
été vérifié et contre quoi, et ce qui reste entièrement à faire.

Branche : `claude/dazzling-heisenberg-y3hfst`
Cadrage complet : [`BRIEF_CHARACTER_FACTORY.md`](./BRIEF_CHARACTER_FACTORY.md)
État technique : [`ARCHITECTURE.md`](./ARCHITECTURE.md)

---

## 1. Avertissement sur ce qui a été vérifié

La session qui a écrit ce code tournait dans un conteneur cloud isolé,
**sans aucun accès aux DGX ni au réseau de Cal**.

Ce qui a été réellement testé, dans ce conteneur :

- le front, dans Chromium, avec les vraies fontes ;
- la traduction du dialecte Anthropic vers le dialecte OpenAI, contre un
  **faux serveur de 60 lignes de Python** écrit pour l'occasion ;
- l'API : soumission d'un travail, progression en SSE, artefact PNG
  servi, jeton, contrôle d'alignement, verrouillage du visage ;
- le montage à un seul port, depuis un navigateur vierge.

Ce qui n'a **jamais** été testé :

- un vrai serveur d'inférence ;
- un vrai DGX ;
- ComfyUI, qui n'apparaît nulle part dans ce dépôt ;
- un tunnel Cloudflare ;
- Redis, RQ, MinIO — le code a un chemin pour eux, jamais exercé ;
- tout ce qui est en aval de l'étage Identité.

Traiter donc chaque réglage par défaut comme une hypothèse à vérifier,
pas comme un fait.

---

## 2. À faire en premier, dans cet ordre

### a. L'inventaire

```sh
./check.sh
```

Ne touche à rien. Cherche un serveur d'inférence sur les ports usuels et
relève ses modèles, dit si `cloudflared` est là et si un tunnel tourne,
liste les dépendances Python, repère Redis et MinIO. Termine en écrivant
les commandes à lancer, remplies.

Si le serveur écoute ailleurs : `FACTORY_SCAN_PORTS="… …" ./check.sh`.

**Ne rien installer avant d'avoir lu cette sortie.** La machine sert
probablement déjà la moitié de ce dont on a besoin.

### b. Le premier tour réel

```sh
export FACTORY_LLM_URL=…      # ce que check.sh a trouvé
export FACTORY_LLM_MODEL=…
./start.sh
```

Ouvrir `http://localhost:8000`. La page doit se charger, reconnaître
l'API comme moteur sans rien demander, afficher le nom du modèle servi,
et remplir la fiche au premier message.

C'est le premier moment où le code rencontre un vrai modèle. **S'attendre
à ce que ça casse là.** Les points fragiles connus :

- Un modèle local suit rarement les appels d'outils aussi proprement
  qu'un modèle propriétaire. `js/llm.js` tolère des arguments JSON
  illisibles en rendant un objet vide, mais si le modèle n'appelle pas
  les outils du tout, la fiche ne se remplira pas.
- `data/methodology.md` a été écrit pour Claude. Il faudra probablement
  le resserrer pour un modèle local — c'est le seul fichier à toucher
  pour changer le comportement, il est chargé au démarrage.
- Le contexte : la méthode fait 11 Ko, plus les images en base64. Un
  modèle à 8 k de contexte ne tiendra pas.

### c. L'accès depuis l'extérieur

[`CLOUDFLARE.md`](./CLOUDFLARE.md). Si un tunnel tourne déjà, lui ajouter
une entrée `ingress` vers le port 8000 plutôt que d'en ouvrir un second.

---

## 3. La page en ligne — le point bloquant

GitHub Pages sert aujourd'hui la branche
`claude/interactive-character-generator-5IwJo`, **pas** `main`, et pas la
branche de ce travail. Tant que c'est le cas, rien de cette refonte n'est
visible sur `calculment0r.github.io/Character_Sheet`.

Trois façons d'en sortir, au choix de Cal :

1. Basculer le réglage Pages sur `claude/dazzling-heisenberg-y3hfst`
   (Settings → Pages → Branch). Un clic, rien à pousser.
2. Fusionner cette branche dans celle que Pages sert déjà.
3. Fusionner dans `main` et basculer Pages sur `main`.

À noter : servie par GitHub Pages, la page est sur une **autre origine**
que l'API. Il faut alors poser `FACTORY_CORS_ORIGINS` côté DGX et coller
l'adresse du tunnel dans l'écran MOTEUR. Servie par l'API à travers le
tunnel, il n'y a rien à régler. Les deux marchent, la seconde est plus
simple pour travailler.

---

## 4. Ce qui existe

**Front** — `index.html`, `assets/`, `js/`. Deux vues : la console
d'accueil (dalles pleines, langage du banc NL) et le banc de travail en
trois colonnes. Seul l'étage Identité fonctionne : 21 champs remplis par
appels d'outils, widgets interactifs, deux fabricants de prompt (gabarit
historique, et format Ref2VA en six sections).

**Moteur** — `js/llm.js` ne parle qu'un dialecte en interne et traduit
vers la cible. Repli sur panne de transport uniquement ; un 4xx qui
refuse la requête ne bascule pas.

**API** — `api/`. Les routes du §1.3 du brief, jeton, CORS, SSE,
stockage. Démarre sans Redis ni MinIO, en le disant. Sert aussi le front,
ce qui permet de n'ouvrir qu'un tunnel.

**Trois règles du brief tenues par le code** : le visage ne se verrouille
qu'une fois (409 au second appel) ; le rig refuse un bind en T-pose ;
l'alignement des vues refuse au-delà de ±5°, bouclage à 360° compris.

**Typographie** — Venus Rising porte l'affichage. Norelli ne contient
que 54 signes (A-Z, a-z, espace : ni chiffre, ni accent, ni ponctuation)
et ne sert donc que deux chaînes écrites pour elle : le logotype et le
titre de la console. Les deux `.otf` sont des fontes commerciales
Typodermic servies en clair depuis un dépôt public — à vérifier contre
leur licence.

---

## 5. Ce qui n'existe pas

| Étage | Manque |
|---|---|
| Visage, Costumes | un worker image résident |
| Planche, Vues | un worker H3 résident |
| Mesh 3D | TRELLIS 2 et Hunyuan3D 2.1 derrière l'interface `MeshEngine` |
| Rig | UniRig, le mapping vers `SOMALayer.public_joint_names`, le delta de bind |
| Animation | Kimodo, SAM 3D Body, la timeline, le mixage par masque de joints |

Hors étages : la persistance (l'entrepôt de l'API est un dictionnaire en
mémoire, perdu au redémarrage), le visualiseur three.js du §9, la couche
mains et visage du §13.

**Si ComfyUI tourne déjà sur les DGX**, c'est probablement par lui que
doit passer l'étage image, via son API (`POST /prompt`, puis
`GET /history/{id}`), plutôt que par un worker Python à écrire. Le
contrat d'un worker est volontairement minuscule — voir
`api/workers/stub.py` :

```python
def run(*, report, job_id: str = "", **kwargs) -> dict:
    report(0.5, "à mi-chemin")
    return {"url": "…"}
```

Un worker ComfyUI tient dans ce contrat : soumettre le workflow, suivre
la file, récupérer l'image, la ranger avec `storage.put`, rendre son URL.

---

## 6. Décisions encore ouvertes

Le §14 du brief en liste cinq. Deux ont été tranchées par Cal :

- **Thème** : sombre NL, tel quel. Le §9 du brief demandait du clair ;
  l'artefact de référence fait foi. Tout est dans `assets/tokens.css`.
- **Moteur de texte** : DGX d'abord, repli Anthropic.

Restent à trancher : H3 local 768 px contre API 2K ; vues séparées contre
orbite continu découpé ; conversion de pose MHR → SOMA (mapping de
joints, `PoseInversion`, ou détour SMPL-X). Le disque sur le visage est
implémenté en option (`mask_face_in_fullbody`) et attend son A/B.

---

## 7. Carte du dépôt

```
index.html              la page — console + banc
assets/tokens.css       toutes les couleurs et les fontes, source unique
assets/rack.css         les composants du banc NL
assets/factory.css      conversation, fiche, widgets
assets/fonts/           Venus Rising, Norelli
js/config.js            réglages, origine implicite
js/llm.js               client unifié DGX / Anthropic
js/schema.js            champs, outils, préambule, étages
js/promptbuilder.js     gabarit historique + Ref2VA six sections
js/ui.js                tout le DOM
js/app.js               orchestration, boucle d'agent
data/methodology.md     la méthode chargée au démarrage
data/sheet_template.txt le gabarit de prompt historique
api/                    FastAPI — voir api/README.md
check.sh                inventaire de la machine
start.sh                lancement, un seul port
legacy/                 l'ancien kit UI, gardé pour mémoire
```
