# API Character Factory

FastAPI, à servir depuis le DGX. Le front statique de GitHub Pages
l'appelle à travers un tunnel Cloudflare : aucun port n'est ouvert sur
le LAN.

## Démarrer

Sans rien installer d'autre que les trois paquets de base, le service
tourne en mode de mise au point : file en mémoire, artefacts sur le
disque local, pas d'authentification. Il le dit au démarrage.

```sh
pip install fastapi "uvicorn[standard]" httpx
uvicorn main:app --host 0.0.0.0 --port 8000
curl localhost:8000/health
```

En production, on ajoute Redis, RQ et MinIO :

```sh
pip install -r requirements.txt

export FACTORY_TOKENS="un-jeton-long-et-aleatoire,un-autre"
export FACTORY_CORS_ORIGINS="https://calculment0r.github.io"
export FACTORY_REDIS_URL="redis://localhost:6379/0"
export FACTORY_MINIO_ENDPOINT="minio.interne:9000"
export FACTORY_MINIO_KEY="…"
export FACTORY_MINIO_SECRET="…"
export FACTORY_LLM_URL="http://localhost:8001"     # vLLM, dialecte OpenAI
export FACTORY_LLM_MODEL="Qwen3-VL-32B-Instruct"
export FACTORY_NODE="dgx-01"

uvicorn main:app --host 0.0.0.0 --port 8000
```

## Réglages

| Variable | Défaut | Effet |
|---|---|---|
| `FACTORY_TOKENS` | vide | Jetons acceptés, séparés par des virgules. Vide = ouvert, et le service prévient dans les logs. |
| `FACTORY_CORS_ORIGINS` | Pages + localhost | Origines autorisées. À restreindre en production. |
| `FACTORY_REDIS_URL` | vide | Sans elle, la file est en mémoire et tout est perdu au redémarrage. |
| `FACTORY_MINIO_*` | vide | Sans eux, les artefacts vont dans `FACTORY_LOCAL_STORE` et sont servis par l'API. |
| `FACTORY_LLM_URL` | vide | Le serveur d'inférence local, en dialecte OpenAI. |
| `FACTORY_NODE` | `dgx-01` | Étiquette du nœud. Deux DGX partagent la file, pas l'étiquette. |
| `FACTORY_PRESIGN_SECONDS` | 3600 | Durée de vie des URL présignées MinIO. |

## Le relais de modèle

Le front peut parler au modèle de deux façons.

1. **En direct.** On colle l'URL du serveur d'inférence dans l'écran
   MOTEUR. Il faut alors que ce serveur pose lui-même les en-têtes CORS
   — sous vLLM, `--allowed-origins '["https://calculment0r.github.io"]'`.
2. **À travers l'API.** On colle l'URL de l'API. Elle expose
   `/v1/models` et `/v1/chat/completions` exactement comme le ferait le
   serveur d'inférence, et le navigateur ne connaît jamais l'adresse
   interne du modèle ni son jeton.

La seconde est préférable dès qu'on sort du réseau local : une seule
adresse publique, un seul jeton, une seule liste d'origines.

## Routes

```
GET  /health                          état du nœud, file, stockage, auth

POST /characters                      créer un personnage
GET  /characters                      lister
GET  /characters/{id}                 lire
POST /characters/{id}/face            générer le visage           → job
POST /characters/{id}/face/lock       figer face_locked_url       (une seule fois)
POST /characters/{id}/costumes        créer un costume
GET  /characters/{id}/costumes        lister

POST /costumes/{id}/sheet             planche H3, 5 frames        → job
POST /costumes/{id}/views             vues orthogonales           → un job par azimut
POST /costumes/{id}/views/check       contrôle d'alignement, ±5°
POST /costumes/{id}/mesh              TRELLIS 2 ou Hunyuan3D      → job

POST /assets/{id}/rig                 auto-rig, A-pose imposée    → job
POST /takes                           vidéo ou images → mouvement → job
POST /timelines/{id}/bake             mixage → NPZ + glTF         → job

GET  /jobs                            lister
GET  /jobs/{id}                       lire
GET  /jobs/{id}/events                progression en SSE
GET  /artifacts/{key}                 lecture locale, hors MinIO

GET  /v1/models                       relais du modèle local
POST /v1/chat/completions             relais du modèle local
```

`/jobs/{id}/events` n'est pas derrière le garde de jeton : `EventSource`
ne sait pas poser d'en-tête. L'identifiant de travail n'est pas
devinable et rien de sensible n'y transite.

## Workers

Un worker = une capacité = un modèle résident. Les poids ne sont jamais
chargés à la demande : le temps de démarrage de H3, Kimodo ou
Hunyuan3D-Paint l'interdit.

`workers/stub.py` ne charge rien : il fabrique un PNG de test, le range
et rend son URL. C'est ce qui permet de valider la chaîne — file,
progression, stockage, affichage — avant qu'un seul GPU soit disponible.
Les workers réels prennent la même signature :

```python
def run(*, report, job_id: str = "", **kwargs) -> dict:
    report(0.5, "à mi-chemin")
    return {"url": "…"}
```

## Ce qui n'est pas encore là

- La persistance. `DB` est un dictionnaire en mémoire : il tient le
  temps d'une session. C'est le sujet du lot suivant.
- Les workers GPU. Seul le factice existe.
- Le rejeu d'un flux SSE après reconnexion : un client qui se rebranche
  reçoit l'état courant, pas l'historique des pas.
