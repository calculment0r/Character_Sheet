"""Squelettes OpenPose de poses naturelles, sur les os d'un personnage.

À lancer sur DGX2 avec le python de ComfyUI (onnxruntime, cv2), comme
`apose_skeleton.py` dont il reprend la détection et le dessin :
  ~/comfyui-env/bin/python tools/remote/natural_skeleton.py <plein_pied.png> <dossier> [largeur hauteur] [--poses=a,b]

DWPose relève le plein pied validé. Les longueurs d'os (bras, avant-bras,
cuisse, jambe), le buste, la tête et les mains sont ceux du personnage ;
chaque pose de `data/natural_poses.json` ne donne que des angles et des
cibles : les membres se posent en 3D (directions, ou IK à deux os pour
une main sur la hanche, dans une poche, sur un bras), la tête tourne
comme un cylindre (voir `view()`), le corps tourne autour de la
verticale et se projette de face. Toutes les poses partagent une échelle
et une ligne de sol : la hauteur d'un pied au sommet du crâne reste
celle du personnage.

Écrit <dossier>/<pose>.png (le squelette), <pose>.json (les points),
natural.json (la détection brute, en pixels du plein pied, pour les
recadrages de détails) et skeletons_debug.png.
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apose_skeleton import detect, draw  # noqa: E402

POSES = Path(__file__).resolve().parents[2] / "data/natural_poses.json"
MARGIN = 0.05

# OpenPose 18 : 0 nez, 1 cou, 2-4 épaule/coude/poignet droits, 5-7 gauches,
# 8-10 hanche/genou/cheville droites, 11-13 gauches, 14-17 yeux et oreilles.
ARMS = {"R": (2, 3, 4), "L": (5, 6, 7)}
LEGS = {"R": (8, 9, 10), "L": (11, 12, 13)}
HEAD = (0, 14, 15, 16, 17)


def unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def turn(p, cx, deg):
    """Rotation autour de l'axe vertical x = cx, z = 0 ; même sens que les
    azimuts des vues : x' = x cos t - z sin t."""
    t = math.radians(deg)
    x, z = p[0] - cx, p[2]
    return np.array([cx + x * math.cos(t) - z * math.sin(t), p[1], x * math.sin(t) + z * math.cos(t)])


def turn_vec(v, deg):
    return turn(np.asarray(v, dtype=float), 0.0, deg)


def roll(p, c, deg):
    """Rotation dans le plan de l'image, horaire à l'écran (y vers le bas)."""
    a = math.radians(deg)
    dx, dy = p[0] - c[0], p[1] - c[1]
    out = np.array(p, dtype=float)
    out[0], out[1] = c[0] + math.cos(a) * dx - math.sin(a) * dy, c[1] + math.sin(a) * dx + math.cos(a) * dy
    return out


def between(a, b):
    """La rotation la plus courte qui porte la direction a sur b."""
    a, b = unit(a), unit(b)
    v, c = np.cross(a, b), float(a @ b)
    if np.linalg.norm(v) < 1e-8:
        return np.eye(3) if c > 0 else np.diag([-1.0, 1.0, -1.0])
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def ik(root, target, l1, l2, pole):
    """IK à deux os : le coude (ou le genou) vers `pole`, la main au plus
    près de la cible."""
    d = target - root
    dist = float(np.linalg.norm(d))
    u = d / dist
    dist = min(max(dist, abs(l1 - l2) + 1e-3), (l1 + l2) * 0.999)
    along = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    h = math.sqrt(max(l1 * l1 - along * along, 0.0))
    p = unit(pole)
    v = unit(p - (p @ u) * u)
    return root + along * u + h * v, root + dist * u


