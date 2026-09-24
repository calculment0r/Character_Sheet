"""Traitement d'image de la chaîne, sans GPU.

La passe de contrôle du §6.2 : avant d'envoyer des vues à la 3D, on
détoure, on recentre, on met toutes les silhouettes à la même échelle et
on égalise la marge. Des entrées mal cadrées donnent un mesh pire qu'une
seule image — la passe mesure donc aussi, et le rapport dit ce qu'elle a
corrigé.

Le détourage intégré suffit pour ce que H3 rend quand on lui demande un
fond neutre uni : on estime la couleur du fond sur le pourtour et on
garde ce qui s'en écarte. Pour un fond chargé, `FACTORY_PREP=rembg`
passe par rembg (BiRefNet), s'il est installé.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

NEUTRAL = (128, 128, 128)


# ── détourage ──────────────────────────────────────────────────────

def _shift_max(m: np.ndarray, r: int) -> np.ndarray:
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx or dy:
                out = np.maximum(out, np.roll(np.roll(m, dy, 0), dx, 1))
    return out


def _shift_min(m: np.ndarray, r: int) -> np.ndarray:
    return 1.0 - _shift_max(1.0 - m, r)


def _components(small: np.ndarray) -> tuple[np.ndarray, dict[int, int], set[int]]:
    """Étiquette les taches d'un masque booléen (4-connexité). Rend les
    étiquettes, la taille de chaque tache, et celles qui touchent le bord."""
    labels = np.zeros(small.shape, dtype=np.int32)
    sizes: dict[int, int] = {}
    border: set[int] = set()
    sh, sw = small.shape
    current = 0
    for y0, x0 in zip(*np.nonzero(small)):
        if labels[y0, x0]:
            continue
        current += 1
        stack = [(y0, x0)]
        labels[y0, x0] = current
        count = 0
        while stack:
            y, x = stack.pop()
            count += 1
            if y in (0, sh - 1) or x in (0, sw - 1):
                border.add(current)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < sh and 0 <= nx < sw and small[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = current
                    stack.append((ny, nx))
        sizes[current] = count
    return labels, sizes, border


def _upscale(small: np.ndarray, scale: int, shape: tuple[int, int]) -> np.ndarray:
    return np.kron(small, np.ones((scale, scale), dtype=bool))[: shape[0], : shape[1]]


def _clean(mask: np.ndarray) -> np.ndarray:
    """Garde la plus grande tache et bouche ses petits trous. Le travail
    se fait sur une version réduite : il sert à écarter les poussières et
    à reboucher un vêtement de la couleur du fond, pas à tracer le
    contour, qui garde sa pleine résolution."""
    h, w = mask.shape
    scale = max(1, max(h, w) // 256)
    small = mask[::scale, ::scale]

    labels, sizes, _ = _components(small)
    if not sizes:
        return mask
    keep = labels == max(sizes, key=sizes.get)

    # Un trou fermé petit devant la silhouette est un morceau de
    # vêtement couleur de fond ; un grand espace fermé (un bras replié
    # sur la hanche) reste du fond. L'A-pose évite ce second cas.
    holes, hole_sizes, touching = _components(~keep)
    limit = 0.015 * keep.sum()
    for lab, size in hole_sizes.items():
        if lab not in touching and size <= limit:
            keep |= holes == lab

    big = _upscale(keep, scale, (h, w))
    # On dilate un peu pour ne pas rogner les bords fins sous-échantillonnés.
    grown = _shift_max(big.astype(np.float32), 1) > 0
    filled = _upscale(keep & ~small, scale, (h, w)) & grown
    return (mask & grown) | filled


def background_color(rgb: np.ndarray, border: int = 8) -> np.ndarray:
    strip = np.concatenate([rgb[:border].reshape(-1, 3), rgb[-border:].reshape(-1, 3),
                            rgb[:, :border].reshape(-1, 3), rgb[:, -border:].reshape(-1, 3)])
    return np.median(strip, axis=0)


def matte_builtin(img: Image.Image) -> Image.Image:
    """Alpha par écart à la couleur du fond, adouci et nettoyé."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    bg = background_color(rgb)
    dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
    # Rampe douce entre 18 et 42 niveaux d'écart : le bruit du fond reste
    # transparent, un vêtement gris proche du fond reste opaque.
    alpha = np.clip((dist - 18.0) / 24.0, 0.0, 1.0)
    solid = _clean(alpha > 0.5)
    # Le contour garde sa rampe douce ; l'intérieur de la silhouette est
    # plein, même là où le vêtement frôle la couleur du fond.
    inner = _shift_min(solid.astype(np.float32), 1) > 0.5
    alpha = np.where(inner, 1.0, np.where(solid, np.maximum(alpha, 0.5), 0.0))
    out = img.convert("RGBA")
    out.putalpha(Image.fromarray((alpha * 255).astype(np.uint8)))
    return out


