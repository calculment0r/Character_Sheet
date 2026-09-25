"""Squelette OpenPose en A-pose, aux proportions d'un plein pied.

À lancer sur DGX2 avec le python de ComfyUI (onnxruntime, cv2) :
  ~/comfyui-env/bin/python tools/remote/apose_skeleton.py <plein_pied.png> <sortie.png> [largeur hauteur]

DWPose relève le corps, les mains et le visage du plein pied validé.
Les bras sont redressés à 45° de la verticale, les jambes légèrement
ouvertes, les longueurs d'os gardées ; les mains suivent l'avant-bras
en bloc. Le squelette est remis à l'échelle de la hauteur du cadre et
centré. Écrit aussi <sortie>.json (points en pixels) et <sortie>_debug.png.
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


def apose(body, lhand, rhand):
    body = list(body)
    down = lambda deg, side: np.array([side * math.sin(math.radians(deg)), math.cos(math.radians(deg))])
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


def fit(parts, w, h, margin=0.05):
    pts = np.array([p for part in parts if part for p in part if p is not None])
    top, bottom = pts[:, 1].min(), pts[:, 1].max()
    head_room = (bottom - top) * 0.06   # le nez n'est pas le sommet du crâne
    span = pts[:, 0].max() - pts[:, 0].min() + (bottom - top) * 0.04   # bout des doigts au-delà des points
    scale = min(h * (1 - 2 * margin) / (bottom - top + head_room), w * (1 - 2 * margin) / span)
    cx = (pts[:, 0].min() + pts[:, 0].max()) / 2
    shift = np.array([w / 2, h - h * margin - (bottom - top) * scale])   # pieds au bas du cadre
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
    body = apose(body, lhand, rhand)
    body, lhand, rhand, face = fit([body, lhand, rhand, face], w, h)
    canvas = draw(body, lhand, rhand, face, w, h)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
    for az in views:
        v = draw(*view(body, lhand, rhand, face, w, az), w, h)
        cv2.imwrite(str(dest.with_name(f"{dest.stem}_{az:03d}.png")), cv2.cvtColor(v, cv2.COLOR_RGB2BGR))
    debug = cv2.addWeighted(cv2.cvtColor(img, cv2.COLOR_RGB2BGR), 0.5, cv2.cvtColor(natural, cv2.COLOR_RGB2BGR), 1, 0)
    cv2.imwrite(str(dest.with_name(dest.stem + "_debug.png")), debug)
    dest.with_suffix(".json").write_text(json.dumps(
        {"width": w, "height": h, "body": [None if p is None else [float(p[0]), float(p[1])] for p in body]}))
    print("squelette :", dest)


if __name__ == "__main__":
    main()
