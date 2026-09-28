"""Squelette OpenPose en A-pose, aux proportions d'un plein pied.

À lancer sur DGX2 avec le python de ComfyUI (onnxruntime, cv2) :
  ~/comfyui-env/bin/python tools/remote/apose_skeleton.py <plein_pied.png> <sortie.png> [largeur hauteur]

DWPose relève le corps, les mains et le visage du plein pied validé.
Les bras sont redressés à 45° de la verticale, les jambes légèrement
ouvertes, les longueurs d'os gardées ; les mains suivent l'avant-bras
en bloc. Le squelette est remis à l'échelle de la hauteur du cadre et
centré. Écrit aussi <sortie>.json (points en pixels) et <sortie>_debug.png.

Le cadrage suit la silhouette réelle, relevée sur l'image (sommet du
crâne, semelles) : un squelette ne fixe que la place des articulations,
pas la longueur des membres. Cadré sur le nez et les chevilles (les
chevilles au bas du cadre, 6 % de marge au-dessus du nez), il laissait
un personnage trop grand pour le cadre : le modèle le rétrécissait (tronc
−8 à −17 %, jambes −10 %) en gardant les mains sur les poignets du
squelette — des bras 10 à 20 % trop longs, relevés le 28/09 sur les
personnages MJ et sur David (essai-atelier).
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path.home() / "ComfyUI/custom_nodes/comfyui_controlnet_aux/src"))
from custom_controlnet_aux.dwpose import DwposeDetector, util  # noqa: E402

ARM_DEG = 45.0   # bras : angle avec la verticale
LEG_DEG = 4.0   # jambes : angle avec la verticale

# OpenPose 18 : 0 nez, 1 cou, 2-4 épaule/coude/poignet droits, 5-7 gauches,
# 8-10 hanche/genou/cheville droites, 11-13 gauches, 14-17 yeux et oreilles.
# « Droit » est la droite du personnage : à gauche de l'image.


def detect(path: Path):
    img = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
    det = DwposeDetector.from_pretrained("yzd-v/DWPose", "yzd-v/DWPose", det_filename="yolox_l.onnx",
                                         pose_filename="dw-ll_ucoco_384.onnx", torchscript_device="cuda")
    poses = det.detect_poses(img)
    if not poses:
        sys.exit("DWPose : personne trouvée")
    pose = max(poses, key=lambda p: p.body.total_score)

    def px(kps):   # detect_poses rend des pixels
        return None if kps is None else [None if k is None else np.array([k.x, k.y]) for k in kps]

    return img, px(pose.body.keypoints), px(pose.left_hand), px(pose.right_hand), px(pose.face)


def rotate(points, center, angle):
    c, s = math.cos(angle), math.sin(angle)
    m = np.array([[c, -s], [s, c]])
    return [None if p is None else center + m @ (p - center) for p in points]


def chain(body, idx, direction):
    """Remet la chaîne body[idx[0]] → idx[1] → idx[2] droite, le long de `direction`."""
    a, b, c = (body[i] for i in idx)
    if a is None or b is None or c is None:
        return None
    l1, l2 = np.linalg.norm(b - a), np.linalg.norm(c - b)
    old = math.atan2(*(c - b)[::-1])
    body[idx[1]] = a + direction * l1
    body[idx[2]] = body[idx[1]] + direction * l2
    new = math.atan2(*(body[idx[2]] - body[idx[1]])[::-1])
    return c, new - old   # ancien poignet (ou cheville) et rotation de l'avant-bras


def down(deg, side):
    return np.array([side * math.sin(math.radians(deg)), math.cos(math.radians(deg))])


def apose(body, lhand, rhand):
    """Ancienne remise en A-pose : bras et jambes seulement, le tronc et la
    tête gardés tels quels. Sert encore quand un point du tronc manque."""
    body = list(body)
    # droite du personnage à gauche de l'image : vers -x
    for idx, side, hand in (((2, 3, 4), -1, rhand), ((5, 6, 7), +1, lhand)):
        old_wrist = body[idx[2]]
        r = chain(body, idx, down(ARM_DEG, side))
        if r and hand:
            _, turn = r
            moved = [None if p is None else p - old_wrist + body[idx[2]] for p in hand]
            hand[:] = rotate(moved, body[idx[2]], turn)
    for idx, side in (((8, 9, 10), -1), ((11, 12, 13), +1)):
        chain(body, idx, down(LEG_DEG, side))
    return body


def _len(body, a, b):
    return None if body[a] is None or body[b] is None else float(np.linalg.norm(body[b] - body[a]))


def _longest(body, *pairs):
    """La plus grande longueur relevée d'un os pris des deux côtés : un
    membre vu en raccourci (tourné, avancé) paraît toujours plus court."""
    got = [x for x in (_len(body, a, b) for a, b in pairs) if x]
    return max(got) if got else None


# Visage à 68 points (ordre iBUG, celui de DWPose) : chaque point et son
# symétrique ; ceux de l'axe (arête du nez, milieu des lèvres) sont à eux-mêmes.
FACE_MIRROR = ([16 - i for i in range(17)] + [26, 25, 24, 23, 22, 21, 20, 19, 18, 17]
               + [27, 28, 29, 30] + [35, 34, 33, 32, 31]
               + [45, 44, 43, 42, 47, 46, 39, 38, 37, 36, 41, 40]
               + [54, 53, 52, 51, 50, 49, 48, 59, 58, 57, 56, 55]
               + [64, 63, 62, 61, 60, 67, 66, 65]
               + [69, 68])   # les pupilles, quand le relevé en a 70 (OpenPose)


def frontal_face(face):
    """Le visage redressé et de face : les yeux remis à l'horizontale
    autour du bout du nez, puis chaque point moyenné avec le miroir de son
    symétrique par rapport à l'arête du nez. Un visage tourné ou penché
    donne ainsi un visage droit, de la même taille, centré sur son nez."""
    if face is None or len(face) < 68 or any(face[i] is None for i in (30, 36, 39, 42, 45)):
        return None
    tip = face[30]
    reye, leye = (face[36] + face[39]) / 2, (face[42] + face[45]) / 2
    tilt = math.atan2(*(leye - reye)[::-1])
    upright = rotate(face, tip, -tilt)
    bridge = [upright[i][0] for i in (27, 28, 29, 30) if upright[i] is not None]
    mid = float(np.mean(bridge))
    out = []
    for i, p in enumerate(upright):
        j = FACE_MIRROR[i] if i < len(FACE_MIRROR) and FACE_MIRROR[i] < len(upright) else i
        q = upright[j]
        if p is None or q is None:
            out.append(None if p is None else np.array([mid, p[1]]) if j == i else p)
            continue
        mirrored = np.array([2 * mid - q[0], q[1]])
        out.append(np.array([mid, (p[1] + q[1]) / 2]) if j == i else (p + mirrored) / 2)
    return out, float(-tilt)


def canonical(body, lhand, rhand, face):
    """L'A-pose de référence, quelle que soit la pose de la photo : le tronc
    droit sous le cou, épaules et hanches à niveau et centrées sur un même
    axe vertical, bras à 45°, jambes droites légèrement ouvertes, la tête
    droite et de face. Les longueurs d'os viennent de la photo, chacune à sa
    plus grande valeur gauche/droite ; les largeurs d'épaules et de hanches
    sont celles relevées. Seule la pose est rendue symétrique : l'image
    garde ce qui est propre au personnage (une manche, un sac, une coiffure
    d'un côté), que le modèle lit sur le plein pied.

    Un tronc penché (−7° sur Survêt, 28/09) ou une tête tournée gardés
    dans le squelette, le modèle ne redressait que les bras : les vues
    héritaient d'un corps de travers."""
    b = list(body)
    if any(b[i] is None for i in (1, 2, 5, 8, 11)):
        return apose(body, lhand, rhand), face
    neck = b[1].copy()
    torso = float(np.linalg.norm((b[8] + b[11]) / 2 - neck))
    sw = float(np.linalg.norm(b[5] - b[2])) / 2
    hw = float(np.linalg.norm(b[11] - b[8])) / 2
    drop = float(((b[2][1] + b[5][1]) / 2) - neck[1])   # les épaules sous le cou (≈ 0 en OpenPose)
    ua, fa = _longest(b, (2, 3), (5, 6)), _longest(b, (3, 4), (6, 7))
    th, sn = _longest(b, (8, 9), (11, 12)), _longest(b, (9, 10), (12, 13))
    old = {i: None if b[i] is None else b[i].copy() for i in range(len(b))}

    axis = np.array([1.0, 0.0])
    mid_hip = neck + np.array([0.0, torso])
    b[2], b[5] = neck + np.array([-sw, drop]), neck + np.array([sw, drop])
    b[8], b[11] = mid_hip - axis * hw, mid_hip + axis * hw
    # bras et mains : la main suit le poignet et tourne avec l'avant-bras
    for (s, e, w), side, hand in (((2, 3, 4), -1, rhand), ((5, 6, 7), +1, lhand)):
        if ua is None or fa is None:
            continue
        d = down(ARM_DEG, side)
        b[e] = b[s] + d * ua
        b[w] = b[e] + d * fa
        if hand and old[w] is not None:
            ref = old[e] if old[e] is not None else old[s]
            turn = math.atan2(*d[::-1]) - math.atan2(*(old[w] - ref)[::-1]) if ref is not None else 0.0
            moved = [None if p is None else p - old[w] + b[w] for p in hand]
            hand[:] = rotate(moved, b[w], turn)
    for (s, k, a), side in (((8, 9, 10), -1), ((11, 12, 13), +1)):
        if th is None or sn is None:
            continue
        d = down(LEG_DEG, side)
        b[k] = b[s] + d * th
        b[a] = b[k] + d * sn
    # la tête : le nez droit au-dessus du cou, yeux et oreilles de niveau
    nn = _len(body, 1, 0) or torso * 0.45
    nose = neck - np.array([0.0, nn])
    front = frontal_face(face)
    if front:
        f, _ = front
        f = [None if p is None else p - f[30] + nose for p in f]
        b[0] = nose
        if f[36] is not None and f[39] is not None:
            b[14] = (f[36] + f[39]) / 2
        if f[42] is not None and f[45] is not None:
            b[15] = (f[42] + f[45]) / 2
        jaw = float(abs(f[16][0] - f[0][0])) / 2 if f[0] is not None and f[16] is not None else None
    else:
        f = None
        ex = abs(b[15][0] - b[14][0]) / 2 if b[14] is not None and b[15] is not None else nn * 0.2
        ey = ((b[14][1] + b[15][1]) / 2 - body[0][1]) if b[14] is not None and b[15] is not None and body[0] is not None else -nn * 0.12
        b[0] = nose
        b[14], b[15] = nose + np.array([-ex, ey]), nose + np.array([ex, ey])
        jaw = None
    ears = abs(old[17][0] - old[16][0]) / 2 if old[16] is not None and old[17] is not None else None
    er = max(x for x in (ears, jaw, nn * 0.3) if x)
    ey = ((b[14][1] + b[15][1]) / 2) if b[14] is not None and b[15] is not None else nose[1] - nn * 0.12
    b[16], b[17] = np.array([nose[0] - er, ey]), np.array([nose[0] + er, ey])
    return b, f


def silhouette(img):
    """Le haut du crâne et le bas des semelles, en pixels : les lignes de
    l'image où le personnage se détache du fond. Le fond est estimé ligne
    par ligne sur les bords gauche et droit : un fond de studio est souvent
    un dégradé vertical (David, 28/09). None si le fond n'est pas lisible."""
    edges = np.concatenate([img[:, :12], img[:, -12:]], axis=1).astype(np.int16)
    bg = np.median(edges, axis=1)[:, None, :]
    diff = np.abs(img.astype(np.int16) - bg).max(axis=2)
    rows = np.where((diff > 28).sum(axis=1) > img.shape[1] * 0.004)[0]
    if len(rows) < img.shape[0] * 0.3:
        return None
    return float(rows[0]), float(rows[-1])


def fit(parts, w, h, margin=0.05, extent=None):
    """Met le squelette au cadre. `extent` = (sommet du crâne, semelles)
    dans les coordonnées des points : c'est la silhouette entière, et non
    le nez et les chevilles, qui doit tenir dans le cadre — à l'échelle où
    le modèle pourra la dessiner sans la rétrécir."""
    pts = np.array([p for part in parts if part for p in part if p is not None])
    top, bottom = pts[:, 1].min(), pts[:, 1].max()
    if extent:
        top, bottom = min(top, extent[0]), max(bottom, extent[1])
        head_room = 0.0
    else:
        head_room = (bottom - top) * 0.06   # le nez n'est pas le sommet du crâne
    span = pts[:, 0].max() - pts[:, 0].min() + (bottom - top) * 0.04   # bout des doigts au-delà des points
    scale = min(h * (1 - 2 * margin) / (bottom - top + head_room), w * (1 - 2 * margin) / span)
    cx = (pts[:, 0].min() + pts[:, 0].max()) / 2
    shift = np.array([w / 2, h - h * margin - (bottom - top) * scale])   # semelles au bas du cadre
    out = []
    for part in parts:
        out.append(None if part is None else
                   [None if p is None else (p - np.array([cx, top])) * scale + shift for p in part])
    if (np.array([p for part in out if part for p in part if p is not None])[:, 0] < 0).any():
        sys.exit("le squelette déborde du cadre : élargir")
    return out


def draw(body, lhand, rhand, face, w, h):
    from custom_controlnet_aux.dwpose.types import Keypoint

    def kp(part):
        return None if part is None else [None if p is None else Keypoint(p[0] / w, p[1] / h) for p in part]

    canvas = np.zeros((h, w, 3), np.uint8)
    canvas = util.draw_bodypose(canvas, kp(body))
    canvas = util.draw_handpose(canvas, kp(lhand))
    canvas = util.draw_handpose(canvas, kp(rhand))
    canvas = util.draw_facepose(canvas, kp(face))
    return canvas


HEAD = (0, 14, 15, 16, 17)   # nez, yeux, oreilles


def view(body, lhand, rhand, face, w, azimuth):
    """Le squelette de face vu depuis une caméra tournée de `azimuth` degrés
    autour de l'axe vertical, au même cadrage que la face : même échelle,
    même ligne de sol. Le corps est un plan (Z = 0) ; la tête est un
    cylindre d'axe vertical passant entre les oreilles : chaque point du
    visage y a son angle φ (0 devant, +90° l'oreille gauche), tourne avec
    lui et disparaît derrière — un visage plat tourné de 45° se lisait
    comme une face étroite, et les 3/4 revenaient vers la face."""
    t = math.radians(azimuth)
    ears = (body[16], body[17]) if body[16] is not None and body[17] is not None else None
    hx = (ears[0][0] + ears[1][0]) / 2 if ears else (body[0][0] if body[0] is not None else w / 2)
    r = abs(ears[1][0] - ears[0][0]) / 2 if ears else 40.0

    def project(p, z=0.0):
        x = p[0] - w / 2
        return np.array([w / 2 + x * math.cos(t) - z * math.sin(t), p[1]])

    def on_head(p, radius=1.0, limit=0.1):
        """Un point de la tête sur le cylindre ; None s'il passe derrière."""
        phi = math.asin(max(-0.98, min(0.98, (p[0] - hx) / r)))
        if math.cos(phi - t) < limit:
            return None
        cx = w / 2 + (hx - w / 2) * math.cos(t)
        return np.array([cx + r * radius * math.sin(phi - t), p[1]])

    out = []
    for i, p in enumerate(body):
        if p is None:
            out.append(None)
        elif i in HEAD:
            if i in (16, 17):   # les oreilles, à ±90°
                phi = math.radians(-90 if i == 16 else 90)
                seen = math.cos(phi - t) > -0.15
                cx = w / 2 + (hx - w / 2) * math.cos(t)
                out.append(np.array([cx + r * math.sin(phi - t), p[1]]) if seen else None)
            else:
                out.append(on_head(p, radius=1.3 if i == 0 else 1.05, limit=-0.1))
        else:
            out.append(project(p))
    hands = [None if hd is None else [None if p is None else project(p) for p in hd] for hd in (lhand, rhand)]
    shown = None if face is None else [None if p is None else on_head(p) for p in face]
    return out, hands[0], hands[1], shown


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--vues=")]
    views = [int(v) for a in sys.argv[1:] if a.startswith("--vues=") for v in a[7:].split(",")]
    src, dest = Path(args[0]), Path(args[1])
    w, h = (int(args[2]), int(args[3])) if len(args) > 3 else (1344, 1792)
    img, body, lhand, rhand, face = detect(src)
    ih, iw = img.shape[:2]
    natural = draw(body, lhand, rhand, face, iw, ih)
    extent = silhouette(img)
    if extent is None:
        print("silhouette illisible : cadrage sur le nez et les chevilles")
    body, face = canonical(body, lhand, rhand, face)
    body, lhand, rhand, face = fit([body, lhand, rhand, face], w, h, extent=extent)
    canvas = draw(body, lhand, rhand, face, w, h)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
    for az in views:
        v = draw(*view(body, lhand, rhand, face, w, az), w, h)
        cv2.imwrite(str(dest.with_name(f"{dest.stem}_{az:03d}.png")), cv2.cvtColor(v, cv2.COLOR_RGB2BGR))
    debug = cv2.addWeighted(cv2.cvtColor(img, cv2.COLOR_RGB2BGR), 0.5, cv2.cvtColor(natural, cv2.COLOR_RGB2BGR), 1, 0)
    cv2.imwrite(str(dest.with_name(dest.stem + "_debug.png")), debug)
    dest.with_suffix(".json").write_text(json.dumps(
        {"width": w, "height": h, "body": [None if p is None else [float(p[0]), float(p[1])] for p in body],
         "extent": None if extent is None else [extent[0], extent[1]], "source_size": [iw, ih]}))
    print("squelette :", dest)


if __name__ == "__main__":
    main()
