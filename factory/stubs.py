"""Les remplaçants, pour parcourir la chaîne sans GPU.

Ils ne font pas semblant : chaque image porte l'étiquette FACTICE, et le
manifeste range le moteur qui a produit chaque fichier. Mais ils
produisent de vrais fichiers, aux bonnes dimensions et avec la bonne
géométrie — un mannequin en A-pose vu sous l'azimut demandé, sur fond
neutre — pour que le détourage, la normalisation, le contrôle d'angle
et le viewer travaillent sur quelque chose de plausible.
"""

from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFont

BG = (128, 128, 128)


def _joints() -> dict[str, tuple[float, float, float]]:
    """Le mannequin, en mètres, face à +Z, Y en haut, +X à sa gauche — les
    conventions glTF, les mêmes que le viewer et que SOMA. Ses points sont
    ceux du squelette SOMA en A-pose : le mesh factice et son rig factice
    tombent ainsi exactement l'un sur l'autre."""
    from .skeleton import soma_apose, soma_spec

    spec = soma_spec()
    pos = soma_apose(spec)
    at = {n: pos[i] for i, n in enumerate(spec["names"])}
    head = (at["Head"] + at["HeadEnd"]) / 2
    pick = {
        "head": head, "neck": at["Neck1"], "chest": at["Chest"], "pelvis": at["Hips"],
        "l_shoulder": at["LeftArm"], "r_shoulder": at["RightArm"],
        "l_elbow": at["LeftForeArm"], "r_elbow": at["RightForeArm"],
        "l_wrist": at["LeftHand"], "r_wrist": at["RightHand"],
        "l_hand": at["LeftHandMiddle2"], "r_hand": at["RightHandMiddle2"],
        "l_hip": at["LeftLeg"], "r_hip": at["RightLeg"],
        "l_knee": at["LeftShin"], "r_knee": at["RightShin"],
        "l_ankle": at["LeftFoot"], "r_ankle": at["RightFoot"],
        "l_toe": at["LeftToeEnd"], "r_toe": at["RightToeEnd"],
        "nose": head + [0.0, -0.01, 0.10],
    }
    return {k: tuple(float(x) for x in v) for k, v in pick.items()}


JOINTS = _joints()

LIMBS = [  # (a, b, largeur en mètres, partie)
    ("pelvis", "chest", 0.30, "top"), ("chest", "neck", 0.12, "skin"),
    ("l_shoulder", "l_elbow", 0.10, "top"), ("r_shoulder", "r_elbow", 0.10, "top"),
    ("l_elbow", "l_wrist", 0.08, "skin"), ("r_elbow", "r_wrist", 0.08, "skin"),
    ("l_wrist", "l_hand", 0.07, "skin"), ("r_wrist", "r_hand", 0.07, "skin"),
    ("l_hip", "l_knee", 0.15, "bottom"), ("r_hip", "r_knee", 0.15, "bottom"),
    ("l_knee", "l_ankle", 0.11, "bottom"), ("r_knee", "r_ankle", 0.11, "bottom"),
    ("l_ankle", "l_toe", 0.07, "shoes"), ("r_ankle", "r_toe", 0.07, "shoes"),
    ("l_shoulder", "r_shoulder", 0.12, "top"),
]


def _palette(seed: int) -> dict:
    rnd = random.Random(seed)
    skin = rnd.choice([(233, 196, 170), (201, 150, 116), (150, 101, 72), (98, 64, 44)])
    return {
        "skin": skin,
        "hair": rnd.choice([(40, 30, 24), (92, 58, 34), (182, 142, 90), (20, 20, 22)]),
        "top": tuple(rnd.randint(40, 200) for _ in range(3)),
        "bottom": tuple(rnd.randint(30, 120) for _ in range(3)),
        "shoes": (34, 32, 30),
    }


def _label(img: Image.Image, text: str) -> None:
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    x, y = 8, img.height - 20
    w = int(d.textlength(text, font=font)) + 10
    d.rectangle((x - 4, y - 3, x + w, y + 14), fill=(10, 13, 11))
    d.text((x, y), text, fill=(224, 103, 74), font=font)


def _project(p, az: float):
    """Caméra en orbite : azimut 0 = face, 90 = profil gauche du sujet."""
    a = math.radians(az)
    x, y, z = p
    sx = x * math.cos(a) - z * math.sin(a)
    depth = x * math.sin(a) + z * math.cos(a)
    return sx, y, depth


