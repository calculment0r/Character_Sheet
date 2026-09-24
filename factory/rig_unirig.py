"""UniRig vers SOMA 77, sur un DGX, par ssh (`remote.py`).

UniRig prédit un squelette et des poids de peau sur le mesh. Le travail
côté machine est dans `tools/remote/unirig_entry.py` (sans Blender) : il
rend, dans le repère et l'unité du mesh qu'on lui donne, les parents et
positions des os et, par sommet d'entrée, les os et poids.

Le brief (§10.3) prévoyait une table de noms UniRig → SOMA établie une
fois. Le modèle publié (classe `articulationxl`) nomme ses os `bone_0`,
`bone_1`… dans l'ordre où il les génère, et leur nombre varie d'un mesh
à l'autre : les noms ne portent rien. La correspondance se fait donc sur
la topologie et la géométrie, par des règles fixes, établies une fois :

  - le bassin est la première fourche à trois branches ou plus : la
    branche qui monte le plus haut est la colonne, les deux qui
    descendent le plus bas sont les jambes ;
  - en haut de la colonne, la fourche du thorax : la branche la plus
    haute est le cou et la tête, les deux plus écartées sont les bras ;
  - le long d'une chaîne, le genou et le coude sont les articulations
    les plus proches de mi-chemin ; la main est la fourche des doigts
    ou le bout du bras ; la cheville est l'avant-dernière de la jambe ;
  - l'avant se lit aux pieds (les orteils pointent devant) : si le mesh
    regarde ailleurs que +Z, mesh et squelette tournent d'un quart de
    tour entier autour de Y pour s'y remettre ; la gauche du personnage
    est alors +X.

Un os UniRig sans équivalent — doigts compris — verse ses poids sur son
ancêtre mappé le plus proche. Une articulation SOMA sans équivalent est
posée sur les proportions SOMA : le gabarit SOMA en A-pose, mis à
l'échelle sur les articulations reconnues, reporté depuis son ancêtre
reconnu le plus proche avec l'orientation réelle de l'os qui y mène. Elle
n'a pas de poids.

`rig.py` fait le reste : refus de la T-pose, bind en A-pose, écriture.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from . import config, gltf, remote, rig, skeleton
from .project import ChainError

ENTRY = config.REPO / "tools" / "remote" / "unirig_entry.py"
MIN_MATCHED = 14


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
    parents = pred["parents"].astype(int)
    positions = pred["joint_positions"].astype(np.float64)
    sj, sw = pred["skin_joints"].astype(int), pred["skin_weights"].astype(np.float64)
    if sj.shape[0] != base:
        raise ChainError(f"UniRig rend des poids pour {sj.shape[0]} sommets, le mesh en a {base}")

    spec = skeleton.soma_spec()
    table, yaw = match_soma(parents, positions)
    if yaw:
        turn = _yaw_matrix(yaw)
        positions = positions @ turn.T
        _turn_mesh(mesh, turn)
        report(0.75, f"le mesh regardait à {math.degrees(-yaw):.0f}° de +Z : remis face à +Z")
    soma_pos = soma_positions(spec, positions, table)
    owner = unirig_owner(parents, table, spec)
    weights = {(mi, pi): to_soma_weights(sj[a:b], sw[a:b], owner) for mi, pi, a, b in spans}
    report(0.8, f"UniRig : {len(parents)} os, {len(table)} reconnus et reportés sur SOMA 77")
    return skeleton.soma_rig_from_apose(soma_pos, spec), weights, mesh


# ── reconnaissance du squelette ────────────────────────────────────

def _children(parents: np.ndarray) -> list[list[int]]:
    ch: list[list[int]] = [[] for _ in parents]
    for j, p in enumerate(parents):
        if p >= 0:
            ch[p].append(j)
    return ch


def _run(j: int, ch: list[list[int]]) -> list[int]:
    """La chaîne sans fourche qui part de j ; son dernier élément est une
    fourche (plusieurs enfants) ou un bout."""
    out = [j]
    while len(ch[out[-1]]) == 1:
        out.append(ch[out[-1]][0])
    return out


def _yaw_matrix(yaw: float) -> np.ndarray:
    c, s = math.cos(yaw), math.sin(yaw)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def _nearest(js: list[int], score) -> int | None:
    return min(js, key=score) if js else None


def _mid(chain: list[int], a: int, b: int, pos: np.ndarray) -> int | None:
    """L'articulation strictement entre a et b la plus proche de mi-longueur."""
    i, k = chain.index(a), chain.index(b)
    inner = chain[i + 1:k]
    if not inner:
        return None
    pts = pos[chain[i:k + 1]]
    arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    half = arc[-1] / 2
    return _nearest(inner, lambda j: abs(arc[chain.index(j) - i] - half))


