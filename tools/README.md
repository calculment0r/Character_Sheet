# Outils

## `mock_llm.py` — un faux serveur de modèle

Parle le dialecte OpenAI sur `/v1/models` et `/v1/chat/completions`, et
rend une séquence fixe : d'abord deux appels d'outils qui remplissent la
fiche, puis un widget à puces, puis une phrase de clôture.

Il sert à vérifier la chaîne complète sans mobiliser un GPU, et à
isoler un problème : si le tour passe contre lui et échoue contre le
vrai modèle, le défaut est dans le modèle ou dans la méthode, pas dans
le code du front.

```sh
python3 tools/mock_llm.py          # écoute sur 8812
```

Puis, dans un autre terminal :

```sh
FACTORY_LLM_URL=http://127.0.0.1:8812 ./start.sh
```

## `e2e.mjs` — la vérification bout en bout

Pilote un vrai navigateur contre une Factory en marche : chargement,
console, reconnaissance du moteur sans réglage, entrée dans un étage,
tour complet, widget, retour, vue étroite, santé de l'API, erreurs de
console.

```sh
npm install playwright        # une fois
node tools/e2e.mjs http://127.0.0.1:8000
```

Rend 0 si tout passe, 1 sinon — utilisable tel quel dans un CI.

Si Chromium est déjà installé ailleurs :

```sh
CHROME_PATH=/chemin/vers/chrome node tools/e2e.mjs
```

## La séquence complète, depuis un dépôt frais

```sh
python3 tools/mock_llm.py &
FACTORY_LLM_URL=http://127.0.0.1:8812 PORT=8000 ./start.sh &
sleep 3
node tools/e2e.mjs http://127.0.0.1:8000
```
