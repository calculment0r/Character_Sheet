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


def read(path: Path, *, workdir: Path, name: str = "vue") -> str:
    """Le BVH de SAM 3D Body pour une image."""
    comfy = Comfy(config.comfyui_url("sam3dbody"))
    wf = _workflow(comfy.upload(Path(path)))
    bvh = next(p for p in comfy.run(wf, workdir, prefix=f"sam3d_{name}") if p.suffix.lower() == ".bvh")
    return bvh.read_text(encoding="utf-8")


def yaw(path: Path, *, workdir: Path, name: str = "vue") -> float:
    """Le cap brut du corps sur une image, en degrés. Premier passage réel
    le 25/09 : 10 s pour cinq vues, profils à ±5° — le signe est bon."""
    return root_yaw(read(path, workdir=workdir, name=name))


# ── le corps en 3D : face carrée, bras à leur place ─────────────────
#
# Le BVH de BuildPoseFile porte tout le squelette MHR (127 os, anonymes :
# joint_002…). Relevé le 28/09 sur les A-poses des personnages MJ : la
# colonne va de 53 à 56 ; épaule, coude et poignet sont 58, 59, 60 d'un
# côté et 108, 109, 110 de l'autre.

SPINE = (53, 56)
ARM_A, ARM_B = (58, 59, 60), (108, 109, 110)
SQUARE_DEG = 4.0        # bassin et buste de face, à la caméra
ARM_FWD = 0.45          # un bras ne part ni devant ni derrière le buste (cosinus)
ARM_GAP = 0.35          # les deux bras au même niveau : pas l'un devant, l'autre derrière


def _joints(bvh: str) -> tuple[np.ndarray, list[np.ndarray]]:
    """Positions et orientations de chaque os à la première frame."""
    lines = bvh.splitlines()
    parent: list[int | None] = []
    offset: list[np.ndarray] = []
    chans: list[list[str]] = []
    stack: list[int] = []
    cur = -1
    i = 0
    while not lines[i].strip().startswith("MOTION"):
        t = lines[i].split()
        if t and t[0] in ("ROOT", "JOINT", "End"):
            parent.append(stack[-1] if stack else None)
            offset.append(np.zeros(3))
            chans.append([])
            cur = len(parent) - 1
        elif t and t[0] == "{":
            stack.append(cur)
        elif t and t[0] == "}":
            stack.pop()
        elif t and t[0] == "OFFSET":
            offset[stack[-1]] = np.array([float(v) for v in t[1:4]])
        elif t and t[0] == "CHANNELS":
            chans[stack[-1]] = t[2:]
        i += 1
    frame = [float(v) for v in lines[next(k for k in range(i, len(lines))
                                          if lines[k].strip().startswith("Frame Time")) + 1].split()]
    pos, rot, k = [], [], 0
    for j, par in enumerate(parent):
        val = dict(zip(chans[j], frame[k:k + len(chans[j])]))
        k += len(chans[j])
        local = _rot_zxy(val.get("Zrotation", 0.0), val.get("Xrotation", 0.0), val.get("Yrotation", 0.0))
        if par is None:
            pos.append(np.array([val.get("Xposition", 0.0), val.get("Yposition", 0.0), val.get("Zposition", 0.0)]))
            rot.append(local)
        else:
            pos.append(pos[par] + rot[par] @ offset[j])
            rot.append(rot[par] @ local)
    return np.array(pos), rot


def body(bvh: str) -> dict:
    """Ce que montre le corps : rotation du bassin et du buste par rapport
    à la caméra (0 = de face), et, pour chaque bras, de combien il part
    devant (+) ou derrière (−) le plan des épaules, en cosinus, dans le
    repère du buste. Une A-pose de face bien carrée : bassin et buste à
    2° près (Chauve, Costaud, David) ; Survêt à 10°, Manteau à 20° le
    28/09. Un profil juste : les deux bras au même niveau ; les profils
    ratés montrent un bras devant et l'autre derrière (+0,42 / −0,32), ou
    les deux derrière (−0,43 / −0,62)."""
    pos, rot = _joints(bvh)
    side = pos[ARM_B[0]] - pos[ARM_A[0]]
    x = side / np.linalg.norm(side)
    up = pos[SPINE[1]] - pos[SPINE[0]]
    up = up - x * (up @ x)
    up /= np.linalg.norm(up)
    frame = np.stack([x, up, np.cross(x, up)])
    arms = {}
    for name, (sh, _, wr) in (("a", ARM_A), ("b", ARM_B)):
        v = frame @ (pos[wr] - pos[sh])
        arms[name] = round(float(v[2] / np.linalg.norm(v)), 3)
    fwd = rot[0][:, 2]
    return {"pelvis_deg": round(math.degrees(math.atan2(fwd[0], fwd[2])), 1),
            "chest_deg": round(math.degrees(math.atan2(side[2], side[0])), 1), "arms_forward": arms}


def square(b: dict) -> list[str]:
    """Ce qui empêche une A-pose d'être de face."""
    return [f"{'bassin' if k == 'pelvis_deg' else 'buste'} tourné de {b[k]:+.0f}° (SAM 3D Body)"
            for k in ("pelvis_deg", "chest_deg") if abs(b[k]) > SQUARE_DEG]


def arms_placed(b: dict) -> list[str]:
    """Ce qui cloche dans les bras d'une vue : un bras devant ou derrière
    le buste, ou l'un devant et l'autre derrière."""
    fa, fb = b["arms_forward"]["a"], b["arms_forward"]["b"]
    out = [f"un bras part {'devant' if f > 0 else 'derrière'} ({f:+.2f})" for f in (fa, fb) if abs(f) > ARM_FWD]
    if abs(fa - fb) > ARM_GAP:
        out.append(f"bras l'un devant, l'autre derrière ({fa:+.2f} / {fb:+.2f})")
    return out


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
