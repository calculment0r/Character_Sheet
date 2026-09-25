"""`./usine gabarit` : adopte un workflow ComfyUI qui marche déjà.

H3 tourne dans ComfyUI sur la machine : le plus sûr est de partir du
workflow qu'on y utilise, plutôt que d'en réécrire un. On l'exporte au
format API (Workflow → Export (API)), et cette commande le marque pour
la chaîne :

  - les nœuds LoadImage deviennent `REF 1`, `REF 2`… dans l'ordre ; si
    l'image entre dans une entrée extensible (`ref_images.ref_image_0`
    du nœud Ref2VA natif), on ajoute des nœuds REF jusqu'à neuf, le
    maximum du nœud ;
  - le texte du prompt devient `{{prompt}}`, qu'il soit écrit dans le
    nœud ou branché sur un nœud primitif de texte ;
  - la graine devient `{{seed}}` ;
  - la taille et le nombre de frames deviennent `{{width}}`,
    `{{height}}`, `{{frames}}` — un nombre de frames calculé (durée ×
    cadence) est remplacé par la valeur que la chaîne demande ;
  - le nœud de sauvegarde devient `OUT`. Une sortie vidéo est remplacée
    par un SaveImage sur les frames décodées : pas de recompression, et
    le son n'est plus décodé ;
  - ce qui ne mène plus à `OUT` est retiré, et les nœuds qui ne servent
    qu'à l'aperçu en direct dans l'interface sont court-circuités : en
    lot, personne ne les regarde, et d'une version de leur paquet à
    l'autre leurs entrées changent.

Tout ce qui est changé est affiché, pour relecture ; rien n'est deviné
en silence. Un champ mal reconnu se corrige à la main dans le JSON.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import config

IMAGE_LOADERS = {"LoadImage", "LoadImageFromPath", "Load Image", "VHS_LoadImagePath"}
PROMPT_KEYS = ("prompt", "text", "positive", "text_g", "caption")
TEXT_SOURCES = ("value", "text", "string", "prompt")      # l'entrée d'un nœud primitif de texte
SEED_KEYS = ("seed", "noise_seed")
FRAME_KEYS = ("length", "num_frames", "frames", "frame_count", "video_length")
SAVE_STILL = {"SaveImage", "Image Save"}
SAVE_ANIM = {"SaveAnimatedWEBP", "SaveAnimatedPNG", "SaveWEBM", "SaveVideo", "VHS_VideoCombine"}
MAX_REFS = 9          # ref_images du nœud MiniMaxH3ReferenceToVideo : 0 à 9
AUTOGROW = re.compile(r"^(\w+\.\w+?_)(\d+)$")              # ref_images.ref_image_0
# Nœud d'aperçu seul → l'entrée qu'il laisse passer telle quelle.
PREVIEW_ONLY = {"ModelPreviewOverrideKJ": "model"}


def _is_link(v) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def _title(node: dict) -> str:
    return node.get("_meta", {}).get("title", "")


def _order(nid: str):
    return (0, int(nid)) if nid.isdigit() else (1, nid)


def _new_id(wf: dict) -> str:
    return str(max((int(n) for n in wf if n.isdigit()), default=0) + 1)


def _text_source(wf: dict, link) -> str | None:
    """La clé du texte d'un nœud primitif branché en entrée, ou None."""
    src = wf.get(link[0], {})
    if any(isinstance(src.get("inputs", {}).get(k), str) for k in TEXT_SOURCES) and \
            not any(_is_link(v) for v in src.get("inputs", {}).values()):
        return next(k for k in TEXT_SOURCES if isinstance(src["inputs"].get(k), str))
    return None


def _grow_refs(wf: dict, loaders: list[str], changes: list[str]) -> list[str]:
    """Une image branchée sur une entrée extensible : on ajoute des
    chargeurs jusqu'à MAX_REFS, branchés à la suite. Rend les chargeurs
    dans l'ordre des références : celui de l'entrée extensible d'abord —
    `ref_image_0` est `<Picture 1>` pour le modèle —, puis les autres."""
    ordered: list[str] = []
    for nid in sorted(wf, key=_order):
        inputs = wf[nid].get("inputs", {})
        grown: dict[str, list[int]] = {}
        for key, val in inputs.items():
            m = AUTOGROW.match(key)
            if m and _is_link(val) and val[0] in loaders:
                grown.setdefault(m.group(1), []).append(int(m.group(2)))
        for prefix, used in grown.items():
            used.sort()
            template = inputs[f"{prefix}{used[0]}"][0]
            nxt = used[-1] + 1
            for _ in range(MAX_REFS - len(used)):
                new = _new_id(wf)
                wf[new] = json.loads(json.dumps(wf[template]))
                inputs[f"{prefix}{nxt}"] = [new, 0]
                used.append(nxt)
                changes.append(f"nœud {new} ({wf[new]['class_type']}) ajouté → {nid}.{prefix}{nxt}")
                nxt += 1
            ordered += [inputs[f"{prefix}{i}"][0] for i in used if inputs[f"{prefix}{i}"][0] not in ordered]
    return ordered + [nid for nid in loaders if nid not in ordered]


def _prune(wf: dict, outs: list[str], changes: list[str]) -> None:
    """Retire ce qui ne mène à aucun nœud OUT : ComfyUI ne l'exécute
    pas, et un nœud orphelin garde des valeurs que plus rien ne remplit."""
    keep, todo = set(), list(outs)
    while todo:
        nid = todo.pop()
        if nid in keep or nid not in wf:
            continue
        keep.add(nid)
        todo.extend(v[0] for v in wf[nid].get("inputs", {}).values() if _is_link(v))
    for nid in sorted(set(wf) - keep, key=_order):
        changes.append(f"nœud {nid} ({wf[nid]['class_type']}) retiré : il ne mène plus à OUT")
        del wf[nid]