def match_soma(parents: np.ndarray, pos: np.ndarray) -> tuple[dict[int, str], float]:
    """Rend {indice UniRig : nom SOMA} et le quart de tour (radians,
    autour de Y) qui remet le personnage face à +Z."""
    ch = _children(parents)
    roots = np.where(parents < 0)[0]
    if len(roots) != 1:
        raise ChainError(f"UniRig : {len(roots)} racines, on en attend une")
    pelvis = next((j for j in _run(int(roots[0]), ch) if len(ch[j]) >= 3), None)
    if pelvis is None:
        raise ChainError("UniRig : pas de bassin (aucune fourche à trois branches : colonne et deux jambes)")
    branches = [_run(c, ch) for c in ch[pelvis]]
    spine = max(branches, key=lambda c: pos[c[-1], 1])
    legs = sorted((c for c in branches if c is not spine), key=lambda c: pos[c[-1], 1])[:2]
    if len(legs) < 2 or min(pos[legs[0][-1], 1], pos[legs[1][-1], 1]) > pos[pelvis, 1]:
        raise ChainError("UniRig : les deux jambes ne descendent pas sous le bassin")

    def foot(leg: list[int]) -> tuple[int, int | None, np.ndarray | None]:
        """La cheville, l'articulation des orteils et leur point. La
        cheville ouvre le premier segment plus horizontal que vertical :
        le pied, entre la jambe qui descend et les orteils devant."""
        for a, b in zip(leg, leg[1:]):
            d = pos[b] - pos[a]
            if abs(d[1]) < math.hypot(d[0], d[2]):
                return a, b, pos[b]
        end = leg[-1]
        if ch[end]:                                   # fourche au pied : les orteils sont ses enfants
            return end, None, pos[ch[end]].mean(axis=0)
        return end, None, None

    fwd = np.zeros(3)
    for leg in legs:
        ankle, _, toe = foot(leg)
        if toe is None:
            continue
        d = toe - pos[ankle]
        d[1] = 0.0
        if np.linalg.norm(d) > 1e-6:
            fwd += d / np.linalg.norm(d)
    yaw = 0.0
    if np.linalg.norm(fwd) > 1e-6:
        # Un quart de tour entier : le mesh sort d'un moteur 3D aligné sur ses axes.
        yaw = -round(math.atan2(fwd[0], fwd[2]) / (math.pi / 2)) * (math.pi / 2)
    p = pos @ _yaw_matrix(yaw).T

    t: dict[int, str] = {pelvis: "Hips"}
    left_leg, right_leg = sorted(legs, key=lambda c: -p[c, 0].mean())
    for side, leg in (("Left", left_leg), ("Right", right_leg)):
        ankle, toe, _ = foot(leg)
        t[leg[0]] = f"{side}Leg"
        t[ankle] = f"{side}Foot"
        if toe is not None:
            t[toe] = f"{side}ToeBase"
        if (knee := _mid(leg, leg[0], ankle, p)) is not None:
            t[knee] = f"{side}Shin"

    chest = spine[-1]
    if len(ch[chest]) < 3:
        raise ChainError("UniRig : pas de thorax (fourche cou et deux bras) en haut de la colonne")
    t[chest] = "Chest"
    inner = spine[:-1]
    y0, y1 = p[pelvis, 1], p[chest, 1]
    s1 = _nearest(inner, lambda j: abs(p[j, 1] - (y0 + (y1 - y0) / 3)))
    if s1 is not None:
        t[s1] = "Spine1"
        s2 = _nearest([j for j in inner if j != s1 and p[j, 1] > p[s1, 1]],
                      lambda j: abs(p[j, 1] - (y0 + 2 * (y1 - y0) / 3)))
        if s2 is not None:
            t[s2] = "Spine2"

    tops = [_run(c, ch) for c in ch[chest]]
    neck = max(tops, key=lambda c: p[c[-1], 1])
    arms = sorted((c for c in tops if c is not neck), key=lambda c: p[c[-1], 0])
    head_chain = neck
    if ch[head_chain[-1]]:                            # fourche à la tête (mâchoire, yeux) : la tête est la fourche
        top = max(ch[head_chain[-1]], key=lambda j: p[j, 1])
        t[head_chain[-1]] = "Head"
        if p[top, 1] > p[head_chain[-1], 1]:
            t[top] = "HeadEnd"
        if len(head_chain) > 1:
            t[head_chain[0]] = "Neck1"
    elif len(head_chain) >= 3:
        t[head_chain[0]], t[head_chain[-2]], t[head_chain[-1]] = "Neck1", "Head", "HeadEnd"
    elif len(head_chain) == 2:
        t[head_chain[0]], t[head_chain[1]] = "Neck1", "Head"
    else:
        t[head_chain[0]] = "Head"

    for side, arm in (("Right", arms[0]), ("Left", arms[-1])):
        hand = arm[-1]
        first = arm[0]
        if len(arm) >= 4:
            t[first] = f"{side}Shoulder"
            first = arm[1]
        t[first] = f"{side}Arm"
        t[hand] = f"{side}Hand"
        if (elbow := _mid(arm, first, hand, p)) is not None:
            t[elbow] = f"{side}ForeArm"

    if len(t) < MIN_MATCHED:
        raise ChainError(f"UniRig : seulement {len(t)} articulations reconnues sur {len(parents)}")
    return t, yaw


