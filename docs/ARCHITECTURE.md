# Character Factory — où en est la chaîne

Document d'état. Le cadrage complet est dans
[`BRIEF_CHARACTER_FACTORY.md`](./BRIEF_CHARACTER_FACTORY.md) ; ce
fichier-ci dit seulement ce qui existe, ce qui n'existe pas, et où c'est.

## Le découpage

```
GitHub Pages (statique)                 ← index.html, aucun secret
        │  HTTPS
        ▼
Cloudflare Tunnel                       ← pas d'ouverture de port sur le LAN
        │
        ▼
API FastAPI (api/)                      ← jeton, CORS, SSE
        │
        ├── file de travaux             Redis + RQ, ou mémoire en mise au point
        ├── stockage                    MinIO présigné, ou disque local
        ├── relais /v1                  vers le serveur d'inférence local
        └── workers                     un process par capacité, modèle résident
```

## Ce qui tourne aujourd'hui

**Le front.** `index.html` plus `assets/` et `js/`. Deux vues.

*La console* est la page d'accueil, reprise de la home du banc NL :
carte héro, carte de compte, puis la pile des huit étages en dalles
pleines. La famille corail marque le lot en cours, la verte marque
l'aval. On entre dans un étage en cliquant sa dalle, on revient par le
logotype ou par le bouton CONSOLE.

*Le banc* est la vue de travail, en trois colonnes : le rack des huit
étages à gauche, l'étage actif au centre, la fiche d'identité à droite.
L'étage Identité est complet et fonctionne : la conversation remplit les
vingt-et-un champs par appels d'outils, les widgets évitent d'avoir à
taper, et deux fabricants de prompt sortent soit le gabarit historique,
soit le format Ref2VA en six sections.

**Le moteur de texte.** `js/llm.js` ne parle qu'un dialecte en interne
et traduit vers la cible : OpenAI pour le DGX, natif pour Anthropic. Le
repli ne se déclenche que sur une panne de transport — réseau coupé,
tunnel fermé, 5xx, 429. Un 4xx qui refuse la requête elle-même ne
bascule pas, parce que la même requête échouerait pareil en face.

**L'API.** Les routes du §1.3 existent, avec jeton, CORS, SSE et
stockage. Les étages GPU sont branchés sur le worker factice : la file,
la progression et le rangement sont réels, seul le calcul est simulé.

**Trois règles du brief sont tenues par le code, pas par la
documentation.** Le visage ne se verrouille qu'une fois, un second appel
rend 409. Le rig refuse un bind en T-pose, parce que la conversion vers
la T-pose SOMA est un delta et pas un bind. Le contrôle d'alignement
refuse au-delà de ±5°, en tenant compte du bouclage à 360°.

## Ce qui n'existe pas encore

| Étage | Manque |
|---|---|
| Visage, Costumes | un worker image résident |
| Planche, Vues | un worker H3 résident |
| Mesh 3D | TRELLIS 2 et Hunyuan3D 2.1 derrière l'interface `MeshEngine` |
| Rig | UniRig, le mapping vers `SOMALayer.public_joint_names`, le delta de bind |
| Animation | Kimodo, SAM 3D Body, la timeline et le mixage par masque de joints |

Hors étages : la persistance (l'entrepôt de l'API est un dictionnaire en
mémoire), le visualiseur three.js du §9, et la couche mains/visage du §13.

## Faire tourner

```sh
./check.sh     # ce qui est déjà en place, et quoi lancer
./start.sh     # la Factory, page et API sur un seul port
```

L'API sert aussi la page, donc un seul port : `http://localhost:8000`.
Page et API partagent l'origine, ce qui supprime la question du CORS et
dispense de coller quoi que ce soit dans l'écran MOTEUR — le front
reconnaît sa propre origine comme moteur et adopte le modèle que l'API
déclare servir.

Pour ouvrir depuis l'extérieur, un seul tunnel suffit : voir
[`CLOUDFLARE.md`](./CLOUDFLARE.md).

Les deux morceaux peuvent aussi tourner séparément — `python3 -m
http.server` d'un côté, `uvicorn` de l'autre — mais il faut alors régler
`FACTORY_CORS_ORIGINS` et coller l'adresse de l'API dans MOTEUR.

## Les arbitrages

Le §14 du brief en liste cinq. Deux sont tranchés :

- **Thème.** Sombre NL, tel quel. Le §9 du brief demandait du clair avec
  un accent orange acide ; l'artefact de référence fait foi. Tout est
  dans `assets/tokens.css`, un passage en clair reste un changement d'un
  seul fichier.
- **Moteur de texte.** DGX local d'abord, repli Anthropic. Réglable dans
  l'écran MOTEUR, y compris en DGX seul pour un fonctionnement souverain.

Les trois autres restent ouverts : H3 local 768 px contre API 2K, vues
séparées contre orbite continu découpé, et la conversion de pose
MHR → SOMA. Le disque sur le visage est implémenté en option
(`mask_face_in_fullbody`) et attend son A/B, comme le brief le demande.

## Une note sur la typographie

Quatre fontes, deux servies par Google Fonts et deux posées dans
`assets/fonts/` :

| Fonte | Rôle | Source |
|---|---|---|
| Chakra Petch | interface, texte courant | Google Fonts |
| Azeret Mono | étiquettes, références, valeurs | Google Fonts |
| Venus Rising | affichage — noms d'étages, rangées de rack, étiquettes de voie | `assets/fonts/` |
| Norelli Black | logotype, et lui seul | `assets/fonts/` |

Norelli remplace la « Four Zero » du banc NL d'origine. Son jeu ne
compte que **54 signes : A-Z, a-z et l'espace. Pas un chiffre, pas un
accent, pas un signe de ponctuation** — pas même la virgule ni le point.
Toute chaîne qu'on lui confie doit donc être écrite pour elle. Deux le
sont, et ce sont les deux seules : le logotype, et le titre de la
console, « DU PROMPT AU PERSONNAGE », écrit sans virgule et sans accent
exprès. Partout ailleurs le navigateur basculerait de police en plein
mot sur le caractère manquant, et ça se verrait.

Venus Rising porte tout le reste de l'affichage : elle couvre les
accents, les chiffres et 688 glyphes.

Les deux fichiers `.otf` sont des fontes commerciales Typodermic. Elles
partent en clair dans un dépôt public, donc servies en téléchargement à
qui visite le site : à vérifier contre la licence d'exploitation avant
que la page soit diffusée largement.

## Une note sur GitHub Pages

Le site publié sert actuellement la branche
`claude/interactive-character-generator-5IwJo`, pas `main` — qui ne
contenait que deux fichiers. Tant que le réglage Pages pointe sur cette
branche, cette refonte ne sera pas visible en ligne. Il faut soit
fusionner vers la branche servie, soit basculer le réglage sur `main`
une fois la fusion faite.
