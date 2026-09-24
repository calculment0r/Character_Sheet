# La chaîne en local

Tout tourne sur la machine. Pas de serveur, pas de file, pas de tunnel :
une commande par étage, `./usine <commande>`, et un dossier par
personnage sous `projects/`. L'API de `api/` reste dans le dépôt pour
plus tard ; la chaîne locale ne s'en sert pas.

---

## Démarrer

Dans l'environnement Python où vivent les modèles :

```sh
pip install -r requirements.txt     # numpy, Pillow
./usine doctor                      # ce qui tourne déjà : GPU, ComfyUI et ses nœuds, paquets
./usine doctor --ecrire             # range les moteurs trouvés dans factory.local.json
```

`./usine` prend `.venv/bin/python` (ou `.venv/Scripts/python.exe` sous
Windows) s'il existe, sinon `python3` ; `FACTORY_PYTHON=/chemin/vers/python`
en impose un autre.

### Depuis le PC de Cal (Windows), les modèles sur les DGX

La chaîne peut tourner sur le PC : elle ne fait que parler à ComfyUI,
qui tourne sur les DGX et y garde H3 résident. Sous Git Bash :

```sh
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt
echo '{ "comfyui_url": "http://192.168.10.205:8189" }' > factory.local.json
./usine doctor
```

`:8189` est l'instance `ComfyUI-H3TEST` de DGX1 : c'est elle qui a les
nœuds du workflow H3 retenu (Spectrum, Sol-Attn, modèle Singularity,
LoRA turbo). DGX2 a la même instance sur `192.168.10.247:8189`, avec
moins de modèles.

### H3, par ComfyUI

H3 tourne déjà dans ComfyUI : c'est le moteur par défaut. La chaîne lui
confie un workflow et récupère les images ; ComfyUI garde le modèle
chargé entre deux appels.

Le plus sûr est de partir du workflow H3 qui marche déjà :

1. dans ComfyUI, ouvrir le workflow Ref2VA qui donne de bonnes images ;
2. **Workflow → Export (API)** ;
3. `./usine gabarit export.json`

La commande marque le workflow et affiche chaque changement :

| Dans le workflow | Devient |
|---|---|
| les nœuds LoadImage, dans l'ordre | `REF 1`, `REF 2`, `REF 3` |
| le texte du prompt | `{{prompt}}` |
| la graine | `{{seed}}` |
| largeur, hauteur, nombre de frames | `{{width}}`, `{{height}}`, `{{frames}}` |
| le nœud SaveImage | `OUT` |

La sortie peut être un SaveImage sur les frames décodées (le mieux :
pas de recompression), une image animée, ou une vidéo, dont la chaîne
tire les frames par ffmpeg. Un étage qui a
moins de références que le gabarit n'a de nœuds REF retire ceux qui
restent vides, avec les liens qui partaient d'eux.

Pour l'orbite (`./usine vues --orbite`) : même geste avec
`--nom h3_orbit.json`, sur un workflow qui produit un plan long.

**Le gabarit en place** (`workflows/h3_ref2va.json`) vient du workflow
Ref2VA natif qui a tourné sur DGX1 (`MiniMaxH3ReferenceToVideo`, modèle
Singularity ref2va int8, LoRA turbo 4 pas, 8 pas, Sol-Attn, Spectrum).
Son export API, relevé dans les métadonnées d'une de ses images, est
gardé dans `tools/fixtures/h3_ref2va_native_api.json` ; le refaire :

```sh
./usine gabarit tools/fixtures/h3_ref2va_native_api.json --force
```

Ce que le vrai nœud impose, relevé sur `/object_info` :

- largeur et hauteur par pas de 32 : 768 × 768, 768 × 1344, 1344 × 768 ;
- frames sur la grille 17k + 5 : 5 pour une image, 124 pour l'orbite ;
- jusqu'à neuf images, sur l'entrée extensible `ref_images` ; dans le
  prompt, la première est `<Picture 1>`.

Le prompt suit le guide que MiniMax livre avec les poids
(`VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`, dans
`ComfyUI/models/diffusers/MiniMax-H3/docs/` sur DGX1) : `<Subject n>`
qui citent leurs `<Picture n>`, `[reference generation]`, une ligne de
rétention par sujet, `[Shot 1]`. Voir `factory/prompts.py`.

Si ComfyUI n'écoute pas sur `127.0.0.1:8188` :
`export FACTORY_COMFYUI_URL=http://127.0.0.1:<port>`.

---

## Le parcours

La fiche d'identité se remplit dans la page (`./usine page`, étage
Identité), puis **Exporter l'identité .json**.

