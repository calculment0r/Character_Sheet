"""UniRig vers SOMA 77, sur un DGX, par ssh (`remote.py`).

UniRig prédit un squelette et des poids de peau sur le mesh. Le travail
côté machine est dans `tools/remote/unirig_entry.py` : il rend, dans le
repère et l'unité du mesh qu'on lui donne, les noms, parents et
positions des os d'UniRig et, par sommet d'entrée, les os et poids.

Ici, le passage à SOMA 77 (brief §10.3) :

  - une table de noms UniRig → SOMA, établie une fois et versionnée dans
    `data/unirig_to_soma.json` — jamais recalculée par personnage ;
  - un os UniRig sans équivalent verse ses poids sur son ancêtre mappé le
    plus proche ;
  - une articulation SOMA sans équivalent (doigts, mâchoire, yeux,
    bouts) est posée sur les proportions SOMA : le gabarit SOMA en A-pose,
    mis à l'échelle sur les articulations prédites, reporté depuis son
    ancêtre prédit le plus proche avec l'orientation réelle de l'os qui
    y mène. Elle n'a pas de poids.

`rig.py` fait le reste : refus de la T-pose, bind en A-pose, écriture.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import config, gltf, remote, rig, skeleton
from .project import ChainError

ENTRY = config.REPO / "tools" / "remote" / "unirig_entry.py"
MAPPING = config.REPO / "data" / "unirig_to_soma.json"


def run(*, mesh_glb: Path, workdir: Path, report=lambda p, m: None):
    mesh = rig._mesh_parts(mesh_glb)
    g = mesh["glb"]
    verts, faces, spans = [], [], []
    base = 0
    for pr in mesh["primitives"]:
        prim = g.doc["meshes"][pr["mesh"]]["primitives"][pr["primitive"]]
        v = pr["positions"]
        f = (g.read_accessor(prim["indices"]).reshape(-1, 3) if "indices" in prim
             else np.arange(len(v)).reshape(-1, 3))
        verts.append(v)
        faces.append(f + base)
        spans.append((pr["mesh"], pr["primitive"], base, base + len(v)))
        base += len(v)
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / "mesh.npz"
    np.savez(src, vertices=np.concatenate(verts).astype(np.float32), faces=np.concatenate(faces).astype(np.int32))

    got = remote.run("unirig", ENTRY, args=["--mesh", "{job}/mesh.npz", "--out", "{job}/rig.npz"],
                     inputs={"mesh.npz": src}, outputs=["rig.npz"], workdir=workdir, report=report)
    pred = np.load(got["rig.npz"], allow_pickle=False)
    names = [str(n) for n in pred["joint_names"]]
    parents = pred["parents"].astype(int)
    positions = pred["joint_positions"].astype(np.float64)
    sj, sw = pred["skin_joints"].astype(int), pred["skin_weights"].astype(np.float64)
    if sj.shape[0] != base:
        raise ChainError(f"UniRig rend des poids pour {sj.shape[0]} sommets, le mesh en a {base}")

    spec = skeleton.soma_spec()
    table = load_mapping(names)
    soma_pos = soma_positions(spec, names, positions, table)
    owner = unirig_owner(names, parents, table, spec)
    weights = {}
    for mi, pi, a, b in spans:
        weights[(mi, pi)] = to_soma_weights(sj[a:b], sw[a:b], owner)
    report(0.8, f"UniRig : {len(names)} os, {len(table)} reportés sur SOMA 77")
    return skeleton.soma_rig_from_apose(soma_pos, spec), weights, mesh


def load_mapping(names: list[str]) -> dict[str, str]:
    """La table UniRig → SOMA. Les os UniRig qu'elle ne nomme pas sont
    permis (ils versent leurs poids plus haut) ; une table vide ne l'est
    pas."""
    if not MAPPING.exists():
        raise ChainError(f"{MAPPING.relative_to(config.REPO)} manque : la table UniRig → SOMA s'écrit une fois, "
                         f"sur les noms qu'UniRig rend ({', '.join(names[:8])} …)")
    table = {k: v for k, v in json.loads(MAPPING.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    hit = {k: v for k, v in table.items() if k in names}
    if len(hit) < 10:
        raise ChainError(f"la table UniRig → SOMA ne reconnaît que {len(hit)} os sur {len(names)} rendus par UniRig")
    return hit


def soma_positions(spec: dict, names: list[str], positions: np.ndarray, table: dict[str, str]) -> np.ndarray:
    soma_names, soma_parents = spec["names"], spec["parents"]
    tmpl = skeleton.soma_apose(spec)
    known = {soma_names.index(s): positions[names.index(u)] for u, s in table.items()}
    idx = np.array(sorted(known))
    got = np.array([known[i] for i in idx])
    # Échelle : la dispersion des articulations prédites contre celle du gabarit.
    scale = float(np.linalg.norm(got - got.mean(0), axis=1).mean()
                  / max(1e-9, np.linalg.norm(tmpl[idx] - tmpl[idx].mean(0), axis=1).mean()))
    out = np.zeros_like(tmpl)
    for j in range(len(soma_names)):
        if j in known:
            out[j] = known[j]
            continue
        a = soma_parents[j]
        while a >= 0 and a not in known:
            a = soma_parents[a]
        if a < 0:
            raise ChainError(f"SOMA {soma_names[j]} : aucun ancêtre prédit par UniRig")
        pa = soma_parents[a]
        while pa >= 0 and pa not in known:
            pa = soma_parents[pa]
        turn = (gltf.matrix_from_quat(skeleton.rotation_between(tmpl[a] - tmpl[pa], known[a] - known[pa]))
                if pa >= 0 else np.eye(3))
        out[j] = known[a] + scale * (turn @ (tmpl[j] - tmpl[a]))
    return out


def unirig_owner(names: list[str], parents: np.ndarray, table: dict[str, str], spec: dict) -> np.ndarray:
    """Pour chaque os UniRig, l'articulation SOMA qui reçoit ses poids :
    la sienne, ou celle de son ancêtre mappé le plus proche."""
    soma_names = spec["names"]
    owner = np.zeros(len(names), dtype=int)
    for i in range(len(names)):
        a = i
        while a >= 0 and names[a] not in table:
            a = int(parents[a])
        if a < 0:
            raise ChainError(f"os UniRig {names[i]} : aucun ancêtre dans la table UniRig → SOMA")
        owner[i] = soma_names.index(table[names[a]])
    return owner


def to_soma_weights(joints: np.ndarray, weights: np.ndarray, owner: np.ndarray, k: int = 4):
    """Poids UniRig (V, n) → SOMA (V, 4) : on somme par articulation SOMA,
    on garde les quatre plus fortes, on renormalise."""
    v = joints.shape[0]
    dense = np.zeros((v, int(owner.max()) + 1))
    np.add.at(dense, (np.repeat(np.arange(v), joints.shape[1]), owner[joints].ravel()), weights.ravel())
    top = np.argsort(-dense, axis=1)[:, :k]
    w = np.take_along_axis(dense, top, axis=1)
    w /= np.maximum(w.sum(axis=1, keepdims=True), 1e-9)
    return top.astype(np.uint16), w.astype(np.float32)
