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
occupe ~10 Go (trois modèles Qwen3-TTS en bf16), le modèle de texte ~19 Go.

## Les modèles

- **Qwen3-TTS-12Hz-1.7B-VoiceDesign** : une voix depuis une description
  écrite (audition), et les prises jouées (description + consigne de jeu).
- **Qwen3-TTS-12Hz-1.7B-Base** (ou 0.6B, `voice_clone_model`) : la voix
  verrouillée clonée depuis `ref_neutral.wav` + sa transcription (mode
  ICL). Tout ce qui doit « sonner comme lui ».
- **ECAPA** (`speechbrain/spkrec-ecapa-voxceleb`) : similarité de timbre
  d'une prise à la référence ; à défaut, l'encodeur de locuteur de
  Qwen3-TTS Base (juge et partie, le `/health` le dit).
- Plus tard, non construit : **H3 audio seul (Ref2VA)** pour les
  répliques qui comptent (jeu appris sur la vidéo, `docs` : « Dialogues
  joués »).

## Le contrat (API du studio)

Actions : `POST /api/characters/<slug>/actions/<action>`, corps JSON. Les
longues rendent `{"job": …}` (suivre `GET /api/jobs/<id>`), les rapides
répondent tout de suite `{"result": …}`. Les travaux de voix ont leur
propre file : ils ne font pas attendre les images.

| Action | Paramètres | Genre | Effet |
|---|---|---|---|
| `voice_design` | `description?`, `n?`=4, `text?`, `seed?`, `redraft?` | travail | Sans `description` : celle déjà écrite, sinon le modèle de texte l'écrit depuis la fiche (âge, genre, origine, carrure, PSYCHE, `speech_style`) et la range ; `redraft: true` la fait réécrire. N candidates sur le paragraphe d'essai (`voice.TEST_TEXT`) ou `text` : `voice/cand-NNN.wav` + `cand-NNN.json` (description, graine, texte, moteur, durée, temps de calcul). |
| `voice_lock` | `candidate`: "1".."n" | rapide | `voice/ref_neutral.wav`, `ref_neutral.txt`, `card.yaml` (format Voice Lab), `consentement.txt`. **Une seule fois** : refusé (409) si déjà verrouillée. |
| `voice_unlock` | — | rapide | La référence part dans `voice/archive/<date>-…` ; les prises restent. |
| `line` | `text`, `direction?`, `context?`, `takes?`=3, `seed?`, `line?` | travail | Refusé sans voix verrouillée. Sans `direction`, le modèle de texte écrit l'état de jeu (français) et sa consigne (anglais) ; une direction française est traduite en consigne. Prises par VoiceDesign (description + consigne), chacune mesurée contre la référence : `voice/lines/<id>/take-N.wav` (+ `.json`). `line: "l001"` ajoute des prises à une réplique existante. |
| `line_keep` | `line`, `take`: "N" | rapide | La prise gardée. |