def mannequin(size: tuple[int, int], *, azimuth: float, seed: int, mask_face: bool = False,
              label: str | None = "FACTICE") -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, BG)
    d = ImageDraw.Draw(img)
    pal = _palette(seed)
    # Tout le personnage dans le cadre, mains comprises : en A-pose les
    # mains s'écartent de 0,64 m de l'axe.
    px_per_m = min(h * 0.84 / 1.75, w * 0.88 / 1.36)
    ox, oy = w / 2, h * 0.5 + 0.84 * px_per_m

    def pt(p):
        sx, sy, _ = _project(p, azimuth)
        return ox + sx * px_per_m, oy - sy * px_per_m

    # Du plus loin au plus proche, pour que les membres se recouvrent juste.
    parts = []
    for a, b, width, part in LIMBS:
        depth = (_project(JOINTS[a], azimuth)[2] + _project(JOINTS[b], azimuth)[2]) / 2
        parts.append((depth, a, b, width, part))
    head_depth = _project(JOINTS["head"], azimuth)[2]
    parts.append((head_depth + 0.01, "head", None, 0.22, "head"))

    for depth, a, b, width, part in sorted(parts):
        if part == "head":
            cx, cy = pt(JOINTS["head"])
            r = 0.11 * px_per_m
            d.ellipse((cx - r * 0.86, cy - r, cx + r * 0.86, cy + r), fill=pal["skin"])
            # Les cheveux : une calotte, qui couvre tout le crâne de dos.
            facing = math.cos(math.radians(azimuth))
            d.chord((cx - r * 0.9, cy - r * 1.05, cx + r * 0.9, cy + r * (0.2 if facing > -0.2 else 1.0)),
                    180, 360, fill=pal["hair"])
            if facing < -0.2:
                d.ellipse((cx - r * 0.86, cy - r, cx + r * 0.86, cy + r), fill=pal["hair"])
            nx, ny = pt(JOINTS["nose"])
            if facing > 0.3:
                for ex in (-0.35, 0.35):
                    ecx = cx + ex * r * facing
                    d.ellipse((ecx - r * 0.08, cy - r * 0.12, ecx + r * 0.08, cy + r * 0.02), fill=(30, 30, 32))
                d.line((cx - r * 0.25 * facing, cy + r * 0.45, cx + r * 0.25 * facing, cy + r * 0.45),
                       fill=(120, 60, 60), width=max(1, int(r * 0.06)))
            if abs(math.sin(math.radians(azimuth))) > 0.5:
                d.ellipse((nx - r * 0.12, ny - r * 0.1, nx + r * 0.12, ny + r * 0.1), fill=pal["skin"])
            if mask_face:
                d.ellipse((cx - r * 1.15, cy - r * 1.15, cx + r * 1.15, cy + r * 1.15), fill=(150, 150, 150))
            continue
        (x0, y0), (x1, y1) = pt(JOINTS[a]), pt(JOINTS[b])
        lw = max(2, int(width * px_per_m))
        color = pal[part]
        d.line((x0, y0, x1, y1), fill=color, width=lw)
        for x, y in ((x0, y0), (x1, y1)):
            d.ellipse((x - lw / 2, y - lw / 2, x + lw / 2, y + lw / 2), fill=color)

    # L'ombre de contact demandée par le prompt.
    fx, fy = pt((0.0, 0.0, 0.0))
    d.ellipse((fx - 0.35 * px_per_m, fy - 4, fx + 0.35 * px_per_m, fy + 6), fill=(116, 116, 116))
    if label:
        _label(img, f"{label} · az {azimuth:g}°")
    return img


def portrait(size: tuple[int, int], *, seed: int, label: str | None = "FACTICE") -> Image.Image:
    """Le portrait neutre du §3, en caricature : face, regard caméra,
    bouche fermée, fond uni."""
    w, h = size
    rnd = random.Random(seed)
    pal = _palette(seed)
    img = Image.new("RGB", size, BG)
    d = ImageDraw.Draw(img)
    cx, cy = w / 2, h * (0.44 + rnd.uniform(-0.02, 0.02))
    rx, ry = w * rnd.uniform(0.17, 0.21), h * rnd.uniform(0.24, 0.28)
    d.ellipse((w * 0.12, h * 0.78, w * 0.88, h * 1.3), fill=pal["top"])
    d.rectangle((cx - rx * 0.4, cy + ry * 0.7, cx + rx * 0.4, h * 0.84), fill=pal["skin"])
    d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=pal["skin"])
    d.chord((cx - rx * 1.08, cy - ry * 1.12, cx + rx * 1.08, cy + ry * 0.2), 180, 360, fill=pal["hair"])
    for ex in (-0.38, 0.38):
        ecx = cx + ex * rx
        d.ellipse((ecx - rx * 0.13, cy - ry * 0.02, ecx + rx * 0.13, cy + ry * 0.09), fill=(245, 245, 240))
        d.ellipse((ecx - rx * 0.06, cy + ry * 0.0, ecx + rx * 0.06, cy + ry * 0.08), fill=(40, 34, 30))
    d.line((cx, cy + ry * 0.1, cx - rx * 0.06, cy + ry * 0.38), fill=tuple(int(c * 0.8) for c in pal["skin"]), width=3)
    d.line((cx - rx * 0.3, cy + ry * 0.55, cx + rx * 0.3, cy + ry * 0.55), fill=(130, 70, 66), width=4)
    if label:
        _label(img, f"{label} · graine {seed}")
    return img


def plate(size: tuple[int, int], *, seed: int, mask_face: bool, label: str | None = "FACTICE") -> Image.Image:
    """La planche du §5.4 corrigé : cinq pleins pieds, trois gros plans
    et une expression."""
    w, h = size
    img = Image.new("RGB", size, BG)
    top_h = int(h * 0.64)
    cell = w // 5
    for i, az in enumerate((0, 90, 180, 270, 45)):
        panel = mannequin((cell, top_h), azimuth=az, seed=seed, mask_face=mask_face, label=None)
        img.paste(panel, (i * cell, 0))
    close = h - top_h
    for i in range(4):
        face = portrait((close, close), seed=seed, label=None)
        if i == 1:
            face = face.transform(face.size, Image.AFFINE, (1.6, 0, -close * 0.3, 0, 1, 0), fillcolor=BG)
        img.paste(face, (i * (w // 4) + (w // 4 - close) // 2, top_h))
    if label:
        _label(img, f"{label} · planche · graine {seed}{' · disque' if mask_face else ''}")
    return img
