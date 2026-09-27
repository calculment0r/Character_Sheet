"""La couleur du mesh reprise dans les vues validées.

TRELLIS.2 peint le mesh depuis ses voxels de texture : la couleur passe
par une grille de 1 536 de côté pour tout le corps — le visage n'y tient
qu'en quelques dizaines de voxels —, et elle sort plus sombre et plus
terne que les vues (hoodie bleu roi devenu marine, zip doré, grains gris
sur le pantalon blanc, essai-atelier v001–v003, 27/09).

Les vues, elles, sont ce que Cal a validé. On les reprojette sur l'atlas
UV du mesh, comme le font les chaînes de texture multi-vues publiées
(MV-Adapter, Step1X-3D, Hunyuan3D-Paint) :

  - chaque vue est une caméra à son azimut mesuré, calée sur la
    silhouette du mesh (hauteur, puis décalage latéral au meilleur
    recouvrement) ;
  - chaque texel prend la couleur des vues qui le voient (z-buffer),
    pondérée par l'incidence (cos⁴), loin des bords du masque et des
    ruptures de profondeur du mesh ;
  - la chaîne n'envoie que la face et le dos (`TRUSTED`) ; une vue non
    sûre (un profil) ne passerait que là où elle s'accorde avec la
    couleur du mesh — essayé, les profils laissent des traînées ;
  - là où aucune vue ne voit la surface (sous les bras, entre les
    jambes, dessus de la tête), la couleur des voxels reste, filtrée
    (médian) et ramenée vers celle des vues par une table couleur →
    couleur apprise sur les texels bien vus (`ColorLUT`).

Tout en numpy : un rasteriseur par paquets de triangles (espace UV et
espace image), sans OpenGL ni torch — le venv de la chaîne n'a que
numpy et Pillow.
"""

from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from . import gltf

FOV = 20.0          # celui du gabarit Pixal3D ; les vues de Qwen sont proches d'un téléobjectif
TRUSTED = ("front", "back")


# ── rasterisation ──────────────────────────────────────────────────

