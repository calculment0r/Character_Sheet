"""La planche de présentation, composée en code (Pillow), en 3840 × 2160.

Une planche de modèle, calme : le nom et une ligne d'identité en tête,
la palette à côté ; à gauche les poses naturelles sur une même ligne de
sol et à une même échelle (px/m), une réglette de 0 à 2 m et une
silhouette de référence de 1,75 m ; à droite les expressions, yeux
alignés sur une même ligne et même écart entre les yeux, puis les
détails, posés sur la ligne de sol des poses. Filets fins, étiquettes en
capitales mono espacées, le nom en Venus Rising, la prose en Chakra
Petch — les règles du thème (`CLAUDE.md`).

Les couleurs viennent de `assets/tokens.css`, lues à l'exécution : la
variante sombre reprend les jetons de l'application (--bg, --panel,
--ink…), la claire les jetons --paper* (fond de studio gris).
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config

W, H = 3840, 2160
M = 120
THEMES = ("clair", "sombre")
DEFAULT_THEME = "clair"
FONTS = config.REPO / "assets/fonts"

ROLES = {
    "clair":  {"bg": "--paper", "tile": "--paper2", "ink": "--paper-ink", "ink2": "--paper-ink2",
               "ink3": "--paper-ink3", "line": "--paper-line", "shade": "--paper-shade"},
    "sombre": {"bg": "--bg", "tile": "--panel", "ink": "--ink", "ink2": "--ink2", "ink3": "--ink3",
               "line": "--line", "shade": "--shade"},
}


# ── jetons et fontes ───────────────────────────────────────────────

def tokens() -> dict[str, tuple[int, int, int, int]]:
    """Les couleurs de `assets/tokens.css`, en RGBA."""
    css = (config.REPO / "assets/tokens.css").read_text(encoding="utf-8")
    out = {}
    for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", css):
        value = value.strip()
        if m := re.fullmatch(r"#([0-9a-fA-F]{6})", value):
            h = m.group(1)
            out[name] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
        elif m := re.fullmatch(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)", value):
            out[name] = (int(m.group(1)), int(m.group(2)), int(m.group(3)), round(float(m.group(4)) * 255))
    return out


def colors(theme: str) -> dict[str, tuple[int, int, int, int]]:
    tok = tokens()
    return {role: tok[name] for role, name in ROLES[theme].items()}


def font(kind: str, size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    path = {"display": "venus-rising.otf", "mono": "azeret-mono.ttf", "prose": "chakra-petch-light.ttf"}[kind]
    f = ImageFont.truetype(str(FONTS / path), size)
    if kind == "mono":
        f.set_variation_by_name(weight)
    return f


def text_width(s: str, f: ImageFont.FreeTypeFont, tracking: float = 0.0) -> float:
    return sum(f.getlength(c) for c in s) + tracking * f.size * max(len(s) - 1, 0)


def text(d: ImageDraw.ImageDraw, xy, s: str, f: ImageFont.FreeTypeFont, fill, tracking: float = 0.0,
         align: str = "left") -> float:
    """Du texte espacé (`tracking` en em), aligné à gauche, au centre ou
    à droite de `xy` ; `xy` est le haut de la ligne. Rend sa largeur."""
    x, y = xy
    width = text_width(s, f, tracking)
    if align == "center":
        x -= width / 2
    elif align == "right":
        x -= width
    if not tracking:
        d.text((x, y), s, font=f, fill=fill)
        return width
    for c in s:
        d.text((x, y), c, font=f, fill=fill)
        x += f.getlength(c) + tracking * f.size
    return width


def label(d, cx: float, y: float, s: str, f, fill, room: float, tracking: float = 0.2) -> None:
    """Une étiquette centrée ; sur deux lignes si elle déborde de `room`,
    coupée à l'espace le plus proche du milieu."""
    if text_width(s, f, tracking) <= room or " " not in s:
        text(d, (cx, y), s, f, fill, tracking, "center")
        return
    cuts = [i for i, c in enumerate(s) if c == " "]
    i = min(cuts, key=lambda k: abs(k - len(s) / 2))
    text(d, (cx, y), s[:i], f, fill, tracking, "center")
    text(d, (cx, y + f.size * 1.5), s[i + 1:], f, fill, tracking, "center")


def wrap(s: str, f: ImageFont.FreeTypeFont, width: float) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        test = f"{cur} {word}".strip()
        if f.getlength(test) <= width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def caps(s: str) -> str:
    return s.upper().replace("'", "’")