def with_mask(img: Image.Image, mask: Image.Image) -> Image.Image:
    """Pose un masque de détoureur en alpha. On ne garde que la plus
    grande tache — une ombre portée détachée, une poussière s'en vont —
    et le contour garde la douceur du masque."""
    soft = np.asarray(mask.convert("L").resize(img.size, Image.LANCZOS), dtype=np.float32)
    solid = _clean(soft > 127)
    inner = _shift_min(solid.astype(np.float32), 1) > 0.5
    alpha = np.where(inner, 255.0, np.where(solid, np.maximum(soft, 128.0), 0.0))
    out = img.convert("RGBA")
    out.putalpha(Image.fromarray(alpha.astype(np.uint8)))
    return out


def matte(img: Image.Image, engine: str = "builtin", *, workdir: Path | None = None) -> Image.Image:
    if img.mode == "RGBA" and np.asarray(img)[..., 3].min() < 250:
        return img  # déjà détouré
    if engine == "comfyui":
        from . import comfy

        mask = comfy.remove_background([img], workdir=workdir or Path.cwd() / ".matte")[0]
        return with_mask(img, mask)
    if engine == "rembg":
        from rembg import new_session, remove  # facultatif, lourd

        global _REMBG
        try:
            _REMBG
        except NameError:
            _REMBG = new_session("birefnet-general")
        return remove(img, session=_REMBG)
    return matte_builtin(img)


# ── orbite ─────────────────────────────────────────────────────────

def silhouette_width(mask: Image.Image) -> int:
    a = np.asarray(mask.convert("L")) > 127
    xs = np.nonzero(a.any(axis=0))[0]
    return int(xs[-1] - xs[0]) if len(xs) else 0


