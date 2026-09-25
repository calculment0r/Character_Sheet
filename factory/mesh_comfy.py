"""Le mesh par ComfyUI : TRELLIS.2, nœuds natifs de ComfyUI 0.35.

Le gabarit `workflows/trellis2_mv.json` vient du modèle de workflow
`3d_pixal3d_multi_views` livré avec ComfyUI, converti par son interface :
quatre vues — REF 1 face, REF 2 gauche, REF 3 dos, REF 4 droite —,
détourées par BiRefNet, recadrées, conditionnées par Pixal3D
multi-vues, puis forme, raffinement et texture TRELLIS.2 ; remaillage,
dépliage UV, cuisson de la couleur, du métal, de la rugosité, des
normales et de l'occlusion. Un GLB sort.

Il sort dans le repère et à l'échelle du modèle. On le remet aux
conventions de la chaîne — mètres, Y en haut, pieds à y = 0, centré en
x et z, à la hauteur du personnage — en réécrivant les sommets : le rig
lit les positions brutes, pas les transformations de nœuds.

La capacité peut tourner sur une autre machine que H3 :
`comfyui_url_trellis` dans factory.local.json.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import config, gltf
from .comfy import Comfy, fill, load_template
from .project import ChainError

# Le moteur exposé → sa capacité, son gabarit multi-vues, son gabarit
# image unique. Le second vient du modèle `3d_pixal3d_trellis2_image_to_model`
# de ComfyUI, aiguillages figés sur la branche TRELLIS.2 pure (sans
# Pixal3D ni MoGe).
TEMPLATES = {"trellis2": ("trellis", "trellis2_mv.json", "trellis2_single.json")}
ORDER = ("front", "left", "back", "right")


def generate(engine: str, *, views: dict[str, Path], single_view: Path | None, dest: Path, seed: int,
             texture: bool = True, height_m: float = 1.75, report=lambda p, m: None) -> dict:
    if engine not in TEMPLATES:
        raise ChainError(f"{engine} n'a pas encore de gabarit ComfyUI")
    capability, multi, single = TEMPLATES[engine]
    comfy = Comfy(config.comfyui_url(capability))
    if single_view is not None:
        report(0.05, f"envoi de la vue unique à {comfy.url}")
        name, refs = single, [comfy.upload(Path(single_view))]
    else:
        missing = [k for k in ORDER if k not in views]
        if missing:
            raise ChainError(f"vues préparées manquantes pour le multi-vues : {', '.join(missing)}")
        report(0.05, f"envoi des quatre vues à {comfy.url}")
        name, refs = multi, [comfy.upload(Path(views[k])) for k in ORDER]
    wf = fill(load_template(name), {"seed": seed}, refs)
    work = dest.parent / ".comfy"
    paths = [p for p in comfy.run(wf, work, report=report, prefix="trellis2") if p.suffix.lower() == ".glb"]
    if not paths:
        raise ChainError("TRELLIS.2 n'a rendu aucun GLB")
    report(0.8, "mise aux conventions : mètres, Y en haut, pieds au sol")
    info = conform(paths[0], dest, height_m=height_m)
    return {"raw": str(paths[0]), **info}


def conform(src: Path, dest: Path, *, height_m: float) -> dict:
    """Réécrit les sommets : l'axe le plus long devient Y (un humain
    debout), la hauteur devient celle du personnage, les pieds vont à
    y = 0 et le centre en x = z = 0. Les normales suivent la rotation."""
    g = gltf.GLB.load(src)
    prims = [(mi, pi, prim) for mi, mesh in enumerate(g.doc.get("meshes", []))
             for pi, prim in enumerate(mesh["primitives"])]
    pos = {(mi, pi): g.read_accessor(prim["attributes"]["POSITION"]).astype(np.float64) for mi, pi, prim in prims}
    allp = np.concatenate(list(pos.values()))
    ext = allp.max(axis=0) - allp.min(axis=0)
    up = int(np.argmax(ext))
    # Rotation qui porte l'axe le plus long sur Y ; aucune si c'est déjà lui.
    rot = np.eye(3)
    if up == 2:        # Z en haut → Y en haut : -90° autour de X
        rot = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)
    elif up == 0:      # X en haut → Y en haut : +90° autour de Z
        rot = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], dtype=np.float64)
    turned = allp @ rot.T
    lo, hi = turned.min(axis=0), turned.max(axis=0)
    scale = height_m / (hi[1] - lo[1])
    offset = np.array([-(lo[0] + hi[0]) / 2, -lo[1], -(lo[2] + hi[2]) / 2])
    for mi, pi, prim in prims:
        p = (pos[(mi, pi)] @ rot.T + offset) * scale
        prim["attributes"]["POSITION"] = g.accessor(p.astype(np.float32), target=gltf.ARRAY_BUFFER, minmax=True)
        if "NORMAL" in prim["attributes"] and up != 1:
            nrm = g.read_accessor(prim["attributes"]["NORMAL"]).astype(np.float64) @ rot.T
            prim["attributes"]["NORMAL"] = g.accessor(nrm.astype(np.float32), target=gltf.ARRAY_BUFFER)
    # Les transformations de nœuds sont remises à zéro : tout est dans les sommets.
    for node in g.doc.get("nodes", []):
        for k in ("matrix", "translation", "rotation", "scale"):
            node.pop(k, None)
    g.doc.setdefault("asset", {})["extras"] = {"conformed": {"up_axis_in": "XYZ"[up], "scale": round(scale, 6),
                                                             "height_m": height_m}}
    g.save(dest)
    return {"up_axis_in": "XYZ"[up], "scale": scale, "extent_in": ext.round(4).tolist()}
