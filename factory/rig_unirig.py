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


def run(*, mesh_glb: Path, workdir: Path, front: Path | None = None, report=lambda p, m: None):
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
    marks: dict[str, np.ndarray] = {}
    if front is not None and Path(front).exists():
        # Les articulations se posent sur l'anatomie du personnage lui-même,
        # relevée sur la vue de face dont le mesh est tiré : les règles de
        # topologie seules mettaient la main au bout des doigts et le coude
        # au tiers du bras d'un personnage stylisé (Costaud, 28/09).
        verts = np.concatenate([pr["positions"] for pr in mesh["primitives"]]).astype(np.float64)
        marks = anatomy(Path(front), verts, workdir=workdir, report=report)
        if len(marks) >= MIN_MATCHED:
            soma_pos = soma_positions_from(spec, marks, soma_pos)
            owner = nearest_owner(parents, positions, soma_pos, spec)
        else:
            report(0.78, f"vue de face : {len(marks)} repères seulement, squelette UniRig gardé")
            marks = {}
    weights = {(mi, pi): to_soma_weights(sj[a:b], sw[a:b], owner) for mi, pi, a, b in spans}
    report(0.8, f"UniRig : {len(parents)} os, {len(table)} reconnus"
                + (f" ; {len(marks)} articulations posées sur l'anatomie de la vue de face" if marks else "")
                + " — reportés sur SOMA 77")
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


# ── l'anatomie de la vue de face ───────────────────────────────────

# OpenPose 18 (DWPose) et main à 21 points : poignet 0 ; pouce 1-4 ;
# index 5-8, majeur 9-12, annulaire 13-16, auriculaire 17-20.
BODY = {"LeftArm": 5, "LeftForeArm": 6, "LeftHand": 7, "RightArm": 2, "RightForeArm": 3, "RightHand": 4,
        "LeftLeg": 11, "LeftShin": 12, "LeftFoot": 13, "RightLeg": 8, "RightShin": 9, "RightFoot": 10,
        "Neck1": 1, "LeftEye": 15, "RightEye": 14}
FINGERS = {"Thumb": (1, 2, 3, 4), "Index": (5, 6, 7, 8), "Middle": (9, 10, 11, 12), "Ring": (13, 14, 15, 16),
           "Pinky": (17, 18, 19, 20)}
MIN_SCORE = 0.3


def _silhouette_box(img: np.ndarray) -> tuple[float, float, float, float]:
    if img.ndim == 3 and img.shape[2] == 4:
        m = img[:, :, 3] > 16
    else:
        m = np.abs(img[:, :, :3].astype(int) - img[0, 0, :3].astype(int)).max(2) > 24
    ys, xs = np.where(m)
    if len(xs) < 100:
        raise ChainError("vue de face : silhouette introuvable")
    return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())


