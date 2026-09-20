# BRIEF — CHARACTER FACTORY

Document de cadrage destiné à une session Claude Code.
Repo cible : `https://github.com/calculment0r/Character_Sheet`
Rédigé le 20 septembre 2026. Toutes les versions de modèles sont à revérifier au démarrage.

---

## 0. OBJECTIF

Chaîne complète et souveraine : **prompt ou photo → visage validé → costumes → character sheet → vues orthogonales → mesh 3D PBR → rig compatible SOMA/Kimodo → animation pilotée par vidéo ou par contraintes → timeline multi-couches**.

Tout tourne sur les DGX. Le front est une page statique servie par GitHub Pages, accessible depuis n'importe quel réseau, qui parle à une API exposée depuis le DGX.

Non-objectif : ne pas réimplémenter les modèles. On orchestre des briques existantes.

---

## 1. ARCHITECTURE SYSTÈME

### 1.1 Découpage

```
GitHub Pages (statique, three.js)      ← le front, aucun secret
        │  HTTPS
        ▼
Cloudflare Tunnel (cloudflared)        ← pas d'ouverture de port sur le LAN
        │
        ▼
API FastAPI (DGX-01)                   ← auth par token, rate limit
        │
        ├── Redis + RQ (file de jobs)
        ├── MinIO (S3 on-prem, URLs présignées)
        └── Workers GPU (un process par capacité)
              h3 | hunyuan3d | trellis | sam3dbody | kimodo | unirig | mediapipe
```

### 1.2 Règles

- **Aucun secret dans le repo.** Le front ne connaît que l'URL de l'API et un token utilisateur saisi dans l'UI, stocké en `localStorage`.
- **Tous les artefacts** (images, meshes, NPZ, textures) transitent par MinIO en URLs présignées. Le front ne télécharge jamais depuis le DGX directement.
- **Progression** en SSE (`GET /jobs/{id}/events`), pas de polling.
- **Un worker = une capacité = un modèle résident.** Les modèles ne sont jamais chargés à la demande : temps de démarrage prohibitif (H3, Kimodo, Hunyuan3D-Paint).
- **Montée à deux DGX** : les workers portent un label `node`. La file RQ est partagée. Aucun état local au worker sauf le cache de poids. Prévoir ça dès le départ même si on démarre sur un seul DGX.

### 1.3 Endpoints minimaux

```
POST /characters                      créer un personnage
POST /characters/{id}/face            générer/itérer le visage
POST /characters/{id}/costumes        créer un costume
POST /costumes/{id}/sheet             générer la character sheet (H3)
POST /costumes/{id}/views             générer les vues orthogonales (H3)
POST /costumes/{id}/mesh              lancer Hunyuan3D ou TRELLIS
POST /assets/{id}/rig                 auto-rig + retarget SOMA
POST /takes                           vidéo/images → mouvement (SAM 3D Body ou Kimodo)
POST /takes/{id}/layers               couche mains/visage (MediaPipe/HaMeR)
POST /timelines/{id}/bake             mixage → NPZ + glTF final
GET  /jobs/{id}/events                SSE
```

---

## 2. MODÈLE DE DONNÉES

```
Character
  id, name, style: "photoreal" | "stylized"
  face_refs[]            images d'entrée fournies par l'utilisateur
  face_prompt
  face_locked_url        le visage validé, autorité pour tout le reste
  identity               { mhr_identity[45], mhr_scales[68] }   ← rempli à l'étape rig

Costume
  id, character_id, name
  refs[]                 images de vêtement
  prompt
  status: draft | validated

Sheet
  id, costume_id
  panels[]               { role, azimuth, elevation, url }
  h3_job_id, prompt_used

OrthoViews
  id, costume_id
  front_url, left_url, back_url, right_url, threequarter_url
  delighted: bool

Asset3D
  id, costume_id, engine: "hunyuan3d-2.1" | "trellis2"
  mesh_url, albedo_url, metallic_url, roughness_url, normal_url
  version, parent_version

Rig
  id, asset3d_id
  skeleton: "soma77"
  bind_pose: "apose" | "tpose"
  bind_delta_url         matrices de passage bind → T-pose SOMA
  skin_url

Take
  id, source: "video" | "images" | "authored"
  body_engine: "sam3dbody" | "kimodo"
  npz_url                somaskel77
  fps, frame_count

Timeline
  id, character_id, costume_id
  tracks[]  { type: "body"|"hands_l"|"hands_r"|"face", take_id, in, out, weight, time_warp }
```