class Rest:
    """Le personnage tel que DWPose le lit, en 3D (z = 0) : ses os et ses
    repères."""

    def __init__(self, body, lhand, rhand, face):
        missing = [i for i in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13) if body[i] is None]
        if missing:
            sys.exit(f"DWPose : points manquants sur le plein pied {missing}")
        self.b = [None if p is None else np.array([p[0], p[1], 0.0]) for p in body]
        self.face = face
        # Chaque main à son poignet, au plus près : le nom gauche/droite
        # de DWPose ne se discute pas.
        hands = {"R": None, "L": None}
        for hand in (lhand, rhand):
            if not hand or hand[0] is None:
                continue
            side = min(("R", "L"), key=lambda s: np.linalg.norm(hand[0] - self.b[ARMS[s][2]][:2]))
            hands[side] = [None if p is None else np.array([p[0], p[1], 0.0]) for p in hand]
        self.hands = hands
        b = self.b
        mean = lambda pairs: float(np.mean([np.linalg.norm(b[i] - b[j]) for i, j in pairs]))  # noqa: E731
        self.upper, self.fore = mean([(2, 3), (5, 6)]), mean([(3, 4), (6, 7)])
        self.thigh, self.shin = mean([(8, 9), (11, 12)]), mean([(9, 10), (12, 13)])
        self.sw = float(np.linalg.norm(b[5] - b[2]))
        top = min(p[1] for p in (b[i] for i in HEAD) if p is not None)
        self.ground = max(b[10][1], b[13][1])
        self.height = self.ground - top
        ears = (b[16], b[17]) if b[16] is not None and b[17] is not None else None
        self.hx = (ears[0][0] + ears[1][0]) / 2 if ears else b[0][0]
        self.r = abs(ears[1][0] - ears[0][0]) / 2 if ears else self.sw * 0.28


def pose(rest: Rest, spec: dict):
    b = rest.b
    H, SW = rest.height, rest.sw
    rh, lh, neck = b[8], b[11], b[1]
    pelvis = (rh + lh) / 2
    cx = pelvis[0]
    yaw, twist = spec.get("yaw", 0.0), spec.get("torso_yaw", 0.0)

    hips = {"R": roll(rh, pelvis, spec.get("hip_drop", 0.0)), "L": roll(lh, pelvis, spec.get("hip_drop", 0.0))}
    neck2 = roll(neck, pelvis, spec.get("spine_lean", 0.0))
    moved = neck2 - neck
    shoulders = {s: turn(roll(b[ARMS[s][0]] + moved, neck2, spec.get("shoulder_drop", 0.0)), cx, twist)
                 for s in ("R", "L")}
    neck3 = turn(neck2, cx, twist)
    marks = {"hip_R": hips["R"], "hip_L": hips["L"], "shoulder_R": shoulders["R"], "shoulder_L": shoulders["L"],
             "neck": neck3, "pelvis": pelvis}

    out = [None] * 18
    out[1] = neck3
    hands = {}
    for s, (i0, i1, i2) in ARMS.items():
        arm = spec["arms"][s]
        S = shoulders[s]
        if "ik" in arm:
            base = marks[arm["ik"]["from"]]
            ox, oy, oz = arm["ik"]["offset"]
            elbow, wrist = ik(S, base + np.array([ox * SW, oy * H, oz * H]), rest.upper, rest.fore, arm["pole"])
        else:
            elbow = S + unit(turn_vec(arm["upper"], twist)) * rest.upper
            wrist = elbow + unit(turn_vec(arm["fore"], twist)) * rest.fore
        out[i0], out[i1], out[i2] = S, elbow, wrist
        hand = rest.hands.get(s)
        if arm.get("hand", "keep") == "keep" and hand:
            m = between(b[i2] - b[i1], wrist - elbow)
            hands[s] = [None if p is None else wrist + m @ (p - b[i2]) for p in hand]
        else:
            hands[s] = None
    for s, (i0, i1, i2) in LEGS.items():
        leg = spec["legs"][s]
        knee = hips[s] + unit(leg["thigh"]) * rest.thigh
        out[i0], out[i1], out[i2] = hips[s], knee, knee + unit(leg["shin"]) * rest.shin

    # Une caméra à hauteur des yeux, à 3,5 hauteurs de corps : ce qui
    # avance grandit et s'écarte de la ligne des yeux — le pied de devant
    # descend, celui de derrière remonte, comme sur une photo.
    eye = min(p[1] for p in (b[14], b[15], b[0]) if p is not None)
    dist = 3.5 * H

    def project(p):
        if p is None:
            return None
        q = turn(p, cx, yaw)
        k = dist / (dist - q[2])
        return np.array([cx + (q[0] - cx) * k, eye + (q[1] - eye) * k])

    body = [project(p) for p in out]
    hands = {s: None if h is None else [project(p) for p in h] for s, h in hands.items()}

    # La tête : un cylindre d'axe vertical (voir `view()` d'apose_skeleton),
    # qui suit le cou et tourne de yaw + torso_yaw + head_yaw.
    T = math.radians(yaw + twist + spec.get("head_yaw", 0.0))
    head_3d = turn(np.array([rest.hx, eye, 0.0]) + moved, cx, twist)
    center = project(head_3d)
    head_k = dist / (dist - turn(head_3d, cx, yaw)[2])
    neck_2d = body[1]
    r = rest.r

    def on_head(p, radius=1.0, limit=0.1, phi=None):
        if p is None:
            return None
        if phi is None:
            phi = math.asin(max(-0.98, min(0.98, (p[0] - rest.hx) / r)))
        if math.cos(phi - T) < limit:
            return None
        q = np.array([center[0] + r * head_k * radius * math.sin(phi - T), eye + (p[1] + moved[1] - eye) * head_k])
        return roll(q, neck_2d, spec.get("head_roll", 0.0))

    for i in HEAD:
        p = b[i]
        if i in (16, 17):
            body[i] = on_head(p, phi=math.radians(-90 if i == 16 else 90), limit=-0.15)
        else:
            body[i] = on_head(p, radius=1.3 if i == 0 else 1.05, limit=-0.1)
    face = None if rest.face is None else [on_head(p) for p in rest.face]
    return body, hands["L"], hands["R"], face


