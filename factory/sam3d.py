"""SAM 3D Body par ComfyUI (nœuds natifs de ComfyUI 0.35).

Premier usage : mesurer l'azimut des vues (§6.2), pour que le contrôle
±5° porte sur ce que les images montrent et plus sur ce qu'on a
demandé. Pour chaque vue : SAM3DBody_Predict, puis BuildPoseFile en BVH,
que SaveGLB écrit tel quel. La racine du BVH (le bassin, canaux
Z-X-Y intrinsèques, en degrés) donne l'orientation globale du corps
dans le repère de la caméra ; son axe avant, projeté au sol, donne le
cap. L'azimut d'une vue est l'écart de cap avec la vue de face — ce qui
annule le décalage propre au modèle.

Le signe : azimut 90 = caméra du côté gauche du sujet, qui regarde vers
la gauche de l'image (convention de la chaîne). Avec une caméra
« Y en haut, Z vers l'objectif », ce sujet a un cap de −90°, d'où
`SIGN = -1`. À confirmer au premier passage réel : sur des vues déjà
vérifiées à l'œil, `left` doit sortir vers 90.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from . import config
from .comfy import Comfy

SIGN = -1.0


def _workflow(image: str) -> dict:
    model = config.setting("sam3d_body", "sam_3d_body_dinov3_bf16.safetensors")
    return {
        "1": {"class_type": "SAM3DBody_Loader", "inputs": {"model_file": model}},
        "2": {"class_type": "LoadImage", "inputs": {"image": image}, "_meta": {"title": "REF 1"}},
        "3": {"class_type": "SAM3DBody_Predict",
              "inputs": {"sam3d_body_model": ["1", 0], "image": ["2", 0], "run_hand_refinement": False,
                         "fov": 0.0, "batch_size": 64}},
        "4": {"class_type": "BuildPoseFile",
              "inputs": {"pose_data": ["3", 0], "format": "bvh", "format.units": "m", "sam3d_body_model": ["1", 0],
                         "fps": 24.0, "camera_translation": "off", "track_index": 0}},
        "5": {"class_type": "SaveGLB", "inputs": {"mesh": ["4", 0], "filename_prefix": "usine/sam3d"},
              "_meta": {"title": "OUT"}},
    }


def _rot_zxy(z: float, x: float, y: float) -> np.ndarray:
    a, b, c = (math.radians(v) for v in (x, y, z))
    rz = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
    ry = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
    return rz @ rx @ ry


def root_yaw(bvh: str) -> float:
    """Le cap du bassin à la première frame, en degrés : 0 = face à +Z."""
    lines = bvh.splitlines()
    chans = next(ln.split()[2:] for ln in lines if ln.strip().startswith("CHANNELS"))
    first = lines[next(i for i, ln in enumerate(lines) if ln.strip().startswith("Frame Time")) + 1].split()
    val = dict(zip(chans, (float(v) for v in first)))
    r = _rot_zxy(val["Zrotation"], val["Xrotation"], val["Yrotation"])
    fwd = r[:, 2]
    return math.degrees(math.atan2(fwd[0], fwd[2]))


def yaw(path: Path, *, workdir: Path, name: str = "vue") -> float:
    """Le cap brut du corps sur une image, en degrés. Premier passage réel
    le 25/09 : 10 s pour cinq vues, profils à ±5° — le signe est bon."""
    comfy = Comfy(config.comfyui_url("sam3dbody"))
    wf = _workflow(comfy.upload(Path(path)))
    bvh = next(p for p in comfy.run(wf, workdir, prefix=f"sam3d_{name}") if p.suffix.lower() == ".bvh")
    return root_yaw(bvh.read_text(encoding="utf-8"))


def azimuth(view_yaw: float, front_yaw: float) -> float:
    """L'azimut d'une vue, relatif à la face, dans la convention de la chaîne."""
    return round((SIGN * (view_yaw - front_yaw)) % 360.0, 1)


def measure_azimuths(views: dict[str, Path], *, workdir: Path, report=lambda p, m: None) -> dict[str, dict]:
    """Mesure chaque vue ; rend, par vue, l'azimut relatif à la face et
    le cap brut. Il faut la vue de face : c'est la référence."""
    if "front" not in views:
        raise ValueError("la mesure d'azimut se fait par rapport à la vue de face : elle manque")
    yaws = {}
    for k, (name, path) in enumerate(views.items()):
        report(k / len(views), f"SAM 3D Body · {name}")
        yaws[name] = yaw(path, workdir=workdir, name=name)
    ref = yaws["front"]
    return {n: {"azimuth": azimuth(y, ref), "yaw_raw": round(y, 1)} for n, y in yaws.items()}
