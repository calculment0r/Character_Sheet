"""Réglages de la chaîne locale.

Tout tourne sur la machine : pas de serveur, pas de file, pas de
tunnel. Chaque capacité du §1.1 du brief a un moteur, choisi dans cet
ordre de priorité :

  1. l'option --moteur d'une commande, pour un essai ponctuel ;
  2. la variable FACTORY_<CAPACITÉ>, par exemple FACTORY_H3=comfyui ;
  3. le fichier factory.local.json à la racine du dépôt, que
     `./usine doctor --ecrire` remplit avec ce qu'il a trouvé ;
  4. le défaut : ComfyUI pour H3, le factice pour le reste — il
     fabrique de vrais fichiers de test et le dit.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCAL_CONFIG = REPO / "factory.local.json"

# Une capacité = un modèle. `prep` et `bake` n'en chargent pas de gros.
CAPABILITIES = {
    "h3":        ("stub", "comfyui", "python"),
    "portrait":  ("stub", "comfyui"),
    "brief":     ("stub", "ollama"),
    "prep":      ("comfyui", "builtin", "rembg"),
    "delight":   ("off", "hunyuan"),
    "trellis":   ("stub", "comfyui", "python"),
    "hunyuan3d": ("stub", "python"),
    "unirig":    ("stub", "python"),
    "kimodo":    ("stub", "python"),
    "sam3dbody": ("stub", "python"),
}

# H3 tourne déjà dans ComfyUI sur la machine (confirmé par Cal) : c'est
# le moteur par défaut. Le détourage aussi passe par ComfyUI (BiRefNet) :
# le détourage intégré ne tient pas les fonds de studio que rend H3,
# dégradé et ombre du sujet compris. Les autres capacités restent
# factices tant que `./usine doctor` n'a pas trouvé leur modèle.
DEFAULTS = {"h3": "comfyui", "portrait": "comfyui", "brief": "ollama", "prep": "comfyui", "delight": "off",
            "trellis": "stub",
            "hunyuan3d": "stub", "unirig": "stub", "kimodo": "stub", "sam3dbody": "stub"}

_override: dict[str, str] = {}


@lru_cache(maxsize=1)
def _file() -> dict:
    try:
        return json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def setting(name: str, default: str = "") -> str:
    """Une valeur simple : variable d'environnement, sinon le fichier local."""
    env = os.getenv(f"FACTORY_{name.upper()}")
    if env:
        return env
    return str(_file().get(name, default))


def backend(capability: str) -> str:
    if capability in _override:
        return _override[capability]
    value = (os.getenv(f"FACTORY_{capability.upper()}")
             or _file().get("backends", {}).get(capability)
             or DEFAULTS[capability])
    if value not in CAPABILITIES[capability]:
        raise ValueError(f"moteur inconnu pour {capability} : {value} "
                         f"(possibles : {', '.join(CAPABILITIES[capability])})")
    return value


def force_backend(capability: str, value: str) -> None:
    """--moteur sur la ligne de commande : vaut pour ce seul appel."""
    if value not in CAPABILITIES[capability]:
        raise ValueError(f"moteur inconnu pour {capability} : {value}")
    _override[capability] = value


def projects_root() -> Path:
    return Path(setting("projects", str(REPO / "projects"))).expanduser()


def comfyui_url(capability: str | None = None) -> str:
    """Le ComfyUI d'une capacité. `comfyui_url_<capacité>` (par exemple
    `comfyui_url_trellis`, ou FACTORY_COMFYUI_URL_TRELLIS) l'envoie sur
    une autre machine que H3 ; sinon `comfyui_url` vaut pour tout."""
    if capability:
        own = setting(f"comfyui_url_{capability}")
        if own:
            return own.rstrip("/")
    return setting("comfyui_url", "http://127.0.0.1:8188").rstrip("/")


def workflows_dir() -> Path:
    return Path(setting("workflows", str(REPO / "workflows")))