def layout(posed: dict, rest: Rest, w: int, h: int, iw: int, ih: int) -> dict:
    """Une échelle et une ligne de sol pour toutes les poses : celles du
    plein pied. Quand le cadre est celui du plein pied (1152 × 2048), le
    squelette tombe à la taille et sur le sol du personnage en <image1> —
    Qwen garde la taille de <image1> (essai du 27/09 : un squelette agrandi
    de 11 % donnait un personnage à la taille d'origine). L'échelle ne
    baisse que si une pose déborde du cadre."""
    scale = min(w / iw, h / ih)
    for parts in posed.values():
        xs = [p[0] for part in parts if part for p in part if p is not None]
        scale = min(scale, w * (1 - 2 * MARGIN) / (max(xs) - min(xs) + 0.08 * rest.height))
    out = {}
    for name, parts in posed.items():
        body = parts[0]
        xs = [p[0] for part in parts if part for p in part if p is not None]
        mid = (max(xs) + min(xs)) / 2
        ground = max(body[10][1], body[13][1])
        shift = np.array([w / 2, min(rest.ground * scale, h * (1 - MARGIN))])
        out[name] = [None if part is None else
                     [None if p is None else (p - np.array([mid, ground])) * scale + shift for p in part]
                     for part in parts]
    return out, scale


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    wanted = [v for a in sys.argv[1:] if a.startswith("--poses=") for v in a[8:].split(",") if v]
    src, folder = Path(args[0]), Path(args[1])
    w, h = (int(args[2]), int(args[3])) if len(args) > 3 else (1152, 2048)
    specs = {p["id"]: p for p in json.loads(POSES.read_text(encoding="utf-8"))["poses"]}
    unknown = [n for n in wanted if n not in specs]
    if unknown:
        sys.exit(f"poses inconnues : {unknown}")
    img, body, lhand, rhand, face = detect(src)
    rest = Rest(body, lhand, rhand, face)
    folder.mkdir(parents=True, exist_ok=True)
    ih, iw = img.shape[:2]
    lst = lambda part: None if part is None else [None if p is None else [float(p[0]), float(p[1])] for p in part]  # noqa: E731
    (folder / "natural.json").write_text(json.dumps({
        "width": iw, "height": ih, "body": lst(body), "hands": {s: lst(None if v is None else [None if p is None else p[:2] for p in v]) for s, v in rest.hands.items()},
        "face": lst(face), "height_px": rest.height, "shoulder_px": rest.sw}))
    # L'échelle se règle sur toutes les poses, même pour n'en refaire qu'une.
    posed = {n: pose(rest, spec) for n, spec in specs.items()}
    tiles = []
    placed, scale = layout(posed, rest, w, h, iw, ih)
    for name, (b, lh, rh, fc) in placed.items():
        if wanted and name not in wanted:
            continue
        canvas = draw(b, lh, rh, fc, w, h)
        cv2.imwrite(str(folder / f"{name}.png"), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
        # `scale` : pixels du cadre par pixel du plein pied — la planche en
        # tire l'échelle en px/m de chaque pose.
        (folder / f"{name}.json").write_text(json.dumps({"width": w, "height": h, "scale": scale, "body": lst(b)}))
        tiles.append(cv2.resize(canvas, (w // 4, h // 4)))
    cv2.imwrite(str(folder / "skeletons_debug.png"), cv2.cvtColor(np.hstack(tiles), cv2.COLOR_RGB2BGR))
    print("squelettes :", ", ".join(wanted or posed))


if __name__ == "__main__":
    main()