# ── palette ────────────────────────────────────────────────────────

def _to_lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]]).T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1)


def palette(rgba: Image.Image, k: int = 8, keep: int = 6, merge: float = 14.0, floor: float = 0.012) -> list[dict]:
    """Les couleurs du personnage détouré : k-means dans Lab (k-means++,
    graine fixe), les groupes proches (ΔE < `merge`) fondus, triés par
    surface. Rend [{hex, share}] ; la couleur est la médiane du groupe."""
    a = np.asarray(rgba.convert("RGBA"))
    px = a[..., :3][a[..., 3] > 220].reshape(-1, 3).astype(np.float64)
    if len(px) < k:
        return []
    px = px[:: max(1, len(px) // 40000)]
    lab = _to_lab(px)
    rng = np.random.default_rng(0)
    centers = [lab[rng.integers(len(lab))]]
    for _ in range(k - 1):
        d2 = np.min([((lab - c) ** 2).sum(1) for c in centers], axis=0)
        if d2.sum() <= 0:       # moins de couleurs que de groupes
            break
        centers.append(lab[rng.choice(len(lab), p=d2 / d2.sum())])
    centers = np.array(centers)
    k = len(centers)
    for _ in range(20):
        label = np.argmin(((lab[:, None, :] - centers[None]) ** 2).sum(2), axis=1)
        centers = np.array([lab[label == i].mean(0) if (label == i).any() else centers[i] for i in range(k)])
    groups = [[i] for i in range(k)]
    share = np.bincount(label, minlength=k) / len(lab)
    merged = True
    while merged:
        merged = False
        cent = [np.average(centers[g], axis=0, weights=share[g] + 1e-9) for g in groups]
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if np.linalg.norm(cent[i] - cent[j]) < merge:
                    groups[i] += groups.pop(j)
                    merged = True
                    break
            if merged:
                break
    out = []
    for g in groups:
        mask = np.isin(label, g)
        s = float(mask.mean())
        if s < floor:
            continue
        rgb = np.median(px[mask], axis=0)
        out.append({"hex": "#{:02x}{:02x}{:02x}".format(*(int(round(v)) for v in rgb)), "share": round(s, 4)})
    return sorted(out, key=lambda c: -c["share"])[:keep]


# ── pièces ─────────────────────────────────────────────────────────

def _bbox(rgba: Image.Image, threshold: int = 40) -> tuple[int, int, int, int]:
    a = np.asarray(rgba.getchannel("A")) > threshold
    ys, xs = np.nonzero(a)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _shadow(canvas: Image.Image, cx: float, y: float, w: float, color) -> None:
    """Une ombre de contact douce sous les pieds."""
    pad = 60
    layer = Image.new("L", (int(w + 2 * pad), 2 * pad), 0)
    ImageDraw.Draw(layer).ellipse((pad, pad - 11, pad + w, pad + 11), fill=color[3])
    layer = layer.filter(ImageFilter.GaussianBlur(10))
    solid = Image.new("RGBA", layer.size, color[:3] + (255,))
    solid.putalpha(layer)
    canvas.alpha_composite(solid, (int(cx - w / 2 - pad), int(y - pad)))


def reference_figure(height_px: float, color) -> Image.Image:
    """Une silhouette humaine neutre, en aplat, de `height_px` de haut :
    la référence de 1,75 m. Dessinée en quatre fois plus grand, réduite."""
    s = 4
    hh = height_px * s
    w = int(hh * 0.30)
    img = Image.new("L", (w, int(hh) + 4 * s), 0)
    d = ImageDraw.Draw(img)
    cx = w / 2
    y = lambda f: hh * (1 - f)  # noqa: E731  (f : fraction de la hauteur, depuis le sol)

    def limb(x0, f0, x1, f1, width):
        d.line((cx + x0 * hh, y(f0), cx + x1 * hh, y(f1)), fill=255, width=int(width * hh))
        for x, f in ((x0, f0), (x1, f1)):
            r = width * hh / 2
            d.ellipse((cx + x * hh - r, y(f) - r, cx + x * hh + r, y(f) + r), fill=255)

    d.ellipse((cx - 0.048 * hh, y(0.995), cx + 0.048 * hh, y(0.872)), fill=255)          # tête
    limb(0, 0.87, 0, 0.83, 0.045)                                                          # cou
    d.polygon([(cx - 0.104 * hh, y(0.818)), (cx + 0.104 * hh, y(0.818)), (cx + 0.076 * hh, y(0.61)),
               (cx + 0.088 * hh, y(0.50)), (cx - 0.088 * hh, y(0.50)), (cx - 0.076 * hh, y(0.61))], fill=255)
    limb(-0.098, 0.80, -0.118, 0.625, 0.044)                                               # bras
    limb(0.098, 0.80, 0.118, 0.625, 0.044)
    limb(-0.118, 0.625, -0.126, 0.46, 0.036)                                               # avant-bras
    limb(0.118, 0.625, 0.126, 0.46, 0.036)
    limb(-0.127, 0.455, -0.127, 0.405, 0.030)                                              # mains
    limb(0.127, 0.455, 0.127, 0.405, 0.030)
    limb(-0.046, 0.50, -0.050, 0.27, 0.070)                                                # cuisses
    limb(0.046, 0.50, 0.050, 0.27, 0.070)
    limb(-0.050, 0.27, -0.052, 0.035, 0.050)                                               # jambes
    limb(0.050, 0.27, 0.052, 0.035, 0.050)
    limb(-0.052, 0.016, -0.074, 0.012, 0.026)                                              # pieds
    limb(0.052, 0.016, 0.074, 0.012, 0.026)
    small = img.resize((w // s, img.height // s), Image.LANCZOS)
    out = Image.new("RGBA", small.size, color[:3] + (255,))
    out.putalpha(small.point(lambda v: v * color[3] // 255))
    return out


# ── la planche ─────────────────────────────────────────────────────

def _facts(p, cos: dict, height: float) -> tuple[str, str]:
    sheet = p.sheet
    facts = [sheet.get("gender", "")]
    age = str(sheet.get("age") or "")
    if re.search(r"\d", age):
        facts.append(age if re.search(r"[a-zA-Z]", age) else f"{age} ans")
    facts += [sheet.get("ethnicity", ""), f"{height:.2f} m".replace(".", ","), sheet.get("body_type", ""),
              sheet.get("role", ""), f"tenue {cos.get('name') or ''}".strip()]
    line = "  ·  ".join(caps(str(f).strip()) for f in facts if str(f).strip() and len(str(f).strip()) > 1)
    prose = next((str(sheet.get(k)).strip() for k in ("core_theme", "personality_traits", "face_description")
                  if str(sheet.get(k) or "").strip()), "") or p.face.get("brief", "")
    return line, prose


def compose(p, key: str, dest: Path, *, theme: str = DEFAULT_THEME) -> Path:
    from .chain import height_m
    from .presentation import state_of

    cos = p.data["costumes"][key]
    st = state_of(cos)
    col = colors(theme)
    canvas = Image.new("RGBA", (W, H), col["bg"])
    d = ImageDraw.Draw(canvas, "RGBA")
    height = height_m(p.sheet)
    label_f, small_f = font("mono", 24), font("mono", 20)
    split = 2440            # la séparation des deux colonnes
    bx = split + 70         # début de la colonne de droite

    # En-tête : le nom, ce qu'on sait de lui, une ligne de prose, la palette.
    line, prose = _facts(p, cos, height)
    text(d, (M, M), caps(f"planche de présentation  ·  {p.data['slug']}"), label_f, col["ink3"], 0.24)
    name = caps(p.data["name"])
    size = 132
    while size > 60 and text_width(name, font("display", size), 0.06) > split - M - 60:
        size -= 6
    text(d, (M, M + 62), name, font("display", size), col["ink"], 0.06)
    text(d, (M, M + 62 + size + 44), line, font("mono", 28), col["ink2"], 0.2)
    pf = font("prose", 42)
    y = M + 58
    for ln in wrap(prose[:1].upper() + prose[1:], pf, W - M - bx)[:2]:
        d.text((bx, y), ln, font=pf, fill=col["ink2"])
        y += 58
    px0, py0 = bx, M + 262
    text(d, (px0, py0 - 44), "PALETTE", label_f, col["ink3"], 0.24)
    sw = min(170, (W - M - bx - 5 * 20) // 6)
    for i, c in enumerate(st.get("palette", [])[:6]):
        x = px0 + i * (sw + 20)
        rgb = tuple(int(c[k:k + 2], 16) for k in (1, 3, 5))
        d.rounded_rectangle((x, py0, x + sw, py0 + 64), radius=4, fill=rgb)
        d.rounded_rectangle((x, py0, x + sw, py0 + 64), radius=4, outline=col["line"], width=1)
        text(d, (x, py0 + 80), c.upper(), small_f, col["ink3"], 0.12)
    top = 500
    d.line((M, top, W - M, top), fill=col["line"], width=1)

    # Les poses : une échelle (px/m), une ligne de sol, une réglette.
    y0 = top + 56
    ground = H - M - 72
    d.line((split, y0, split, ground + 48), fill=col["line"], width=1)
    text(d, (M, y0), "01", label_f, col["ink2"], 0.24)
    text(d, (M + 64, y0), "POSES  ·  TAILLE", label_f, col["ink3"], 0.24)
    ppm = (ground - (y0 + 90)) / 2.0
    figures = _pose_figures(p, st, height, p.path(f"costumes/{key}/presentation"))
    ruler_w, ref_label = 120, "RÉF. 1,75 M"
    gap_min = 28
    ref = reference_figure(1.75 * ppm, col["tile"])
    widths = [ref.width] + [f["w"] * ppm for f in figures]
    room = (split - 60) - (M + ruler_w)
    need = sum(widths) + gap_min * (len(widths))
    if need > room:
        ppm *= (room - gap_min * len(widths)) / sum(widths)
        ref = reference_figure(1.75 * ppm, col["tile"])
        widths = [ref.width] + [f["w"] * ppm for f in figures]
    gap = (room - sum(widths)) / max(len(widths), 1)
    _ruler(d, M + 70, ground, ppm, height, split - 60, col, small_f)
    x = M + ruler_w + gap / 2
    canvas.alpha_composite(ref, (int(x), int(ground - ref.height + 4)))
    text(d, (x + ref.width / 2, ground + 30), ref_label, small_f, col["ink3"], 0.2, "center")
    x += ref.width + gap
    for f, w in zip(figures, widths[1:]):
        img = f["img"].resize((max(1, int(f["img"].width * ppm / f["ppm"])), max(1, int(f["img"].height * ppm / f["ppm"]))),
                              Image.LANCZOS)
        bx0, by0, bx1, by1 = _bbox(img)
        img = img.crop((bx0, by0, bx1, by1))
        feet = _feet(img)
        _shadow(canvas, x + feet[0], ground, feet[1] * 1.25 + 40, col["shade"])
        canvas.alpha_composite(img, (int(x + (w - img.width) / 2), int(ground - img.height)))
        label(d, x + w / 2, ground + 30, caps(f["label"]), small_f, col["ink3"], w + gap - 16)
        x += w + gap

    # Les expressions : yeux sur une même ligne, même écart entre les yeux.
    text(d, (bx, y0), "02", label_f, col["ink2"], 0.24)
    text(d, (bx + 64, y0), "EXPRESSIONS", label_f, col["ink3"], 0.24)
    cols, gx = 3, 30
    tw = (W - M - bx - (cols - 1) * gx) / cols
    detail_side = (W - M - bx - 3 * 24) / 4
    lab_h = 58
    detail_top = ground - detail_side
    ey0 = y0 + 56
    th = (detail_top - 110 - ey0 - 2 * lab_h - 24) / 2
    for i, e in enumerate(st["panels"]["expressions"][:6]):
        r, c = divmod(i, cols)
        tx, ty = bx + c * (tw + gx), ey0 + r * (th + lab_h + 24)
        tile = _expression_tile(p, e, int(tw), int(th), col)
        canvas.alpha_composite(tile, (int(tx), int(ty)))
        text(d, (tx + tw / 2, ty + th + 18), caps(e["label"]), small_f, col["ink3"], 0.2, "center")

    # Les détails : des recadrages du plein pied, posés sur la ligne de sol.
    text(d, (bx, detail_top - 80), "03", label_f, col["ink2"], 0.24)
    text(d, (bx + 64, detail_top - 80), "DÉTAILS", label_f, col["ink3"], 0.24)
    for i, e in enumerate(st["panels"]["details"][:4]):
        tx = bx + i * (detail_side + 24)
        img = Image.open(p.path(e["file"])).convert("RGBA").resize((int(detail_side), int(detail_side)), Image.LANCZOS)
        canvas.alpha_composite(img, (int(tx), int(detail_top)))
        d.rectangle((tx, detail_top, tx + detail_side - 1, detail_top + detail_side - 1), outline=col["line"], width=1)
        text(d, (tx + detail_side / 2, ground + 30), caps(e["label"]), small_f, col["ink3"], 0.2, "center")

    text(d, (W - M, H - 62), caps(f"character factory  ·  {date.today():%d.%m.%Y}"), small_f, col["ink3"], 0.2,
         "right")
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(dest, optimize=True)
    return dest


def _ruler(d, x, ground, ppm, height, right, col, f) -> None:
    """La réglette de 0 à 2 m, un trait tous les 10 cm, et un filet à la
    taille du personnage, sur toute la largeur des poses."""
    d.line((x, ground, x, ground - 2.0 * ppm), fill=col["ink3"], width=1)
    for k in range(21):
        yy = ground - k * 0.1 * ppm
        long = k % 5 == 0
        d.line((x, yy, x + (22 if long else 11), yy), fill=col["ink3"], width=1)
        if long:
            lab = f"{k / 10:g}".replace(".", ",") + (" M" if k == 20 else "")
            text(d, (x - 14, yy - 11), lab, f, col["ink3"], 0.1, "right")
    d.line((x, ground, right, ground), fill=col["line"], width=1)
    yy = ground - height * ppm
    for xx in range(int(x + 30), int(right), 18):
        d.line((xx, yy, min(xx + 8, right), yy), fill=col["line"], width=1)


def _feet(img: Image.Image) -> tuple[float, float]:
    """Le centre et la largeur des pieds : les 3 % du bas de la silhouette."""
    a = np.asarray(img.getchannel("A")) > 60
    rows = a[int(a.shape[0] * 0.97):]
    xs = np.nonzero(rows.any(axis=0))[0]
    if not len(xs):
        return img.width / 2, img.width * 0.5
    return (xs.min() + xs.max()) / 2, float(xs.max() - xs.min())


def _pose_figures(p, st: dict, height: float, folder: Path) -> list[dict]:
    """Les poses détourées et leur échelle en px/m, une seule pour toutes.

    Le plein pied détouré mesure `height` mètres. Les poses sortent dans
    son cadre ; Qwen y garde à peu près sa taille, pas exactement. L'étalon
    est donc la tête : le rapport entre la hauteur du visage de chaque pose
    et celle du plein pied (MTCNN), dont on prend la médiane — une pose de
    dos ou de profil, au visage plus petit, ne tire pas l'échelle. Sans
    visages mesurés (factice), l'échelle du squelette."""
    fb = folder / "fullbody_cut.png"
    base_ppm = None
    if fb.exists():
        x0, y0, x1, y1 = _bbox(Image.open(fb).convert("RGBA"))
        base_ppm = (y1 - y0) / height
    face = st.get("fullbody_face")
    ratios = [(e["box"][3] - e["box"][1]) / (face[3] - face[1]) for e in st["panels"]["poses"]
              if face and e.get("box")]
    out = []
    for e in st["panels"]["poses"]:
        if not e.get("cut") or not p.path(e["cut"]).exists():
            continue
        img = Image.open(p.path(e["cut"])).convert("RGBA")
        if ratios:
            scale = float(np.median(ratios))
        else:
            skel = p.path(e.get("skeleton", "")).with_suffix(".json") if e.get("skeleton") else None
            scale = json.loads(skel.read_text(encoding="utf-8")).get("scale", 1.0) if skel and skel.exists() else 1.0
        ppm = (base_ppm or (_bbox(img)[3] - _bbox(img)[1]) / height) * scale
        x0, _, x1, _ = _bbox(img)
        out.append({"img": img, "ppm": ppm, "w": (x1 - x0) / ppm, "label": e["label"]})
    return out


def _expression_tile(p, e: dict, tw: int, th: int, col) -> Image.Image:
    """Une expression détourée sur son aplat : le milieu des yeux à 45 %
    de la hauteur, 23 % de la largeur entre les yeux."""
    tile = Image.new("RGBA", (tw, th), col["tile"])
    src = p.path(e.get("cut") or e["file"])
    img = Image.open(src).convert("RGBA")
    eyes = e.get("eyes")
    if eyes:
        (lx, ly), (rx, ry) = eyes
    else:
        lx, ly, rx, ry = img.width * 0.4, img.height * 0.42, img.width * 0.6, img.height * 0.42
    k = 0.23 * tw / max(rx - lx, 1.0)
    img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
    ex, ey = (lx + rx) / 2 * k, (ly + ry) / 2 * k
    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    layer.paste(img, (int(round(tw / 2 - ex)), int(round(0.45 * th - ey))), img)
    tile.alpha_composite(layer)
    ImageDraw.Draw(tile, "RGBA").rectangle((0, 0, tw - 1, th - 1), outline=col["line"], width=1)
    return tile
