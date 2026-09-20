# Ouvrir la Factory depuis l'extérieur

Le but : ouvrir la page depuis n'importe quel réseau, sans ouvrir un
seul port sur le LAN du DGX. C'est le rôle du tunnel Cloudflare.

**Un seul tunnel suffit.** L'API sert aussi la page : la page et l'API
partagent donc la même adresse, il n'y a pas de CORS à régler et rien
à coller dans l'écran MOTEUR.

---

## Le chemin le plus court — 5 minutes, sans compte Cloudflare

### 1. Installer `cloudflared` sur le DGX

```sh
curl -L -o cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x cloudflared
sudo mv cloudflared /usr/local/bin/
cloudflared --version
```

### 2. Démarrer la Factory

```sh
pip install fastapi "uvicorn[standard]" httpx

# l'adresse de ton serveur d'inférence local
export FACTORY_LLM_URL=http://localhost:8001
export FACTORY_LLM_MODEL=Qwen3-VL-32B-Instruct

./start.sh
```

Vérifie sur place :

```sh
curl localhost:8000/health
```

### 3. Ouvrir le tunnel

Dans un autre terminal :

```sh
cloudflared tunnel --url http://localhost:8000
```

Cloudflare répond avec une adresse du genre :

```
https://quelque-chose-au-hasard.trycloudflare.com
```

### 4. Ouvrir cette adresse dans ton navigateur

C'est tout. La page se charge, détecte l'API toute seule, et la pastille
en haut à droite passe au vert avec le nom du modèle servi.

**Ce que vaut ce tunnel-là :** il est parfait pour essayer, mais
l'adresse change à chaque redémarrage de `cloudflared`, et **quiconque a
le lien entre**. À garder pour un essai, pas pour la semaine.

---

## Le montage durable — avec un compte et un domaine

Il faut un compte Cloudflare et un domaine dont les serveurs de noms
pointent vers Cloudflare.

### 1. Autoriser la machine, une seule fois

```sh
cloudflared tunnel login
```

Une page s'ouvre dans le navigateur : choisis ton domaine.

### 2. Créer le tunnel

```sh
cloudflared tunnel create character-factory
```

Note l'identifiant renvoyé (`<UUID>`), et le fichier d'identifiants
déposé dans `~/.cloudflared/<UUID>.json`.

### 3. Lui donner un nom de domaine

```sh
cloudflared tunnel route dns character-factory factory.tondomaine.fr
```

### 4. Écrire la configuration

`~/.cloudflared/config.yml` :

```yaml
tunnel: character-factory
credentials-file: /root/.cloudflared/<UUID>.json

ingress:
  - hostname: factory.tondomaine.fr
    service: http://localhost:8000
  - service: http_status:404
```

### 5. Lancer, puis installer en service

```sh
cloudflared tunnel run character-factory

# une fois que ça marche, pour que ça survive au redémarrage
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

La Factory est à `https://factory.tondomaine.fr`.

---

## Fermer la porte

Le tunnel rend l'adresse publique. Deux verrous, à poser dès que le lien
sort de ton écran.

### Un jeton sur l'API

```sh
export FACTORY_TOKENS="colle-ici-une-longue-chaine-aleatoire"
./start.sh
```

Puis dans la page, bouton **MOTEUR**, champ *Jeton* : colle la même
chaîne, enregistre.

La page elle-même reste ouverte — c'est normal, elle ne contient aucun
secret. C'est l'API qui est fermée.

### Cloudflare Access, si tu veux une porte avant la porte

Dans le tableau de bord Cloudflare, **Zero Trust → Access →
Applications**, ajoute `factory.tondomaine.fr` et une règle
« e-mails autorisés ». Cloudflare demande alors une identification
avant même que la requête atteigne le DGX. C'est le verrou le plus
solide, et il ne coûte rien pour quelques utilisateurs.

---

## Si ça ne marche pas

| Ce que tu vois | Ce que c'est |
|---|---|
| La pastille reste rouge, « non configuré » | L'API ne répond pas. Vérifie `curl localhost:8000/health` sur le DGX. |
| « dgx muet — 502 » | L'API tourne mais le serveur d'inférence, non. Vérifie `FACTORY_LLM_URL`. |
| 401 sur chaque appel | `FACTORY_TOKENS` est posé et le jeton n'est pas dans l'écran MOTEUR. |
| La page se charge, la progression d'un travail ne bouge pas | La progression passe en SSE. Si un proxy la met en tampon, elle arrive d'un coup à la fin. Le tunnel Cloudflare la laisse passer ; un autre proxy devant, pas forcément. |
| `502 Bad Gateway` de Cloudflare | `cloudflared` tourne mais rien n'écoute sur le port visé. |
| L'adresse `trycloudflare.com` ne répond plus | Elle a changé : `cloudflared` a redémarré. C'est la limite de ce tunnel-là. |

---

## Et GitHub Pages dans tout ça ?

Deux façons de servir la page, au choix.

**Par le tunnel** (ce guide) : une seule adresse, pas de CORS, la page
suit toujours le code qui tourne sur le DGX. C'est le plus simple pour
travailler.

**Par GitHub Pages** : la page est publique et rapide, mais elle est sur
une autre origine que l'API. Il faut alors, sur le DGX :

```sh
export FACTORY_CORS_ORIGINS="https://calculment0r.github.io"
```

et, dans la page, coller l'adresse du tunnel dans l'écran MOTEUR.

À noter : le site publié sert aujourd'hui la branche
`claude/interactive-character-generator-5IwJo`, pas `main`. Tant que le
réglage Pages pointe là, la refonte ne sera pas visible en ligne.
