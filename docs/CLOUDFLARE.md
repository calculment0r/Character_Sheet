# Ouvrir la Factory depuis l'extérieur

Le but (Cal, 28/09) : « on devrait pouvoir bosser avec cet outil de
n'importe où… sans tailscale. Je veux que mes amis puissent aller sur la
page github et faire leur perso quand les dgx sont allumés. »

Sans ouvrir un seul port sur le LAN des DGX : un **tunnel Cloudflare
sortant** part de DGX1, **une seule adresse** sur un domaine le reçoit,
et **Cloudflare Access** — une porte à e-mails — la garde.

```
page GitHub ──https──▶ https://<studio>/bridge/health     public, sans porte : « allumées ? prêtes ? »

navigateur ──https──▶ Cloudflare Access (e-mails autorisés)
                         │ pose Cf-Access-Authenticated-User-Email
                         ▼
                      tunnel sortant ──▶ cloudflared, sur DGX1
                                           ├─ /bridge/*  → http://localhost:8770   le pont (tools/bridge.py)
                                           └─ le reste   → http://localhost:8765   le studio
```

- **Le studio** (`./usine studio`, :8765) sert la page, la file des rendus,
  les fichiers des personnages. Derrière la porte, chacun y fait ses
  personnages ; ceux des autres se regardent sans se toucher.
- **Le pont** (`tools/bridge.py`, :8770) est toujours allumé, en service
  systemd. Il dit si les DGX sont allumées et ce qui tourne
  (`/bridge/health`, public), et démarre derrière la porte ce qui manque :
  studio, ComfyUI :8188, H3 :8189, Ollama, sur DGX1 comme sur DGX2 (par
  ssh sur le câble direct). Il sait aussi arrêter H3, pour libérer la mémoire.