def rasterize(xy: np.ndarray, faces: np.ndarray, H: int, W: int, depth: np.ndarray | None = None,
              budget: int = 6_000_000) -> tuple[np.ndarray, np.ndarray]:
    """Couvre la grille H × W (centres de pixels) par les triangles `faces`
    posés en `xy` (pixels). Rend (face, bary) : l'indice du triangle par
    pixel (-1 si aucun) et ses coordonnées barycentriques. Avec `depth`
    (une profondeur par sommet), le plus proche gagne ; sans, le dernier."""
    face_id = np.full(H * W, -1, dtype=np.int64)
    bary = np.zeros((H * W, 3), dtype=np.float32)
    zbest = np.full(H * W, np.inf, dtype=np.float32) if depth is not None else None
    T = xy[faces].astype(np.float64)                       # F,3,2
    x0, y0, x1, y1, x2, y2 = T[:, 0, 0], T[:, 0, 1], T[:, 1, 0], T[:, 1, 1], T[:, 2, 0], T[:, 2, 1]
    den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    px0 = np.ceil(T[:, :, 0].min(1) - 0.5).astype(np.int64).clip(0, W - 1)
    px1 = np.floor(T[:, :, 0].max(1) - 0.5).astype(np.int64).clip(-1, W - 1)
    py0 = np.ceil(T[:, :, 1].min(1) - 0.5).astype(np.int64).clip(0, H - 1)
    py1 = np.floor(T[:, :, 1].max(1) - 0.5).astype(np.int64).clip(-1, H - 1)
    w, h = px1 - px0 + 1, py1 - py0 + 1
    ok = (w > 0) & (h > 0) & (np.abs(den) > 1e-12)
    span = np.maximum(w, h)
    size = 1
    while True:
        sel = np.nonzero(ok & (span <= size) & (span > size // 2 if size > 1 else True))[0]
        if len(sel):
            oy, ox = np.divmod(np.arange(size * size), size)
            step = max(1, budget // (size * size))
            for s in range(0, len(sel), step):
                f = sel[s:s + step]
                X = px0[f, None] + ox[None]
                Y = py0[f, None] + oy[None]
                valid = (ox[None] < w[f, None]) & (oy[None] < h[f, None])
                cx, cy = X + 0.5, Y + 0.5
                d = den[f, None]
                b0 = ((y1[f, None] - y2[f, None]) * (cx - x2[f, None]) + (x2[f, None] - x1[f, None]) * (cy - y2[f, None])) / d
                b1 = ((y2[f, None] - y0[f, None]) * (cx - x2[f, None]) + (x0[f, None] - x2[f, None]) * (cy - y2[f, None])) / d
                b2 = 1.0 - b0 - b1
                inside = valid & (b0 >= -1e-6) & (b1 >= -1e-6) & (b2 >= -1e-6)
                r, c = np.nonzero(inside)
                if not len(r):
                    continue
                pix = Y[r, c] * W + X[r, c]
                fid = f[r]
                bb = np.stack([b0[r, c], b1[r, c], b2[r, c]], 1).astype(np.float32)
                if depth is None:
                    face_id[pix] = fid
                    bary[pix] = bb
                    continue
                z = (bb * depth[faces[fid]]).sum(1).astype(np.float32)
                order = np.lexsort((z, pix))
                pix, fid, bb, z = pix[order], fid[order], bb[order], z[order]
                first = np.ones(len(pix), dtype=bool)
                first[1:] = pix[1:] != pix[:-1]
                pix, fid, bb, z = pix[first], fid[first], bb[first], z[first]
                better = z < zbest[pix]
                pix, fid, bb, z = pix[better], fid[better], bb[better], z[better]
                zbest[pix] = z
                face_id[pix] = fid
                bary[pix] = bb
        if size >= span[ok].max(initial=0):
            break
        size *= 2
    return face_id.reshape(H, W), bary.reshape(H, W, 3)


# ── caméras ────────────────────────────────────────────────────────

class View:
    """Une vue : une image RGBA, un azimut (0 face, 90 flanc gauche du
    personnage, +X), une caméra perspective calée sur le mesh."""

    def __init__(self, name: str, image: Image.Image, azimuth: float, trusted: bool) -> None:
        self.name, self.azimuth, self.trusted = name, float(azimuth), trusted
        rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
        self.rgb, self.alpha = rgba[..., :3], rgba[..., 3]
        self.H, self.W = self.alpha.shape

    def camera(self, center: np.ndarray, height: float) -> None:
        a = math.radians(self.azimuth)
        self.fwd = -np.array([math.sin(a), 0.0, math.cos(a)])          # regarde vers le centre
        self.right = np.array([math.cos(a), 0.0, -math.sin(a)])
        self.up = np.array([0.0, 1.0, 0.0])
        self.dist = 1.1 * height * 0.5 / math.tan(math.radians(FOV) / 2)
        self.eye = center - self.fwd * self.dist
        self.s, self.tx, self.ty = 1.0, 0.0, 0.0

    def project(self, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pixels (x, y) et profondeur le long de l'axe."""
        d = P - self.eye
        z = d @ self.fwd
        u, v = (d @ self.right) / z, (d @ self.up) / z
        return np.stack([u * self.s + self.tx, -v * self.s + self.ty], 1), z

    def fit(self, V: np.ndarray, F: np.ndarray) -> float:
        """Cale l'échelle et le décalage sur la silhouette de l'image :
        hauteur et pied d'abord, puis le décalage latéral au meilleur
        recouvrement (le haut du corps peut différer — bras d'un profil)."""
        ys, xs = np.nonzero(self.alpha > 0.5)
        top, bot = ys.min(), ys.max() + 1
        d = V - self.eye
        z = d @ self.fwd
        u, v = (d @ self.right) / z, (d @ self.up) / z
        self.s = (bot - top) / (v.max() - v.min())
        self.ty = top + v.max() * self.s
        self.tx = xs.mean() - (u.mean() * self.s)
        # recouvrement en basse résolution, décalage latéral seul
        k = max(1, self.H // 256)
        Hs, Ws = self.H // k, self.W // k
        xy, zz = self.project(V)
        fid, _ = rasterize(xy / k, F, Hs, Ws, depth=zz)
        mesh = fid >= 0
        img = np.asarray(Image.fromarray((self.alpha * 255).astype(np.uint8)).resize((Ws, Hs), Image.BILINEAR)) > 127
        best, shift = -1.0, 0
        for dx in range(-Ws // 6, Ws // 6 + 1):
            m = np.roll(mesh, dx, axis=1)
            iou = (m & img).sum() / max(1, (m | img).sum())
            if iou > best:
                best, shift = iou, dx
        self.tx += shift * k
        return float(best)


# ── lecture et écriture du GLB ─────────────────────────────────────

def _primitive(g: gltf.GLB):
    mesh = g.doc["meshes"][0]
    if len(g.doc["meshes"]) != 1 or len(mesh["primitives"]) != 1:
        raise ValueError("la reprojection attend un mesh à une seule primitive")
    prim = mesh["primitives"][0]
    at = prim["attributes"]
    V = g.read_accessor(at["POSITION"]).astype(np.float64)
    N = g.read_accessor(at["NORMAL"]).astype(np.float64) if "NORMAL" in at else None
    UV = g.read_accessor(at["TEXCOORD_0"]).astype(np.float64)
    F = g.read_accessor(prim["indices"]).astype(np.int64).reshape(-1, 3)
    mat = g.doc["materials"][prim.get("material", 0)]
    tex = mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    img_index = g.doc["textures"][tex]["source"]
    return V, N, UV, F, img_index


def _image(g: gltf.GLB, img_index: int) -> Image.Image:
    bv = g.doc["bufferViews"][g.doc["images"][img_index]["bufferView"]]
    start = bv.get("byteOffset", 0)
    return Image.open(io.BytesIO(bytes(g.bin[start:start + bv["byteLength"]]))).convert("RGB")


def replace_image(g: gltf.GLB, img_index: int, data: bytes, mime: str = "image/png") -> None:
    """Remplace les octets d'une image et recompacte le tampon binaire."""
    target = g.doc["images"][img_index]["bufferView"]
    chunks = []
    for i, bv in enumerate(g.doc["bufferViews"]):
        start = bv.get("byteOffset", 0)
        chunks.append(data if i == target else bytes(g.bin[start:start + bv["byteLength"]]))
    g.bin = bytearray()
    for bv, blob in zip(g.doc["bufferViews"], chunks):
        while len(g.bin) % 4:
            g.bin.append(0)
        bv["byteOffset"] = len(g.bin)
        bv["byteLength"] = len(blob)
        g.bin.extend(blob)
    g.doc["images"][img_index]["mimeType"] = mime


# ── la reprojection ────────────────────────────────────────────────

def _vertex_normals(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    N = np.zeros_like(V)
    for k in range(3):
        np.add.at(N, F[:, k], fn)
    return N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)


def _sample(img: np.ndarray, xy: np.ndarray) -> np.ndarray:
    """Bilinéaire, xy en pixels (centres à +0,5)."""
    H, W = img.shape[:2]
    x = np.clip(xy[:, 0] - 0.5, 0, W - 1.001)
    y = np.clip(xy[:, 1] - 0.5, 0, H - 1.001)
    x0, y0 = x.astype(np.int64), y.astype(np.int64)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    im = img.reshape(H, W, -1)
    out = (im[y0, x0] * (1 - fx) * (1 - fy) + im[y0, x0 + 1] * fx * (1 - fy)
           + im[y0 + 1, x0] * (1 - fx) * fy + im[y0 + 1, x0 + 1] * fx * fy)
    return out if img.ndim == 3 else out[:, 0]


def _erode(alpha: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return alpha
    im = Image.fromarray((alpha * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(2 * px + 1))
    return np.asarray(im, dtype=np.float32) / 255.0


def _depth_edges(zbuf: np.ndarray, jump: float, radius: int) -> np.ndarray:
    """1 près d'une rupture de profondeur (ou du bord du mesh), 0 ailleurs,
    adouci sur `radius` pixels."""
    z = np.where(np.isfinite(zbuf), zbuf, 1e9)
    e = np.zeros(z.shape, dtype=bool)
    e[:, 1:] |= np.abs(z[:, 1:] - z[:, :-1]) > jump
    e[1:, :] |= np.abs(z[1:, :] - z[:-1, :]) > jump
    im = Image.fromarray(e.astype(np.uint8) * 255)
    im = im.filter(ImageFilter.MaxFilter(2 * (radius // 2) + 1)).filter(ImageFilter.BoxBlur(radius // 2 + 1))
    return np.asarray(im, dtype=np.float32) / 255.0


def _dilate_fill(img: np.ndarray, mask: np.ndarray, rounds: int = 64) -> np.ndarray:
    """Étend les texels couverts dans les marges de l'atlas (le filtrage
    bilinéaire et les mipmaps y lisent)."""
    out = img.copy()
    have = mask.copy()
    for _ in range(rounds):
        if have.all():
            break
        acc = np.zeros_like(out)
        cnt = np.zeros(have.shape, dtype=np.float32)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            sh = np.roll(have, (dy, dx), axis=(0, 1))
            acc += np.roll(out, (dy, dx), axis=(0, 1)) * sh[..., None]
            cnt += sh
        grow = (~have) & (cnt > 0)
        out[grow] = acc[grow] / cnt[grow][:, None]
        have = have | grow
    return out


def project_views(glb_in: Path, glb_out: Path, views: list[View], *, reject: float = 0.28,
                  report=lambda p, m: None) -> dict:
    """Repeint l'albedo du GLB depuis les vues. Rend un rapport chiffré :
    part des texels repris des vues, calage de chaque vue, correction de
    couleur appliquée aux voxels."""
    g = gltf.GLB.load(glb_in)
    V, N, UV, F, img_index = _primitive(g)
    if N is None or len(N) != len(V):
        N = _vertex_normals(V, F)
    albedo_img = _image(g, img_index)
    T = albedo_img.size[0]
    # Les grains des voxels (points blancs sur l'ourlet bleu, gris sur le
    # pantalon blanc) sont de petites taches isolées : un filtre médian
    # les efface là où la couleur des voxels reste.
    albedo = np.asarray(albedo_img.filter(ImageFilter.MedianFilter(7)), dtype=np.float32) / 255.0

    lo, hi = V.min(0), V.max(0)
    center, height = (lo + hi) / 2, hi[1] - lo[1]
    fits = {}
    for v in views:
        v.camera(center, height)
        fits[v.name] = round(v.fit(V, F), 4)
    report(0.2, "vues calées sur la silhouette")

    # texels → position, normale (glTF : v vers le bas de l'image)
    fid, bary = rasterize(np.stack([UV[:, 0] * T, UV[:, 1] * T], 1), F, T, T)
    cov = fid >= 0
    f = fid[cov]
    b = bary[cov].astype(np.float64)
    P = (b[:, :, None] * V[F[f]]).sum(1)
    Nt = (b[:, :, None] * N[F[f]]).sum(1)
    Nt /= np.maximum(np.linalg.norm(Nt, axis=1, keepdims=True), 1e-12)
    base = albedo[cov].astype(np.float64)
    report(0.4, f"{cov.sum()} texels")

    tol = 0.012 * height
    samples = []
    for v in views:
        xy, z = v.project(V)
        zfid, zbary = rasterize(xy, F, v.H, v.W, depth=z)
        zbuf = np.full((v.H, v.W), np.inf)
        hit = zfid >= 0
        zb = zbary[hit].astype(np.float64)
        zbuf[hit] = (zb * z[F[zfid[hit]]]).sum(1)
        pxy, pz = v.project(P)
        inside = (pxy[:, 0] >= 0) & (pxy[:, 0] < v.W) & (pxy[:, 1] >= 0) & (pxy[:, 1] < v.H)
        xi = np.clip(pxy[:, 0].astype(np.int64), 0, v.W - 1)
        yi = np.clip(pxy[:, 1].astype(np.int64), 0, v.H - 1)
        # le plus proche des 3 × 3 voisins : un texel au bord d'une arête n'est pas caché
        zn = np.full(len(P), np.inf)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                zn = np.minimum(zn, zbuf[np.clip(yi + dy, 0, v.H - 1), np.clip(xi + dx, 0, v.W - 1)])
        visible = inside & (pz <= zn + tol)
        to_cam = v.eye[None] - P
        cos = (Nt * to_cam).sum(1) / np.linalg.norm(to_cam, axis=1)
        edge = _sample(_erode(v.alpha, max(2, v.H // 400)), pxy)
        # Loin des ruptures de profondeur du mesh : près d'un bord (joue
        # devant la capuche, main devant le fond), le mesh et l'image ne
        # tombent pas au pixel près, et la joue lirait la capuche.
        far = _sample(1.0 - _depth_edges(zbuf, 0.03 * height, max(3, v.H // 120)), pxy)
        w = np.where(visible & (cos > 0.3), np.clip(cos, 0, 1) ** 4 * np.clip(edge, 0, 1) * far, 0.0)
        col = _sample(v.rgb, pxy)
        samples.append((v, w, col))
        report(0.4 + 0.4 * (len(samples) / len(views)), f"vue {v.name}")

    # correction des voxels vers les vues, apprise sur les texels bien vus d'une vue sûre
    ref_w = np.zeros(len(P))
    ref_c = np.zeros((len(P), 3))
    for v, w, col in samples:
        if v.trusted:
            better = w > ref_w
            ref_w[better], ref_c[better] = w[better], col[better]
    lut = ColorLUT.learn(base[ref_w > 0.5], ref_c[ref_w > 0.5])
    corrected = lut.apply(base)

    acc = np.zeros((len(P), 3))
    wsum = np.zeros(len(P))
    rejected = {}
    for v, w, col in samples:
        # Une vue sûre n'est jamais écartée : là où elle contredit les voxels,
        # ce sont les voxels qui ont tort (moustache verte, yeux noirs,
        # 27/09). Un profil ne passe que là où il s'accorde avec eux.
        if not v.trusted:
            bad = np.linalg.norm(col - corrected, axis=1) > reject
            rejected[v.name] = round(float((bad & (w > 0)).sum() / max(1, (w > 0).sum())), 4)
            w = np.where(bad, 0.0, w)
        acc += w[:, None] * col
        wsum += w
    seen = wsum > 1e-4
    mix = np.clip(wsum / 0.1, 0, 1)[:, None]
    projected = np.where(seen[:, None], acc / np.maximum(wsum, 1e-8)[:, None], corrected)
    final = mix * projected + (1 - mix) * corrected

    out = np.zeros_like(albedo)
    out[cov] = final.astype(np.float32)
    out = _dilate_fill(out, cov)
    buf = io.BytesIO()
    Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)).save(buf, format="PNG")
    replace_image(g, img_index, buf.getvalue())
    g.doc.setdefault("asset", {}).setdefault("extras", {})["albedo_from_views"] = [v.name for v in views]
    g.save(glb_out)
    report(1.0, "albedo repris des vues")
    return {"texels": int(cov.sum()), "from_views": round(float((mix[:, 0] > 0.5).mean()), 4),
            "fit_iou": fits, "voxel_shift": lut.summary(), "rejected": rejected, "texture": T}


def _shift(a: np.ndarray, s: int, axis: int) -> np.ndarray:
    """Décalage d'une case le long d'un axe, sans bouclage (zéros au bord)."""
    out = np.zeros_like(a)
    src = [slice(None)] * a.ndim
    dst = [slice(None)] * a.ndim
    src[axis], dst[axis] = (slice(0, -1), slice(1, None)) if s > 0 else (slice(1, None), slice(0, -1))
    out[tuple(dst)] = a[tuple(src)]
    return out


class ColorLUT:
    """Une table de correction couleur → couleur, apprise sur les paires
    (voxel, vue) : chaque case de la grille RGB garde l'écart moyen de ses
    texels, les cases pauvres l'empruntent à leurs voisines, les cases sans
    voisin ne corrigent rien. Une correction par canal ne suffit pas : le
    hoodie, la peau et les chaussures ne se sont pas assombries de la même
    façon, et une droite apprise surtout sur le hoodie bleuit le reste."""

    N = 12

    def __init__(self, delta: np.ndarray) -> None:
        self.delta = delta

    @classmethod
    def learn(cls, base: np.ndarray, target: np.ndarray, full: int = 40) -> "ColorLUT":
        n = cls.N
        # On n'apprend que des paires de même teinte : un marine devenu bleu
        # roi est une dérive de couleur, une peau devant une capuche est un
        # défaut de calage (le mesh montre la nuque là où l'image montre la
        # capuche) — appris, il bleuirait toute la peau hors des vues.
        def chroma(c):
            return c / (c.sum(1, keepdims=True) + 0.15)
        same = np.linalg.norm(chroma(base) - chroma(target), axis=1) < 0.08
        base, target = base[same], target[same]
        idx = np.clip((base * (n - 1)).round().astype(np.int64), 0, n - 1)
        flat = (idx[:, 0] * n + idx[:, 1]) * n + idx[:, 2]
        diff = target - base
        # médiane par case, puis moyenne des texels proches d'elle : un texel
        # mal calé (bord d'une main, d'une manche) lit la couleur voisine et
        # tirerait une simple moyenne vers elle
        cnt = np.bincount(flat, minlength=n ** 3)
        start = np.concatenate([[0], np.cumsum(cnt)[:-1]])
        med = np.zeros((n ** 3, 3))
        filled = cnt > 0
        for c in range(3):
            order = np.lexsort((diff[:, c], flat))
            med[filled, c] = diff[order[start[filled] + cnt[filled] // 2], c]
        keep = np.linalg.norm(diff - med[flat], axis=1) < 0.12
        cnt = np.bincount(flat[keep], minlength=n ** 3).astype(np.float64)
        own = np.stack([np.bincount(flat[keep], weights=diff[keep, c], minlength=n ** 3) for c in range(3)], 1)
        own = (own / np.maximum(cnt, 1)[:, None]).reshape(n, n, n, 3)
        conf0 = np.minimum(cnt / full, 1.0).reshape(n, n, n)
        # les cases pauvres empruntent à leurs voisines ; une case pleine
        # garde son propre écart (la peau n'hérite pas du hoodie)
        d, conf = own * conf0[..., None], conf0.copy()
        for _ in range(2):
            d2, c2 = d.copy(), conf.copy()
            for ax in range(3):
                for s in (1, -1):
                    d2 += 0.5 * _shift(d, s, ax)
                    c2 += 0.5 * _shift(conf, s, ax)
            d, conf = d2 / 4.0, c2 / 4.0
        borrowed = d / np.maximum(conf, 1e-6)[..., None] * np.minimum(conf / 0.1, 1.0)[..., None]
        # l'emprunt reste timide : une case voisine peut être une autre matière
        delta = conf0[..., None] * own + (1 - conf0[..., None]) * borrowed * 0.3
        return cls(delta)

    def apply(self, base: np.ndarray) -> np.ndarray:
        n = self.N
        x = np.clip(base, 0, 1) * (n - 1)
        i0 = np.clip(np.floor(x).astype(np.int64), 0, n - 2)
        f = x - i0
        out = np.zeros_like(base)
        for dr in (0, 1):
            for dg in (0, 1):
                for db in (0, 1):
                    w = ((f[:, 0] if dr else 1 - f[:, 0]) * (f[:, 1] if dg else 1 - f[:, 1])
                         * (f[:, 2] if db else 1 - f[:, 2]))
                    out += w[:, None] * self.delta[i0[:, 0] + dr, i0[:, 1] + dg, i0[:, 2] + db]
        return np.clip(base + out, 0, 1)

    def summary(self) -> float:
        return round(float(np.abs(self.delta).mean()), 4)