def bypass_previews(wf: dict, changes: list[str]) -> None:
    """Court-circuite les nœuds d'aperçu : leurs consommateurs reçoivent
    directement ce que le nœud recevait."""
    for nid in sorted(wf, key=_order):
        node = wf.get(nid)
        if not isinstance(node, dict) or node.get("class_type") not in PREVIEW_ONLY:
            continue
        source = node.get("inputs", {}).get(PREVIEW_ONLY[node["class_type"]])
        if not _is_link(source):
            continue
        for other in wf.values():
            if not isinstance(other, dict):
                continue
            for key, val in other.get("inputs", {}).items():
                if _is_link(val) and val[0] == nid:
                    other["inputs"][key] = list(source)
        changes.append(f"nœud {nid} ({node['class_type']}) court-circuité : aperçu seul")
        del wf[nid]


def adopt(workflow: dict) -> tuple[dict, list[str], list[str]]:
    """Rend le gabarit, la liste des changements et les avertissements."""
    wf = json.loads(json.dumps(workflow))
    changes: list[str] = []
    warnings: list[str] = []
    if "nodes" in wf and "links" in wf:
        raise ValueError("c'est l'export « Save » (format éditeur) : réexporte avec Workflow → Export (API)")
    bypass_previews(wf, changes)

    # Les références, dans l'ordre des nœuds ; une entrée extensible
    # reçoit des chargeurs en plus.
    loaders = sorted((nid for nid, n in wf.items() if n.get("class_type") in IMAGE_LOADERS), key=_order)
    loaders = _grow_refs(wf, loaders, changes)
    for i, nid in enumerate(loaders, 1):
        wf[nid].setdefault("_meta", {})["title"] = f"REF {i}"
        changes.append(f"nœud {nid} ({wf[nid]['class_type']}) → REF {i}")
    if not loaders:
        warnings.append("aucun nœud LoadImage : le workflow ne prendra pas de référence")

    # Le prompt : le premier champ texte libre d'un nœud qui n'est pas
    # un négatif, écrit dans le nœud ou branché sur un primitif de texte.
    # H3 n'a pas de prompt négatif ; s'il en traîne un, on le signale.
    prompt_done = False
    for nid in sorted(wf, key=_order):
        node = wf[nid]
        if "neg" in _title(node).lower():
            warnings.append(f"nœud {nid} « {_title(node)} » : un prompt négatif — H3 n'en a pas (§5.3), à retirer")
            continue
        for key in PROMPT_KEYS:
            val = node.get("inputs", {}).get(key)
            if prompt_done:
                break
            if isinstance(val, str) and len(val) > 0 and "{{" not in val:
                node["inputs"][key] = "{{prompt}}"
                changes.append(f"nœud {nid} ({node['class_type']}).{key} → {{{{prompt}}}}")
                prompt_done = True
            elif _is_link(val) and _text_source(wf, val):
                node["inputs"][key] = "{{prompt}}"
                changes.append(f"nœud {nid} ({node['class_type']}).{key} → {{{{prompt}}}}, "
                               f"au lieu du texte de {val[0]} ({wf[val[0]]['class_type']})")
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
        has_frames = any(k in inputs for k in FRAME_KEYS)
        sized = has_frames or "latent" in node.get("class_type", "").lower()
        for key in FRAME_KEYS:
            val = inputs.get(key)
            if isinstance(val, int):
                inputs[key] = "{{frames}}"
                changes.append(f"nœud {nid} ({node['class_type']}).{key} → {{{{frames}}}}")
            elif _is_link(val):
                inputs[key] = "{{frames}}"
                changes.append(f"nœud {nid} ({node['class_type']}).{key} → {{{{frames}}}}, "
                               f"au lieu du calcul de {val[0]} ({wf.get(val[0], {}).get('class_type', '?')})")
        if sized:
            for key, ph in (("width", "{{width}}"), ("height", "{{height}}")):
                if isinstance(inputs.get(key), int):
                    inputs[key] = ph
                    changes.append(f"nœud {nid} ({node['class_type']}).{key} → {ph}")

    # La sortie : un SaveImage s'il y en a un ; sinon on en branche un
    # sur les frames qu'une sortie vidéo recevait, et la vidéo s'en va.
    stills = [nid for nid, n in wf.items() if n.get("class_type") in SAVE_STILL]
    anims = [nid for nid, n in wf.items() if n.get("class_type") in SAVE_ANIM]
    if not stills:
        for nid in anims:
            frames = wf[nid].get("inputs", {}).get("images")
            if _is_link(frames):
                new = _new_id(wf)
                wf[new] = {"class_type": "SaveImage", "inputs": {"images": frames, "filename_prefix": "usine/h3"},
                           "_meta": {"title": "SaveImage"}}
                changes.append(f"nœud {new} (SaveImage) ajouté sur les frames de {frames[0]}, "
                               f"à la place de {nid} ({wf[nid]['class_type']})")
                del wf[nid]
                stills.append(new)
                break
    outs = stills or anims
    for nid in outs:
        wf[nid].setdefault("_meta", {})["title"] = "OUT"
        changes.append(f"nœud {nid} ({wf[nid]['class_type']}) → OUT")
    if not stills and anims:
        warnings.append("la sortie est une vidéo : la chaîne en tire les frames par ffmpeg ; un SaveImage "
                        "sur les frames décodées évite la recompression")
    if not outs:
        warnings.append("aucun nœud de sortie reconnu")
    else:
        _prune(wf, outs, changes)
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