```sh
./usine nouveau --identite vera_solen_identity.json
./usine visage vera-solen --variantes 4          # grille de portraits neutres
./usine visage-ok vera-solen 3                   # verrouille — une seule fois
./usine costume vera-solen veste --ref blouson.png --ref bottes.png
./usine pleinpied vera-solen --variantes 2
./usine pleinpied-ok vera-solen 1
./usine planche vera-solen --ab                  # avec et sans disque, même graine
./usine planche-ok vera-solen s002
./usine vues vera-solen                          # 0°, 90°, 180°, 270° et le 3/4
./usine prep vera-solen                          # détourage, recentrage, marges égales
./usine controle vera-solen                      # ±5°, refus explicite au-delà
./usine mesh vera-solen                          # TRELLIS 2 ; --moteur3d hunyuan3d-2.1
./usine voir vera-solen                          # le viewer 3D sur le dernier mesh
./usine rig vera-solen                           # SOMA 77, bind en A-pose, 5 poses de contrôle
./usine voir vera-solen --a rig:1                # regarder les cinq poses
./usine rig-ok vera-solen accepte
./usine prise vera-solen --kimodo "walks forward, stops, looks around" --frames 150
./usine prise vera-solen --video plan.mp4
./usine prise vera-solen --main poing
./usine timeline vera-solen main --piste body:t001 --piste body:t002:at=120:fondu=15 --piste hands_r:t003
./usine bake vera-solen main
./usine voir vera-solen --a bake:main
```

`./usine etat vera-solen` dit à tout moment où en est le personnage et
quelle commande vient ensuite. `./usine -h` et `./usine <commande> -h`
donnent toutes les options.

### Ce que la chaîne refuse

Les règles dures du brief sont des refus, pas des conseils :

- un second verrouillage du visage (§3) ;
- un plein pied sans visage verrouillé, une planche sans plein pied
  validé, des vues sans planche validée (§4, §5.6) ;
- un mesh multi-vues dont les vues n'ont pas passé le contrôle, ou l'ont
  raté : au-delà de ±5°, des vues mal alignées donnent un résultat pire
  qu'une seule image (§6.2) — `--une-vue` part alors du 3/4 ;
- un bind en T-pose, qu'il soit demandé (`--pose tpose`) ou mesuré sur
  le squelette : bras à plus de 70° de la verticale (§8).

### Les options qui comptent

| Commande | Option | Effet |
|---|---|---|
| `visage` | `--graine N` | verrouillage de graine : relancer en ne changeant que le prompt |
| `visage` | `--ref photo.jpg` | partir d'une photo, passe de normalisation |
| `planche` | `--ab` | deux planches même graine, avec et sans disque sur le visage (§5.5) |
| `vues` | `--orbite` | un plan en orbite redécoupé, au lieu d'une génération par vue |
| `prep` | `--delight` | passe de delight, si un moteur est réglé |
| `controle` | `--angle left=93` | un angle relevé à la main l'emporte sur l'angle demandé |
| `mesh` | `--moteur3d hunyuan3d-2.1` | Hunyuan3D au lieu de TRELLIS 2 — licence à lire |
| `timeline` | `--piste type:prise:at=…:fondu=…:poids=…:vitesse=…:in=…:out=…` | une piste |
| tout | `--moteur h3=stub` | forcer un moteur pour un seul appel |

---

## Les moteurs

| Capacité | Moteurs | Défaut | Sur la machine |
|---|---|---|---|
| `h3` | `comfyui`, `python`, `stub` | `comfyui` | DGX1, `ComfyUI-H3TEST` :8189 |
| `prep` | `comfyui` (BiRefNet), `builtin`, `rembg` | `comfyui` | DGX1 :8189 |
| `delight` | `off`, `hunyuan` | `off` | — |
| `trellis` | `comfyui`, `python`, `stub` | `stub` | DGX2 :8188, nœuds natifs TRELLIS.2 |
| `hunyuan3d` | `python`, `stub` | `stub` | nœud présent sur DGX2 mais cassé (numpy 2.5) |
| `unirig` | `python` (ssh), `stub` | `stub` | DGX2, `~/UniRig` |
| `kimodo` | `python` (ssh), `stub` | `stub` | DGX2, `~/kimodo` |
| `sam3dbody` | `python`, `stub` | `stub` | mesure d'azimut : ComfyUI DGX1 |

Le choix se fait par `FACTORY_<CAPACITÉ>=…`, par `factory.local.json`
(`./usine doctor --ecrire`), ou le temps d'un appel par `--moteur`.

Le réglage en place sur le PC de Cal :

