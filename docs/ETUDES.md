# Études et essais — pose, vues, planche (25/09/2026)

Écrit après cinq études menées en parallèle (planches de personnage,
contrôle de pose, vues pour la 3D, inventaire de DGX2, décisions passées
de Cal) et des essais réels sur DGX2 avec `essai-atelier`. **À lire
avant de toucher aux étages plein pied, pose, vues, planche ou mesh.**
[V] = vérifié dans une source ou par un essai, [I] = inférence.

---

## 1. Les décisions de Cal (relevées dans les sessions)

- Toutes les images qu'on valide sortent de **Qwen-Image 2.1** (génère et
  édite), en **INT8 + LoRA turbo Viggle v0.2.1**. H3 ne fait plus que le
  **turnaround de présentation**, une fois tout validé en HD.
- Le **plein pied se fait en pose naturelle** ; une fois validé, on le
  remet en **A-pose** (« notre techno pour passer en modèle 3D préfère
  A-pose ») pour la planche, les vues et le mesh.
- La pose s'impose **par un squelette** (façon ControlNet), pas par le
  prompt.
- Portraits en gros plan, sans vêtements.
- Façon de travailler : chercher (sessions passées, mémoire, gabarits
  ComfyUI de DGX2, Hugging Face) **avant** de choisir ; faire de vraies
  études ; ne pas lui faire tout vérifier.
- Référence de planche de Cal : la planche « Ollie » — turnaround face,
  3/4, profil, 3/4 dos, dos ; expressions en gros plan ; détails ;
  palette ; comparaison de taille ; poses d'action.

## 2. La remise en A-pose — essayée, elle marche

**Méthode** [V, essai 25/09] : `tools/remote/apose_skeleton.py` relève le
plein pied validé par **DWPose** (comfyui_controlnet_aux, onnxruntime
CUDA : `yolox_l.onnx` + `dw-ll_ucoco_384.onnx`, téléchargés dans
`custom_nodes/comfyui_controlnet_aux/ckpts/`), redresse les bras à 45°
et les jambes à 4° en gardant les longueurs d'os (les mains suivent
l'avant-bras en bloc), recadre et dessine un squelette OpenPose (corps,
mains, visage). Qwen-Image 2.1 turbo reçoit le plein pied en `<image1>`
et le squelette en `<image2>` — **une simple référence, sans ControlNet
ni LoRA** — et rend 1344 × 1792.

- 4 essais sur 4 en A-pose, tenue intacte, visage tenu, mains propres,
  aucun trait du squelette recopié. 40 à 65 s l'image.
- Le visage verrouillé en `<image3>` n'apporte rien de visible.
- Pièges : `detect_poses` rend des **pixels** (pas des coordonnées
  normalisées) ; la taille de sortie doit suivre celle du squelette ;
  les bras à 45° demandent un cadre plus large que 9:16.
