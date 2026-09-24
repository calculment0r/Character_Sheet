"""`./usine doctor` : ce qui tourne déjà sur la machine, et quels moteurs
régler. Ne modifie rien, sauf `--ecrire`, qui range ce qu'il a trouvé
dans factory.local.json.

La machine sert probablement déjà la moitié de ce dont la chaîne a
besoin — H3 dans ComfyUI, en particulier. On regarde avant d'installer.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys

from . import config
from .comfy import Comfy, ComfyError

# Des mots qui trahissent un nœud ComfyUI utile, par capacité.
NODE_HINTS = {
    "h3": ("minimax", "hailuo", "h3"),
    "hunyuan3d": ("hunyuan3d", "hy3d"),
    "trellis": ("trellis",),
    "unirig": ("unirig",),
}

# Les paquets Python que chaque moteur `python` importe.
PY_MODULES = {
    "trellis": ("trellis2",),
    "hunyuan3d": ("hy3dshape", "hy3dpaint"),
    "delight": ("diffusers",),
    "unirig": ("lightning",),
    "kimodo": ("kimodo",),
    "sam3dbody": ("sam_3d_body",),
    "prep": ("rembg",),
}


def ok(msg: str) -> None:
    print(f"  [ok]      {msg}")


def no(msg: str) -> None:
    print(f"  [absent]  {msg}")


def see(msg: str) -> None:
    print(f"  [voir]    {msg}")


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def gpus() -> list[str]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def comfy_nodes(url: str) -> tuple[bool, dict[str, list[str]]]:
    try:
        info = Comfy(url, timeout=5).object_info()
    except ComfyError:
        return False, {}
    found: dict[str, list[str]] = {}
    for cap, hints in NODE_HINTS.items():
        found[cap] = sorted(n for n in info if any(h in n.lower() for h in hints))
    return True, found


def run(*, write: bool = False) -> None:
    proposal: dict[str, str] = {}

    print("\n=== MACHINE ===========================================")
    ok(f"python {sys.version.split()[0]} ({sys.executable})")
    for mod in ("numpy", "PIL"):
        (ok if _has(mod) else no)(f"{mod}{'' if _has(mod) else ' — requis : pip install -r requirements.txt'}")
    cards = gpus()
    for c in cards:
        ok(f"GPU {c}")
    if not cards:
        no("aucun GPU visible (nvidia-smi absent ou muet)")
    if _has("torch"):
        try:
            import torch

            ok(f"torch {torch.__version__}, CUDA {'oui' if torch.cuda.is_available() else 'non'}")
        except Exception as exc:  # noqa: BLE001
            see(f"torch présent mais ne se charge pas : {exc}")
    else:
        no("torch — les moteurs `python` en ont besoin, pas ComfyUI")

    print("\n=== COMFYUI ===========================================")
    url = config.comfyui_url()
    candidates = [url] + [f"http://127.0.0.1:{p}" for p in (8188, 8189, 8000, 8080) if f":{p}" not in url]
    alive = None
    for u in candidates:
        up, nodes = comfy_nodes(u)
        if up:
            alive = u
            ok(f"ComfyUI répond sur {u}")
            for cap, names in nodes.items():
                if names:
                    ok(f"nœuds {cap} : {', '.join(names[:8])}{' …' if len(names) > 8 else ''}")
            if nodes.get("h3"):
                proposal["h3"] = "comfyui"
            else:
                see("aucun nœud H3 reconnu — vérifie le nom des nœuds, ou lance H3 par son script")
            break
    if not alive:
        no(f"ComfyUI ne répond pas ({', '.join(candidates)})")

    print("\n=== MOTEURS PYTHON ====================================")
    for cap, mods in PY_MODULES.items():
        present = [m for m in mods if _has(m)]
        if len(present) == len(mods):
            ok(f"{cap} : {', '.join(mods)}")
            if cap in ("trellis", "hunyuan3d", "kimodo", "sam3dbody") and cap not in proposal:
                proposal[cap] = "python"
            if cap == "delight":
                proposal.setdefault("delight", "hunyuan") if _has("hy3dpaint") or _has("hy3dgen") else None
            if cap == "prep":
                proposal["prep"] = "rembg"
        else:
            no(f"{cap} : {', '.join(m for m in mods if m not in present)}")
    unirig = config.setting("unirig_dir")
    if unirig:
        ok(f"UniRig : {unirig}")
        proposal["unirig"] = "python"
    else:
        no("UniRig : pose FACTORY_UNIRIG_DIR sur le dépôt cloné")

    print("\n=== RÉGLAGE ACTUEL ====================================")
    for cap in config.CAPABILITIES:
        cur = config.backend(cap)
        hint = f"  → {proposal[cap]} disponible" if cap in proposal and proposal[cap] != cur else ""
        print(f"  {cap:10s} {cur}{hint}")

    print("\n=== À FAIRE ===========================================")
    if not proposal:
        print("  rien de détecté : la chaîne tournera en factice, étiqueté comme tel.")
    else:
        for cap, value in proposal.items():
            print(f"  export FACTORY_{cap.upper()}={value}")
        if alive and alive != url:
            print(f"  export FACTORY_COMFYUI_URL={alive}")
        print("  ou : ./usine doctor --ecrire   (range tout dans factory.local.json)")
    if write:
        data = {}
        if config.LOCAL_CONFIG.exists():
            data = json.loads(config.LOCAL_CONFIG.read_text(encoding="utf-8"))
        data.setdefault("backends", {}).update(proposal)
        if alive:
            data["comfyui_url"] = alive
        config.LOCAL_CONFIG.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"\n  écrit : {config.LOCAL_CONFIG}")
    print()