def orbit_picks(widths: list[int], azimuths: dict[str, float]) -> dict[str, dict]:
    """Choisit les frames d'une orbite sur la largeur de la silhouette.

    La caméra de H3 ne tourne pas à vitesse constante (départ lent,
    relevé sur la machine) : on ne peut pas prendre les frames au prorata.
    Mais en A-pose, bras écartés, la largeur est maximale de face et de
    dos et minimale de profil, avec des creux nets. D'où les repères :
    la face au départ (le prompt part de face), les deux profils aux deux
    creux, le dos au sommet entre eux. Le 3/4 se lit sur l'envergure :
    tant que les bras dominent, largeur ≈ envergure × cos(azimut).

    Le sens de rotation est celui que le prompt demande — vers la gauche
    du sujet, donc le premier creux est le profil gauche. Il est supposé,
    pas mesuré : c'est ce que SAM 3D Body viendra vérifier."""
    w = np.convolve(np.asarray(widths, dtype=np.float64), np.ones(3) / 3, mode="same")
    w[0], w[-1] = widths[0], widths[-1]
    n = len(w)
    first = int(np.argmin(w[: n // 2 + n // 8]))
    rest = w.copy()
    rest[: first + n // 6] = np.inf
    second = int(np.argmin(rest))
    back = first + int(np.argmax(w[first: second + 1]))
    front_w = float(w[0])
    marks = {0.0: 0, 90.0: first, 180.0: back, 270.0: second}

    def plateau(i: int) -> int:
        """Combien de frames, de part et d'autre, restent à 1 % de la
        largeur du repère : le repère peut être n'importe où là-dedans."""
        tol, a, b = 0.01 * w[i], i, i
        while a > 0 and abs(w[a - 1] - w[i]) <= tol:
            a -= 1
        while b < n - 1 and abs(w[b + 1] - w[i]) <= tol:
            b += 1
        return max(i - a, b - i)

    picks = {"_marks": {"front": 0, "left": first, "back": back, "right": second, "frames": n}}
    for name, az in azimuths.items():
        if az == 0.0:
            picks[name] = {"frame": 0, "azimuth": 0.0, "precision_deg": None, "source": "départ du plan"}
        elif az in marks:
            i = marks[az]
            # Vitesse locale de la caméra : 90° entre ce repère et ses voisins.
            around = [marks.get(az - 90.0, 0), marks.get(az + 90.0, n - 1)]
            speed = max(90.0 / max(1, abs(i - j)) for j in around)
            picks[name] = {"frame": i, "azimuth": az, "precision_deg": round(max(0.5, plateau(i)) * speed, 1),
                           "source": "repère de silhouette"}
        elif 0.0 < az < 90.0:
            target = front_w * np.cos(np.radians(az))
            i = int(np.argmin(np.abs(w[: first + 1] - target)))
            est = float(np.degrees(np.arccos(np.clip(w[i] / front_w, -1.0, 1.0))))
            picks[name] = {"frame": i, "azimuth": round(est, 1), "precision_deg": round(90.0 / max(1, first), 1),
                           "source": "envergure (largeur ≈ cos)"}
        else:
            raise ValueError(f"azimut {az}° : pas de repère sur l'orbite pour le choisir")
    return picks


# ── mesure et normalisation ────────────────────────────────────────

def silhouette_box(rgba: Image.Image, threshold: int = 128) -> tuple[int, int, int, int] | None:
    a = np.asarray(rgba.getchannel("A"))
    ys, xs = np.nonzero(a >= threshold)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def margins(box: tuple[int, int, int, int], size: tuple[int, int]) -> dict:
    x0, y0, x1, y1 = box
    w, h = size
    return {"left": x0 / w, "right": (w - x1) / w, "top": y0 / h, "bottom": (h - y1) / h}


def normalize_views(views: dict[str, Image.Image], *, size: int = 1024, margin: float = 0.08) -> tuple[dict, dict]:
    """Met toutes les silhouettes à la même hauteur, centrées, pieds sur
    la même ligne, dans un carré transparent. Rend les images et un
    rapport chiffré, avant et après.

    La hauteur est le seul invariant d'une vue à l'autre : un profil est
    plus étroit qu'une face, mais le personnage a la même taille sous
    tous les angles. C'est donc sur elle qu'on règle l'échelle."""
    target_h = size * (1.0 - 2.0 * margin)
    out: dict[str, Image.Image] = {}
    report: dict[str, dict] = {}
    heights = {}
    for name, img in views.items():
        box = silhouette_box(img)
        if box is None:
            raise ValueError(f"vue {name} : aucune silhouette détectée")
        heights[name] = box[3] - box[1]
        report[name] = {"box_before": list(box), "margins_before": margins(box, img.size),
                        "silhouette_height_px": heights[name]}

    for name, img in views.items():
        x0, y0, x1, y1 = report[name]["box_before"]
        crop = img.crop((x0, y0, x1, y1))
        k = target_h / (y1 - y0)
        w = max(1, round(crop.width * k))
        h = max(1, round(crop.height * k))
        crop = crop.resize((w, h), Image.LANCZOS)
        if w > size:
            raise ValueError(f"vue {name} : silhouette plus large que haute, le cadrage est à revoir")
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        left = (size - w) // 2
        top = round(size * margin)
        canvas.alpha_composite(crop, (left, top))
        out[name] = canvas
        box = silhouette_box(canvas)
        report[name].update(scale=round(k, 4), box_after=list(box), margins_after=margins(box, canvas.size))

    hs = np.array(list(heights.values()), dtype=np.float64)
    spread = float((hs.max() - hs.min()) / hs.mean()) if len(hs) > 1 else 0.0
    summary = {"size": size, "margin": margin, "height_spread_before": round(spread, 4)}
    return out, {"views": report, "summary": summary}


def on_neutral(rgba: Image.Image, color: tuple[int, int, int] = NEUTRAL) -> Image.Image:
    base = Image.new("RGBA", rgba.size, color + (255,))
    base.alpha_composite(rgba)
    return base.convert("RGB")


# ── choix d'une frame ──────────────────────────────────────────────

def sharpness(img: Image.Image) -> float:
    """Variance du laplacien : plus c'est net, plus c'est grand."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def sharpest(frames: list[Image.Image]) -> int:
    """Sur cinq frames d'un plan figé, on garde la plus nette."""
    return int(np.argmax([sharpness(f) for f in frames]))


# ── planche contact ────────────────────────────────────────────────

def contact_sheet(images: list[tuple[str, Image.Image]], *, cell: int = 320, cols: int = 4) -> Image.Image:
    """Toutes les images côte à côte, légendées : pour choisir d'un coup
    d'œil entre des candidats, ou relire quatre vues."""
    rows = (len(images) + cols - 1) // cols
    pad, cap = 10, 22
    # Les teintes de --panel et --cy dans assets/tokens.css.
    sheet = Image.new("RGB", (cols * (cell + pad) + pad, rows * (cell + cap + pad) + pad), (16, 20, 19))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for i, (label, img) in enumerate(images):
        r, c = divmod(i, cols)
        x, y = pad + c * (cell + pad), pad + r * (cell + cap + pad)
        thumb = img.copy()
        thumb.thumbnail((cell, cell))
        if thumb.mode == "RGBA":
            thumb = on_neutral(thumb)
        sheet.paste(thumb, (x + (cell - thumb.width) // 2, y + (cell - thumb.height) // 2))
        draw.text((x, y + cell + 5), label, fill=(185, 207, 216), font=font)
    return sheet


def load(path: str | Path) -> Image.Image:
    img = Image.open(path)
    img.load()
    return img


VIDEO = {".mp4", ".webm", ".mov", ".mkv", ".avi"}


def frames_of(path: str | Path) -> list[Image.Image]:
    """Toutes les frames d'une sortie : une image, une image animée
    (WEBP, GIF, PNG animé) ou une vidéo — celle-ci par ffmpeg, que
    ComfyUI a presque toujours à côté de lui."""
    path = Path(path)
    if path.suffix.lower() in VIDEO:
        import shutil
        import subprocess
        import tempfile

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(f"{path.name} est une vidéo et ffmpeg est introuvable — installe-le, ou ajoute "
                               f"un SaveImage au workflow")
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([ffmpeg, "-loglevel", "error", "-i", str(path), f"{tmp}/f_%05d.png"], check=True)
            return [load(p).convert("RGB") for p in sorted(Path(tmp).glob("f_*.png"))]
    img = Image.open(path)
    out = []
    for i in range(getattr(img, "n_frames", 1)):
        img.seek(i)
        out.append(img.convert("RGB").copy())
    return out
