# Reprise — à lire en premier (état au 28/09/2026, soir)

La session du 28/09 (soir) s'arrête parce qu'elle est pleine. Lire ce
fichier, `CLAUDE.md`, la mémoire
(`C:\Users\calcu\.claude\projects\C--Character-Factory\memory\`), puis
`docs/BRIEF_CAL_2026-09-28.md` : tout ce que Cal a dit le 28/09, mot pour
mot, §1 à §11.

## Règles de travail (Cal, répétées, toutes fermes)

- **ssh / scp / curl seulement** vers `dgx1` et `dgx2`. Sur le PC : lire,
  écrire des fichiers, git — rien d'autre (pas de python, tar, node, Edge
  en local ; pas d'artifact ; jamais le navigateur intégré ni Chrome ; aucun
  outil qui demande une autorisation). Captures : Chromium sans affichage
  **sur DGX2**. Le dire en tête du brief de chaque sous-agent (deux l'ont
  enfreint le 28/09).
- **Chercher avant de faire, rien inventer** : chaque choix technique part
  de la documentation du modèle visé et d'un essai, avec sa source ; « non
  documenté » plutôt qu'une supposition. Vérifier qu'une proposition ne
  contredit pas une décision de Cal avant de la lui faire (le 28/09 on a
  rouvert Qwen-Edit 2509/2511 alors que Qwen-Image 2.1 est décidé : faute).
- **Juste par construction** : une méthode robuste, pas des filtres qui
  vérifient et relancent au cas par cas. Les mesures sont des garde-fous.
- **Les deux DGX en parallèle** : DGX1 porte le studio, DGX2 est son miroir
  exact et calcule en même temps (`comfyui_peers` dans `factory.local.json`
  de chacun ; A-poses et vues se répartissent seules). Tout ajout (modèle,
  nœud, paquet) sur les deux, par le câble direct 169.254.110.6 ↔
  169.254.42.193.
- **Le lien pour tester à chaque réponse** : **http://192.168.10.247:8765/**
  (Tailscale http://100.108.108.65:8765/). Le studio tourne sur DGX1, mais
  le Wi-Fi de DGX1 est accroché en 2,4 GHz (135 ms, 5 % de pertes) : on
  passe par DGX2 (5 GHz), où `tools/relay.py` renvoie vers DGX1 par le
  câble direct. Le relais ne survit pas à un redémarrage : sur DGX2,
  `cd ~/Character_Factory && setsid nohup python3 tools/relay.py 8765
  169.254.110.6:8765 > ~/relay_studio.log 2>&1 < /dev/null &`.
  Viewer d'un rig : `viewer.html?src=%2Ffiles%2F<slug>%2Fcostumes%2F<k>%2Frig%2Fv00N%2Frigged.glb`.
- Réponses en français, courtes, factuelles, avec images ; dire ce qui est
  moche. Télécharger : demander d'abord (nom, source, taille).

## Ce qui a été fait le 28/09 au soir (13 commits, poussés, tirés sur les deux DGX)

- **A-pose depuis n'importe quelle pose** : squelette cible reconstruit
  droit et symétrique (`tools/remote/apose_skeleton.py::canonical`, visage
  redressé) ; consigne « de face, carré », et « chaque détail d'un seul côté
  y reste » (`pose.py`, `SQUARE`, `SIDES`) ; mesure durcie (`apose_pick.py` :
  tronc, niveau, genoux, jambes, pieds, tête ; SAM 3D Body refuse un bassin
  ou un buste tourné de plus de 4°) ; 4 propositions par tour.
- **Vues** : squelette des profils avec les bras 10° en avant et le bras du
  fond caché ; chaque consigne dit où sont les bras ; SAM 3D Body mesure la
  place des bras dans chaque vue et relance (`sam3d.body`, `arms_placed`) ;
  deux vues par étape, une par machine.
- **Rig sur l'anatomie** (`rig_unirig.anatomy`) : les articulations SOMA
  sont posées sur la vue de face relevée par DWPose (corps, 21 points par
  main), reportées dans le mesh ; orteils sur le pied du mesh ; poids
  UniRig versés à l'os SOMA le plus proche. Proportions stylisées gardées.
  `docs/ETUDES.md` §10.
- **Survêt riggé** de bout en bout avec tout ça (A-pose n° 21, vues à ±5°,
  mesh v1, rig v1 accepté) :
  http://192.168.10.247:8765/viewer.html?src=%2Ffiles%2Fmj-survet%2Fcostumes%2Ftenue-1%2Frig%2Fv001%2Frigged.glb
- **La page personnage** (plus d'onglets) : visage en grand et ses looks,
  plein pied et tenues, qui il est (IA), expressions (ajouter / écarter /
  annuler), 3D dans la page, voix, taille, palette, tenues, où il en est ;
  un seul orange. Anciens onglets en étapes plein cadre (`#/p/<slug>/visage`…).
- **Serveur** : `factory/looks.py` (look = retouche Krea 2 du visage
  verrouillé, qui ne change jamais), `expression_add / remove / restore`
  (expressions en mouvement du visage, pas en noms d'émotion).
  `chain_check` : 67/67.
- **Miroir** vérifié : code, venvs de la chaîne, nœuds, modèles de la chaîne
  (7 copiés vers DGX1), Ollama `qwen3-vl-32b-32k`. Les venvs ComfyUI
  diffèrent (numpy, transformers…). H3 :8189 arrêté sur les deux
  (`sudo systemctl start comfyui-h3test` pour le turnaround ou l'orbite).
- **cloudflared** 2026.9.3 installé sur les deux DGX (accord de Cal), rien
  ne tourne.
- Bug corrigé : les cartes « Ce qui attend » répondent enfin.

## En cours au moment de la coupure

Deux sous-agents travaillaient ; leurs changements NE sont PAS commités.
Faire `git status` : relire, tester (`chain_check` sur une copie DGX2),
intégrer ou jeter.
- **Le pont** : `tools/bridge.py` (+ `tools/character-factory-bridge.service`),
  port 8770 sur DGX1 : `GET /bridge/health` public (DGX1, DGX2, studio,
  ComfyUI, H3, Ollama, mémoire), `POST /bridge/start|stop` protégé (en-tête
  Cloudflare Access ou LAN). Un panneau « Machines » dans le studio. Les
  **propriétaires** : `owner` par personnage, « les miens / tous », refus
  d'agir sur celui d'un autre sauf `admins`. Un bloc « Les machines » dans
  `index.html`. Il écrit aussi dans `docs/CLOUDFLARE.md`, mais sur l'idée
  d'un domaine, que Cal n'a pas : à reprendre (voir plus bas).
- **L'audit de sécurité** du studio avant exposition est fini (rapport
  complet hors dépôt, qui est public :
  `C:\Users\calcu\AppData\Local\Temp\claude\C--Character-Factory\a996411d-2100-440b-9bb5-690c404b1245\scratchpad\audit_securite.md`).
  Verdict : **ne rien ouvrir sur internet en l'état**. Corrigé le 28/09 :
  la lecture de fichiers par `..` dans `_static` (commit 01aea49). Reste :
  - critique : aucune authentification dans le studio, qui écoute sur
    toutes les interfaces (le relais de DGX2 aussi) → studio sur
    127.0.0.1 derrière le tunnel, jeton Access vérifié dans `_dispatch` ;
  - critique : pas de droits par utilisateur (détruire, verrouiller un
    visage, annuler le travail d'un autre) → propriétaires et admins
    (c'est ce que fait le pont, non commité) ;
  - haut : un upload EPS passe par Ghostscript → n'ouvrir que
    PNG/JPEG/WEBP ;
  - haut : requêtes forgées depuis une autre page → cookie Access en Lax,
    contrôle de `Origin` / `Sec-Fetch-Site` avant chaque écriture ;
  - haut : file GPU sans quota par personne.
  Rien trouvé en XSS ni en injection de commande ; `/v1` n'est pas un
  proxy ouvert.

## La direction pour la suite : une plateforme Cloudflare (Cal, 28/09 soir)

Cal n'a **pas de nom de domaine** (c'est pour ça que tout passe par GitHub
Pages). Il a un **compte Cloudflare avec R2**. Ses mots : « on va se faire
un truc en Cloudflare où les trucs seront hébergés dessus car j'ai un
compte R2 pour stocker les trucs. on va faire une porte d'entrée car de
toutes façons on va mettre d'autres outils dessus. mais mes outils iront
taper les DGX pour load/unload les modèles et faire le travail, certains
auront besoin d'un agent aussi » — « comme le modèle avec qui on itère pour
caractériser notre perso par exemple ».

Donc :
- les pages de ses outils hébergées chez Cloudflare (sous *.workers.dev ou
  *.pages.dev, faute de domaine), les fichiers dans R2 ;
- une **porte d'entrée commune** à tous ses outils (ses amis y entrent) ;
- les outils **appellent les DGX** pour charger / décharger les modèles et
  faire le travail : c'est le rôle du pont (`/bridge`) et d'un tunnel
  sortant depuis les DGX (cloudflared est installé) ;
- certains outils ont **un agent** (l'étage Identité : le modèle avec qui on
  caractérise le personnage).

Avant de construire : lire la documentation Cloudflare à jour (compétence
`cloudflare`, `agents-sdk`, `wrangler` disponibles) sur ce qui marche **sans
domaine** : Workers et Pages sur workers.dev, Access / Zero Trust sur
workers.dev, un Worker qui joint un service privé derrière un tunnel sans
nom d'hôte public, R2 pour les images et les meshes, Agents SDK pour les
agents. Proposer l'architecture à Cal avec les sources, avant d'écrire.

## Décisions qui attendent Cal

1. **Licence de Qwen-Image 2.1** : « research or evaluation purposes only »
   depuis le 20/09/2026
   (https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE). Si
   l'outil produit pour de vrai, l'A-pose et les vues ne peuvent plus
   passer par lui.
2. **Méthode « géométrie d'abord »** (étude
   `…\scratchpad\canon\ETUDE_CANON.md`, planche `comparaison_survet.jpg`,
   script `…\scratchpad\canon\scripts\canon_body.py`) : SAM 3D Body → corps
   mis en A-pose EN 3D → squelette 3D projeté à chaque azimut (cachés
   retirés) → Qwen 2.1. Face propre, profils aux bras le long du corps,
   logo du bon côté ; 13 s par personnage ; tout est installé. À intégrer
   dans `pose.skeletons` (l'ancien squelette 2D en secours) — c'est la
   méthode robuste demandée. Piège vu : Qwen peut encore rendre un profil
   du mauvais côté (le contrôle d'azimut le rattrape). Option de l'étude,
   à soumettre : ne plus générer les profils mais les rendre depuis le mesh
   texturé (le mesh n'utilise que face et dos).
3. **Wi-Fi de DGX1** : la connexion `nick&su` est forcée en `band bg`
   (2,4 GHz) alors que la box émet en 5 GHz (canal 116, mieux capté). La
   commande (réglage système : à lancer par Cal) n'a pas pris :
   `ssh -J dgx2 dgx@169.254.110.6 'sudo nmcli connection modify "nick&su" 802-11-wireless.band a && sudo nmcli connection up "nick&su"'`.
4. **Objets** (étude `…\scratchpad\objets\ETUDE_OBJETS.md`) : chaîne
   séparée des personnages. TRELLIS.2 n'accepte officiellement qu'une
   image (carré serré, fond détouré, 1024) ; aucun modèle installé ne fait
   les vues d'un objet justes (essai botte : Krea 2 1/4, Qwen 2.1 0/4).
   Nos gabarits : `trellis2_single.json` marge 1,1 au lieu de 1,0,
   `trellis2_mv.json` recadre chaque vue à part.

## À faire ensuite, dans l'ordre

1. Intégrer ou jeter le travail du pont et de l'audit (ci-dessus).
2. Méthode « géométrie d'abord » dans la chaîne ; refaire **Manteau** (même
   défaut que Survêt : buste tourné de 15–20°) et re-rigger Chauve, Costaud,
   Garçon, Marionnette avec le rig sur l'anatomie.
3. La plateforme Cloudflare (étude d'abord, sans domaine).
4. Les poses naturelles tirées des images de Cal (banque de squelettes,
   `…\scratchpad\research_poses.md`) ; la planche les utilise.
5. Les expressions en mouvement (H3 A → B, `…\scratchpad\research_expressions.md`).
6. « Je change quelque chose → ce qui est refait » : le moteur des
   dépendances (maquette : http://192.168.10.247:8765/docs/img/maquettes/fiche.html#suite ;
   carte des données : `…\scratchpad\data_map.md`).
7. Le rangement en dossiers et la page objet (maquette :
   http://192.168.10.247:8765/docs/img/maquettes/objets.html).
8. Les défauts du look « cicatrice » (trop marquée, tête tournée) : la
   consigne est resserrée, à revoir en image ; Krea 2 met UltraReal sur un
   personnage illustré né d'une phrase (bug de `krea2.generate`).

## Machines

- **DGX1** : studio (`cd ~/Character_Factory && PYTHONUNBUFFERED=1 setsid
  nohup ./usine studio > studio.log 2>&1 < /dev/null &`, arrêt
  `pkill -f "[m] factory studio"` ; il relit `factory.local.json` au
  démarrage seulement), ComfyUI :8188 (`~/ComfyUI/venv`), Ollama. D'autres
  projets de Cal y tournent (marlin, reelbench, nirva-*, audiolab) : ne pas
  y toucher. Une autre session envoie parfois des rendus H3 sur :8188.
- **DGX2** : miroir (studio arrêté ; ses `projects/mj-*` sont d'anciennes
  copies marquées « running » : ne pas y lancer le studio sans les
  resynchroniser depuis DGX1), ComfyUI :8188 (`~/comfyui-env`), le relais
  :8765 vers DGX1, Chromium sans affichage pour les captures.
- Copies de travail jetables : `/tmp/cf_try2` (DGX2, pour `chain_check`),
  `/tmp/cf_page`, `/tmp/cf_rig` (DGX1), `/tmp/canon`, `/tmp/objets`.
