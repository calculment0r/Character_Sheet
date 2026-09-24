"""Le mannequin 3D factice.

Même mannequin que les images factices (`stubs.JOINTS`), en volume :
des capsules le long des membres, en A-pose, avec de vraies cartes PBR
— couleur, métal, rugosité, normales — pour que le viewer ait quelque
chose à montrer canal par canal, et que le rig ait un mesh à skinner.

Chaque sommet retient le segment qui l'a produit et sa position le long
du segment : c'est ce qui permet au rig factice de calculer des poids.
"""

from __future__ import annotations

import io
import math
import random

import numpy as np
from PIL import Image

from .stubs import JOINTS, LIMBS, _palette

ATLAS = {"skin": (0, 0), "top": (1, 0), "bottom": (0, 1), "shoes": (1, 1), "head": (0, 0)}
ROUGH = {"skin": 0.5, "head": 0.5, "top": 0.82, "bottom": 0.7, "shoes": 0.32}


def _frame(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    helper = np.array([0.0, 0.0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(axis, helper)
    u /= np.linalg.norm(u)
    return u, np.cross(axis, u)


def _tube(a: np.ndarray, b: np.ndarray, r: float, seg: int = 16, rings: int = 8):
    """Un cylindre fermé par deux demi-sphères : une capsule."""
    axis = b - a
    length = np.linalg.norm(axis)
    axis /= length
    u, v = _frame(axis)
    pos, nrm, uv, tpar = [], [], [], []
    caps = 4
    # anneaux : demi-sphère basse, fût, demi-sphère haute
    rows = []
    for i in range(caps):
        phi = -math.pi / 2 + (math.pi / 2) * i / caps
        rows.append((math.sin(phi) * r, math.cos(phi), math.sin(phi)))
    for i in range(rings + 1):
        rows.append((length * i / rings, 1.0, 0.0))
    for i in range(1, caps + 1):
        phi = (math.pi / 2) * i / caps
        rows.append((length + math.sin(phi) * r, math.cos(phi), math.sin(phi)))
    for k, (h, radial, axial) in enumerate(rows):
        t = min(max(h / length, 0.0), 1.0)
        for j in range(seg + 1):
            ang = 2 * math.pi * j / seg
            d = math.cos(ang) * u + math.sin(ang) * v
            n = radial * d + axial * axis
            pos.append(a + axis * h + d * r * radial)
            nrm.append(n / (np.linalg.norm(n) or 1.0))
            uv.append((j / seg, k / (len(rows) - 1)))
            tpar.append(t)
    idx = []
    for k in range(len(rows) - 1):
        for j in range(seg):
            p0 = k * (seg + 1) + j
            p1 = p0 + seg + 1
            idx += [(p0, p0 + 1, p1), (p0 + 1, p1 + 1, p1)]
    return np.array(pos), np.array(nrm), np.array(uv), np.array(idx), np.array(tpar)


def _ellipsoid(c: np.ndarray, radii: tuple[float, float, float], seg: int = 24, rings: int = 16):
    pos, nrm, uv = [], [], []
    for i in range(rings + 1):
        th = math.pi * i / rings
        for j in range(seg + 1):
            ph = 2 * math.pi * j / seg
            d = np.array([math.sin(th) * math.sin(ph), math.cos(th), math.sin(th) * math.cos(ph)])
            pos.append(c + d * radii)
            n = d / np.array(radii)
            nrm.append(n / np.linalg.norm(n))
            uv.append((j / seg, i / rings))
    idx = []
    for i in range(rings):
        for j in range(seg):
            p0 = i * (seg + 1) + j
            p1 = p0 + seg + 1
            idx += [(p0, p1, p0 + 1), (p0 + 1, p1, p1 + 1)]
    return np.array(pos), np.array(nrm), np.array(uv), np.array(idx)


def mannequin_mesh() -> dict:
    """Le maillage, et pour chaque sommet : (joint a, joint b, t)."""
    P, N, UV, I, SEG = [], [], [], [], []
    base = 0
    for a, b, width, part in LIMBS:
        pa, pb = np.array(JOINTS[a]), np.array(JOINTS[b])
        p, n, uv, idx, t = _tube(pa, pb, width / 2)
        ox, oy = ATLAS[part]
        uv = np.column_stack([ox * 0.5 + uv[:, 0] * 0.5, oy * 0.5 + uv[:, 1] * 0.5])
        P.append(p), N.append(n), UV.append(uv), I.append(idx + base)
        SEG += [(a, b, float(x)) for x in t]
        base += len(p)
    # La tête tient entre les articulations Head et HeadEnd de SOMA, sans
    # dépasser le sommet du crâne.
    p, n, uv, idx = _ellipsoid(np.array(JOINTS["head"]) + np.array([0, 0.005, 0.01]), (0.078, 0.092, 0.09))
    uv = uv * 0.5
    P.append(p), N.append(n), UV.append(uv), I.append(idx + base)
    SEG += [("neck", "head", 1.0)] * len(p)
    return {"positions": np.vstack(P).astype(np.float32), "normals": np.vstack(N).astype(np.float32),
            "uvs": np.vstack(UV).astype(np.float32),
            "indices": np.vstack(I).astype(np.uint32), "segments": SEG}


def pbr_maps(seed: int, size: int = 512) -> dict[str, Image.Image]:
    """Quatre cartes : couleur, métal, rugosité, normales. Les motifs
    sont là pour que chaque canal se lise dans le viewer."""
    rnd = random.Random(seed)
    pal = _palette(seed)
    h = size // 2
    yy, xx = np.mgrid[0:h, 0:h].astype(np.float32)
    albedo = np.zeros((size, size, 3), np.float32)
    rough = np.zeros((size, size), np.float32)
    metal = np.zeros((size, size), np.float32)
    height = np.zeros((size, size), np.float32)
    noise = np.random.default_rng(seed).random((size, size)).astype(np.float32)

    for part, (ox, oy) in (("skin", (0, 0)), ("top", (1, 0)), ("bottom", (0, 1)), ("shoes", (1, 1))):
        sl = (slice(oy * h, oy * h + h), slice(ox * h, ox * h + h))
        base = np.array(pal[part], np.float32) / 255.0
        tone = np.ones((h, h), np.float32)
        hmap = np.zeros((h, h), np.float32)
        if part == "top":
            stripe = (np.sin(xx / h * 2 * math.pi * rnd.randint(6, 12)) > 0.6).astype(np.float32)
            tone = 1.0 - 0.18 * stripe
            hmap = 0.5 * np.sin((xx + yy) / 3.0)
            # une rangée de boutons métalliques, pour le canal métal
            for k in range(6):
                cy, cx = h * (0.15 + 0.13 * k), h * 0.5
                disc = ((yy - cy) ** 2 + (xx - cx) ** 2) < (h * 0.025) ** 2
                metal[sl][disc] = 1.0
                rough[sl][disc] = 0.25
        elif part == "bottom":
            hmap = 0.5 * np.sin((xx - yy) / 2.0)
            tone = 1.0 - 0.08 * (np.sin((xx - yy) / 2.0) > 0)
        elif part == "shoes":
            tone = 0.9 + 0.1 * noise[sl]
        else:
            tone = 0.97 + 0.03 * noise[sl]
        albedo[sl] = base * tone[..., None]
        r = rough[sl]
        rough[sl] = np.where(r > 0, r, ROUGH[part] + 0.06 * (noise[sl] - 0.5))
        height[sl] = hmap + 0.3 * noise[sl]

    gy, gx = np.gradient(height)
    nrm = np.dstack([-gx * 0.8, -gy * 0.8, np.ones_like(gx)])
    nrm /= np.linalg.norm(nrm, axis=2, keepdims=True)

    def img(a, mode="L"):
        return Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8), mode)

    return {"albedo": img(albedo, "RGB"), "metallic": img(metal), "roughness": img(np.clip(rough, 0, 1)),
            "normal": img(nrm * 0.5 + 0.5, "RGB")}


def packed_metallic_roughness(maps: dict[str, Image.Image]) -> Image.Image:
    """glTF range la rugosité dans le vert et le métal dans le bleu."""
    r = np.full(np.asarray(maps["roughness"]).shape, 255, np.uint8)
    return Image.merge("RGB", (Image.fromarray(r), maps["roughness"], maps["metallic"]))


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