- Même méthode publiée [V] : workflow `qwen_image_2_1_image_edit_openpose.json`
  de nomadoor (PR #139 de Comfy-with-ComfyUI), même base INT8, prompt :
  « Change the pose of the character in <image1> to match the pose in
  <image2>. Preserve the character's identity, clothing, appearance, and
  art style from <image1>. »

**Encodeur** [V] : ComfyUI #16435 — `TextEncodeQwenImage21` à
`resolution=1024` peut couvrir une édition d'un bruit fin ; 992 ou 1056
sont propres. Comparé sur le même rendu : 1056 est un peu plus fin.
`qwen21.generate(..., resolution=1056)`.

**Autres voies, écartées pour l'instant** :
- `alibaba-pai/Qwen-Image-2.1-Fun-Controlnet-Union` (vrai ControlNet,
  pose comprise) : support natif ComfyUI pas fusionné (PR #16519), nœuds
  tiers T8 ou b2renger ; compatibilité turbo et références non
  documentée.
- LoRA de pose, d'angles ou de planche pour Qwen-Edit 2509/2511 (AnyPose,
  Multiple-Angles fal/dx8152, tarn59 turnaround, InstantX, DiffSynth) :
  **faites pour le 20B, inutilisables sur Qwen-Image 2.1 (7B)**, et
  l'échec est silencieux (image presque identique à sans LoRA).

## 3. Les vues — essayées

Trois voies comparées sur `essai-atelier`, depuis son A-pose de face :

| Voie | Résultat |
|---|---|
| Planche 4 cases en une image (2048 × 1152) | cohérente ; profils justes (bras le long du corps, comme il se doit vus de côté) ; mais ~900 px de haut par silhouette, bras de la face coupés, cases inégales |
| Une édition par vue, prompt seul | HD, dos juste ; **profils faux** : bras tendus vers l'avant à 45° au lieu d'écartés sur le côté |
| **Une édition par vue + squelette tourné** | HD, **profils, dos et côtés justes**, même échelle et même ligne de sol ; les 3/4 tournent trop peu (~20° au lieu de 45°) |

**Retenu** : une édition par vue, l'A-pose de face en `<image1>` et le
squelette A-pose **tourné à l'azimut de la vue** en `<image2>`
(`apose_skeleton.py --vues=45,90,180,270,315`). Le squelette est le plan
du corps (Z = 0), la tête reçoit une profondeur (nez, yeux), les points
cachés sont retirés (dos : oreilles seules ; profil : l'œil et l'oreille
du côté vu). Toutes les vues partagent le cadrage de la face : c'est ce
qu'attend la reconstruction. Azimut : 0 face, 90 flanc gauche du
personnage (+X, il regarde le bord gauche de l'image), 180 dos, 270 flanc
droit. ~50 s la vue en 1344 × 1792.

**Mesuré (25/09, soir)** — SAM 3D Body par ComfyUI (`sam3d.py`), premier
passage réel, 10 s pour cinq vues, signe confirmé : profil gauche 93,8°,
dos 179,0°, profil droit 265,2° → contrôle ±5° passé sur angles mesurés.
Le 3/4 (demandé 45°) sortait à 320° : tourné du **mauvais côté**, ce que
le contrôle, limité aux quatre vues du mesh, ne voyait pas.

Corrigé en trois temps :
- **tête en cylindre** dans le squelette (`view()` de `apose_skeleton.py`) :
  chaque point du visage a son angle autour de l'axe du cou et se cache
  derrière — une tête plate tournée se lisait comme une face étroite ;
- **direction dans l'image** dans le prompt des 3/4 (« vers le bord gauche
  de l'image »), comme pour les profils ;
- **mesurer puis choisir** (`chain.views`, `qwen21-pose`) : chaque vue est
  mesurée dès qu'elle sort, relancée sur la graine suivante si elle
  s'écarte (±5° pour les vues du mesh, ±10° pour le 3/4), trois essais au
  plus, la plus proche gardée, son angle mesuré passé au contrôle.
  Justification : même corrigé, Qwen rate un 3/4 sur deux environ
  (mesures : 315° demandé → 321, 322, 325, 349 ; 45° → 0, 342, 32, 360).

**Écarté** :
- `template_qwen_Image_2512_360_lora` : un **panorama équirectangulaire**
  (décor), pas une orbite de personnage [V].
- LoRA orbite `ML-Intern-lab/Qwen-Image-2.1-viewpoint-orbit-LoRA`
  (`qwen21_viewpoint_orbit_lora`, sur DGX2) : entraîné sur des **objets**
  (Google Scanned Objects), 90° est son cas le plus dur [V] ; et ses clés
  MLP (`gate_layer`, `proj`) ne correspondent pas au `gate_up` fusionné
  de la base ComfyUI — une partie risque d'être ignorée au chargement
  [V, inventaire]. Méthode `qwen21-orbit` de `views_qwen.py` à tester
  avant tout usage.
- Modèles multi-vues dédiés (MV-Adapter SDXL, PSHuman, Era3D, SV3D) :
  angles exacts mais fidélité et résolution bien en dessous de Qwen 2.1.
  MV-Adapter reste un secours comme guide de silhouette.

## 4. Ce qu'attend le mesh

- **Pixal3D multi-vues** (TRELLIS.2, `Pixal3DMultiViewConditioning`) :
  **quatre vues au plus — face, gauche, dos, droite**, FOV 20, caméras à
  0/90/180/270° à hauteur des yeux ; le cadrage est la caméra (pas de
  recadrage indépendant par vue) ; alpha comme masque [V]. Le gabarit
  officiel `3d_pixal3d_multi_views` accepte aussi une planche 4 vues qu'il
  découpe. Tous ses poids sont sur DGX2 ; `workflows/trellis2_mv.json` en
  est tiré.
- Le sens de « left » (flanc gauche du personnage ou gauche du
  spectateur) n'est pas confirmé : le vérifier sur un personnage
  asymétrique.
- Les 3/4 ne nourrissent pas Pixal3D : ils servent la planche et le
  contrôle (rendre le mesh à 45° et comparer).
- Hunyuan3D 2.1 n'a pas de multi-vues pour la forme ; seul Hunyuan3D-2mv
  (base 2.0) en a, et ses poids ne sont pas sur DGX2.

## 5. La planche

**Branchée (25/09, soir)** d'après le workflow Civitai trouvé par Cal,
« Qwen Image 2.1 Character Reference Sheet Generator – Face + Wardrobe +
Pose » (nikhilprasanth, https://civitai.com/models/2960890 ; le zip
demande une connexion, méthode relevée sur sa page et ses captures) :
identité, tenue et mise en page données à part — visage, planche de
garde-robe, image de mannequin (face, dos, buste) qui ne donne que la
composition — et une planche 1920 × 1088 en trois cases : face et dos en
pied, gros plan tête et épaules.

Notre version (`chain.sheet`, moteur `qwen21`, après l'A-pose) [V, essai
sur `essai-atelier`, 8 planches, ~66 s l'une] : `<image1>` le visage
verrouillé **coupé au-dessus du col**, `<image2>` l'A-pose validée (la
tenue exacte), `<image3>` une mise en page faite des squelettes A-pose
(face, dos, buste agrandi).
- Le portrait verrouillé entier fait passer son haut dans le gros plan
  (t-shirt noir au lieu du hoodie), même en l'interdisant en prose : il
  faut le couper au-dessus du col.
- Mise en page en squelettes contre mannequin rendu d'après eux : le
  mannequin est plus joli mais le gros plan perd la tenue (col de pull,
  pas de capuche) ; les squelettes la tiennent (zip, cordons, capuche).
- Face et dos en A-pose, même tenue, même visage, même échelle.

**Le workflow lui-même, rejoué** (zip téléchargé par Cal ; JSON et
`Mannequin.png` gardés hors du dépôt, public ; `tools/remote/civitai_sheet_try.py`
lit les prompts dans le JSON). Ce qu'il fait vraiment : modèle de base
INT8 **sans turbo**, 30 pas, CFG 3,5, long prompt négatif, encodeur à
`resolution` 0, sortie 16:9 d'un mégapixel (1344 × 768) ; `<image1>` le
mannequin (bras le long du corps), `<image2>` le visage, `<image3>` une
planche de garde-robe générée d'abord depuis une image de la tenue.
Sur `essai-atelier` [V, 25/09] :
- la garde-robe **invente** harnais, holster, ceinturon et un hoodie
  délavé taché : son prompt énumère « harnesses, holsters, straps,
  weathering » et le modèle les dessine — et ils passent dans toutes les
  planches tirées d'elle ;
- avec notre mannequin en A-pose, la base remet les bras le long du
  corps ; le gros plan porte un t-shirt dans trois planches sur quatre ;
- 217 s la garde-robe, 375 à 430 s la planche, contre 66 s la nôtre.

Notre version (A-pose validée comme tenue, squelettes comme mise en page,
visage coupé au col, turbo) reste celle de la chaîne. Le mode « base »
(pas, CFG, négatif) est gardé dans `qwen21.generate(base=…)`.

Deux usages, deux méthodes [V, pratique publiée ; I, choix] :

- **Pour la chaîne** : pas de planche générée d'un coup. Les vues HD,
  chacune contrôlée, sont la matière du mesh.
- **Pour Cal (présentation, façon Ollie)** : la planche **se compose en
  code** à partir des morceaux validés — turnaround = les vues ;
  expressions = gros plans du visage édités par Qwen depuis le visage
  verrouillé ; détails = recadrages du plein pied HD (rien d'inventé) ;
  palette = k-means sur le plein pied ; comparaison de taille = la hauteur
  du personnage ; poses d'action = éditions guidées par squelette. Même
  démarche que le gabarit officiel « Character Turnaround Sheet
  Generator » et le nœud FLUX.2 Klein de muse-collective (génération par
  morceaux, composition à la fin).
- Une planche en une image (prompt « ONE AND THE SAME man… », format
  proche du carré selon le réécriveur PE-I2I de Qwen 2.1) peut servir de
  témoin de cohérence, jamais d'entrée au mesh.
- Les deux gabarits de planche de DGX2 (`templates-character_sheet`,
  `api_google_nano_banana_create_turnaround_sheet`) passent par l'API
  Gemini : pas en local.

## 6. Inventaire DGX2 utile (25/09)

- Prêts : Qwen 2.1 INT8 + turbo Viggle, Qwen-Edit 2509/2511 fp8 et leurs
  LoRA angles, DWPose, Sapiens/Sapiens2, SAM 3D Body, BiRefNet, Pixal3D
  multi-vues, TRELLIS.2, Hunyuan3D 2.1.
- `TextEncodeQwenImage21` et `QwenImage21Cache` n'existent que sur
  ComfyUI `:8188`.
- Absents : SeedVR2 (poids), base SDXL pour SUPIR, SDPose, Pixal3D image
  unique, Hunyuan3D-2mv, tout ControlNet Qwen.

## 7. Sources principales

- Viggle turbo : https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo
- Workflow pose nomadoor : https://github.com/nomadoor/Comfy-with-ComfyUI/pull/139
- Bug encodeur : https://github.com/Comfy-Org/ComfyUI/issues/16435
- Fun ControlNet 2.1 : https://huggingface.co/alibaba-pai/Qwen-Image-2.1-Fun-Controlnet-Union · https://github.com/Comfy-Org/ComfyUI/pull/16519
- Orbite 2.1 : https://huggingface.co/ML-Intern-lab/Qwen-Image-2.1-viewpoint-orbit-LoRA
- Pixal3D : https://github.com/TencentARC/Pixal3D · https://docs.comfy.org/tutorials/3d/pixal3d
- Dérive des LoRA caméra : https://arxiv.org/html/2609.04603
- Planches : https://github.com/muse-collective-26/muse-character-sheet-klein · https://huggingface.co/Qwen/Qwen-Image-2.1-PE-I2I · https://note.com/sepiablue/n/n02026f718c3f