def anatomy(front: Path, verts: np.ndarray, *, workdir: Path, report=lambda p, m: None) -> dict[str, np.ndarray]:
    """Les articulations SOMA lues sur la vue de face (DWPose), reportées
    dans le repère du mesh. La vue préparée et le mesh ont la même
    silhouette (même rapport largeur/hauteur, pieds au sol ; Costaud,
    28/09 : 0,82 et 0,82) : un point de l'image passe au mesh par
    l'échelle des hauteurs ; sa profondeur est le milieu du volume du mesh
    à cet endroit. Les orteils se lisent sur le pied du mesh, le sommet du
    crâne en haut du mesh au-dessus du nez."""
    from PIL import Image

    from . import apose_pick

    entry = apose_pick.measure([front], workdir=workdir / "anatomy", report=report)[str(front)]
    body = entry.get("body") or []
    if len(body) < 14:
        return {}
    img = np.asarray(Image.open(front))
    x0, y0, x1, y1 = _silhouette_box(img)
    lo, hi = verts.min(0), verts.max(0)
    height = float(hi[1] - lo[1])
    scale = height / max(1.0, y1 - y0)
    cx_img, cx_mesh = (x0 + x1) / 2, (lo[0] + hi[0]) / 2

    def xy(q) -> np.ndarray:
        return np.array([cx_mesh + (q[0] - cx_img) * scale, lo[1] + (y1 - q[1]) * scale])

    def at(q, radius: float = 0.025) -> np.ndarray:
        """Le point de l'image, au milieu du volume du mesh derrière lui."""
        c = xy(q)
        d = np.linalg.norm(verts[:, :2] - c, axis=1)
        near = verts[d < radius * height]
        if len(near) < 6:
            near = verts[np.argsort(d)[:30]]
        return np.array([c[0], c[1], (near[:, 2].min() + near[:, 2].max()) / 2])

    def ok(q) -> bool:
        return q is not None and len(q) > 2 and q[2] >= MIN_SCORE

    out: dict[str, np.ndarray] = {}
    for name, i in BODY.items():
        if i < len(body) and ok(body[i]):
            out[name] = at(body[i], 0.018 if "Eye" in name else 0.025)
    for name in ("LeftEye", "RightEye"):               # les yeux : dans la tête, juste derrière la surface
        if name in out:
            near = verts[np.linalg.norm(verts[:, :2] - out[name][:2], axis=1) < 0.02 * height]
            if len(near):
                out[name][2] = near[:, 2].max() - 0.012 * height
    if ok(body[8]) and ok(body[11]):
        out["Hips"] = (at(body[8]) + at(body[11])) / 2
    if ok(body[0]):
        nose = xy(body[0])
        col = verts[np.abs(verts[:, 0] - nose[0]) < 0.04 * height]
        if len(col):
            top = float(col[:, 1].max())
            out["HeadEnd"] = at([body[0][0], y1 - (top - lo[1]) / scale + 0.02 * (y1 - y0), 1.0], 0.03)
    # les doigts : chaque main relevée va au poignet le plus proche
    wrists = {s: out[f"{s}Hand"] for s in ("Left", "Right") if f"{s}Hand" in out}
    for key in ("left_hand", "right_hand"):
        hand = entry.get(key) or []
        if len(hand) < 21 or not ok(hand[0]) or not wrists or sum(ok(q) for q in hand) < 15:
            continue
        w = xy(hand[0])
        side = min(wrists, key=lambda s_: float(np.linalg.norm(wrists[s_][:2] - w)))
        for finger, ks in FINGERS.items():
            pts = [hand[k] for k in ks]
            if not all(ok(q) for q in pts):
                continue
            names = [f"{side}Hand{finger}{n}" for n in (("1", "2", "3", "End") if finger == "Thumb"
                                                         else ("2", "3", "4", "End"))]
            for n, q in zip(names, pts):
                out[n] = at(q, 0.012)
            if finger != "Thumb":                     # le métacarpe, entre le poignet et la jointure
                out[f"{side}Hand{finger}1"] = wrists[side] + 0.35 * (out[names[0]] - wrists[side])
    # les orteils : l'avant du pied du mesh, au sol
    for side in ("Left", "Right"):
        ankle = out.get(f"{side}Foot")
        if ankle is None:
            continue
        foot = verts[(verts[:, 1] < ankle[1]) & (np.abs(verts[:, 0] - ankle[0]) < 0.06 * height)]
        if len(foot) < 10:
            continue
        tip = foot[np.argmax(foot[:, 2])]
        ground = lo[1] + 0.015 * height
        out[f"{side}ToeEnd"] = np.array([tip[0], ground, tip[2]])
        base = ankle + 0.7 * (tip - ankle)
        out[f"{side}ToeBase"] = np.array([base[0], ground, base[2]])
    report(0.78, f"vue de face : {len(out)} articulations relevées")
    return out


