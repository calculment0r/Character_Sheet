# Character Factory

Chaîne souveraine : prompt ou photo → visage validé → costumes → planche
→ vues orthogonales → mesh PBR → rig SOMA → animation. Le front est une
page statique ; tout le calcul tourne sur les DGX.

Cadrage : `docs/BRIEF_CHARACTER_FACTORY.md`
Reprise du travail : `docs/HANDOFF.md` — **à lire en premier**
État technique : `docs/ARCHITECTURE.md`

## Lancer

```sh
./check.sh     # ce qui tourne déjà sur la machine, et quoi lancer
./start.sh     # la Factory : page et API sur un seul port
```

L'API sert aussi la page. Une seule origine, donc pas de CORS, et rien à
coller dans l'écran MOTEUR : le front reconnaît son origine comme moteur.

## Vérifier

```sh
python3 tools/mock_llm.py &                      # un faux modèle, sans GPU
FACTORY_LLM_URL=http://127.0.0.1:8812 ./start.sh &
node tools/e2e.mjs http://127.0.0.1:8000         # npm install playwright
```

`tools/e2e.mjs` rend 0 si tout passe. **Le lancer après toute
modification du front.**

## Le thème — règles dures

**`theme.html` est le catalogue.** Il charge les mêmes CSS que
l'application : ouvrir cette page suffit à voir tous les jetons, les
quatre fontes et chaque composant réel. Il n'y a jamais deux vérités.

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

Origine du langage : `docs/reference/nl-bench.css`, extrait du banc NL.
Captures de référence : `docs/img/`.

## Le code

- `js/llm.js` ne parle qu'un dialecte en interne — blocs de contenu
  Anthropic — et traduit vers la cible : OpenAI pour le DGX, natif pour
  Anthropic. **Ne pas répandre un second dialecte ailleurs.**
- Le repli entre moteurs ne se déclenche que sur une panne de transport.
  Un 4xx qui refuse la requête ne bascule pas : elle échouerait pareil
  en face.
- `data/methodology.md` est le seul fichier à toucher pour changer le
  comportement du modèle. Il est chargé au démarrage.
- Un worker a un contrat minuscule, voir `api/workers/stub.py` :
  `run(*, report, job_id="", **kwargs) -> dict`.

## Trois règles du brief tenues par le code

Ne pas les affaiblir sans une décision explicite de Cal.

- Le visage ne se verrouille qu'une fois ; un second appel rend 409.
- Le rig refuse un bind en T-pose : la conversion vers la T-pose SOMA
  est un delta, pas un bind.
- Le contrôle d'alignement des vues refuse au-delà de ±5°.

## GitHub Pages

Le site publié sert la branche `claude/interactive-character-generator-5IwJo`,
pas `main` ni la branche de travail. Tant que le réglage Pages pointe
là, la refonte n'est pas visible en ligne.