Règle dure : **une seule identité MHR par personnage**, figée à la première validation et réutilisée partout. Si l'identité change entre deux costumes ou deux keyframes, les longueurs d'os bougent et tout le reste devient incohérent.

---

## 3. ÉTAPE 1 — LE VISAGE

Deux entrées possibles, le reste de la chaîne est identique :

- **Prompt seul** : génération d'un portrait. Modèle image laissé au choix de l'implémenteur (FLUX, SDXL, Qwen-Image), mais il doit tourner en local.
- **Image fournie** : on part de la photo, avec une passe de normalisation (cadrage, lumière neutre, regard caméra).

Le paramètre `style` (photoreal / stylized) n'est pas un simple mot dans le prompt : il conditionne aussi le choix du modèle 3D en aval (voir §7) et les réglages de delight.

**Livrable de l'étape** : un portrait neutre validé — lumière égale, bouche fermée, regard caméra, fond uni. C'est l'image qui fera autorité sur l'identité dans toutes les générations H3 suivantes.

UI : grille de variantes, sélection, relance avec verrouillage de seed. Bouton « valider le visage » qui fige `face_locked_url`.

---

## 4. ÉTAPE 2 — LES COSTUMES

N costumes par personnage, indépendants. Entrée : images de vêtement, ou description texte, ou les deux.

Génération d'un plein pied habillé, en gardant le visage verrouillé comme référence d'identité.

Pratique documentée à reprendre pour les fiches de costume pures (sans identité) : H3 répond bien à la consigne du **mannequin mat uniforme, sans visage, sans yeux, sans cheveux, sans texture de peau**, quand on veut documenter un vêtement sans que le modèle invente un visage. À utiliser si on ajoute un mode « fiche vêtement seule ».

**Livrable** : un plein pied de référence par costume, validé par l'utilisateur, qui devient l'entrée de l'étape 3.

---

## 5. ÉTAPE 3 — LA CHARACTER SHEET (MiniMax H3)

### 5.1 Ce qu'est H3