- **La page d'état** (GitHub Pages) interroge `/bridge/health` et propose
  « Ouvrir le studio ». Elle ne démarre jamais rien : le démarrage se fait
  dans le studio (panneau « Machines » de l'en-tête), ou sur la page du
  pont (`/bridge/`) quand le studio lui-même dort — toujours derrière la porte.

---

## Ce que Cal doit faire

Rien de ceci n'est fait par la chaîne : compte, domaine, téléchargement et
installation restent à Cal.

1. **Un domaine chez Cloudflare.** Un compte Cloudflare (gratuit) et un
   domaine dont les serveurs de noms pointent vers Cloudflare. Choisir le
   nom d'hôte du studio, par exemple `studio.<ton-domaine>`.
2. **`cloudflared` sur DGX1** (téléchargement : à faire soi-même, depuis
   les versions officielles de Cloudflare, paquet arm64 — les DGX Spark
   sont en ARM).
3. **Le tunnel** : `cloudflared tunnel login`, `create`, `route dns`, le
   fichier `config.yml` à deux règles de chemin, puis le service — § 1 à 5
   plus bas.
4. **Cloudflare Access** : deux applications sur le même nom d'hôte — tout
   protégé par la liste d'e-mails, sauf `/bridge/health` en « Bypass » (§ 6).
   Y mettre les e-mails des amis, et le sien.
5. **Le pont en service** sur DGX1 (§ 7) :
   `sudo cp tools/character-factory-bridge.service /etc/systemd/system/`,
   `daemon-reload`, `enable --now`.
6. **Donner l'adresse** (§ 8) : le nom d'hôte dans `FACTORY_DOMAIN`, en tête
   du script de `index.html` (puis pousser et fusionner pour GitHub Pages),
   et dans `public_host` de `factory.local.json` sur DGX1 (ou
   `BRIDGE_PUBLIC_HOST` dans l'unité du pont), puis redémarrer le pont.
7. **Se reconnaître soi-même** dans `factory.local.json` de DGX1 (§ 9) :
   `"aliases": {"<ton e-mail>": "cal"}` pour rester « cal » quand tu passes
   par la porte (tes personnages, tes droits d'administrateur) ; `admins`
   si d'autres doivent pouvoir tout toucher. Redémarrer le studio après.
8. **À la maison, par DGX2** : le panneau « Machines » du studio lit le pont
   sur le port 8770 de la machine qui sert la page. Par le relais de DGX2,
   il faut donc aussi le relais du pont — « Tout démarrer » le lance, ou à
   la main sur DGX2 :
   `cd ~/Character_Factory && setsid nohup python3 tools/relay.py 8770 169.254.110.6:8770 > ~/relay_bridge.log 2>&1 < /dev/null &`
9. **Vérifier** (§ 10), puis envoyer le lien de la page GitHub aux amis.

---

## Le montage durable — le studio et le pont, derrière une porte

### 1. Autoriser la machine, une seule fois (DGX1)

```sh
cloudflared tunnel login
```

Une page s'ouvre dans le navigateur (ou une adresse à ouvrir ailleurs) :
choisis ton domaine. Le certificat va dans `~/.cloudflared/cert.pem`.

### 2. Créer le tunnel

```sh
cloudflared tunnel create character-factory
```

Note l'identifiant renvoyé (`<UUID>`), et le fichier d'identifiants
déposé dans `~/.cloudflared/<UUID>.json`.

### 3. Lui donner le nom d'hôte du studio

```sh
cloudflared tunnel route dns character-factory studio.<ton-domaine>
```

### 4. Écrire la configuration : deux règles de chemin

`~/.cloudflared/config.yml`, sur DGX1 :

```yaml
tunnel: character-factory
credentials-file: /home/dgx/.cloudflared/<UUID>.json

ingress:
  # le pont : l'état des machines, leur démarrage
  - hostname: studio.<ton-domaine>
    path: ^/bridge(/|$)
    service: http://localhost:8770
  # tout le reste : le studio (la page, l'API, les fichiers)
  - hostname: studio.<ton-domaine>
    service: http://localhost:8765
  - service: http_status:404
```

`path` est une expression régulière, lue dans l'ordre : la première règle
qui colle gagne. Le tunnel parle au studio et au pont sur `localhost` : ni
l'un ni l'autre n'a besoin d'être ouvert sur Internet.

### 5. Lancer, puis installer en service

```sh
cloudflared tunnel run character-factory      # essai, au premier plan

# une fois que ça marche, pour que ça survive au redémarrage
sudo cloudflared --config /home/dgx/.cloudflared/config.yml service install
sudo systemctl enable --now cloudflared
```

**Ne pas donner l'adresse avant le § 6** : sans la porte, le studio et le
pont refusent tout ce qui vient du tunnel (403), mais la porte, c'est
Cloudflare Access.

### 6. Cloudflare Access : tout protégé, sauf `/bridge/health`

Dans le tableau de bord Cloudflare, **Zero Trust → Access → Applications
→ Add an application → Self-hosted**, deux fois :

| Application | Domaine | Chemin | Politique |
|---|---|---|---|
| Character Factory | `studio.<ton-domaine>` | *(vide : tout)* | **Allow** — Include : *Emails* — la liste des e-mails autorisés |
| Character Factory · santé | `studio.<ton-domaine>` | `bridge/health` | **Bypass** — Include : *Everyone* |

La plus précise l'emporte : `/bridge/health` passe sans porte, tout le
reste demande l'e-mail. Méthode de connexion : le code à usage unique
envoyé par e-mail (« One-time PIN ») suffit, rien à installer pour les
amis. Gratuit jusqu'à 50 personnes.

Access pose sur chaque requête l'en-tête
`Cf-Access-Authenticated-User-Email` : le studio en fait le
propriétaire des personnages créés, le pont le note dans son journal.

### 7. Le pont en service (DGX1)

L'unité est dans le dépôt, `tools/character-factory-bridge.service`
(utilisateur `dgx`, python du système, port 8770) :

```sh
sudo cp ~/Character_Factory/tools/character-factory-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now character-factory-bridge
curl -s localhost:8770/bridge/health
journalctl -u character-factory-bridge -f     # qui a démarré quoi
```

Ce que le pont démarre, et comment :

| Quoi | DGX1 (la machine du pont) | DGX2 (par `ssh dgx@169.254.42.193`, le câble) |
|---|---|---|
| ComfyUI :8188 | `sudo -n systemctl start comfyui.service` | idem, par ssh |
| H3 :8189 | `sudo -n systemctl start comfyui-h3test.service` | idem, par ssh |
| Ollama :11434 | `sudo -n systemctl start ollama.service` | idem, par ssh |
| Studio :8765 | comme dans `docs/REPRISE_CAL.md` : `setsid nohup ./usine studio > studio.log` | jamais : c'est celui de DGX1 qui sert |
| Relais | — | `tools/relay.py` 8765 et 8770 → DGX1, s'ils sont absents |

On démarre ce qui manque, on ne redémarre jamais ce qui tourne (une autre
session peut calculer sur ComfyUI). `POST /bridge/stop {"what": "h3"}`
arrête H3 pour libérer la mémoire, refusé tant que sa file n'est pas vide.
Le studio lancé par le pont survit à un redémarrage du pont
(`KillMode=process`).

Essai sans rien lancer : `python3 tools/bridge.py --port 8771 --essai` —
les commandes sont rendues au lieu d'être exécutées.

### 8. Donner l'adresse aux pages

- **La page GitHub** : `index.html`, en tête du script,
  `const FACTORY_DOMAIN = 'studio.<ton-domaine>';`. Tant qu'il est vide, le
  bloc « Les machines » dit « adresse à venir ». Pousser, puis fusionner
  vers la branche de GitHub Pages (`CLAUDE.md`, « GitHub Pages »).
- **Le pont** : le même nom d'hôte dans `factory.local.json` de DGX1,
  `"public_host": "studio.<ton-domaine>"`, ou `BRIDGE_PUBLIC_HOST` dans
  l'unité ; puis `sudo systemctl restart character-factory-bridge`. Il
  n'accepte un démarrage venu d'un navigateur que d'une page de cette
  adresse ou de la maison.
- **Le studio** n'a rien à savoir : derrière le tunnel, il lit le pont sur
  sa propre adresse (`/bridge/…`) ; à la maison, sur le port 8770 de la
  machine qui sert la page.

### 9. Les propriétaires

Chaque personnage a un `owner` dans son `project.json` :

- créé derrière la porte : l'e-mail de `Cf-Access-Authenticated-User-Email` ;
- créé depuis la maison (192.168.10.0/24, le câble 169.254.0.0/16, la
  machine même) : `cal` ;
- les personnages d'avant : `cal` (sans champ `owner`, rien n'est réécrit).

Le casting propose « Les miens / Tous ». Tout se regarde ; détruire,
renommer, changer la fiche, lancer une action, arrêter ou relancer un
rendu sur le personnage d'un autre est refusé (403, le message dit à qui
il est), sauf aux administrateurs.

`factory.local.json` de DGX1 (redémarrer le studio après l'avoir changé) :

```json
{
  "admins": ["cal"],
  "aliases": {"<l'e-mail de Cal>": "cal"},
  "public_host": "studio.<ton-domaine>"
}
```

- `admins` : qui touche à tout (par défaut `["cal"]`) ;
- `aliases` : un e-mail de la porte qui compte pour un nom de la maison —
  sans lui, Cal passé par la porte serait un ami comme un autre ;
- `trusted_networks` (facultatif) : les réseaux de la maison, si un jour
  ils changent (par défaut `192.168.10.0/24`, `169.254.0.0/16`,
  `127.0.0.1/32`). Tailscale n'en fait pas partie.

Une requête marquée par Cloudflare (`Cf-Ray`, `Cf-Connecting-Ip`…) sans
l'e-mail de la porte a pris le tunnel sans Access : le studio et le pont
la refusent, quelle qu'elle soit. L'e-mail n'est cru que s'il arrive d'une
adresse de confiance — le tunnel sort de la machine même.

### 10. Vérifier

```sh
# sur DGX1
curl -s localhost:8770/bridge/health | python3 -m json.tool

# de dehors (téléphone en 4G, par exemple)
curl -s https://studio.<ton-domaine>/bridge/health            # JSON : pas de porte
curl -s -o /dev/null -w '%{http_code}\n' https://studio.<ton-domaine>/   # 302 (ou 403) : la porte
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://studio.<ton-domaine>/bridge/start   # idem : la porte
```

Puis la page GitHub : le bloc « Les machines » doit dire « prêt » et
proposer « Ouvrir le studio ».

---

## Si ça ne marche pas

| Ce que tu vois | Ce que c'est |
|---|---|
| « adresse à venir » sur la page GitHub | `FACTORY_DOMAIN` est vide dans `index.html` (§ 8). |
| « éteintes » sur la page GitHub | DGX1 éteinte, `cloudflared` arrêté, ou le pont arrêté : `systemctl status cloudflared character-factory-bridge`. |
| « à démarrer » | Allumées, mais il manque le studio, ComfyUI ou Ollama : « Tout démarrer » dans le panneau Machines du studio, ou sur `https://<studio>/bridge/`. |
| 403 « cette adresse doit être gardée par Cloudflare Access » | Le tunnel tourne sans la porte, ou pas sur ce chemin : § 6. |
| 403 « … est à cal : tu peux le regarder » | Le personnage d'un autre. Créer le sien, ou être dans `admins`. |
| Cal passé par la porte ne voit plus « les siens » | Son e-mail n'est pas dans `aliases` (§ 9). |
| « machines · pont muet » dans le studio, à la maison | Le pont ne répond pas sur :8770 de la machine de la page : par DGX2, il manque le relais du pont (« Ce que Cal doit faire », 8). |
| Le démarrage répond « la porte a expiré » | La session Access est finie : recharger la page. |
| `502 Bad Gateway` de Cloudflare | `cloudflared` tourne mais rien n'écoute sur le port visé : le studio dort (`/bridge/` le démarre) ou le pont est arrêté. |
| La progression d'un rendu ne bouge pas | Le studio relève la file toutes les 1,5 s ; un proxy de plus devant peut mettre en tampon. Le tunnel Cloudflare, non. |

---

## Pour mémoire : l'API de la console (`api/`, :8000)

`api/`, `start.sh` et `check.sh` restent pour le jour où la console devra
piloter la chaîne à distance (`CLAUDE.md`). Ce qui suit les concerne, pas
le studio.

### Regarder ce qui est déjà là

```sh
./check.sh
```

Il ne touche à rien. Il cherche un serveur d'inférence sur les ports
usuels et relève les modèles qu'il sert, dit si `cloudflared` est
installé et si un tunnel tourne déjà, liste les dépendances Python
présentes, repère Redis et MinIO, et vérifie que le port de la Factory
est libre. `FACTORY_SCAN_PORTS="8000 9999 12345" ./check.sh` si le
serveur d'inférence écoute ailleurs.

### Le chemin le plus court — sans compte Cloudflare

```sh
export FACTORY_LLM_URL=http://localhost:8001
export FACTORY_LLM_MODEL=Qwen3-VL-32B-Instruct
./start.sh
curl localhost:8000/health
cloudflared tunnel --url http://localhost:8000
```

Cloudflare répond avec une adresse `https://…trycloudflare.com` qui
change à chaque redémarrage de `cloudflared`, et que **quiconque a le lien
peut ouvrir** : à garder pour un essai. Pointé sur le studio (:8765), un
tel tunnel n'a pas de porte : le studio refuse alors tout ce qui en vient.

### Un jeton sur l'API

```sh
export FACTORY_TOKENS="colle-ici-une-longue-chaine-aleatoire"
./start.sh
```

Puis dans la console, bouton **MOTEUR**, champ *Jeton* : la même chaîne.
Servie par GitHub Pages, la console est sur une autre origine que l'API :
`export FACTORY_CORS_ORIGINS="https://calculment0r.github.io"` sur le DGX,
et l'adresse du tunnel dans l'écran MOTEUR.

| Ce que tu vois | Ce que c'est |
|---|---|
| La pastille reste rouge, « non configuré » | L'API ne répond pas : `curl localhost:8000/health` sur le DGX. |
| « dgx muet — 502 » | L'API tourne, le serveur d'inférence non : `FACTORY_LLM_URL`. |
| 401 sur chaque appel | `FACTORY_TOKENS` est posé et le jeton n'est pas dans l'écran MOTEUR. |
