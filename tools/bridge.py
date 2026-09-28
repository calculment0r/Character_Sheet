"""Le pont : l'état des DGX pour tout le monde, leur démarrage derrière la porte.

Cal, 28/09 : « je veux que mes amis puissent aller sur la page github et
faire leur perso quand les dgx sont allumés ». Le pont tourne toujours
sur DGX1, à côté du studio (tools/character-factory-bridge.service) ;
le tunnel Cloudflare lui envoie /bridge/*, et tout le reste au studio
:8765 (docs/CLOUDFLARE.md).

  GET  /bridge/health   public : les deux DGX allumées ou non, le studio,
                        ComfyUI :8188, H3 :8189, Ollama, la mémoire,
                        l'heure. Rien de secret : ni adresse, ni chemin,
                        ni personnage. CORS pour la page GitHub.
  GET  /bridge/         protégé : la même chose en page, avec « Tout
                        démarrer » — pour le jour où le studio lui-même dort.
  POST /bridge/start    protégé : {"what": "studio"|"comfyui"|"h3"|"ollama"|"all",
                                   "machine": "dgx1"|"dgx2"|"all"}
  POST /bridge/stop     protégé : {"what": "h3", "machine": …}, pour libérer la mémoire.

Protégé : la requête vient d'une adresse de confiance — le réseau de la
maison, le câble direct, ou la machine même, d'où sort le tunnel — et
porte l'e-mail que pose Cloudflare Access (Cf-Access-Authenticated-
User-Email), ou vient de la maison sans être passée par Cloudflare. Une
requête marquée par Cloudflare sans e-mail a pris le tunnel sans la
porte : refusée, 403. Un POST de navigateur doit venir d'une page de la
maison ou de l'adresse du studio, en JSON : jamais de la page GitHub.

ComfyUI, H3 et Ollama sont des services systemd (`sudo -n systemctl
start …`, sudo sans mot de passe est en place) ; DGX2 se joint par ssh
sur le câble direct. Le studio ne tourne que sur DGX1, lancé comme dans
docs/REPRISE_CAL.md ; sur DGX2, ce sont les relais (tools/relay.py) qui
renvoient :8765 et :8770 vers DGX1. On démarre ce qui manque ; on ne
redémarre jamais ce qui tourne (une autre session peut calculer dessus).

  python3 tools/bridge.py                        # :8770, pour de vrai
  python3 tools/bridge.py --port 8771 --essai    # rien ne se lance : les commandes sont rendues

Bibliothèque standard seulement : le pont tourne avec le python du
système, sans le venv du dépôt.
"""

from __future__ import annotations

import argparse
import copy
import ipaddress
import json
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
PORT = 8770
GITHUB_PAGES = "https://calculment0r.github.io"

# Ce que pose Cloudflare : l'e-mail de la porte, et les marques d'une
# requête passée par son réseau (donc par le tunnel).
ACCESS_EMAIL = "Cf-Access-Authenticated-User-Email"
CLOUDFLARE_MARKS = ("Cf-Ray", "Cf-Connecting-Ip", "Cf-Visitor", "Cdn-Loop", "Cf-Access-Jwt-Assertion")

# La maison, le câble direct entre les DGX, la machine même.
TRUSTED = ["192.168.10.0/24", "169.254.0.0/16", "127.0.0.1/32"]

UNITS = {"comfyui": "comfyui.service", "h3": "comfyui-h3test.service", "ollama": "ollama.service"}
PORTS = {"studio": 8765, "comfyui": 8188, "h3": 8189, "ollama": 11434, "relay": 8765, "relay_bridge": 8770, "ssh": 22}
REMOTE_REPO = "~/Character_Factory"