def soma_positions_from(spec: dict, marks: dict[str, np.ndarray], fallback: np.ndarray) -> np.ndarray:
    """Les 77 articulations : celles relevées sur l'anatomie ; la colonne,
    le cou et la tête le long de l'axe du corps aux proportions du gabarit
    (entre bassin, cou et sommet du crâne relevés) ; les clavicules entre
    le cou et l'épaule ; le reste depuis son ancêtre relevé, gabarit tourné
    sur l'os réel et mis à l'échelle de cet os — celle du personnage, pas
    une moyenne humaine."""
    names, parents = spec["names"], spec["parents"]
    tmpl = skeleton.soma_apose(spec)
    ix = names.index
    known = {ix(n): np.asarray(v, float) for n, v in marks.items() if n in names}

    def along(a: str, b: str, members: tuple[str, ...]) -> None:
        if ix(a) not in known or ix(b) not in known:
            return
        pa, pb, ta, tb = known[ix(a)], known[ix(b)], tmpl[ix(a)], tmpl[ix(b)]
        span = tb[1] - ta[1]
        for m in members:
            f = (tmpl[ix(m)][1] - ta[1]) / span if abs(span) > 1e-6 else 0.5
            known[ix(m)] = pa + f * (pb - pa)

    along("Hips", "Neck1", ("Spine1", "Spine2", "Chest"))
    along("Neck1", "HeadEnd", ("Neck2", "Head"))
    for side in ("Left", "Right"):
        n, sh = ix("Neck1"), ix(f"{side}Arm")
        if n in known and sh in known:
            ts, tn, tsh = tmpl[ix(f"{side}Shoulder")], tmpl[n], tmpl[sh]
            f = float(np.dot(ts - tn, tsh - tn) / max(1e-9, float(np.dot(tsh - tn, tsh - tn))))
            known[ix(f"{side}Shoulder")] = known[n] + f * (known[sh] - known[n])
    out = np.zeros_like(tmpl)
    for j in range(len(names)):
        if j in known:
            out[j] = known[j]
            continue
        a = parents[j]
        while a >= 0 and a not in known:
            a = parents[a]
        if a < 0:
            out[j] = fallback[j]
            continue
        pa = parents[a]
        while pa >= 0 and pa not in known:
            pa = parents[pa]
        if pa < 0:
            out[j] = known[a] + (tmpl[j] - tmpl[a])
            continue
        real, model = known[a] - known[pa], tmpl[a] - tmpl[pa]
        local = float(np.linalg.norm(real) / max(1e-9, float(np.linalg.norm(model))))
        turn = gltf.matrix_from_quat(skeleton.rotation_between(model, real))
        out[j] = known[a] + local * (turn @ (tmpl[j] - tmpl[a]))
    return out


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    t = float(np.clip(np.dot(p - a, ab) / max(1e-12, float(np.dot(ab, ab))), 0.0, 1.0))
    return float(np.linalg.norm(p - (a + t * ab)))


def nearest_owner(parents: np.ndarray, positions: np.ndarray, soma_pos: np.ndarray, spec: dict) -> np.ndarray:
    """Pour chaque os UniRig, l'os SOMA le plus proche : son segment
    (articulation → milieu de ses enfants) contre ceux de SOMA. Les poids
    d'UniRig suivent ainsi la chair qu'ils couvrent, quelle que soit la
    manière dont UniRig a découpé ses chaînes. Les bouts (…End) ne portent
    pas de poids."""
    names, sp = spec["names"], spec["parents"]
    ch = _children(parents)
    sch: list[list[int]] = [[] for _ in names]
    for j, p in enumerate(sp):
        if p >= 0:
            sch[p].append(j)
    segs = [(j, soma_pos[j], soma_pos[sch[j]].mean(axis=0) if sch[j] else soma_pos[j])
            for j, n in enumerate(names) if not n.endswith("End")]
    owner = np.zeros(len(parents), dtype=int)
    for i in range(len(parents)):
        tip = positions[ch[i]].mean(axis=0) if ch[i] else positions[i]
        mid = (positions[i] + tip) / 2
        owner[i] = min(segs, key=lambda s_: _seg_dist(mid, s_[1], s_[2]))[0]
    return owner


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


def top_k_continuous(dense: np.ndarray, k: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Les k influences les plus fortes, sans saut d'un sommet à l'autre.

    Couper net au k-ième fait sauter les poids là où deux os échangent
    leur rang : sur le premier vrai mesh, le haut du torse (bassin,
    colonne, thorax, épaule, bras) se piquait de pointes dès qu'un bras
    bougeait. On retire aux k premiers le poids du (k+1)-ième : un os qui
    sort de la liste y arrive à zéro, et le reste se renormalise."""
    order = np.argsort(-dense, axis=1)
    top = order[:, :k]
    w = np.take_along_axis(dense, top, axis=1)
    if dense.shape[1] > k:
        w = np.maximum(w - np.take_along_axis(dense, order[:, k:k + 1], axis=1), 0.0)
    s = w.sum(axis=1, keepdims=True)
    w = np.where(s > 1e-9, w / np.maximum(s, 1e-9), np.eye(1, k))   # à égalité parfaite : le premier
    return top, w


def to_soma_weights(joints: np.ndarray, weights: np.ndarray, owner: np.ndarray, k: int = 4):
    """Poids UniRig (V, n) → SOMA (V, 4) : on somme par articulation SOMA,
    on garde les quatre plus fortes (`top_k_continuous`)."""
    v = joints.shape[0]
    dense = np.zeros((v, int(owner.max()) + 1))
    np.add.at(dense, (np.repeat(np.arange(v), joints.shape[1]), owner[joints].ravel()), weights.ravel())
    top, w = top_k_continuous(dense, k)
    return top.astype(np.uint16), w.astype(np.float32)
