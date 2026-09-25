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


# Normales des points de la tête, pour savoir s'ils se voient depuis une
# caméra à l'azimut θ (0 = face, 90 = flanc gauche du personnage, +X).
HEAD = {0: (0.0, 1.0), 14: (-0.35, 0.94), 15: (0.35, 0.94), 16: (-1.0, 0.0), 17: (1.0, 0.0)}


def view(body, lhand, rhand, face, w, azimuth):
    """Le squelette de face (plan du corps, Z = 0 sauf la tête) vu depuis
    une caméra tournée de `azimuth` degrés autour de l'axe vertical, au
    même cadrage que la face : même échelle, même ligne de sol."""
    t = math.radians(azimuth)
    cam = np.array([math.sin(t), math.cos(t)])
    ears = np.linalg.norm(body[16] - body[17]) if body[16] is not None and body[17] is not None else 0.0
    depth = {0: 0.65 * ears, 14: 0.55 * ears, 15: 0.55 * ears}

    def project(p, z=0.0):
        x = p[0] - w / 2
        return np.array([w / 2 + x * math.cos(t) - z * math.sin(t), p[1]])

    out = []
    for i, p in enumerate(body):
        if p is None or (i in HEAD and np.dot(HEAD[i], cam) < -0.15):
            out.append(None)
        else:
            out.append(project(p, depth.get(i, 0.0)))
    hands = [None if hd is None else [None if p is None else project(p) for p in hd] for hd in (lhand, rhand)]
    shown = None if face is None or math.cos(t) < 0.5 else [
        None if p is None else project(p, 0.55 * ears) for p in face]
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