# DGX1 porte le studio et le pont ; DGX2, son miroir, se joint par le
# câble direct. `cable` : l'adresse de la machine sur le câble, celle
# vers laquelle les relais de DGX2 renvoient.
MACHINES = {
    "dgx1": {"label": "DGX1", "host": "127.0.0.1", "ssh": None, "cable": "169.254.110.6",
             "services": ["studio", "comfyui", "h3", "ollama"]},
    "dgx2": {"label": "DGX2", "host": "169.254.42.193", "ssh": "dgx@169.254.42.193", "cable": "169.254.42.193",
             "services": ["relay", "comfyui", "h3", "ollama"]},
}
STUDIO_MACHINE = "dgx1"
# Sans eux, on ne travaille pas : le studio, son ComfyUI, le modèle de texte.
ESSENTIAL = [("dgx1", "studio"), ("dgx1", "comfyui"), ("dgx1", "ollama")]
# H3 ne manque jamais : on l'arrête exprès, pour libérer la mémoire.
OPTIONAL = ("h3",)
# L'ordre de démarrage de « all » : les modèles d'abord, la page ensuite.
ORDER = ("ollama", "comfyui", "h3", "relay", "studio")
WHATS = ("studio", "comfyui", "h3", "ollama", "relay", "all")

HEALTH_TTL = 4.0        # s : un relevé sert à tous ceux qui demandent dans l'intervalle
HTTP_TIMEOUT = 1.5
TCP_TIMEOUT = 1.0
COMMAND_TIMEOUT = 60

# Ce que la page du pont charge, et rien d'autre.
STATIC = {
    "assets/tokens.css": "text/css; charset=utf-8",
    "assets/rack.css": "text/css; charset=utf-8",
    "assets/fonts/venus-rising.otf": "font/otf",
    "assets/fonts/norelli-black.otf": "font/otf",
    "js/machines.js": "text/javascript; charset=utf-8",
}

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_config() -> dict:
    """Les réglages : ceux d'ici, puis `factory.local.json` (`trusted_networks`,
    `public_host`), puis un fichier BRIDGE_CONFIG (essais), puis
    BRIDGE_PUBLIC_HOST et BRIDGE_DRY."""
    cfg = {"machines": copy.deepcopy(MACHINES), "trusted": list(TRUSTED), "public_host": "", "dry": False,
           "essential": [list(e) for e in ESSENTIAL], "repo": str(REPO)}
    local = _read_json(REPO / "factory.local.json")
    if isinstance(local.get("trusted_networks"), list):
        cfg["trusted"] = [str(n) for n in local["trusted_networks"]]
    cfg["public_host"] = str(local.get("public_host") or "")
    extra = _read_json(Path(os.environ["BRIDGE_CONFIG"])) if os.getenv("BRIDGE_CONFIG") else {}
    for mid, over in (extra.pop("machines", None) or {}).items():
        cfg["machines"].setdefault(mid, {}).update(over)
    cfg.update(extra)
    cfg["public_host"] = os.getenv("BRIDGE_PUBLIC_HOST", cfg["public_host"]).strip().lower()
    cfg["dry"] = bool(cfg["dry"]) or os.getenv("BRIDGE_DRY", "") not in ("", "0")
    return cfg


# ── qui demande ────────────────────────────────────────────────────

def trusted(ip: str, networks=TRUSTED) -> bool:
    """Une adresse de la maison, du câble direct, ou de la machine même."""
    try:
        addr = ipaddress.ip_address(str(ip).strip("[]"))
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    for net in networks:
        try:
            if addr in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            continue
    return False


def via_cloudflare(headers) -> bool:
    return any(headers.get(h) for h in CLOUDFLARE_MARKS)


def requester(peer: str, headers, networks=TRUSTED) -> str | None:
    """Qui demande : l'e-mail posé par la porte, « cal » pour la maison ;
    None pour qui vient d'ailleurs, ou a pris le tunnel sans la porte."""
    if not trusted(peer, networks):
        return None
    email = str(headers.get(ACCESS_EMAIL) or "").strip().lower()
    if email:
        return email
    return None if via_cloudflare(headers) else "cal"


# ── les relevés ────────────────────────────────────────────────────

