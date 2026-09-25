"""La pose de référence : l'A-pose, imposée par un squelette.

Décision de Cal (25/09) : le plein pied se fait et se valide en pose
naturelle ; une fois validé, on le remet en A-pose, celle que préfèrent
le mesh et le rig (§8), pour les vues. Écrire l'A-pose dans le prompt ne
tient pas (bras le long du corps) : la pose s'impose par un squelette
OpenPose, donné à Qwen-Image 2.1 comme simple image de référence, sans
ControlNet — la méthode du workflow `qwen_image_2_1_image_edit_openpose`
publié pour ComfyUI. Voir `docs/ETUDES.md`.

  squelette  `tools/remote/apose_skeleton.py`, lancé avec le python de
             ComfyUI (DWPose y tourne) : relève le personnage, bras à
             45°, jambes à 4°, longueurs d'os gardées ; `--vues=` le
             tourne autour de l'axe vertical, même cadrage pour toutes
             les vues ;
  rendu      Qwen-Image 2.1 turbo, l'image en <image1>, le squelette en
             <image2>, à la taille du squelette.

Azimut : 0 face, 90 flanc gauche du personnage (+X, il regarde le bord
gauche de l'image), 180 dos, 270 flanc droit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from . import config
from .project import ChainError

SIZE = (1344, 1792)
RESOLUTION = 1056   # 1024 brouille les éditions (ComfyUI #16435)
SCRIPT = config.REPO / "tools/remote/apose_skeleton.py"

LOOK = ("Plain uniform light grey seamless studio background, soft even front light, eye-level camera. "
        "Photorealistic, true-to-life fabric textures, natural hands with five fingers, sharp focus.")
STYLIZED = ("Plain uniform light grey background, soft even light, eye-level camera. Stylised character design "
            "reference, clean shapes, consistent shading, true colours, natural hands.")
APOSE = ("a neutral A-pose, standing straight, arms straight and held about forty-five degrees out to the sides of "
         "the body, hands relaxed with the fingers pointing down and slightly apart, legs straight and slightly apart")

VIEWS = {
    45: "a three-quarter front view: the person is turned forty-five degrees to their right, so we see the front and "
        "the left side of the body",
    90: "an exact left profile: the person is turned ninety degrees to their right and faces the left edge of the "
        "image; we see only the left side of the body, the left arm and left leg nearest to the camera",
    180: "a back view: the camera is directly behind; we see the back, the back of the head and the hair from behind",
    270: "an exact right profile: the person is turned ninety degrees to their left and faces the right edge of the "
         "image; we see only the right side of the body, the right arm and right leg nearest to the camera",
    315: "a three-quarter front view: the person is turned forty-five degrees to their left, so we see the front and "
         "the right side of the body",
}


def text_apose(outfit: str, style: str = "photoreal") -> str:
    return " ".join(filter(None, [
        "Full-body studio photograph of the same person as in <image1>: the same face, hair, skin tone, age and body "
        "build, wearing exactly the same outfit as in <image1>" + (f" ({outfit.strip()})" if outfit.strip() else "")
        + ", every garment, colour, fold and detail unchanged.",
        f"The person now stands in the pose of the skeleton in <image2>: {APOSE}, facing the camera.",
        "The skeleton only guides the pose; the image shows the person alone, without lines or dots.",
        "The whole figure from the top of the head to the soles of the shoes is inside the frame.",
        LOOK if style == "photoreal" else STYLIZED,
    ]))


def text_view(azimuth: int, style: str = "photoreal") -> str:
    return " ".join([
        "Full-body studio photograph of the same person as in <image1>, with exactly the same face, hair, skin tone, "
        "body build and the exact same outfit, every garment, colour and detail unchanged, in the same pose: "
        f"{APOSE}.",
        f"The camera has moved around the person: this is {VIEWS[azimuth]}.",
        "The skeleton in <image2> shows the pose and the viewing angle; the image shows the person alone, without "
        "lines or dots.",
        "The whole figure from the top of the head to the soles of the shoes is inside the frame, at the same size "
        "and on the same ground line as in <image1>.",
        LOOK if style == "photoreal" else STYLIZED,
    ])


def skeletons(image: Path, dest: Path, azimuths: list[int] = (), report=lambda p, m: None) -> dict[int, Path]:
    """Le squelette A-pose de `image` dans `dest`, et un par azimut dans
    `<dest>_<azimut>.png`. Rend {0: dest, azimut: fichier…}."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = {0: dest, **{a: dest.with_name(f"{dest.stem}_{a:03d}.png") for a in azimuths}}
    if config.backend("portrait") == "stub":
        report(0.1, "factice · squelette")
        for a, path in out.items():
            _stub_skeleton(a).save(path)
        return out
    python = Path(config.setting("python_pose", "~/comfyui-env/bin/python")).expanduser()
    cmd = [str(python), str(SCRIPT), str(image), str(dest), str(SIZE[0]), str(SIZE[1])]
    if azimuths:
        cmd.append("--vues=" + ",".join(str(a) for a in azimuths))
    report(0.05, "DWPose · squelette")
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    missing = [p for p in out.values() if not p.exists()]
    if res.returncode or missing:
        tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
        raise ChainError("le squelette n'a pas pu être relevé (DWPose) : " + " / ".join(tail))
    return out


def _stub_skeleton(azimuth: int) -> Image.Image:
    """Un bonhomme en bâtons, pour les essais sans GPU."""
    import math

    w, h = SIZE
    img = Image.new("RGB", SIZE, (0, 0, 0))
    d = ImageDraw.Draw(img)
    c = math.cos(math.radians(azimuth))
    x = lambda dx: w / 2 + dx * c  # noqa: E731
    neck, hip = h * 0.2, h * 0.52
    for (x0, y0), (x1, y1), col in (((x(0), neck), (x(0), hip), (0, 0, 255)),
                                    ((x(-120), neck), (x(-420), h * 0.45), (255, 170, 0)),
                                    ((x(120), neck), (x(420), h * 0.45), (85, 255, 0)),
                                    ((x(-80), hip), (x(-110), h * 0.95), (0, 255, 85)),
                                    ((x(80), hip), (x(110), h * 0.95), (0, 85, 255))):
        d.line([(x0, y0), (x1, y1)], fill=col, width=8)
    d.ellipse([w / 2 - 60, h * 0.07, w / 2 + 60, h * 0.18], outline=(255, 0, 170), width=6)
    return img
