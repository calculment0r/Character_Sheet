# Character Factory

Chaîne souveraine : prompt ou photo → visage validé → costume et plein
pied en pose naturelle → A-pose par squelette → vues orthogonales → mesh
PBR → rig SOMA → animation, et un turnaround H3 de présentation à la fin.
Les études et essais qui fondent ces choix : `docs/ETUDES.md`, à lire
avant de toucher à ces étages. **Tout tourne en
local, sur la machine** — pas d'API pour l'instant, décision de Cal.

Reprise sur la machine : `REPRISE.md` — **à lire en premier**
Cadrage : `docs/BRIEF_CHARACTER_FACTORY.md`
Mode d'emploi de la chaîne : `docs/LOCAL.md`
Reprise du travail : `docs/HANDOFF.md`
État technique : `docs/ARCHITECTURE.md`

## Lancer

On ne travaille que sur **DGX2** (`ssh dgx2`, dépôt dans
`~/Character_Factory`, venv `.venv`). Le PC ne sert qu'à écrire le code
et à ouvrir la page.

```sh
./usine studio            # le studio, sur DGX2 : http://192.168.10.247:8765/
./usine doctor            # ce qui tourne déjà : GPU, ComfyUI et ses nœuds H3, paquets
./usine gabarit export.json   # adopter le workflow H3 de ComfyUI (export API)
./usine etat <perso>      # où en est un personnage, et la commande suivante
```

Le studio (`factory/studio.py`, `studio.html`, `js/studio.js`) mène
chaque personnage à la main, étage par étage, avec une file à un
ouvrier ; `./usine <étage>` fait la même chose en ligne de commande.
Un dossier par personnage sous `projects/`, un manifeste `project.json`.
H3 passe par ComfyUI `:8189`, le reste par `:8188` ; les capacités sans
modèle trouvé par `./usine doctor` sont factices.

`api/`, `start.sh`, `check.sh` et `docs/CLOUDFLARE.md` restent pour le
jour où la page devra piloter la chaîne à distance. Ne pas les étendre
sans que Cal le demande.

## Vérifier

```sh
python3 tools/chain_check.py     # la chaîne entière, moteurs factices + trajet ComfyUI factice
```

Rend 0 si tout passe. **Le lancer après toute modification de
`factory/`.** Il rejoue le skinning glTF comme un viewer : une pose qui
casse se voit dans ses chiffres.

Il mène aussi le studio par son API, du personnage créé au rig accepté.

Après une modification de la console (`console.html`, `js/`, `assets/`) :

```sh
python3 tools/mock_llm.py &
FACTORY_LLM_URL=http://127.0.0.1:8812 ./start.sh &
node tools/e2e.mjs http://127.0.0.1:8000     # npm install playwright
```

## Le thème — règles dures

**`theme.html` est le catalogue.** Il charge les mêmes CSS que
l'application : ouvrir cette page suffit à voir tous les jetons, les
quatre fontes et chaque composant réel. Il n'y a jamais deux vérités.
`viewer.html` suit les mêmes règles, jusque dans son JavaScript : la
scène 3D lit ses couleurs dans les jetons à l'exécution.

1. **Aucune couleur en dur.** Tout passe par une variable de
   `assets/tokens.css`. Si une teinte manque, on ajoute un jeton, on
   n'écrit pas un `#rrggbb` dans un composant.
2. **Sombre, sans bascule.** Il n'y a pas de sélecteur clair/sombre.
   Un passage en clair se ferait en réécrivant `tokens.css`.
3. **Filets, jamais de bordures.** `box-shadow: inset 0 0 0 1px` plutôt
   que `border` : ça ne prend pas de place dans la boîte.
4. **Un seul `.tb.go` orange par écran.** L'orange est l'action.
5. **Les capitales sont pour la machine.** Mono, très espacé, petit,
   pour les étiquettes et les mesures. La prose reste en bas de casse.
