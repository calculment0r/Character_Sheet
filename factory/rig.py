"""L'étage rig (§10) : un squelette SOMA 77 aux mesures du personnage,
lié en A-pose, avec son delta de bind et ses cinq poses de contrôle.

Le chemin retenu par le brief :
  1. UniRig prédit la hiérarchie et les poids de skinning sur le mesh ;
  2. un mapping de noms UniRig → SOMA, établi une fois, versionné dans
     data/, jamais recalculé par personnage ;
  3. le delta de bind A-pose → T-pose SOMA, rangé avec le rig ;
  4. cinq poses de contrôle, jouées dans le viewer : un rig qui passe
     les cinq est accepté.

Le rig sort en un GLB : le mesh, le squelette SOMA en A-pose, les poids,
et un clip par pose de contrôle (`control/<pose>`).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import config, gltf, skeleton
from .skeleton import CONTROL_LABELS, CONTROL_POSES, Skeleton

# Au-delà, le bras est plus près de l'horizontale que des 45° de
# l'A-pose : le mesh a été généré en T-pose, et le brief l'interdit (§8.1).
TPOSE_LIMIT_DEG = 70.0


class RigRefused(Exception):
    pass


def arm_angle(sk: Skeleton) -> float:
    """Angle moyen des bras avec la verticale, dans le bind : ~45° en
    A-pose, ~90° en T-pose."""
    rot, pos = sk.fk(sk.bind)
    angles = []
    for arm, fore in (("l_arm", "l_forearm"), ("r_arm", "r_forearm")):
        a, f = sk.role(arm), sk.role(fore)
        if a is None or f is None:
            continue
        bone = pos[f] - pos[a]
        angles.append(np.degrees(np.arccos(np.clip(-bone[1] / np.linalg.norm(bone), -1, 1))))
    return float(np.mean(angles)) if angles else 45.0


def build(*, mesh_glb: Path, out_dir: Path, report=lambda p, m: None) -> dict:
    """Rigge un mesh ; rend les chemins produits et le squelette."""
    backend = config.backend("unirig")
    out_dir.mkdir(parents=True, exist_ok=True)
    if backend == "stub":
        report(0.3, "factice · squelette SOMA posé sur le mannequin")
        sk, weights, mesh = _stub_skeleton_and_weights(mesh_glb)
    else:
        from . import rig_unirig

        sk, weights, mesh = rig_unirig.run(mesh_glb=mesh_glb, workdir=out_dir / ".unirig", report=report)

    angle = arm_angle(sk)
    if angle > TPOSE_LIMIT_DEG:
        raise RigRefused(f"les bras du mesh font {angle:.0f}° avec la verticale : c'est une T-pose. "
                         f"Le bind se fait en A-pose, vers 45° (§8.1) — régénère les vues en A-pose.")

    report(0.7, "écriture du rig et des poses de contrôle")
    glb = _write(sk, weights, mesh, out_dir / "rigged.glb")
    sk.save(out_dir / "skeleton.json")
    np.savez(out_dir / "bind_delta.npz", joint_names=np.array(sk.names), bind_quats=sk.bind,
             tpose_offsets=sk.offsets)
    meta = {"backend": backend, "skeleton": "soma77", "bind_pose": "apose", "arm_angle_deg": round(angle, 1),
            "joints": sk.count, "control_poses": [f"control/{n}" for n in CONTROL_POSES],
            "hip_height_m": round(sk.hip_height(), 4)}
    (out_dir / "rig.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"glb": glb, "skeleton": out_dir / "skeleton.json", "bind_delta": out_dir / "bind_delta.npz",
            "meta": meta, "backend": backend}


# ── écriture ───────────────────────────────────────────────────────

def _write(sk: Skeleton, weights: dict, mesh: dict, dest: Path) -> Path:
    """Le GLB du rig : le mesh et ses matières repris tels quels, le
    squelette en A-pose, les poids, un clip par pose de contrôle."""
    g = mesh["glb"]
    joints_nodes, root = skeleton.add_skeleton(g, sk)
    ibm = np.linalg.inv(sk.bind_matrices())
    skin = g.skin(joints_nodes, ibm, skeleton=root, name="soma77")
    for prim_ref in mesh["primitives"]:
        mi, pi = prim_ref["mesh"], prim_ref["primitive"]
        prim = g.doc["meshes"][mi]["primitives"][pi]
        idx, w = weights[(mi, pi)]
        prim["attributes"]["JOINTS_0"] = g.accessor(idx, gltf.USHORT, target=gltf.ARRAY_BUFFER)
        prim["attributes"]["WEIGHTS_0"] = g.accessor(w, target=gltf.ARRAY_BUFFER)
    for node in mesh["nodes"]:
        g.doc["nodes"][node]["skin"] = skin
    for name in CONTROL_POSES:
        q, r = skeleton.control_pose(sk, name)
        # Deux clés : le bind, puis la pose, une seconde plus tard.
        quats = np.stack([sk.bind, q])
        roots = np.stack([sk.offsets[0], r])
        skeleton.add_clip(g, sk, f"control/{name}", quats, roots, fps=1.0)
    g.doc.setdefault("asset", {})["extras"] = {
        "skeleton": "soma77", "bind_pose": "apose",
        "control_poses": {f"control/{k}": v for k, v in CONTROL_LABELS.items()},
    }
    return g.save(dest)


# ── factice ────────────────────────────────────────────────────────

def _mesh_parts(mesh_glb: Path) -> dict:
    """Les primitives du mesh, leurs positions, et les nœuds qui les portent."""
    g = gltf.GLB.load(mesh_glb)
    prims, nodes = [], []
    for ni, node in enumerate(g.doc.get("nodes", [])):
        if "mesh" in node:
            nodes.append(ni)
    for mi, mesh in enumerate(g.doc.get("meshes", [])):
        for pi, prim in enumerate(mesh["primitives"]):
            prims.append({"mesh": mi, "primitive": pi,
                          "positions": g.read_accessor(prim["attributes"]["POSITION"]).astype(np.float64)})
    return {"glb": g, "primitives": prims, "nodes": nodes}


def skin_by_proximity(sk: Skeleton, positions: np.ndarray, k: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Poids par distance aux os de l'A-pose — assez pour le factice et
    pour vérifier la chaîne ; UniRig prédit les vrais."""
    _, pos = sk.fk(sk.bind)
    segs = []
    for j, p in enumerate(sk.parents):
        if p >= 0:
            segs.append((p, pos[p], pos[j]))
    d = np.zeros((len(positions), len(segs)))
    owners = np.zeros(len(segs), dtype=int)
    for s, (owner, a, b) in enumerate(segs):
        ab = b - a
        t = np.clip(((positions - a) @ ab) / max(ab @ ab, 1e-9), 0, 1)
        closest = a + t[:, None] * ab
        d[:, s] = np.linalg.norm(positions - closest, axis=1)
        owners[s] = owner
    order = np.argsort(d, axis=1)[:, :k]
    dist = np.take_along_axis(d, order, axis=1)
    w = 1.0 / np.maximum(dist, 1e-4) ** 4
    idx = owners[order]
    # Deux segments peuvent appartenir au même os : on fusionne.
    out_idx = np.zeros((len(positions), k), dtype=np.uint16)
    out_w = np.zeros((len(positions), k), dtype=np.float32)
    for v in range(len(positions)):
        acc: dict[int, float] = {}
        for i, ww in zip(idx[v], w[v]):
            acc[int(i)] = acc.get(int(i), 0.0) + float(ww)
        items = sorted(acc.items(), key=lambda x: -x[1])[:k]
        total = sum(x[1] for x in items)
        for n, (i, ww) in enumerate(items):
            out_idx[v, n] = i
            out_w[v, n] = ww / total
    return out_idx, out_w


def _stub_skeleton_and_weights(mesh_glb: Path):
    """Le mannequin factice est bâti sur le squelette SOMA en A-pose : ses
    articulations sont donc connues exactement, sans prédiction."""
    spec = skeleton.soma_spec()
    sk = skeleton.soma_rig_from_apose(skeleton.soma_apose(spec), spec)
    mesh = _mesh_parts(mesh_glb)
    weights = {(pr["mesh"], pr["primitive"]): skin_by_proximity(sk, pr["positions"]) for pr in mesh["primitives"]}
    return sk, weights, mesh
