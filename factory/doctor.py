"""`./usine doctor` : ce qui tourne déjà, et où. Ne modifie rien, sauf
`--ecrire`, qui range ce qu'il a trouvé dans factory.local.json.

Il n'exécute rien non plus sur les machines : pour chaque capacité
servie par ComfyUI, il valide à blanc les gabarits contre `/object_info`
du ComfyUI de cette capacité — chaque nœud existe, chaque fichier de
poids cité est connu du serveur. Pour les capacités lancées par ssh
(Kimodo, UniRig), il vérifie la machine (`hostname`, les DGX sont des
clones) et la présence du python de leur venv.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys

from . import config
from .comfy import Comfy, ComfyError, load_template, matte_workflow, validate

# Capacité → les gabarits (ou workflows construits) qu'elle envoie à ComfyUI.
COMFY_CAPS = {
    "h3": ("h3_ref2va.json", "h3_orbit.json"),
    "prep": ("<détourage BiRefNet>",),
    "trellis": ("trellis2_mv.json", "trellis2_single.json"),
    "sam3dbody": ("<mesure d'azimut SAM 3D Body>",),
    "views": ("<vues par LoRA d'angle Qwen>",),
}
# Capacité lancée par ssh → le script d'entrée qu'elle exécute.
REMOTE_CAPS = {"kimodo": "kimodo_entry.py", "unirig": "unirig_entry.py"}
# Ce qui doit aussi être là, sur la machine : (ce que c'est, test shell).
REMOTE_NEEDS = {
    "kimodo": [("poids Kimodo-SOMA-RP-v1", "test -d ~/kimodo/checkpoints/Kimodo-SOMA-RP-v1"),
               ("encodeur de texte Llama-3-8B-Instruct (dépôt Meta à accès restreint, licence à accepter sur "
                "Hugging Face, puis téléchargement)",
                "ls ~/.cache/huggingface/hub/models--meta-llama--Meta-Llama-3-8B-Instruct/snapshots/*/*.safetensors")],
    "unirig": [("checkpoint squelette", "test -s ~/UniRig/experiments/skeleton/articulation-xl_quantization_256/model.ckpt"),
               ("checkpoint peau", "test -s ~/UniRig/experiments/skin/articulation-xl/model.ckpt")],
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


def _workflows(cap: str) -> dict[str, dict]:
    if cap == "prep":
        return {"détourage BiRefNet": matte_workflow(["x.png"])}
    if cap == "sam3dbody":
        from . import sam3d

        return {"mesure SAM 3D Body": sam3d._workflow("x.png")}
    if cap == "views":
        from . import views_qwen

        return {f"vues {m}": views_qwen.workflow(m) for m in views_qwen.METHODS}
    out = {}
    for name in COMFY_CAPS[cap]:
        try:
            out[name] = load_template(name)
        except ComfyError as exc:
            out[name] = {"_absent": str(exc)}
    return out


def check_comfy(cap: str, cache: dict) -> bool:
    url = config.comfyui_url(cap)
    if url not in cache:
        try:
            cache[url] = Comfy(url, timeout=10).object_info()
        except ComfyError:
            cache[url] = None
    info = cache[url]
    if info is None:
        no(f"{cap} : ComfyUI ne répond pas sur {url}")
        return False
    good = True
    for name, wf in _workflows(cap).items():
        if "_absent" in wf:
            no(f"{cap} : {wf['_absent']}")
            good = False
            continue
        problems = validate(wf, info)
        if problems:
            good = False
            no(f"{cap} : {name} sur {url}")
            for pb in problems[:6]:
                print(f"              {pb}")
            if len(problems) > 6:
                print(f"              … et {len(problems) - 6} autres")
        else:
            ok(f"{cap} : {name} valide sur {url}")
    return good


def check_remote(cap: str) -> bool:
    from . import remote

    host, python = config.setting(f"remote_{cap}"), config.setting(f"python_{cap}")
    if not host or not python:
        no(f"{cap} : remote_{cap} / python_{cap} non réglés")
        return False
    entry = config.REPO / "tools" / "remote" / REMOTE_CAPS[cap]
    if not entry.exists():
        no(f"{cap} : script d'entrée absent ({entry.relative_to(config.REPO)})")
        return False
    try:
        res = remote._ssh(host, f"hostname && test -x {python} && echo PY", timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        no(f"{cap} : ssh {host} impossible ({exc})")
        return False
    lines = res.stdout.split()
    if not lines:
        no(f"{cap} : {host} injoignable ({res.stderr.strip()[-200:]})")
        return False
    if host.lower().startswith("dgx") and lines[0].lower() != host.lower():
        no(f"{cap} : l'alias {host} répond {lines[0]} — vérifie ~/.ssh/config (les DGX sont des clones)")
        return False
    if "PY" not in lines:
        no(f"{cap} : {python} absent sur {lines[0]}")
        return False
    ok(f"{cap} : {python} sur {lines[0]}")
    good = True
    for what, test in REMOTE_NEEDS.get(cap, []):
        if remote._ssh(host, f"{test} >/dev/null 2>&1", timeout=30).returncode:
            no(f"{cap} : {what}")
            good = False
        else:
            ok(f"{cap} : {what.split(' (')[0]}")
    return good


def run(*, write: bool = False) -> None:
    proposal: dict[str, str] = {}

    print("\n=== CE POSTE ==========================================")
    ok(f"python {sys.version.split()[0]} ({sys.executable})")
    for mod in ("numpy", "PIL"):
        (ok if _has(mod) else no)(f"{mod}{'' if _has(mod) else ' — requis : pip install -r requirements.txt'}")
    cards = gpus()
    for c in cards:
        ok(f"GPU {c}")
    if not cards:
        see("pas de GPU ici : les modèles tournent sur les machines ci-dessous")

    print("\n=== COMFYUI, PAR CAPACITÉ =============================")
    cache: dict = {}
    for cap in COMFY_CAPS:
        # La mesure SAM 3D Body n'est pas un moteur : elle ne se règle pas.
        if check_comfy(cap, cache) and "comfyui" in config.CAPABILITIES.get(cap, ()):
            proposal[cap] = "comfyui"

    print("\n=== PAR SSH ===========================================")
    for cap in REMOTE_CAPS:
        if check_remote(cap):
            proposal[cap] = "python"

    print("\n=== RÉGLAGE ACTUEL ====================================")
    for cap in config.CAPABILITIES:
        cur = config.backend(cap)
        where = ""
        if cur == "comfyui":
            where = f"  ({config.comfyui_url(cap)})"
        elif cur == "python" and cap in REMOTE_CAPS:
            where = f"  (ssh {config.setting(f'remote_{cap}') or '?'})"
        hint = f"  → {proposal[cap]} disponible" if cap in proposal and proposal[cap] != cur else ""
        print(f"  {cap:10s} {cur}{where}{hint}")

    if write:
        data = {}
        if config.LOCAL_CONFIG.exists():
            data = json.loads(config.LOCAL_CONFIG.read_text(encoding="utf-8"))
        data.setdefault("backends", {}).update(proposal)
        config.LOCAL_CONFIG.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"\n  écrit : {config.LOCAL_CONFIG}")
    elif any(proposal.get(c) != config.backend(c) for c in proposal):
        print("\n  ./usine doctor --ecrire   range les moteurs disponibles dans factory.local.json")
    print()
