# La voix — étage, conversation, jeu

Chaque personnage a une **voix conçue**, **verrouillée** comme son visage,
avec laquelle on **parle** (au micro ou par écrit) et qui **joue** des
répliques dirigées. Tout en local, français d'abord.

Études qui fondent les choix : artefacts de Cal du 24/09, 26/09, 27/09
(veille), « Banc audio DGX2 », « Dialogues joués » ; résumé : une
conversation vocale ouverte en français est une **cascade** STT → modèle
de texte → TTS cloné (les modèles full-duplex ouverts ne parlent
qu'anglais et chinois) ; moteur de voix **Qwen3-TTS** (Apache 2.0).

## Ce qui tourne où

| Pièce | Machine | Port | Code |
|---|---|---|---|
| Studio (étage Voix, conversation écrite, config) | DGX2 | 8765 (HTTP), `--https 8766` en option | `factory/studio.py`, `factory/voice.py` |
| Service vocal : Qwen3-TTS, conversation en direct, page de test | **DGX1** | 8770 (HTTP), 8771 (HTTPS) | `factory/voice_server.py`, `factory/voice_qwen.py` |
| Oreille : Kyutai STT 1B en_fr, fin de tour sémantique | DGX1 | 5930 | `~/voix/serveur-kyutai-stt.py` (existant, réutilisé tel quel) |
| Modèle de texte de la conversation : `qwen3-vl:30b-a3b-instruct` (MoE, 3 B actifs, sans réflexion) | DGX1 (Ollama) | 11434 | `factory/voice_chat.py` |

DGX1 plutôt que DGX2 : une conversation ne doit pas attendre derrière une
image Qwen (34 Go) ou H3 (100 Go exclusifs) de DGX2. Le service vocal
occupe ~12 Go (trois modèles Qwen3-TTS en bf16 + ECAPA), le modèle de
texte ~25 Go (contexte 8k). **Mais DGX1 n'est pas libre** : son ComfyUI
y passe des mesh TRELLIS de la chaîne ; pendant l'un d'eux, le RTF du
clonage passe de 0,6-0,9 à 2,4 et le premier son de 2,5 s à 8-12 s.

`qwen3:30b-a3b` d'Ollama est la version « Thinking 2507 » : elle réfléchit
à voix haute 30 s quoi qu'on lui dise (`think: false`, `/no_think`).
La variante instruct du même MoE a été copiée de DGX2 sur DGX1 (lien
direct 169.254.x, 20 Go en 70 s) et créée dans son Ollama.

## Les modèles

- **Qwen3-TTS-12Hz-1.7B-VoiceDesign** : une voix depuis une description
  écrite (audition), et les prises jouées (description + consigne de jeu).
- **Qwen3-TTS-12Hz-1.7B-Base** (`voice_clone_model`) : la voix verrouillée
  clonée depuis `ref_neutral.wav` + sa transcription (mode ICL) — prises
  « voix », conversation écrite du studio.
- **Qwen3-TTS-12Hz-0.6B-Base** (`voice_chat_clone_model`) : le même
  clonage pour la conversation en direct, ~25 % plus rapide, même
  ressemblance ECAPA ; **en flux** (paquets de 6 puis 12 trames, voir
  `voice_qwen.clone_stream` : qwen-tts 0.1.1 ne le fait pas lui-même).
- **ECAPA** (`speechbrain/spkrec-ecapa-voxceleb`) : similarité de timbre
  d'une prise à la référence ; à défaut, l'encodeur de locuteur de
  Qwen3-TTS Base (juge et partie, le `/health` le dit). Peu fiable sous
  1,5 s de son.
- Plus tard, non construit : **H3 audio seul (Ref2VA)** pour les
  répliques qui comptent (jeu appris sur la vidéo, « Dialogues joués »).

## Le contrat (API du studio)

Actions : `POST /api/characters/<slug>/actions/<action>`, corps JSON. Les
longues rendent `{"job": …}` (suivre `GET /api/jobs/<id>`), les rapides
répondent tout de suite `{"result": …}`. Les travaux de voix ont leur
propre file : ils ne font pas attendre les images.

| Action | Paramètres | Genre | Effet |
|---|---|---|---|
| `voice_design` | `description?`, `n?`=4, `text?`, `seed?`, `redraft?` | travail | Sans `description` : celle déjà écrite, sinon le modèle de texte l'écrit depuis la fiche (âge, genre, origine, carrure, PSYCHE, `speech_style`) et la range ; `redraft: true` la fait réécrire. N candidates sur le paragraphe d'essai (`voice.TEST_TEXT`) ou `text` : `voice/cand-NNN.wav` + `cand-NNN.json` (description, graine, texte, moteur, durée, temps de calcul). |
| `voice_lock` | `candidate`: "1".."n" | rapide | `voice/ref_neutral.wav`, `ref_neutral.txt` (ce que Kyutai **entend** dans la candidate, sinon le texte demandé : `locked_text_source`), `card.yaml` (format Voice Lab), `consentement.txt`. **Une seule fois** : refusé (409) si déjà verrouillée. |
| `voice_unlock` | — | rapide | La référence part dans `voice/archive/<date>-…` ; les prises restent. |
| `line` | `text`, `direction?`, `context?`, `takes?`=3, `seed?`, `clone?`=true, `line?` | travail | Refusé sans voix verrouillée. Sans `direction`, le modèle de texte écrit l'état de jeu (français) et sa consigne (anglais) ; une direction française est traduite en consigne. `takes` prises « jeu » par VoiceDesign (description + consigne), gardées parmi jusqu'à 3×`takes` essais : celles dont le timbre reste le plus proche de la référence (`voice_min_similarity` 0,55 arrête les essais) ; plus une prise « voix » clonée (`clone`), timbre tenu, jeu plus neutre. `voice/lines/<id>/take-N.wav` (+ `.json`). `line: "l001"` ajoute des prises à une réplique existante. |
| `line_keep` | `line`, `take`: "N" | rapide | La prise gardée. |

Manifeste, `GET /api/characters/<slug>` → `character.voice` (défauts
posés à l'ouverture pour les anciens manifestes, comme `apose`) :

```json
{"description": "…(anglais, envoyée au moteur)", "description_fr": "…", "register": "grave|medium|aigu",
 "candidates": [{"file": "voice/cand-001.wav", "seed": 7, "at": "…", "text": "…", "description": "…",
                 "engine": "qwen3-tts", "duration_s": 12.1, "gen_s": 9.8}],
 "locked": "voice/ref_neutral.wav", "locked_text": "…", "locked_text_source": "stt|demandé", "locked_at": "…",
 "locked_from": "voice/cand-002.wav", "locked_seed": 8,
 "lines": [{"id": "l001", "text": "…", "direction": "", "play_state": "…(fr)", "instruct": "…(en)",
            "context": "", "attempts": [0.49, 0.2, 0.58],
            "takes": [{"file": "voice/lines/l001/take-1.wav", "kind": "jeu|voix", "seed": 3, "at": "…",
                       "similarity": 0.58, "engine": "…", "duration_s": 2.3, "gen_s": 2.9}],
            "kept": null, "at": "…"}]}
```

`summary.stages` porte un étage `voice` (todo / partial / done).

### Conversation écrite

- `POST /api/characters/<slug>/chat` `{message, history?: [{role, content}]}` →
  `{reply, audio: "/files/<slug>/voice/chat/<id>.wav" | null, audio_error, timings}`.
  `audio` est null sans voix verrouillée, ou si le service vocal manque
  (`audio_error` dit pourquoi) : la réponse écrite vient quand même.
- `POST /api/characters/<slug>/chat/stream`, même corps → `text/event-stream`,
  une ligne `data: {…}` par évènement :
  `{"type":"delta","text"}` au fil du modèle ; `{"type":"audio","index","text","url"}`
  dès qu'un morceau est dit ; `{"type":"error","message"}` ;
  enfin `{"type":"done","reply","audio":[urls],"timings":{first_token_s, first_audio_s, total_s}}`.

La page garde l'historique et le renvoie ; le studio n'en garde rien.

### Conversation en direct

`GET /api/voice/config[?slug=<perso>]` →
`{engine, available, reason?, ws_url, wss_url, https_url, service, health, llm}`.

- Avec `?slug=`, `ws_url` et `wss_url` portent déjà `?slug=<perso>` ; sans,
  la page l'ajoute. **Une WebSocket sans personnage est refusée**
  (`erreur`, puis fermeture). `js/parler.js` ouvre `ws_url` tel quel : il
  faut donc l'appeler avec `?slug=`, ou ajouter le slug à l'adresse.
- `ws_url` pour une page en HTTP, `wss_url` pour une page en HTTPS.
- **Le micro n'est prêté qu'en contexte sûr** (HTTPS ou `localhost`) :
  la page teste `window.isSecureContext` ; sinon elle renvoie vers
  `https_url` (la page de test servie en HTTPS par le service vocal, qui
  relaie tout le studio) ou vers un tunnel ssh. Une page du studio en
  HTTPS (`--https 8766`) qui ouvre `wss://…:8771` doit aussi avoir fait
  accepter le certificat de DGX1 (ouvrir `https_url` une fois).
- `available: false` + `reason` quand le service ne répond pas, ou en
  factice sans service.

La WebSocket (`/ws/chat?slug=…`, service vocal) parle **deux protocoles**,
reconnus au premier message binaire.

**En direct (voix.html)** — flux continu, fin de tour par l'oreille :

- page → service : binaire = PCM int16, 24 kHz, mono, petit-boutiste, par
  paquets de 80 ms (n'importe quelle taille marche) ;
  `{"type":"micro","on":true|false,"cran":"vif|normal|patient"}` (patience de
  la fin de tour : 320 / 640 / 1120 ms) ; `{"type":"texte","message"}` ;
  `{"type":"stop"}` ; `{"type":"lecture_finie"}` (la file de lecture est vide).
- service → page : `pret {personnage, slug, voix, moteur, llm, oreille}` ;
  `ecoute {cran, pause_ms, retard_ms}` ; `mot {texte}` (reconnaissance au fil
  de l'eau) ; `tour {texte, source: voix|texte, suite?}` (`suite` : le tour
  vient d'être complété par des mots arrivés après sa fin) ; `texte {delta}` ;
  `audio {i, j, texte, sr, ms}` **suivi d'un message binaire** (PCM int16 à
  `sr` ; `i` le morceau de phrase, `j` le paquet dans le morceau, à jouer
  bout à bout) ; `fin {texte, mesures}` ; `stop {raison}` (vider la file de
  lecture) ; `erreur {message}`.
- Coupure : `voice_barge_words` (2) mots reconnus pendant que le
  personnage parle arrêtent sa réponse (`stop`) et commencent le tour
  suivant. Casque conseillé.
- Fin de tour : la tête de Kyutai voit le silence avant que le texte
  (décalé de 500 ms) ait fini de sortir ; le service attend une
  ponctuation finale ou 0,3 s sans mot (au plus 0,7 s), et un mot arrivé
  dans les 0,9 s complète le tour au lieu de couper la réponse.
- `mesures` : `premier_mot_ms` (modèle de texte), `premier_morceau_ms`,
  `premier_son_ms` (depuis la fin de tour), `premier_son_apres_dernier_mot_ms`,
  `fin_de_tour_apres_dernier_mot_ms`, `tour_complete_apres_fin`, `rtf` par
  morceau. Chaque tour est aussi écrit dans `voix-mesures.jsonl` (`voice_log`).

**Appuyer pour parler (js/parler.js)** — une prise entière :

- page → service : des morceaux webm/opus (MediaRecorder ; reconnus à
  l'en-tête EBML du premier), puis `{"type":"end"}`.
- service → page : `{"type":"transcript","text"}`, puis
  `{"type":"reply","text","audio":null,"mesures"}` **suivi du son de la
  réponse en WAV** (un message binaire, jouable tel quel par un
  `Blob`/`<audio>`). Pas de flux : le son arrive quand toute la réponse
  est dite, ~2-4 s après `transcript`.

### Factice

`FACTORY_VOICE=stub` (le défaut tant que rien n'est réglé) : toutes les
actions et la conversation écrite marchent et écrivent de vrais WAV
(voyelles synthétiques ; un clone reprend la hauteur de sa référence, la
similarité a donc un sens). `FACTORY_BRIEF=stub` : réponses et
descriptions écrites d'avance. Le direct : `./usine voix-serveur --factice`
(oreille factice : du son puis 0,8 s de silence font un tour ; une prise
webm est « entendue » comme « parole factice »).
`python3 tools/chain_check.py` vérifie tout cela (le direct, si fastapi,
uvicorn et websockets sont installés).

## Mesures (27/09, DGX1, Kévin et Ilse Varga, copies dans `~/cf-voice-projects`)

| Quoi | Mesure |
|---|---|
| Description de la voix par le modèle de texte | ~8 s (modèle chaud) |
| Audition, par candidate (~16 s de son) | 10-13 s GPU libre (RTF ~0,7) ; 17-21 s pendant un mesh TRELLIS (RTF ~1,2) ; 1er chargement 50 s par modèle |
| Diction des candidates (Kyutai STT, 8 voix) | WER 0 % sur 6, 2 % sur 2 (un « Bon, » sauté) |
| Clonage 1.7B / 0.6B, GPU libre | RTF 0,75-0,8 / 0,6 |
| Ressemblance (ECAPA) : clone / prise jouée VoiceDesign | 0,80-0,85 / 0,11-0,64 (3 sur 10 au-dessus de 0,55) |
| Réplique jouée : 6 essais + clone | ~30 s |
| Conversation écrite (studio) : réponse entière dite | texte 1 s, total 5 s ; en flux, 1er son 2,7 s |
| **Direct : fin de la parole → 1er son** (GPU libre, cran vif / normal) | **2,3-3,0 s / 2,3-2,8 s**, médiane ~2,45 s ; pendant un mesh TRELLIS : 8-12 s |
| Dont : fin de tour de Kyutai / dernier mot reconnu | 0,4-0,9 s / 1,15-1,4 s après la fin de la parole |

`tools/voix_mesure.py` rejoue des questions (WAV) dans la WebSocket comme
un micro et mesure depuis la fin de la parole.

## Réglages (`factory.local.json` ou `FACTORY_<NOM>`)

| Réglage | Où | Défaut | Rôle |
|---|---|---|---|
| `backends.voice` | studio | `stub` | `remote` : le service vocal |
| `voice_url` | studio | `http://127.0.0.1:8770` | le service vocal, de serveur à serveur |
| `voice_https_url` | studio | — | son adresse HTTPS vue du navigateur (`https://192.168.10.205:8771`) |
| `voice_llm_url`, `voice_llm_model`, `voice_llm_ctx` | studio et service | `llm_url`, `qwen3-vl:30b-a3b-instruct`, 8192 | le modèle de texte de la voix (contexte toujours explicite : sans lui, Ollama réserve tout le contexte du modèle) |
| `voice_studio_url` | service | `http://127.0.0.1:8765` | le studio, d'où viennent fiche et référence |
| `voice_stt_url` | service | `ws://127.0.0.1:5930/ecoute` | l'oreille Kyutai |
| `voice_clone_model`, `voice_chat_clone_model`, `voice_design_model` | service | 1.7B-Base, 0.6B-Base, 1.7B-VoiceDesign | |
| `voice_stream` | service | 1 | 0 : pas de flux, un morceau entier à la fois |
| `voice_barge_words` | service | 2 | mots qui coupent le personnage |
| `voice_turn_grace_s`, `voice_settle_quiet_s`, `voice_settle_max_s` | service | 0,9 / 0,3 / 0,7 | fin de tour (plus haut) |
| `voice_min_similarity`, `voice_line_attempts` | studio | 0,55 / 3 | prises jouées |

## Lancer

Sur DGX1 (clone `~/cf-voice`, environnement `~/cf-voice-env` : qwen-tts
0.1.1, torch 2.13 cu130, speechbrain, fastapi, uvicorn, websockets) :

```sh
setsid nohup ~/voix/demarre-stt.sh < /dev/null > /dev/null 2>&1 &     # l'oreille, :5930
~/cf-voice/tools/relance-voix.sh                                      # le service, :8770 et :8771
```

(`tools/relance-voix.sh` tue l'ancien service et relance `python -m
factory.voice_server` dans `~/cf-voice`, journal `voix.log` ; son
`factory.local.json` dit où est le studio.)

Sur DGX2, dans `factory.local.json` du studio :

```json
"backends": {"voice": "remote"},
"voice_url": "http://192.168.10.205:8770",
"voice_https_url": "https://192.168.10.205:8771",
"voice_llm_url": "http://192.168.10.205:11434",
"voice_llm_model": "qwen3-vl:30b-a3b-instruct"
```

Tester : ouvrir **https://192.168.10.205:8771/voix.html**, accepter le
certificat auto-signé une fois, choisir le personnage, auditionner,
verrouiller, puis « Se connecter » et « Micro ». Sans HTTPS : un tunnel
ssh depuis le PC, `ssh -L 8770:127.0.0.1:8770 dgx1`, puis
http://localhost:8770/voix.html (localhost est un contexte sûr).

En ligne de commande : `./usine voix <perso>`, `./usine voix-ok <perso> <n>`,
`./usine voix-libre <perso>`, `./usine replique <perso> "…" [--jeu "…"]`,
`./usine replique-ok <perso> l001 2`.

## Limites, et la suite

- La latence dépend de la charge de DGX1 : un mesh TRELLIS la triple. La
  voix n'a pas de priorité sur le GPU.
- VoiceDesign ne tient pas le timbre d'une prise à l'autre : la direction
  de jeu se paie en ressemblance, d'où les essais mesurés. Qwen3-TTS Base
  ne prend pas de consigne. Pistes : une conversion de voix vers la
  référence après la prise jouée ; H3 Ref2VA audio seul ; un TTS qui
  clone ET suit une consigne (Confucius4-TTS, dots.tts — veille du 27/09).
- Le flux du clonage passe par un crochet sur le `forward` du talker de
  qwen-tts 0.1.1 : à revérifier à chaque mise à jour du paquet.
- Le premier mot du modèle de texte arrive vite (~0,15 s) ; ce qui reste
  est la fin de tour (~1,2 s pour que le dernier mot sorte de Kyutai) et
  le premier paquet de son (~0,6 s).
