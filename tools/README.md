# Outils

## `chain_check.py` — la chaîne locale, de bout en bout

Mène un personnage du visage à la timeline cuite par `./usine`, sur les
moteurs factices, et vérifie à chaque étage les fichiers, le manifeste
et les refus du brief. Rejoue le skinning glTF comme un viewer pour
juger le rig sur des chiffres : mains au-dessus de la tête bras levés,
hanches plus basses en accroupi, racine continue d'une prise à l'autre.
Refait enfin le trajet H3 → ComfyUI contre le faux ComfyUI ci-dessous.

```sh
python3 tools/chain_check.py      # numpy et Pillow suffisent
```

Rend 0 si tout passe. À lancer après toute modification de `factory/`.

## `mock_comfy.py` — un faux ComfyUI

Parle les routes dont la chaîne se sert (`/upload/image`, `/prompt`,
`/history`, `/view`…) et rend autant de frames que le workflow en
demande, à la taille demandée, le mannequin factice vu sous l'azimut que
le prompt décrit — une frame nette, les autres floues. Refuse un
workflow où un `{{…}}` n'est pas rempli, ou qui cite une image jamais
envoyée.

```sh
python3 tools/mock_comfy.py 8199
FACTORY_COMFYUI_URL=http://127.0.0.1:8199 ./usine visage <perso>
```

`fixtures/h3_export_api.json` est un exemple d'export API, celui que
`chain_check.py` fait adopter par `./usine gabarit`.

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