MiniMax H3, alias Hailuo 03 : système omni-modal qui génère de la vidéo avec audio synchronisé, 4 à 15 secondes, jusqu'à 2K par l'API, **768 px de petit côté en natif sur les poids ouverts**. Deux variantes : FL2VA (texte + première/dernière frame) et **Ref2VA** (jusqu'à 9 images, 3 vidéos, 3 audios, 12 fichiers maximum) — c'est Ref2VA qu'on utilise.

Poids ouverts sous MiniMax H3 Community License, code et scripts de déploiement chez MiniMax-AI. L'encodeur est Qwen3-VL-32B (états cachés de la couche 50), le VAE visuel est en f16t4d24. La release initiale n'expose que l'attention pleine, la version sparse est annoncée.

**Arbitrage à trancher avec Cal** : poids locaux à 768 px (souverain, gratuit, plus basse résolution) contre API à 2K (0,13 $/s, sortie du réseau). Voir §14.

### 5.2 La ruse des 5 frames

H3 est un modèle vidéo mais rien n'oblige à l'utiliser comme tel. **Cinq frames — le minimum du nœud, soit environ 0,2 s — suffisent à produire une planche figée.** On exporte une frame, c'est la planche. Coût dérisoire par rapport à un clip.

C'est la méthode retenue pour la character sheet.

### 5.3 Règles de prompt H3

- **Pas de prompt négatif.** Toute contrainte s'écrit en prose dans le positif. Les templates officiels finissent par des lignes du type « aucun texte, sous-titre, logo ni filigrane ».
- **Chaque référence doit avoir un rôle explicite et nommé** : « Image 1 définit le visage, les cheveux, l'âge et l'identité du personnage A », « Image 2 définit le costume ». Sans ça, le modèle pioche dans toutes les images et le résultat devient imprévisible.
- **Format Ref2VA en six sections** issu du guide officiel de réécriture : `subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, `non_diegetic_music`. Les deux dernières restent vides ou neutres, on ne veut pas de son.
- **Bloc « prendre tel quel »** : écrire explicitement que chaque pièce, sangle, boucle, couleur et finition conserve exactement ce qu'elle a dans les références, que rien n'est redessiné, simplifié, rangé, embelli ou amélioré, et que la planche documente un design existant, pas une création.
- Fond uni neutre, échelle constante, lumière constante d'un panneau à l'autre.

### 5.4 Layout de la planche — correction de la maquette fournie

La maquette de Cal contient trois pleins pieds (face, dos, 3/4) et quatre gros plans. **Il manque le profil pur.** Or l'étage 3D a besoin de vues orthogonales alignées (voir §6.2). Layout corrigé :

**Rangée haute — pleins pieds, mêmes marges, même échelle :**
1. FULLBODY FRONT — azimut 0°
2. FULLBODY LEFT — azimut 90°
3. FULLBODY BACK — azimut 180°
4. FULLBODY RIGHT — azimut 270°
5. FULLBODY 3/4 — azimut 45° (pour le mode image unique, voir §7.2)

**Rangée basse — gros plans visage :**
6. CLOSE UP FACE / neutre
7. CLOSE UP PROFIL
8. CLOSE UP 3/4
9. CLOSE UP expressions (smile / sad / nervous) — une variante par génération, pas dans la même frame

### 5.5 Le disque sur le visage

Cal a lu qu'il vaut mieux masquer le visage d'un disque dans les panneaux plein pied pour que H3 aille chercher l'identité dans les gros plans HD. **Je n'ai pas trouvé de source qui documente cette astuce précise.** Ce qui est documenté, c'est la logique adjacente : le mannequin sans visage pour les fiches de costume, et surtout le fait que **H3 n'a pas de prompt négatif et fonctionne par attribution explicite de rôles**.

Implémentation retenue, qui couvre les deux hypothèses :
1. **Attribution en prose, obligatoire** : « le visage du personnage est défini exclusivement par Image 1 ; dans les panneaux plein pied, la tête sert uniquement à la silhouette et aux proportions et ne constitue pas une référence d'identité ».
2. **Disque neutre optionnel**, activable par un flag `mask_face_in_fullbody`, appliqué en post sur les crops plein pied avant de les renvoyer comme références aux générations suivantes.

À tester en A/B dès la première semaine et à trancher sur pièces. Ne pas inscrire l'astuce en dur.

### 5.6 Piège de résolution — point important

Une frame 2K découpée en cinq pleins pieds donne environ 400 à 500 px de large par panneau. **C'est insuffisant pour alimenter Hunyuan3D.**

Donc : **la planche sert à valider le design et la cohérence, pas à nourrir la 3D.** Une fois la planche validée, chaque vue orthogonale est régénérée en plein cadre, une génération H3 par vue, à la résolution maximale disponible, la planche étant passée en référence. C'est l'étape 4.

---

## 6. ÉTAPE 4 — LES VUES ORTHOGONALES

### 6.1 Méthode

Pour chaque vue de `{front 0°, left 90°, back 180°, right 270°}` : une génération H3 Ref2VA de 5 frames, plein cadre, avec en références le visage verrouillé, le plein pied du costume et la planche validée. Le prompt décrit l'angle exact et la pose imposée (§8).

Alternative à évaluer : une seule génération d'un **orbite continu** (caméra qui tourne autour d'un sujet immobile sur fond neutre) puis extraction des frames aux azimuts voulus. Avantage : lumière et identité parfaitement constantes par construction. Inconvénient : il faut retrouver l'azimut exact des frames, et la tolérance est serrée.

**À benchmarker en premier**, c'est le choix qui structure tout l'étage 3D. Critère de décision : écart angulaire mesuré sur les frames extraites, et dérive d'identité entre la première et la dernière frame.

### 6.2 Les trois règles non négociables

Documentées côté Hunyuan multi-vues, et elles valent aussi pour TRELLIS :

1. **Même personnage, même lumière** sur les quatre images. Une lumière différente fait déduire une géométrie différente à silhouette égale.
2. **Angles alignés** : face à 0°, profil à 90°, dos à 180°, **tolérance ±5°**. Un profil tiré à 70° est un trois-quarts, et le mesh sort vrillé.
3. **Cadrage de silhouette constant** : même marge autour du personnage sur chaque image. Si la face remplit le cadre et que le profil a 30 % de vide d'un côté, la passe de normalisation se perd.

**Des entrées mal alignées donnent un résultat pire qu'une seule image.** Prévoir une passe de contrôle automatique (détection de silhouette, recentrage, normalisation d'échelle, égalisation de marge) avant d'envoyer au 3D, et un refus explicite avec message si l'écart est hors tolérance.

### 6.3 Detourage et delight

- **Détourage** : alpha propre, fond supprimé. BiRefNet ou rembg.
- **Delight** : passer les vues dans `hunyuan3d-delight-v2-0` (1.3B) qui retire l'éclairage cuit dans l'image avant génération de forme et de texture. Améliore nettement la texture sur source studio — et les sorties H3 sont des sources studio.

---

## 7. ÉTAPE 5 — LA 3D

### 7.1 Les deux moteurs, gardés tous les deux

**Hunyuan3D 2.1** — leader texture. Deux modèles ouverts : Hunyuan3D-DiT (forme, flow matching + ShapeVAE) et Hunyuan3D-Paint (diffusion multi-vues conditionnée mesh, sortie albedo / metallic / roughness). Pipeline complet autour de 29 Go de VRAM. Licence communautaire, **pas MIT, avec une exclusion de territoire signalée pour l'UE, le Royaume-Uni et la Corée du Sud — à faire lire ligne à ligne avant toute mise en production depuis Paris.**

**TRELLIS 2** — Microsoft, MIT, aucune restriction d'usage. Géométrie très détaillée, une sortie décodable en mesh, gaussiennes ou champ de radiance, environ 16 Go de VRAM.

Implémenter les deux derrière une interface `MeshEngine` commune : `generate(views, style, options) -> Asset3D`. Le choix par défaut se fait sur le `style` et sur la contrainte de licence du projet en cours. Exposer le choix dans l'UI.

### 7.2 Mono-vue contre multi-vues

- Le modèle multi-vues natif de la famille Hunyuan (`Hunyuan3D-2mv`, 1.1B) prend **jusqu'à 4 vues front / left / back / right**, combinées par un embedding sinusoïdal 1-D de position de vue. Les slots sont ordonnés : il faut respecter l'ordre.
- Le multi-vues **réduit d'environ moitié l'écart de silhouette** par rapport à l'image unique.
- En mono-vue, **une vue de face donne souvent un dos plat ou creux ; le 3/4 marche mieux.** D'où le panneau 3/4 conservé dans la planche.
- **À vérifier au démarrage** : Hunyuan 3D 3.1 expose un mode multi-vues natif (4 images : face, profil, dos, détail optionnel). Si les contraintes de licence passent, c'est probablement le meilleur chemin. Cal a demandé de rester sur 2.1 — le noter comme option, ne pas l'imposer.

### 7.3 Sortie

GLB unique, textures embarquées en buffer views. Canaux PBR séparés conservés à part pour l'inspection dans le viewer.

---

## 8. LA POSE DE RÉFÉRENCE — T-POSE OU A-POSE

Cal a raison sur le fond : en T-pose, les épaules remontent quand on rabat les bras, parce que le skinning a été calculé à 90°.

### 8.1 Décision

**Générer et modéliser en A-pose**, bras à environ 45° du torse, paumes vers l'intérieur, doigts légèrement écartés, jambes légèrement ouvertes. Meilleure déformation d'épaule, meilleure prédiction de skinning, et le mesh ne fusionne pas bras/torse.

### 8.2 Pourquoi ce n'est pas incompatible avec Kimodo

Kimodo écrit ses NPZ SOMA avec **la T-pose comme pose zéro**, et les rotations SOMA sont relatives à l'orientation de joint en T-pose. Mais SOMA fournit exactement l'outillage pour changer de référentiel :

- `SOMALayer.get_reference_pose(data_key="t_pose_world")` retourne les orientations monde `(J,3,3)` dans l'ordre des joints publics
- `SOMALayer.convert_reference(rotations, from_ref, to_ref)` réexprime des rotations `(B,77,3,3)` dans un autre référentiel, en préservant les rotations locales absolues
- `pose(..., pose2rot=False, reference_pose=...)` consomme le résultat

Donc : **bind pose en A-pose côté personnage, delta de bind stocké dans `Rig.bind_delta_url`, conversion appliquée au retarget.** On ne modélise jamais en T-pose.

### 8.3 Consigne de pose dans le prompt H3

Écrire la pose en prose, explicitement, identique sur les quatre vues : bras à 45°, paumes vers l'intérieur, doigts séparés et visibles, pieds écartés à largeur de hanches, poids réparti, regard horizontal, aucune torsion du bassin ni des épaules, aucune emphase de perspective.

---

## 9. ÉTAPE 6 — LE VIEWER 3D

Page statique, three.js, chargement du GLB depuis URL présignée.

**Fonctions demandées :**
- Orbit, pan, zoom (`OrbitControls`)
- Plusieurs éclairages commutables pour juger les matériaux PBR : studio neutre trois points, HDRI extérieur jour, HDRI intérieur chaud, contre-jour dur, lumière plate sans ombre (contrôle albedo)
- Affichage canal par canal : rendu final, albedo seul, metallic, roughness, normal, wireframe
- Grille au sol, échelle en mètres, silhouette de référence humaine pour juger la taille
- Comparaison A/B entre deux versions ou entre les deux moteurs, côte à côte ou en volet coulissant

**Contraintes UI (préférences Cal, à respecter strictement) :**
- **Thème clair par défaut. Aucune bascule sombre, nulle part.** Accent orange acide et non terreux — la palette exacte est à faire valider par Cal avant de la figer.
- Pas de border-radius excessif, UI dense de type outil professionnel.
- Zoom sémantique plutôt qu'un second niveau ouvert au clic.
- Panneau latéral : se ferme au clic n'importe où hors du panneau, permute si on clique un autre élément.

---

## 10. ÉTAPE 7 — L'AUTO-RIG

### 10.1 Renverser la question

Ni SAM 3D Body ni Kimodo n'attendent que le mesh soit riggé d'une façon précise. SAM ne consomme qu'une image. Kimodo produit du mouvement sur **le squelette SOMA à 77 joints publics**. Le personnage doit juste pouvoir recevoir ce mouvement : c'est un retarget, pas un squelette imposé.

### 10.2 Le squelette SOMA

78 joints internes indexés 0 à 77, joint 0 étant un Root virtuel toujours identité. **77 joints exposés**, `poses[0]` = Hips, qui porte la rotation globale du corps. Répartition :

| Index public | Nombre | Région |
|---|---|---|
| 0-3 | 4 | Hips, Spine1, Spine2, Chest |
| 4-10 | 7 | Neck1, Neck2, Head, HeadEnd, Jaw, LeftEye, RightEye |
| 11-38 | 28 | Bras + main gauche (épaule, bras, avant-bras, main, 5 doigts) |
| 39-66 | 28 | Bras + main droite |
| 67-71 | 5 | Jambe + pied gauche (jambe, tibia, pied, base orteil, bout orteil) |
| 72-76 | 5 | Jambe + pied droite |

Unité native du rig SOMA : **centimètres**. Sortie par défaut en mètres via `output_unit`. Y up, +Z devant, main droite. Vérifier à chaque frontière.

Kimodo réduit en interne vers `somaskel30` : les doigts, la mâchoire et les yeux ne sont pas pilotés par le modèle de mouvement. C'est exactement le trou que comble l'étape 9.

### 10.3 Chemin retenu

1. **UniRig** (code MIT, SIGGRAPH 2025, VAST-AI + Tsinghua) : prédiction autorégressive de la hiérarchie de squelette puis poids de skinning par Bone-Point Cross Attention. Surveiller **SkinTokens**, son successeur annoncé, qui unifie les deux étapes. `Make-It-Animatable` est l'alternative à garder sous le coude.
2. **Mapping de noms** UniRig → `SOMALayer.public_joint_names`, établi une fois et stocké en preset versionné. Ne pas le recalculer par personnage.
3. **Delta de bind** A-pose → T-pose SOMA, stocké dans le rig.
4. **Validation automatique** : cinq poses de contrôle (bras le long du corps, bras levés, accroupi, torsion, pas de marche), rendu des cinq, contrôle visuel dans le viewer. Un rig qui passe les cinq est accepté.

### 10.4 Option à proposer dans l'UI

Pour l'humanoïde pur, **ne pas générer de mesh libre du tout et utiliser SOMA comme corps** : il arrive déjà riggé, skinné, avec ses LOD (18 056 / 4 505 / 612 sommets) et un template USD. On perd le style, on gagne zéro retarget et zéro risque de skinning. À exposer comme mode « base paramétrique » à côté du mode « mesh généré ».

---

## 11. ÉTAPE 8 — L'ANIMATION

### 11.1 Deux sources de mouvement corps

**SAM 3D Body** — vidéo ou photos d'entrée. Feed-forward, une image entre, des paramètres MHR de forme et de pose sortent. Promptable par masques et keypoints 2D.

Limites à coder autour, elles sont structurelles :
- **Aucun modèle temporel, inférence par frame.** Sur vidéo : trajectoires discontinues, identité qui saute, détecteur qui décroche quand le sujet est petit, flou ou occlus.
- Occlusion partielle : tient. **Majeure partie du corps invisible : meshes distordus et instables.** Un plan buste donne des jambes hallucinées et instables d'une image à l'autre.

Remèdes à implémenter :
- **SAM-Body4D** (sans entraînement) : masklets temporellement alignés de SAM 3 en guidage, plus raffinement d'occlusion avant inférence.
- **Lissage en fenêtre glissante dans l'espace latent MHR**, avec verrouillage de la forme et de l'échelle par piste, et optimisation contact-aware du root pour tuer le glissement de pied.
- **Règle métier** : sur un plan où seul le buste est visible, ne pas corriger SAM. Ne contraindre que ce qui est vu (mains, torse) et laisser Kimodo générer les jambes.

**Kimodo** — texte plus contraintes cinématiques. Voir §12 pour le détail des contraintes. C'est la source pour le mouvement inventé, l'in-between et le complément de ce que la vidéo ne montre pas.

### 11.2 Conversion MHR → SOMA

SAM 3D Body sort 45 paramètres d'identité, 204 paramètres de pose, 72 d'expression faciale. SOMA prend MHR comme **backend d'identité par défaut** : `identity_model_type="mhr"`, K = 45, plus `scale_params (B, 68)` **requis** pour MHR.

Le trou est sur la pose : **204 paramètres momentum → 77 axe-angle SOMA**. Trois routes, à trancher (voir §14) :
- mapping de joints puis `convert_reference` — exact, demande d'établir la correspondance de noms
- `PoseInversion` depuis le mesh MHR posé — robuste, plus lourd, moins exact sur le twist
- détour par SMPL-X — outillage existant, erreur cumulée sur deux conversions

Simplification réelle : la contrainte `fullbody` de Kimodo porte sur les **positions** issues de la FK, pas sur les rotations. Une erreur de twist qui ne déplace pas le joint est invisible.

### 11.3 Timeline et mixage

Pistes typées : `body`, `hands_l`, `hands_r`, `face`. Chaque piste référence un Take, avec `in`, `out`, `weight`, `time_warp`.

- Mixage par masque de joints, pas par fondu global. Le corps vient d'une piste, les doigts d'une autre, le visage d'une troisième.
- Crossfade sur les rotations en quaternions (slerp), jamais sur les matrices.
- Bake final : NPZ `somaskel77` plus un canal d'expression MHR 72, plus export glTF avec `AnimationClip`.
- Conventions d'interaction demandées : **ALT + molette change l'échelle du temps, molette seule avance et recule.**

---

## 12. KIMODO — CONTRAINTES ET PIÈGES

Modèle de diffusion de mouvement, environ 17 Go de VRAM dont l'essentiel vient de l'encodeur de texte (LLM2Vec sur LLaMA-3-8B). Code Apache-2.0, poids sous NVIDIA Open Model License. **Utiliser `Kimodo-SOMA-RP-v1`** (700 h de mocap, usage commercial ouvert). Ne pas utiliser la variante SMPL-X, sous licence recherche.

### 12.1 Appel

`load_model("Kimodo-SOMA-RP-v1", device="cuda")` une fois au démarrage, modèle résident dans le worker. Puis par requête :

`model(prompts, num_frames, num_denoising_steps, constraint_lst=[...], cfg_weight=[texte, contrainte], num_samples=N, first_heading_angle=θ, post_processing=True, return_numpy=True)`

Retour : `local_rot_mats`, `global_rot_mats`, `posed_joints`, `root_positions`, `smooth_root_pos`, `foot_contacts`, `global_root_heading`. `multi_prompt=True` enchaîne des segments avec `num_transition_frames` de recouvrement — c'est ce qui donne la timeline multi-prompts.

`TextEncoderAPI(url)` existe et `load_model(text_encoder=...)` accepte un encodeur pré-construit : **l'encodeur de texte est déportable sur un autre process ou un autre DGX.** Un fork ajoute la quantisation bitsandbytes NF4/FP4/Int8 sur LLM2Vec et fait tomber le pic de 15 Go à environ 5 Go. À évaluer.

### 12.2 Schéma de contraintes

Tableau JSON, un objet par contrainte, chacun avec `type` et `frame_indices` :
- `root2d` : `smooth_root_2d [T,2]`, heading optionnel
- `fullbody` : `local_joints_rot [T,J,3]` en **axe-angle radians**, `root_positions [T,3]` où Y est la hauteur absolue des hanches (environ 0,96 m debout)
- `left-hand`, `right-hand`, `left-foot`, `right-foot`, ou `end-effector` avec `joint_names`

Une contrainte à la frame 0 n'est pas obligatoire.

Ne pas réimplémenter le chargement JSON → objets de contraintes : **réutiliser le loader du CLI Kimodo.**

### 12.3 Pièges

- Tout est canonicalisé : **XZ du root à (0,0) à la frame 0.** L'UI ne doit pas laisser poser le premier waypoint ailleurs.
- L'orientation initiale n'est pas dans le JSON, c'est `first_heading_angle` en radians, 0 = face au +Z. Sinon elle est comptée deux fois.
- `global_root_heading` attend `[cos θ, sin θ]` par frame, pas un radian.
- **Pas d'IK au sens solveur, et pas d'interactivité.** Chaque tirage de main est une regénération complète. Pour répondre au geste en temps réel dans le viewer : solveur IK côté client (`CCDIKSolver`) pour le geste, Kimodo pour la passe de validation.
- **Aucun conditionnement objet ni scène.** On ne tagge pas un verre. « Prendre un verre et boire » se code en contraintes end-effector à la position de l'objet aux bonnes frames, plus le prompt. Le modèle ne sait pas qu'il y a un verre, il sait où va la main.
- Tracer un chemin et le faire suivre : c'est `root2d`. La vitesse ne se demande pas, elle se déduit — distance divisée par le nombre de frames. Marche ou course vient du prompt et de la densité temporelle.

---

## 13. ÉTAPE 9 — LA COUCHE MAINS ET VISAGE

C'est le point faible identifié par Cal, et il est réel : Kimodo travaille sur `somaskel30`, donc **aucun doigt, aucune mâchoire, aucun œil**. SAM 3D Body estime bien mains et pieds mais reste fragile.

### 13.1 Mains

- **HaMeR** ou équivalent dédié à la récupération de main 3D donne de meilleures rotations que MediaPipe. MediaPipe Hand Landmarker ne fournit que 21 points par main, en 2D/2.5D : il faut une passe d'ajustement pour en tirer des rotations de joints.
- Cible : les 5 doigts de chaque chaîne SOMA (index publics 11-38 et 39-66).
- Prévoir une bibliothèque de poses de main figées (poing, main ouverte, pince, pointer) utilisable quand aucune source n'est disponible — c'est ce qui sauvera 80 % des plans.

### 13.2 Visage

- **MediaPipe Face Landmarker** sort 52 coefficients de blendshape ARKit. C'est la source la plus simple et la plus stable.
- Cible : les **72 paramètres d'expression MHR**. Écrire une table de correspondance ARKit 52 → MHR 72, établie une fois, stockée en preset. C'est une régression linéaire à caler sur un jeu de poses, pas un mapping un pour un.
- Alternative : piloter directement des blendshapes ARKit sur le mesh généré, et ne pas passer par MHR. Plus simple, mais impose de générer les blendshapes sur le mesh.

### 13.3 Mixage

Les trois couches arrivent dans la timeline comme des pistes indépendantes avec leur propre masque de joints. Aucune ne doit écraser les autres. Un lissage temporel séparé par couche, les mains et le visage ayant des constantes de temps très différentes du corps.

---

## 14. ARBITRAGES À TRANCHER AVEC CAL AVANT DE CODER

À présenter en QCM, avec la conséquence de chaque option :

1. **H3 local 768 px** (souverain, gratuit, plus basse définition) **ou API 2K** (0,13 $/s, sortie du réseau, qualité supérieure pour la 3D) **ou hybride** : local pour l'itération, API pour la passe finale.
2. **Vues orthogonales** : quatre générations séparées (contrôle d'angle exact, risque de dérive d'identité) **ou** un orbite continu découpé (lumière et identité constantes, angles approximatifs).
3. **Conversion de pose MHR → SOMA** : mapping de joints + `convert_reference`, `PoseInversion`, ou détour SMPL-X.
4. **Moteur 3D par défaut** : TRELLIS 2 (MIT, aucune restriction) ou Hunyuan3D 2.1 (meilleure texture, licence à exclusion territoriale).
5. **Disque sur le visage** : à décider sur A/B, pas a priori.

---

## 15. ORDRE DE CONSTRUCTION

**Lot 1 — plomberie.** API FastAPI, Redis + RQ, MinIO, Cloudflare Tunnel, auth, SSE, front statique vide déployé sur GitHub Pages. Un worker factice qui renvoie une image de test. Critère : depuis un réseau extérieur, lancer un job et voir la progression.

**Lot 2 — H3.** Worker H3 résident, génération de planche en 5 frames, builder de prompt Ref2VA en six sections, UI visage puis costumes. Critère : une planche cohérente à partir d'un prompt et de deux images.

**Lot 3 — vues et 3D.** Vues orthogonales, contrôle d'alignement automatique, détourage, delight, TRELLIS 2 puis Hunyuan3D 2.1, viewer three.js avec éclairages et canaux. Critère : un GLB PBR inspectable, jugé bon par Cal.

**Lot 4 — rig.** UniRig, mapping SOMA, delta de bind A-pose, suite de cinq poses de validation. Critère : le personnage prend une pose Kimodo sans épaule cassée.

**Lot 5 — animation.** Worker Kimodo, worker SAM 3D Body, timeline, mixage, bake. Critère : une vidéo d'entrée produit une animation jouée par le personnage.

**Lot 6 — couches.** Mains et visage, bibliothèque de poses de main, table ARKit → MHR. Critère : un plan buste donne des mains et un visage crédibles.

---

## 16. POINTS À REVÉRIFIER AU DÉMARRAGE

Le domaine bouge vite, tout ce qui suit était vrai au 20 septembre 2026 et doit être reconfirmé :

- État exact des poids ouverts H3 et résolution native réelle
- Termes de la MiniMax H3 Community License, en particulier le périmètre régional et le seuil de chiffre d'affaires
- Licence Hunyuan3D 2.1, clause d'exclusion territoriale UE / Royaume-Uni / Corée du Sud
- Termes de redistribution de la SAM License pour SAM 3D Body, dépôt Hugging Face sous acceptation de conditions
- Disponibilité de SkinTokens en remplacement d'UniRig
- Existence d'un exporteur glTF officiel côté Kimodo (à ce jour : NPZ, AMASS npz, CSV MuJoCo uniquement — le glTF est à écrire dans le wrapper)
- Hunyuan 3D 3.1 et son mode multi-vues natif, si la licence convient
