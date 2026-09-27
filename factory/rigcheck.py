"""Mesurer un rig comme le ferait un viewer, sans le regarder.

Le skinning glTF rejoué sur les poses de contrôle du rig (`control/…`) :
la position de chaque articulation et de chaque sommet, pose par pose.
C'est ce que `tools/chain_check.py` vérifie sur le factice, et ce que
l'autopilote mesure sur un vrai rig avant de l'accepter (décision de
Cal du 27/09 : le rig ne passe plus devant lui).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .gltf import GLB, slerp, trs_matrix

ARM_RANGE = (35.0, 55.0)   # bind en A-pose : bras à 45° de la verticale
SQUAT_DROP = 0.12          # accroupi : les hanches descendent d'au moins 12 % de la taille
SPREAD_MAX = 1.6           # aucune pose ne dépasse 1,6 fois la taille : rien ne se disloque


def _pose_overrides(g: GLB, anim: dict, t: float) -> dict:
    out = {}
    for ch in anim["channels"]:
        smp = anim["samplers"][ch["sampler"]]
        times = g.read_accessor(smp["input"]).reshape(-1)
        vals = g.read_accessor(smp["output"])
        i = int(np.clip(np.searchsorted(times, t, side="right") - 1, 0, len(times) - 1))
        j = min(i + 1, len(times) - 1)
        a = 0.0 if j == i else float((t - times[i]) / (times[j] - times[i]))
        path = ch["target"]["path"]
        v = slerp(vals[i], vals[j], a) if path == "rotation" else vals[i] * (1 - a) + vals[j] * a
        out[(path, ch["target"]["node"])] = v
    return out


def skinned(g: GLB, overrides: dict) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Positions des sommets déformés, et position monde de chaque nœud."""
    nodes = g.doc["nodes"]
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    cache: dict[int, np.ndarray] = {}

    def world(i: int) -> np.ndarray:
        if i not in cache:
            n = nodes[i]
            m = trs_matrix(overrides.get(("translation", i), n.get("translation", [0, 0, 0])),
                           overrides.get(("rotation", i), n.get("rotation", [0, 0, 0, 1])),
                           n.get("scale", [1, 1, 1]))
            cache[i] = world(parent[i]) @ m if i in parent else m
        return cache[i]

    skin = g.doc["skins"][0]
    ibm = g.read_accessor(skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
    joint_m = np.stack([world(j) @ ibm[k] for k, j in enumerate(skin["joints"])])
    verts = []
    for mesh in g.doc["meshes"]:
        for prim in mesh["primitives"]:
            p = g.read_accessor(prim["attributes"]["POSITION"]).astype(float)
            jj = g.read_accessor(prim["attributes"]["JOINTS_0"]).astype(int)
            ww = g.read_accessor(prim["attributes"]["WEIGHTS_0"]).astype(float)
            m = np.einsum("vk,vkij->vij", ww, joint_m[jj])
            verts.append(np.einsum("vij,vj->vi", m, np.c_[p, np.ones(len(p))])[:, :3])
    where = {nodes[j].get("name", str(j)): world(j)[:3, 3] for j in skin["joints"]}
    return np.vstack(verts), where


def clip_pose(g: GLB, name: str) -> tuple[np.ndarray, dict]:
    anim = next(a for a in g.doc["animations"] if a["name"] == name)
    times = g.read_accessor(anim["samplers"][0]["input"]).reshape(-1)
    return skinned(g, _pose_overrides(g, anim, float(times[-1])))


def measure(glb: Path, meta: dict) -> dict:
    """Les mesures d'un rig et ses refus : bind en A-pose, 77
    articulations, cinq poses de contrôle, bras levés au-dessus de la
    tête, accroupi plus bas que debout, aucune pose disloquée."""
    g = GLB.load(glb)
    fails = []
    joints = len(g.doc["skins"][0]["joints"])
    clips = [a["name"] for a in g.doc.get("animations", []) if a["name"].startswith("control/")]
    arm = float(meta.get("arm_angle_deg", 0.0))
    if joints != 77:
        fails.append(f"{joints} articulations au lieu de 77")
    if len(clips) < 5:
        fails.append(f"{len(clips)} poses de contrôle au lieu de 5")
    if not ARM_RANGE[0] <= arm <= ARM_RANGE[1]:
        fails.append(f"bind : bras à {arm:.0f}° de la verticale ({ARM_RANGE[0]:g}–{ARM_RANGE[1]:g})")
    bind_v, bind_j = skinned(g, {})
    height = float(bind_v[:, 1].max() - bind_v[:, 1].min())
    out = {"joints": joints, "clips": len(clips), "arm_angle_deg": round(arm, 1), "height_m": round(height, 3)}
    if "control/arms_up" in clips and "control/squat" in clips:
        up_v, up_j = clip_pose(g, "control/arms_up")
        sq_v, sq_j = clip_pose(g, "control/squat")
        head = up_j.get("HeadEnd", up_j.get("Head"))
        raised = min(up_j["LeftHand"][1], up_j["RightHand"][1]) - head[1]
        drop = (bind_j["Hips"][1] - sq_j["Hips"][1]) / max(height, 1e-6)
        spread = max(float(np.linalg.norm(v.max(0) - v.min(0))) for v in (bind_v, up_v, sq_v)) / max(height, 1e-6)
        out.update(hands_above_head_m=round(float(raised), 3), squat_drop=round(float(drop), 3),
                   spread=round(spread, 3))
        if raised <= 0:
            fails.append("bras levés : les mains restent sous la tête")
        if drop < SQUAT_DROP:
            fails.append(f"accroupi : les hanches ne descendent que de {drop:.0%} de la taille")
        if spread > SPREAD_MAX:
            fails.append(f"une pose s'étend sur {spread:.1f} fois la taille : le mesh se disloque")
    return {**out, "ok": not fails, "fails": fails}