def _get(url: str, timeout: float = HTTP_TIMEOUT):
    """Le JSON d'un service ; {} s'il répond autre chose ; None s'il se tait."""
    try:
        with _OPENER.open(url, timeout=timeout) as res:
            body = res.read(1 << 20)
    except urllib.error.HTTPError:
        return {}
    except (OSError, ValueError):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _tcp(host: str, port: int, timeout: float = TCP_TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _gb(n) -> float | None:
    try:
        return round(float(n) / 1024 ** 3, 1)
    except (TypeError, ValueError):
        return None


def meminfo() -> dict | None:
    """La mémoire de la machine même (unifiée sur un DGX Spark)."""
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None
    vals = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable") and rest.split():
            vals[key] = int(rest.split()[0]) * 1024
    if len(vals) < 2:
        return None
    return {"total_gb": _gb(vals["MemTotal"]), "free_gb": _gb(vals["MemAvailable"])}


class Bridge:
    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or load_config()
        self._cache: tuple[float, dict] | None = None
        self._health_lock = threading.Lock()
        self._act_lock = threading.Lock()

    # réglages

    def port(self, m: dict, name: str) -> int:
        return int((m.get("ports") or {}).get(name, PORTS[name]))

    def machine(self, mid: str) -> dict:
        return self.cfg["machines"][mid]

    def forget(self) -> None:
        self._cache = None

    # santé

    def _probe(self, m: dict, service: str):
        host = m["host"]
        if service == "studio":
            got = _get(f"http://{host}:{self.port(m, 'studio')}/api/system")
            if got is None:
                return None
            return {"busy": bool(got.get("running")), "queued": int(got.get("queued") or 0)}
        if service == "relay":
            # Deux relais : le studio (:8765) et le pont (:8770).
            both = [_tcp(host, self.port(m, r)) for r in ("relay", "relay_bridge")]
            return {"parts": both} if any(both) else None
        if service in ("comfyui", "h3"):
            return _get(f"http://{host}:{self.port(m, service)}/system_stats")
        if service == "ollama":
            return _get(f"http://{host}:{self.port(m, 'ollama')}/api/version")
        return None

    def _machine(self, mid: str, pool: ThreadPoolExecutor) -> dict:
        m = self.machine(mid)
        local = not m.get("ssh")
        jobs = {s: pool.submit(self._probe, m, s) for s in m["services"]}
        door = None if local else pool.submit(_tcp, m["host"], self.port(m, "ssh"))
        got = {s: f.result() for s, f in jobs.items()}
        up = local or bool(door and door.result()) or any(v is not None for v in got.values())
        services = []
        for s in m["services"]:
            v = got[s]
            entry = {"id": s, "up": v is not None}
            if s == "studio" and v is not None:
                entry.update(busy=v["busy"], queued=v["queued"])
            if s == "relay" and v is not None:
                entry["up"] = all(v["parts"])
            services.append(entry)
        memory = meminfo() if local else None
        if memory is None:
            # Ailleurs : ce que ComfyUI dit de la mémoire (unifiée : c'est la même).
            stats = next((got[s] for s in ("comfyui", "h3") if got.get(s)), None) or {}
            system = stats.get("system") or {}
            if system.get("ram_total"):
                memory = {"total_gb": _gb(system["ram_total"]), "free_gb": _gb(system.get("ram_free"))}
        return {"id": mid, "label": m.get("label", mid.upper()), "up": up, "memory": memory, "services": services}

    def health(self, fresh: bool = False) -> dict:
        with self._health_lock:
            if not fresh and self._cache and time.monotonic() - self._cache[0] < HEALTH_TTL:
                return self._cache[1]
            # Les machines ensemble, et chacune ses services ensemble : un
            # relevé dure le plus lent des délais, pas leur somme.
            mids = list(self.cfg["machines"])
            with ThreadPoolExecutor(max_workers=16) as probes, ThreadPoolExecutor(max_workers=len(mids)) as outer:
                machines = list(outer.map(lambda mid: self._machine(mid, probes), mids))
            up = {(m["id"], s["id"]): m["up"] and s["up"] for m in machines for s in m["services"]}
            essential = [tuple(e) for e in self.cfg["essential"]]
            missing = [f"{m}:{s}" for m, s in essential if not up.get((m, s))]
            # Sur une machine allumée, tout ce qui dort manque aussi, sauf H3.
            for m in machines:
                for s in m["services"]:
                    key = f"{m['id']}:{s['id']}"
                    if m["up"] and not s["up"] and s["id"] not in OPTIONAL and key not in missing:
                        missing.append(key)
            out = {"service": "character-factory-bridge", "at": now(), "machines": machines, "missing": missing,
                   "ready": not any(tuple(k.split(":")) in essential for k in missing)}
            self._cache = (time.monotonic(), out)
            return out

    # commandes

    def command(self, m: dict, service: str, verb: str = "start") -> list[str]:
        """La commande qui démarre (ou arrête) un service sur sa machine."""
        if service in UNITS:
            line = ["sudo", "-n", "systemctl", verb, UNITS[service]]
            return line if not m.get("ssh") else self._ssh(m, shlex.join(line))
        if service == "studio":
            # Comme dans docs/REPRISE_CAL.md : détaché, son journal dans studio.log.
            text = (f"cd {shlex.quote(self.cfg['repo'])} && PYTHONUNBUFFERED=1 setsid nohup ./usine studio "
                    f"> studio.log 2>&1 < /dev/null &")
            return ["bash", "-lc", text]
        if service in ("relay", "relay_bridge"):
            port = PORTS[service]
            target = self.machine(STUDIO_MACHINE)["cable"]
            log = "relay_studio.log" if service == "relay" else "relay_bridge.log"
            text = (f"cd {REMOTE_REPO} && setsid nohup python3 tools/relay.py {port} {target}:{port} "
                    f"> ~/{log} 2>&1 < /dev/null &")
            return self._ssh(m, text) if m.get("ssh") else ["bash", "-lc", text]
        raise ValueError(service)

    def _ssh(self, m: dict, text: str) -> list[str]:
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", m["ssh"], text]

    def _run(self, cmd: list[str], done: str) -> dict:
        if self.cfg["dry"]:
            return {"result": f"{done} (essai)", "cmd": cmd}
        try:
            res = subprocess.run(cmd, cwd=self.cfg["repo"], capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
                                 stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"result": f"échec : {exc}"}
        if res.returncode != 0:
            why = (res.stderr or res.stdout or "").strip().splitlines()
            return {"result": f"échec : {why[-1] if why else f'code {res.returncode}'}"}
        return {"result": done}

    def _machines(self, machine: str) -> list[str]:
        if machine == "all":
            return list(self.cfg["machines"])
        if machine not in self.cfg["machines"]:
            raise Refused(400, f"machine inconnue : {machine} (dgx1, dgx2 ou all)")
        return [machine]

    def start(self, what: str, machine: str, who: str) -> dict:
        if what not in WHATS:
            raise Refused(400, f"rien à démarrer sous ce nom : {what} (studio, comfyui, h3, ollama ou all)")
        mids = self._machines(machine)
        if what == "studio" and STUDIO_MACHINE not in mids:
            raise Refused(409, f"le studio de {self.machine(mids[0])['label']} ne se démarre pas : c'est celui de "
                               f"{self.machine(STUDIO_MACHINE)['label']} qui sert (les relais y renvoient)")
        if what not in ("all", "studio") and len(mids) == 1 and what not in self.machine(mids[0])["services"]:
            raise Refused(409, f"{self.machine(mids[0])['label']} n'a pas de {what}")
        with self._act_lock:
            health = self.health(fresh=True)
            state = {m["id"]: m for m in health["machines"]}
            steps: list[dict] = []
            for mid in mids:
                m, now_ = self.machine(mid), state[mid]
                wanted = [s for s in ORDER if s in m["services"] and (what == "all" or s == what)]
                if not wanted:
                    continue
                if not now_["up"]:
                    if len(mids) == 1:
                        raise Refused(409, f"{m['label']} est éteinte : allume-la, puis recommence")
                    steps.append(self._step(mid, "machine", {"result": "éteinte : à allumer d'abord"}))
                    continue
                running = {s["id"]: s["up"] for s in now_["services"]}
                for s in wanted:
                    if s == "relay":
                        # Chaque relais à part : on ne relance pas celui qui tourne.
                        for r in ("relay", "relay_bridge"):
                            if _tcp(m["host"], self.port(m, r)):
                                steps.append(self._step(mid, r, {"result": "déjà en marche"}))
                            else:
                                steps.append(self._step(mid, r, self._run(self.command(m, r), "lancé")))
                        continue
                    if running.get(s):
                        steps.append(self._step(mid, s, {"result": "déjà en marche"}))
                        continue
                    steps.append(self._step(mid, s, self._run(self.command(m, s), "lancé" if s == "studio" else "démarré")))
            self.forget()
        _journal(who, f"démarrer {what} sur {machine}", steps)
        return {"ok": not any(s["result"].startswith(("échec", "éteinte")) for s in steps), "dry": self.cfg["dry"],
                "steps": steps}

    def stop(self, what: str, machine: str, who: str) -> dict:
        if what != "h3":
            raise Refused(400, "seul H3 s'arrête d'ici, pour libérer la mémoire : le reste sert à tout le monde")
        mids = self._machines(machine)
        with self._act_lock:
            state = {m["id"]: m for m in self.health(fresh=True)["machines"]}
            steps = []
            for mid in mids:
                m = self.machine(mid)
                if "h3" not in m["services"]:
                    continue
                if not state[mid]["up"]:
                    steps.append(self._step(mid, "h3", {"result": "éteinte"}))
                    continue
                if not next(s["up"] for s in state[mid]["services"] if s["id"] == "h3"):
                    steps.append(self._step(mid, "h3", {"result": "déjà arrêté"}))
                    continue
                queue = _get(f"http://{m['host']}:{self.port(m, 'h3')}/queue") or {}
                if queue.get("queue_running") or queue.get("queue_pending"):
                    if len(mids) == 1:
                        raise Refused(409, f"H3 calcule sur {m['label']} : attends la fin du rendu, ou arrête-le "
                                           f"dans la file du studio")
                    steps.append(self._step(mid, "h3", {"result": "refusé : il calcule"}))
                    continue
                steps.append(self._step(mid, "h3", self._run(self.command(m, "h3", "stop"), "arrêté")))
            self.forget()
        _journal(who, f"arrêter h3 sur {machine}", steps)
        return {"ok": not any(s["result"].startswith(("échec", "refusé")) for s in steps), "dry": self.cfg["dry"],
                "steps": steps}

    def _step(self, mid: str, what: str, res: dict) -> dict:
        return {"machine": mid, "label": self.machine(mid).get("label", mid), "what": what, **res}

    # l'origine d'un navigateur

    def _host_ok(self, host: str | None) -> bool:
        host = (host or "").lower()
        return host == "localhost" or (bool(self.cfg["public_host"]) and host == self.cfg["public_host"]) \
            or trusted(host, self.cfg["trusted"])

    def read_origin(self, origin: str) -> bool:
        """Qui peut lire la santé : la page GitHub, l'adresse du studio, la maison."""
        if not origin:
            return False
        if origin.rstrip("/") == GITHUB_PAGES:
            return True
        return self._host_ok(urlsplit(origin).hostname)

    def write_origin(self, origin: str, host_header: str, cloudflare: bool) -> bool:
        """Qui peut démarrer depuis un navigateur : une page du studio ou de
        la maison — jamais la page GitHub, jamais un site tiers."""
        if not origin:
            return True             # pas un navigateur : curl, un script de la maison
        u = urlsplit(origin)
        if cloudflare:
            return u.scheme == "https" and (u.netloc.lower() == str(host_header or "").lower()
                                            or (bool(self.cfg["public_host"]) and u.hostname == self.cfg["public_host"]))
        return u.scheme in ("http", "https") and self._host_ok(u.hostname)


class Refused(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _journal(who: str, what: str, steps: list[dict]) -> None:
    """Qui a démarré quoi : le journal du service (journalctl -u …)."""
    done = "; ".join(f"{s['machine']}/{s['what']} {s['result']}" for s in steps) or "rien"
    print(f"{now()}  {who} : {what} → {done}", flush=True)


# ── la page du pont ────────────────────────────────────────────────

PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>CHARACTER FACTORY · MACHINES</title>
<meta name="description" content="Les deux DGX : allumées, prêtes, et ce qu'il faut démarrer pour travailler.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23e0674a'/%3E%3Crect x='12' y='12' width='8' height='8' rx='2' fill='%23170c08'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;500;600;700&family=Azeret+Mono:wght@300;400;500&display=swap">
<link rel="stylesheet" href="./assets/tokens.css">
<link rel="stylesheet" href="./assets/rack.css">
<style>
/* La page du pont : que des jetons et les composants de rack.css. */
.pont { max-width: 760px; margin: 0 auto; padding: var(--s6) var(--s6) 64px; display: flex; flex-direction: column; gap: var(--s5); }
.pont h1 { margin: 0; font-family: var(--f-disp); font-weight: 400; font-size: clamp(24px, 3.4vw, 38px);
  line-height: 1.08; letter-spacing: .02em; text-transform: uppercase; }
.pont .prose { margin: 0; max-width: 62ch; color: var(--ink2); font-size: 13px; line-height: 1.7; }
@media (max-width: 640px) { .pont { padding: var(--s4) 16px 48px; } }
</style>
</head>
<body>
<header class="hdr">
  <a class="logo" id="home" href="/" title="le studio"><span class="sq"><i></i></span><span><b>Character Factory</b><small>machines</small></span></a>
  <span class="sp"></span>
  <span class="pill" id="pill"><i></i><span id="pill-text">relevé</span></span>
</header>
<main class="pont">
  <h1>Les machines</h1>
  <p class="prose" id="say">Le pont relève les deux DGX.</p>
  <section class="mach-card">
    <div class="mach-list" id="list"></div>
    <div class="mach-foot">
      <button class="tb go" id="start" type="button" hidden>Tout démarrer</button>
      <a class="tb ghost" id="open" href="/">Ouvrir le studio &#9656;</a>
      <span class="sp"></span><span class="lbl" id="when"></span>
    </div>
  </section>
</main>
<script type="module">
import { fetchHealth, command, machinesList, verdict, when, stepsText } from './js/machines.js';

// Derrière le tunnel, le studio est à la racine ; à la maison, sur :8765.
const studio = location.port === '8770' ? `${location.protocol}//${location.hostname}:8765/` : '/';
document.getElementById('open').href = studio;
document.getElementById('home').href = studio;
let fast = 0;

async function paint() {
  let h = null;
  try { h = await fetchHealth('./health'); } catch (_) { /* le pont se tait : rien à montrer */ }
  const v = verdict(h);
  const studioUp = !!h && h.machines.some((m) => m.services.some((s) => s.id === 'studio' && s.up));
  document.getElementById('pill').className = `pill ${h ? (h.ready ? 'on' : 'work') : 'err'}`;
  document.getElementById('pill-text').textContent = v.word;
  document.getElementById('say').textContent = h ? `${v.text}${studioUp ? '' : ' Le studio dort : « Tout démarrer » le lance, avec les modèles.'}` : v.text;
  document.getElementById('list').innerHTML = machinesList(h);
  document.getElementById('when').textContent = h ? when(h) : '';
  document.getElementById('start').hidden = !h || !(h.missing || []).length;
  document.getElementById('open').hidden = !studioUp;
  setTimeout(paint, Date.now() < fast ? 2500 : 10000);
}

document.getElementById('start').addEventListener('click', async (e) => {
  e.target.disabled = true;
  try {
    const out = await command('', 'start', { what: 'all', machine: 'all' });
    document.getElementById('say').textContent = `C'est parti : ${stepsText(out)}. ComfyUI met une minute à s'éveiller.`;
    fast = Date.now() + 120000;
  } catch (err) {
    document.getElementById('say').textContent = err.message;
  }
  e.target.disabled = false;
});

paint();
</script>
</body>
</html>
"""


# ── le serveur ─────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    bridge: Bridge
    server_version = "usine-pont"

    def log_message(self, fmt, *args) -> None:
        if len(args) > 1 and str(args[1]).startswith(("4", "5")):
            sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code: int = 200, headers: dict | None = None) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", headers)

    def _error(self, code: int, message: str, headers: dict | None = None) -> None:
        self._json({"error": {"message": message}}, code, headers)

    def _who(self) -> str | None:
        return requester(self.client_address[0], self.headers, self.bridge.cfg["trusted"])

    def _cors(self, write: bool) -> dict:
        origin = self.headers.get("Origin") or ""
        b = self.bridge
        ok = (b.write_origin(origin, self.headers.get("Host"), via_cloudflare(self.headers)) if write
              else b.read_origin(origin)) and origin
        if not ok:
            return {"Vary": "Origin"}
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                **({"Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type",
                    "Access-Control-Max-Age": "600"} if write else {"Access-Control-Allow-Methods": "GET"})}

    def _refuse_stranger(self, headers: dict | None = None) -> str | None:
        who = self._who()
        if who is None:
            self._error(403, "le pont ne démarre rien pour un inconnu : passe par l'adresse du studio (la porte "
                             "Cloudflare Access) ou par le réseau de la maison", headers)
        return who

    def do_OPTIONS(self) -> None:
        path = urlsplit(self.path).path
        if path == "/bridge/health":
            return self._send(204, b"", "text/plain", self._cors(write=False))
        if path in ("/bridge/start", "/bridge/stop"):
            return self._send(204, b"", "text/plain", self._cors(write=True))
        self._error(404, "route inconnue")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        try:
            if path == "/bridge/health":
                return self._json(self.bridge.health(), headers=self._cors(write=False))
            if path == "/bridge":
                return self._send(301, b"", "text/plain", {"Location": "/bridge/"})
            if path.startswith("/bridge/") and (path == "/bridge/" or path[len("/bridge/"):] in STATIC):
                if self._refuse_stranger() is None:
                    return None
                if path == "/bridge/":
                    return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                rel = path[len("/bridge/"):]
                return self._send(200, (REPO / rel).read_bytes(), STATIC[rel])
            self._error(404, "route inconnue")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError as exc:
            self._error(500, f"{type(exc).__name__} : {exc}")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in ("/bridge/start", "/bridge/stop"):
            return self._error(404, "route inconnue")
        cors = self._cors(write=True)
        who = self._refuse_stranger(cors)
        if who is None:
            return None
        if not self.bridge.write_origin(self.headers.get("Origin") or "", self.headers.get("Host"),
                                        via_cloudflare(self.headers)):
            return self._error(403, "le démarrage se fait dans le studio, derrière la porte : pas depuis cette page", cors)
        if not str(self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            return self._error(415, "le pont n'accepte que du JSON", cors)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 4096)) or b"{}") if n <= 4096 else None
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return self._error(400, "un objet JSON est attendu : {\"what\": …, \"machine\": …}", cors)
        what = str(body.get("what") or "")
        machine = str(body.get("machine") or "dgx1")
        try:
            out = (self.bridge.start if path.endswith("start") else self.bridge.stop)(what, machine, who)
        except Refused as exc:
            return self._error(exc.code, str(exc), cors)
        self._json(out, headers=cors)


def make_server(host: str, port: int, bridge: Bridge) -> ThreadingHTTPServer:
    handler = type("BridgeHandler", (Handler,), {"bridge": bridge})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="le pont : l'état des DGX, leur démarrage derrière la porte")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--hote", default="0.0.0.0", help="adresse d'écoute (défaut : tout le réseau local)")
    ap.add_argument("--essai", action="store_true", help="ne rien lancer : les commandes sont rendues telles quelles")
    args = ap.parse_args(argv)
    cfg = load_config()
    cfg["dry"] = cfg["dry"] or args.essai
    server = make_server(args.hote, args.port, Bridge(cfg))
    print(f"pont : http://{args.hote}:{args.port}/bridge/health{'  (essai : rien ne se lance)' if cfg['dry'] else ''}",
          flush=True)
    for mid, m in cfg["machines"].items():
        print(f"  {m.get('label', mid)} : {m['host']}{' (par ssh ' + m['ssh'] + ')' if m.get('ssh') else ' (ici)'}",
              flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
