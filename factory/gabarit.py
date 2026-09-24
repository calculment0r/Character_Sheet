"""`./usine gabarit` : adopte un workflow ComfyUI qui marche déjà.

H3 tourne dans ComfyUI sur la machine : le plus sûr est de partir du
workflow qu'on y utilise, plutôt que d'en réécrire un. On l'exporte au
format API (Workflow → Export (API)), et cette commande le marque pour
la chaîne :

  - les nœuds LoadImage deviennent `REF 1`, `REF 2`… dans l'ordre ;
  - le texte du prompt devient `{{prompt}}` ;
  - la graine devient `{{seed}}` ;
  - la taille et le nombre de frames deviennent `{{width}}`,
    `{{height}}`, `{{frames}}` ;
  - le nœud de sauvegarde devient `OUT`.

Tout ce qui est changé est affiché, pour relecture ; rien n'est deviné
en silence. Un champ mal reconnu se corrige à la main dans le JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import config

IMAGE_LOADERS = {"LoadImage", "LoadImageFromPath", "Load Image", "VHS_LoadImagePath"}
PROMPT_KEYS = ("prompt", "text", "positive", "text_g", "caption")
SEED_KEYS = ("seed", "noise_seed")
FRAME_KEYS = ("length", "num_frames", "frames", "frame_count", "video_length")
SAVE_STILL = {"SaveImage", "Image Save"}
SAVE_ANIM = {"SaveAnimatedWEBP", "SaveAnimatedPNG", "SaveWEBM", "SaveVideo", "VHS_VideoCombine"}


def _is_link(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def _title(node: dict) -> str:
    return node.get("_meta", {}).get("title", "")


def _order(nid: str):
    return (0, int(nid)) if nid.isdigit() else (1, nid)


def adopt(workflow: dict) -> tuple[dict, list[str], list[str]]:
    """Rend le gabarit, la liste des changements et les avertissements."""
    wf = json.loads(json.dumps(workflow))
    changes: list[str] = []
    warnings: list[str] = []
    if "nodes" in wf and "links" in wf:
        raise ValueError("c'est l'export « Save » (format éditeur) : réexporte avec Workflow → Export (API)")

    # Les références, dans l'ordre des nœuds.
    loaders = sorted((nid for nid, n in wf.items() if n.get("class_type") in IMAGE_LOADERS), key=_order)
    for i, nid in enumerate(loaders, 1):
        wf[nid].setdefault("_meta", {})["title"] = f"REF {i}"
        changes.append(f"nœud {nid} ({wf[nid]['class_type']}) → REF {i}")
    if not loaders:
        warnings.append("aucun nœud LoadImage : le workflow ne prendra pas de référence")

    # Le prompt : le premier champ texte libre d'un nœud qui n'est pas
    # un négatif. H3 n'a pas de prompt négatif ; s'il en traîne un, on le
    # signale sans y toucher.
    prompt_done = False
    for nid in sorted(wf, key=_order):
        node = wf[nid]
        if "neg" in _title(node).lower():
            warnings.append(f"nœud {nid} « {_title(node)} » : un prompt négatif — H3 n'en a pas (§5.3), à retirer")
            continue
        for key in PROMPT_KEYS:
            val = node.get("inputs", {}).get(key)
            if isinstance(val, str) and not prompt_done and len(val) > 0 and "{{" not in val:
                node["inputs"][key] = "{{prompt}}"
                changes.append(f"nœud {nid} ({node['class_type']}).{key} → {{{{prompt}}}}")
                prompt_done = True
    if not prompt_done:
        warnings.append("aucun champ de prompt reconnu : place {{prompt}} à la main")

    for nid in sorted(wf, key=_order):
        node = wf[nid]
        inputs = node.get("inputs", {})
        for key in SEED_KEYS:
            if isinstance(inputs.get(key), int):
                inputs[key] = "{{seed}}"
                changes.append(f"nœud {nid} ({node['class_type']}).{key} → {{{{seed}}}}")
        has_frames = any(isinstance(inputs.get(k), int) for k in FRAME_KEYS)
        sized = has_frames or "latent" in node.get("class_type", "").lower()
        for key in FRAME_KEYS:
            if isinstance(inputs.get(key), int):
                inputs[key] = "{{frames}}"
                changes.append(f"nœud {nid} ({node['class_type']}).{key} → {{{{frames}}}}")
        if sized:
            for key, ph in (("width", "{{width}}"), ("height", "{{height}}")):
                if isinstance(inputs.get(key), int):
                    inputs[key] = ph
                    changes.append(f"nœud {nid} ({node['class_type']}).{key} → {ph}")

    stills = [nid for nid, n in wf.items() if n.get("class_type") in SAVE_STILL]
    anims = [nid for nid, n in wf.items() if n.get("class_type") in SAVE_ANIM]
    outs = stills or anims
    for nid in outs:
        wf[nid].setdefault("_meta", {})["title"] = "OUT"
        changes.append(f"nœud {nid} ({wf[nid]['class_type']}) → OUT")
    if not stills:
        warnings.append("pas de SaveImage : ajoute-en un sur les frames décodées — la chaîne lit des PNG, "
                        "ou à défaut un WEBP/GIF animé, pas un MP4")
    if not outs:
        warnings.append("aucun nœud de sortie reconnu")
    return wf, changes, warnings


def run(source: str, name: str = "h3_ref2va.json", force: bool = False) -> Path:
    data = json.loads(Path(source).read_text(encoding="utf-8"))
    wf, changes, warnings = adopt(data)
    dest = config.workflows_dir() / name
    if dest.exists() and not force:
        raise ValueError(f"{dest} existe déjà — --force pour le remplacer")
    dest.parent.mkdir(parents=True, exist_ok=True)
    wf["_factory"] = {"adopted_from": Path(source).name, "changes": changes}
    dest.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"gabarit écrit : {dest}")
    for c in changes:
        print(f"  {c}")
    for w in warnings:
        print(f"  attention : {w}")
    return dest