6. **Norelli ne contient que 54 signes** : A-Z, a-z et l'espace. Ni
   chiffre, ni accent, ni ponctuation. Elle ne sert que deux chaînes
   écrites pour elle — le logotype et le titre de la console. Tout le
   reste de l'affichage est en Venus Rising.

Les fontes sont commerciales ; c'est un outil interne, Cal a tranché :
elles restent dans le dépôt.

Origine du langage : `docs/reference/nl-bench.css`, extrait du banc NL.
Captures de référence : `docs/img/`.

## Le code

- `factory/` est la chaîne. `chain.py` porte les étages du visage au
  mesh, `cli_motion.py` le rig, les prises, la timeline et la cuisson ;
  chaque capacité a son module (`h3.py`, `mesh.py`, `rig.py`,
  `motion.py`) avec un moteur factice qui produit de vrais fichiers.
- **Conventions, partout** : mètres, Y en haut, +Z devant, +X à la
  gauche du personnage. Rotations locales relatives à la T-pose neutre
  SOMA, repères alignés sur le monde — la convention de Kimodo, lue
  dans son code. Bind en A-pose, la T-pose n'est qu'un repère.
- `data/soma77.json` : les 77 articulations SOMA, leur hiérarchie et
  la pose neutre, relevées dans Kimodo (Apache-2.0). Ne pas les
  retaper à la main : les régénérer depuis la source si elle change.
- Les workflows ComfyUI de `workflows/` sont au format API ; `REF n`
  reçoit les références, `OUT` est rapatrié, `{{…}}` est rempli.
- `js/llm.js` ne parle qu'un dialecte en interne — blocs de contenu
  Anthropic — et traduit vers la cible : OpenAI pour le DGX, natif pour
  Anthropic. **Ne pas répandre un second dialecte ailleurs.**
- `data/methodology.md` est le seul fichier à toucher pour changer le
  comportement du modèle de l'étage Identité. Il est chargé au démarrage.

## Les règles du brief tenues par le code

Ne pas les affaiblir sans une décision explicite de Cal.

- Le visage ne se verrouille qu'une fois (`project.py`).
- Aucun étage ne se lance avant que le précédent soit validé : plein
  pied après le visage, A-pose après le plein pied, vues après l'A-pose
  (`project.py`, `chain.py`). La planche H3 n'est plus un étage de
  validation : décision de Cal du 25/09, H3 ne sert plus qu'au
  turnaround de présentation.
- Le contrôle d'alignement des vues refuse au-delà de ±5°, bouclage à
  360° compris, et le mesh multi-vues refuse des vues qui ne l'ont pas
  passé (`chain.py`).
- Le rig refuse un bind en T-pose, demandé ou mesuré sur le squelette
  (`cli_motion.py`, `rig.py`).
- H3 n'a pas de prompt négatif : toute contrainte s'écrit en prose, et
  chaque référence porte un rôle nommé (`prompts.py`).

## GitHub Pages

Le site publié, https://calculment0r.github.io/Character_Sheet/, sert la
branche `claude/interactive-character-generator-5IwJo` (réglage du
dépôt). Depuis le 25/09/2026, on y fusionne la branche de travail : la
racine est la **page d'état** (`index.html` : les étages, ce qui tourne,
les vraies images, ce qui attend Cal), la console Identité est
`console.html`, le viewer `viewer.html`, le thème `theme.html`.

Mettre le site à jour, après avoir poussé la branche de travail :

```sh
git fetch origin
git checkout claude/interactive-character-generator-5IwJo
git merge --no-edit claude/epic-wright-y2kbrc
git push origin claude/interactive-character-generator-5IwJo
git checkout claude/epic-wright-y2kbrc
```

La page d'état s'écrit à la main : la tenir à jour à chaque avancée
réelle, avec les images d'un personnage sous `etat/` (JPEG compressés,
le dépôt est public). En ligne, la console et le viewer s'ouvrent mais
ne parlent ni au modèle de texte ni aux fichiers d'un personnage.