Manifeste, `GET /api/characters/<slug>` → `character.voice` (défauts
posés à l'ouverture pour les anciens manifestes, comme `apose`) :

```json
{"description": "…(anglais, envoyée au moteur)", "description_fr": "…", "register": "grave|medium|aigu",
 "candidates": [{"file": "voice/cand-001.wav", "seed": 7, "at": "…", "text": "…", "description": "…",
                 "engine": "qwen3-tts", "duration_s": 12.1, "gen_s": 9.8}],
 "locked": "voice/ref_neutral.wav", "locked_text": "…", "locked_at": "…", "locked_from": "voice/cand-002.wav",
 "locked_seed": 8,
 "lines": [{"id": "l001", "text": "…", "direction": "", "play_state": "…(fr)", "instruct": "…(en)",
            "context": "", "takes": [{"file": "voice/lines/l001/take-1.wav", "seed": 3, "at": "…",
            "similarity": 0.71, "engine": "…", "duration_s": 2.3, "gen_s": 2.9}], "kept": null, "at": "…"}]}
```

`summary.stages` porte un étage `voice` (todo / partial / done).

### Conversation écrite

- `POST /api/characters/<slug>/chat` `{message, history?: [{role, content}]}` →
  `{reply, audio: "/files/<slug>/voice/chat/<id>.wav" | null, timings}`.
  `audio` est null sans voix verrouillée.
- `POST /api/characters/<slug>/chat/stream`, même corps → `text/event-stream`,
  une ligne `data: {…}` par évènement :
  `{"type":"delta","text"}` au fil du modèle ; `{"type":"audio","index","text","url"}`
  dès qu'un morceau est dit ; `{"type":"error","message"}` ;
  enfin `{"type":"done","reply","audio":[urls],"timings":{first_token_s, first_audio_s, total_s}}`.

La page garde l'historique et le renvoie ; le studio n'en garde rien.

### Conversation en direct

`GET /api/voice/config` →
`{engine, available, reason?, ws_url, wss_url, https_url, service, health, llm}`.

- `ws_url` pour une page en HTTP, `wss_url` pour une page en HTTPS
  (`wss://…:8771/ws/chat`) ; ajouter `?slug=<perso>`.
- **Le micro n'est prêté qu'en contexte sûr** (HTTPS ou `localhost`) :
  la page teste `window.isSecureContext` ; sinon elle renvoie vers
  `https_url` (la page de test servie en HTTPS par le service vocal, qui
  relaie tout le studio) ou vers un tunnel ssh.
- `available: false` + `reason` quand le service ne répond pas, ou en
  factice sans service.

Protocole de la WebSocket (`/ws/chat?slug=…`, service vocal) :

- page → service : binaire = PCM int16, 24 kHz, mono, petit-boutiste, par
  paquets de 80 ms (n'importe quelle taille marche) ;
  `{"type":"micro","on":true|false,"cran":"vif|normal|patient"}` (patience de
  la fin de tour : 320 / 640 / 1120 ms) ; `{"type":"texte","message"}` ;
  `{"type":"stop"}` ; `{"type":"lecture_finie"}` (la file de lecture est vide).
- service → page : `pret {personnage, slug, voix, moteur, llm, oreille}` ;
  `ecoute {cran, pause_ms, retard_ms}` ; `mot {texte}` (reconnaissance au fil
  de l'eau) ; `tour {texte, source: voix|texte}` ; `texte {delta}` ;
  `audio {i, texte, sr, ms}` **suivi d'un message binaire** (PCM int16 à `sr`) ;
  `fin {texte, mesures}` ; `stop {raison}` (vider la file de lecture) ;
  `erreur {message}`.
- Coupure : `voice_barge_words` (2) mots reconnus pendant que le
  personnage parle arrêtent sa réponse (`stop`) et commencent le tour
  suivant.
- `mesures` : `premier_mot_ms` (modèle de texte), `premier_morceau_ms`,
  `premier_son_ms` (depuis la fin de tour), `premier_son_apres_dernier_mot_ms`,
  `fin_de_tour_apres_dernier_mot_ms`, `rtf` par morceau. Chaque tour est
  aussi écrit dans `voix-mesures.jsonl` (`voice_log`).

### Factice

`FACTORY_VOICE=stub` (le défaut tant que rien n'est réglé) : toutes les
actions et la conversation écrite marchent et écrivent de vrais WAV
(voyelles synthétiques ; un clone reprend la hauteur de sa référence, la
similarité a donc un sens). `FACTORY_BRIEF=stub` : réponses et
descriptions écrites d'avance. Le direct : `./usine voix-serveur --factice`
(oreille factice : du son puis 0,8 s de silence font un tour).
`python3 tools/chain_check.py` vérifie tout cela.

## Réglages (`factory.local.json` ou `FACTORY_<NOM>`)

| Réglage | Où | Défaut | Rôle |
|---|---|---|---|
| `backends.voice` | studio | `stub` | `remote` : le service vocal |
| `voice_url` | studio | `http://127.0.0.1:8770` | le service vocal, de serveur à serveur |
| `voice_https_url` | studio | — | son adresse HTTPS vue du navigateur (`https://192.168.10.205:8771`) |
| `voice_llm_url`, `voice_llm_model` | studio et service | `llm_url`, `qwen3-vl:30b-a3b-instruct` ; `voice_llm_ctx` 8192 | le modèle de texte de la voix |
| `voice_studio_url` | service | `http://127.0.0.1:8765` | le studio, d'où viennent fiche et référence |
| `voice_stt_url` | service | `ws://127.0.0.1:5930/ecoute` | l'oreille Kyutai |
| `voice_clone_model` | service | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | ou le 0.6B, plus rapide |
| `voice_barge_words` | service | 2 | mots qui coupent le personnage |

## Lancer

Sur DGX1 (dépôt cloné, environnement `~/cf-voice-env`) :

```sh
setsid nohup ~/voix/demarre-stt.sh < /dev/null > /dev/null 2>&1 &     # l'oreille, :5930
cd ~/cf-voice && FACTORY_VOICE_STUDIO_URL=http://192.168.10.247:8765 \
  setsid nohup ~/cf-voice-env/bin/python -m factory.voice_server > voix.log 2>&1 < /dev/null &
```

Sur DGX2, dans `factory.local.json` du studio :

```json
"backends": {"voice": "remote"},
"voice_url": "http://192.168.10.205:8770",
"voice_https_url": "https://192.168.10.205:8771",
"voice_llm_url": "http://192.168.10.205:11434"
```

Tester : ouvrir **https://192.168.10.205:8771/voix.html**, accepter le
certificat auto-signé une fois, choisir le personnage, auditionner,
verrouiller, puis « Se connecter » et « Micro ». Sans HTTPS : tunnel ssh
depuis le PC, `ssh -L 8770:192.168.10.205:8770 -L 8765:127.0.0.1:8765 dgx2`,
puis http://localhost:8770/voix.html (localhost est un contexte sûr).

En ligne de commande : `./usine voix <perso>`, `./usine voix-ok <perso> <n>`,
`./usine voix-libre <perso>`, `./usine replique <perso> "…" [--jeu "…"]`,
`./usine replique-ok <perso> l001 2`.