def _turn_mesh(mesh: dict, turn: np.ndarray) -> None:
    """Tourne les sommets (et normales) du GLB qu'on va rigger."""
    g = mesh["glb"]
    for pr in mesh["primitives"]:
        prim = g.doc["meshes"][pr["mesh"]]["primitives"][pr["primitive"]]
        pr["positions"] = pr["positions"] @ turn.T
        prim["attributes"]["POSITION"] = g.accessor(pr["positions"].astype(np.float32), target=gltf.ARRAY_BUFFER,
                                                    minmax=True)
        if "NORMAL" in prim["attributes"]:
            n = g.read_accessor(prim["attributes"]["NORMAL"]).astype(np.float64) @ turn.T
            prim["attributes"]["NORMAL"] = g.accessor(n.astype(np.float32), target=gltf.ARRAY_BUFFER)


# ── report sur SOMA 77 ─────────────────────────────────────────────

def soma_positions(spec: dict, positions: np.ndarray, table: dict[int, str]) -> np.ndarray:
    soma_names, soma_parents = spec["names"], spec["parents"]
    tmpl = skeleton.soma_apose(spec)
    known = {soma_names.index(s): positions[u] for u, s in table.items()}
    idx = np.array(sorted(known))
    got = np.array([known[i] for i in idx])
    # Échelle : la dispersion des articulations reconnues contre celle du gabarit.
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
            raise ChainError(f"SOMA {soma_names[j]} : aucun ancêtre reconnu dans le squelette UniRig")
        pa = soma_parents[a]
        while pa >= 0 and pa not in known:
            pa = soma_parents[pa]
        turn = (gltf.matrix_from_quat(skeleton.rotation_between(tmpl[a] - tmpl[pa], known[a] - known[pa]))
                if pa >= 0 else np.eye(3))
        out[j] = known[a] + scale * (turn @ (tmpl[j] - tmpl[a]))
    return out


def unirig_owner(parents: np.ndarray, table: dict[int, str], spec: dict) -> np.ndarray:
    """Pour chaque os UniRig, l'articulation SOMA qui reçoit ses poids :
    la sienne, celle de son ancêtre reconnu le plus proche, ou le bassin
    pour ce qui pend au-dessus de lui (une racine posée au sol)."""
    soma_names = spec["names"]
    owner = np.zeros(len(parents), dtype=int)
    for i in range(len(parents)):
        a = i
        while a >= 0 and a not in table:
            a = int(parents[a])
        owner[i] = soma_names.index(table[a] if a >= 0 else "Hips")
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
