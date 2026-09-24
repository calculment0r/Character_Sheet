# Character Factory

Chaîne souveraine : prompt ou photo → visage validé → costumes → planche
→ vues orthogonales → mesh PBR → rig SOMA → animation. **Tout tourne en
local, sur la machine** — pas d'API pour l'instant, décision de Cal.

Cadrage : `docs/BRIEF_CHARACTER_FACTORY.md`
Mode d'emploi de la chaîne : `docs/LOCAL.md` — **à lire en premier**
Reprise du travail : `docs/HANDOFF.md`
État technique : `docs/ARCHITECTURE.md`

## Lancer

```sh
./usine doctor            # ce qui tourne déjà : GPU, ComfyUI et ses nœuds H3, paquets
./usine gabarit export.json   # adopter le workflow H3 de ComfyUI (export API)
./usine page              # la page, pour l'étage Identité
./usine etat <perso>      # où en est un personnage, et la commande suivante
```

Une commande par étage, un dossier par personnage sous `projects/`,
un manifeste `project.json`. H3 passe par ComfyUI (moteur par défaut) ;
les autres capacités sont factices tant que `./usine doctor` n'a pas
trouvé leur modèle.

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

Après une modification de la page (`index.html`, `js/`, `assets/`) :

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
  pied après le visage, planche après le plein pied, vues après la
  planche (`project.py`, `chain.py`).
- Le contrôle d'alignement des vues refuse au-delà de ±5°, bouclage à
  360° compris, et le mesh multi-vues refuse des vues qui ne l'ont pas
  passé (`chain.py`).
- Le rig refuse un bind en T-pose, demandé ou mesuré sur le squelette
  (`cli_motion.py`, `rig.py`).
- H3 n'a pas de prompt négatif : toute contrainte s'écrit en prose, et
  chaque référence porte un rôle nommé (`prompts.py`).

## GitHub Pages

Le site publié sert la branche `claude/interactive-character-generator-5IwJo`,
pas `main` ni la branche de travail. Tant que le réglage Pages pointe
là, la refonte n'est pas visible en ligne — ce qui n'a pas d'importance
tant que tout tourne en local.
