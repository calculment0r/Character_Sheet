"""La mémoire d'un DGX Spark : charger, décharger, ne jamais saturer.

Un Spark partage 128 Go entre CPU et GPU. Saturé, il ne rend pas une
erreur CUDA propre : il gèle, et le tueur OOM ne voit pas la mémoire
CUDA (incident du 24/09/2026 sur DGX2). D'où trois règles, appliquées
par le studio avant chaque travail :

  - un seul gros travail GPU à la fois (la file du studio y veille) ;
  - avant une génération, le modèle de texte est déchargé d'Ollama
    (`keep_alive: 0`) ; il se recharge seul à la conversation suivante ;
  - quand on passe d'une famille de modèles à une autre (H3, Qwen,
    TRELLIS…), on vide ComfyUI (`/free`) avant de charger la suivante ;
  - enfin, on ne lance rien si la mémoire disponible est trop basse :
    on attend un peu, puis on refuse, en le disant.

Hors Linux (le studio lancé sur le PC), on ne lit pas la mémoire de la
machine distante : les deux premières règles s'appliquent quand même.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

from . import config


def ollama_url() -> str:
    return config.setting("llm_url", "http://127.0.0.1:11434").rstrip("/")


def available_gb() -> float | None:
    """MemAvailable de la machine où tourne le studio, en Go."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        for line in open("/proc/meminfo", encoding="ascii"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 / 1024
    except OSError:
        return None
    return None


def _post(url: str, body: dict, timeout: float = 30.0) -> dict | None:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
        return json.loads(raw) if raw else {}
    except (OSError, ValueError):
        return None


def llm_loaded() -> list[str]:
    try:
        with urllib.request.urlopen(f"{ollama_url()}/api/ps", timeout=5) as res:
            return [m["name"] for m in json.loads(res.read()).get("models", [])]
    except (OSError, ValueError, KeyError):
        return []


def unload_llm() -> list[str]:
    """Décharge tous les modèles qu'Ollama garde en mémoire."""
    gone = []
    for name in llm_loaded():
        if _post(f"{ollama_url()}/api/generate", {"model": name, "keep_alive": 0}) is not None:
            gone.append(name)
    return gone


def free_comfy(url: str) -> bool:
    return _post(f"{url.rstrip('/')}/free", {"unload_models": True, "free_memory": True}) is not None


class Manager:
    """Tient la famille de modèles chargée dans chaque ComfyUI, et prépare
    la machine avant chaque travail."""

    def __init__(self, min_free_gb: float = 30.0, wait_s: float = 120.0) -> None:
        self.min_free_gb = float(config.setting("min_free_gb", str(min_free_gb)))
        self.wait_s = wait_s
        self.family: dict[str, str] = {}

    def before_gpu(self, family: str, comfy_url: str | None, say=lambda m: None) -> None:
        gone = unload_llm()
        if gone:
            say(f"modèle de texte déchargé : {', '.join(gone)}")
        if comfy_url and self.family.get(comfy_url) not in (None, family):
            if free_comfy(comfy_url):
                say(f"ComfyUI vidé ({self.family[comfy_url]} → {family})")
        if comfy_url:
            self.family[comfy_url] = family
        self.wait_for(self.min_free_gb, say)

    def before_chat(self, say=lambda m: None) -> None:
        # Le modèle de texte (~20 Go) peut cohabiter avec un travail en
        # cours ; on ne vérifie que la marge.
        self.wait_for(22.0, say, patient=False)

    def wait_for(self, need_gb: float, say, patient: bool = True) -> None:
        free = available_gb()
        if free is None or free >= need_gb:
            return
        if not patient:
            raise MemoryError(f"mémoire disponible {free:.0f} Go, il en faut {need_gb:.0f} : réessaie après le "
                              f"travail en cours")
        say(f"mémoire disponible {free:.0f} Go, on attend d'en avoir {need_gb:.0f}")
        deadline = time.monotonic() + self.wait_s
        while time.monotonic() < deadline:
            time.sleep(5)
            free = available_gb()
            if free is not None and free >= need_gb:
                return
        raise MemoryError(f"mémoire disponible {free:.0f} Go après {self.wait_s:.0f} s d'attente, il en faut "
                          f"{need_gb:.0f} : un autre programme occupe la machine")