```json
{
  "comfyui_url": "http://192.168.10.205:8189",
  "comfyui_url_trellis": "http://192.168.10.247:8188",
  "backends": {"h3": "comfyui", "prep": "comfyui", "trellis": "comfyui"},
  "remote_kimodo": "dgx2", "python_kimodo": "/home/dgx/kimodo/.venv/bin/python",
  "remote_unirig": "dgx2", "python_unirig": "/home/dgx/UniRig/.venv/bin/python", "cwd_unirig": "/home/dgx/UniRig"
}
```

- `comfyui_url_<capacité>` envoie une capacité sur un autre ComfyUI que
  H3 : la 3D part sur DGX2, qui a la mémoire libre, H3 reste sur DGX1.
- Kimodo et UniRig n'ont pas de nœud ComfyUI : `factory/remote.py` copie
  les fichiers sur la machine, lance `tools/remote/<nom>_entry.py` dans
  le venv du modèle, rapatrie le résultat. Il refuse de lancer si l'alias
  ssh répond avec le nom de l'autre DGX (ce sont des clones).
- `./usine doctor` valide à blanc chaque gabarit contre le ComfyUI qui le
  sert (nœuds présents, fichiers de poids connus) et vérifie les venvs
  par ssh. Il n'exécute rien.

### Les vues, en pratique

Une génération par vue ne tient pas les angles avec H3 (voir
`HANDOFF.md`) : passer par l'orbite.

```sh
./usine vues <perso> --orbite     # 124 frames, BiRefNet sur chaque frame, frames choisies sur la silhouette
./usine prep <perso>              # BiRefNet, recentrage, même échelle
./usine controle <perso>          # angles estimés sur la silhouette ; --mesurer : SAM 3D Body
```

**Le factice** (`stub`) produit de vrais fichiers — PNG sur fond neutre,
GLB PBR, GLB skinné, prises NPZ — aux bonnes dimensions et avec la bonne
géométrie, pour que tout l'aval se vérifie. Chaque image porte
l'étiquette FACTICE et `./usine etat` marque « (factice) » tout ce qu'il
a produit.

---

## Le dossier d'un personnage

```
projects/vera-solen/
  project.json                   le manifeste : ce qui est choisi, validé, verrouillé
  face/cand-001.png …            les variantes, avec leur .json (prompt, graine, moteur)
  face/contact.png               toutes les variantes côte à côte
  face/locked.png                le visage qui fait autorité
  costumes/veste/
    refs/                        les images de vêtement
    fullbody/cand-001.png …      fullbody.png, le plein pied validé
    sheets/s001/sheet.png        prompt.txt à côté, les six sections Ref2VA
    views/raw/front.png …        la sortie brute de H3, par vue
    views/prepared/front.png …   détourées, recentrées, même échelle — elles seules vont en 3D
    views/prepared/prep.json     les mesures, avant et après
    mesh/v001/model.glb          albedo.png, metallic.png, roughness.png, normal.png à côté
    rig/v001/rigged.glb          skeleton.json, bind_delta.npz, rig.json
  takes/t001/motion.npz          une prise : rotations locales SOMA 77, racine, cadence
  bake/main/anim.npz             la timeline cuite, et anim.glb qui la joue
```

---

## Les conventions

- **Mètres, Y en haut, +Z devant, +X à la gauche du personnage** —
  celles de glTF, de SOMA et du viewer.
- **Rotations** : locales, relatives à la T-pose neutre de SOMA, dans
  des repères alignés sur le monde — la convention même de Kimodo, lue
  dans son code. Une prise Kimodo se joue donc telle quelle.
- **Bind en A-pose** : le mesh est lié bras à 45° ; la T-pose n'est
  qu'un repère. Le delta de bind est calculé sur les articulations du
  personnage et rangé dans `bind_delta.npz`.
- **Racine** : mise à l'échelle des hanches du personnage, continue
  d'une prise à l'autre sur la timeline.

`data/soma77.json` porte les 77 articulations SOMA, leur hiérarchie et
la pose neutre, relevées dans le dépôt de Kimodo ; `data/hand_poses.json`
la bibliothèque de mains — détendue, ouverte, poing, pince, pointer.

---

## Vérifier

```sh
python3 tools/chain_check.py
```

Mène un personnage du visage à la timeline cuite sur les moteurs
factices, puis refait le trajet H3 → ComfyUI contre un faux ComfyUI
(`tools/mock_comfy.py`). Il rejoue aussi le skinning glTF comme le
ferait un viewer : bras levés au-dessus de la tête, hanches plus basses
en accroupi, racine continue. Rend 0 si tout passe.
